from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request_json(base_url: str, path: str, method: str = "GET", payload=None, token="") -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{base_url}{path}", data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed ({error.code}): {message}") from error


def upload_file(base_url: str, token: str, kind: str, path: Path) -> dict:
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    signed = request_json(
        base_url,
        "/api/admin/uploads/presign",
        "POST",
        {
            "kind": kind,
            "filename": path.name,
            "contentType": content_type,
            "size": path.stat().st_size,
        },
        token,
    )
    request = Request(
        signed["uploadUrl"],
        data=path.read_bytes(),
        method="PUT",
        headers={"Content-Type": content_type},
    )
    with urlopen(request, timeout=600) as response:
        if response.status not in {200, 201}:
            raise RuntimeError(f"OSS upload failed: {response.status}")
    return signed


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload local videos through the production admin API")
    parser.add_argument("manifest", type=Path, help="JSON list containing file, cover, title, summary and durationSeconds")
    parser.add_argument("--base-url", default="http://127.0.0.1:8770")
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not password:
        raise RuntimeError("ADMIN_PASSWORD is required")

    login = request_json(
        args.base_url,
        "/api/admin/login",
        "POST",
        {"username": args.username, "password": password},
    )
    token = login["token"]
    existing = request_json(args.base_url, "/api/admin/videos", token=token).get("items", [])
    existing_titles = {item["title"] for item in existing}
    entries = json.loads(args.manifest.read_text(encoding="utf-8"))

    for entry in entries:
        title = str(entry["title"])
        if title in existing_titles:
            print(f"SKIP existing: {title}")
            continue
        video = upload_file(args.base_url, token, "video", Path(entry["file"]))
        cover = upload_file(args.base_url, token, "image", Path(entry["cover"]))
        created = request_json(
            args.base_url,
            "/api/admin/videos",
            "POST",
            {
                "title": title,
                "summary": entry["summary"],
                "coverUrl": cover["storageRef"],
                "mediaUrl": video["storageRef"],
                "category": entry.get("category", "Health Knowledge"),
                "durationSeconds": int(entry["durationSeconds"]),
                "status": entry.get("status", "PUBLISHED"),
            },
            token,
        )
        print(f"CREATED video {created['id']}: {title}")


if __name__ == "__main__":
    main()
