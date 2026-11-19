from __future__ import annotations

import os
import sqlite3
import tempfile

import oss2

from app.storage import OSS_PREFIX, _bucket


def main() -> None:
    bucket = _bucket()
    objects = list(oss2.ObjectIterator(bucket, prefix=f"{OSS_PREFIX}/backups/database/"))
    if not objects:
        raise RuntimeError("OSS 中没有数据库备份")
    latest = max(objects, key=lambda item: item.last_modified)
    descriptor, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(descriptor)
    try:
        bucket.get_object_to_file(latest.key, path)
        with sqlite3.connect(path) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            table_count = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"OSS 备份完整性检查失败：{integrity}")
        print(f"OSS_BACKUP_VERIFY_OK: {latest.key}, {latest.size} bytes, {table_count} tables")
    finally:
        os.remove(path)


if __name__ == "__main__":
    main()
