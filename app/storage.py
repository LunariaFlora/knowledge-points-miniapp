from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

try:
    import oss2
except ImportError:  # Local tests can run without the optional production dependency.
    oss2 = None


OSS_ACCESS_KEY_ID = os.environ.get("OSS_ACCESS_KEY_ID", "").strip()
OSS_ACCESS_KEY_SECRET = os.environ.get("OSS_ACCESS_KEY_SECRET", "").strip()
OSS_ENDPOINT = os.environ.get("OSS_ENDPOINT", "").strip()
OSS_BUCKET = os.environ.get("OSS_BUCKET", "").strip()
OSS_PREFIX = os.environ.get("OSS_PREFIX", "knowledge-points").strip().strip("/")
OSS_SIGNED_URL_SECONDS = int(os.environ.get("OSS_SIGNED_URL_SECONDS", "21600"))


def enabled() -> bool:
    return bool(oss2 and OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET and OSS_ENDPOINT and OSS_BUCKET)


def _bucket():
    if not enabled():
        raise RuntimeError("OSS storage is not configured")
    return oss2.Bucket(
        oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET), OSS_ENDPOINT, OSS_BUCKET
    )


def _object_key(storage_ref: str) -> str:
    prefix = f"oss://{OSS_BUCKET}/"
    if not storage_ref.startswith(prefix):
        raise ValueError("OSS storage reference does not belong to the configured bucket")
    return storage_ref[len(prefix) :]


def resolve_url(value: str) -> str:
    if not value or not value.startswith("oss://"):
        return value
    if not enabled():
        return ""
    return _bucket().sign_url("GET", _object_key(value), OSS_SIGNED_URL_SECONDS)


def new_upload(kind: str, filename: str, content_type: str, size: int) -> dict:
    allowed = {
        "video": {
            "video/mp4": ("mp4", 5 * 1024 * 1024 * 1024),
            "video/webm": ("webm", 5 * 1024 * 1024 * 1024),
        },
        "image": {
            "image/png": ("png", 5 * 1024 * 1024),
            "image/jpeg": ("jpg", 5 * 1024 * 1024),
            "image/webp": ("webp", 5 * 1024 * 1024),
        },
    }
    if kind not in allowed or content_type not in allowed[kind]:
        raise ValueError("unsupported upload type")
    extension, max_size = allowed[kind][content_type]
    if size <= 0 or size > max_size:
        raise ValueError("invalid upload size")
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", filename.rsplit(".", 1)[0]).strip(".-")[:50]
    safe_stem = safe_stem or kind
    day = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    object_key = f"{OSS_PREFIX}/{kind}s/{day}/{safe_stem}-{uuid.uuid4().hex}.{extension}"
    storage_ref = f"oss://{OSS_BUCKET}/{object_key}"
    headers = {"Content-Type": content_type}
    upload_url = _bucket().sign_url("PUT", object_key, 900, headers=headers)
    return {
        "uploadUrl": upload_url,
        "storageRef": storage_ref,
        "url": resolve_url(storage_ref),
        "headers": headers,
        "size": size,
    }


def list_media_objects() -> list[dict]:
    bucket = _bucket()
    items: list[dict] = []
    for kind in ("video", "image"):
        prefix = f"{OSS_PREFIX}/{kind}s/"
        for item in oss2.ObjectIterator(bucket, prefix=prefix):
            items.append(
                {
                    "storageRef": f"oss://{OSS_BUCKET}/{item.key}",
                    "kind": kind.upper(),
                    "size": int(item.size),
                    "lastModified": datetime.fromtimestamp(
                        int(item.last_modified), timezone.utc
                    ).isoformat(timespec="seconds"),
                }
            )
    return items


def delete_object(storage_ref: str) -> None:
    _bucket().delete_object(_object_key(storage_ref))


def upload_file(object_key: str, source_path: str, content_type: str) -> str:
    if not object_key.startswith(f"{OSS_PREFIX}/backups/"):
        raise ValueError("backup object key must stay inside the configured backup prefix")
    _bucket().put_object_from_file(object_key, source_path, headers={"Content-Type": content_type})
    return f"oss://{OSS_BUCKET}/{object_key}"
