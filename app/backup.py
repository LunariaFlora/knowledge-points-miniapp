from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

try:
    from .storage import upload_file
except ImportError:
    from storage import upload_file


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "database" / "dev.sqlite3"


def verified_backup(source: Path, destination: Path) -> Path:
    if not source.is_file():
        raise FileNotFoundError(f"数据库不存在：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)
        integrity = backup_db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"备份完整性检查失败：{integrity}")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and verify a SQLite backup")
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--oss-key", help="Upload the verified backup to this private OSS object key")
    args = parser.parse_args()
    output = args.output or ROOT / "backups" / f"dev-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
    verified = verified_backup(args.source.resolve(), output.resolve())
    print(verified)
    if args.oss_key:
        print(upload_file(args.oss_key, str(verified), "application/vnd.sqlite3"))


if __name__ == "__main__":
    main()
