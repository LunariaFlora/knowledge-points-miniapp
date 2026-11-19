from __future__ import annotations

import argparse
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a live SQLite backup and verify that it can be reopened")
    parser.add_argument("database", nargs="?", default="database/dev.sqlite3")
    args = parser.parse_args()
    source_path = Path(args.database).resolve()
    if not source_path.is_file():
        raise SystemExit(f"database not found: {source_path}")

    with tempfile.TemporaryDirectory(prefix="knowledge-points-restore-") as temp_dir:
        restored_path = Path(temp_dir) / "restored.sqlite3"
        with closing(sqlite3.connect(source_path)) as source, closing(sqlite3.connect(restored_path)) as restored:
            source_counts = table_counts(source)
            source.backup(restored)
        with closing(sqlite3.connect(restored_path)) as restored:
            integrity = restored.execute("PRAGMA integrity_check").fetchone()[0]
            restored_counts = table_counts(restored)

    if integrity != "ok" or source_counts != restored_counts:
        raise SystemExit("DATABASE_BACKUP_RESTORE_FAILED")
    print(f"DATABASE_BACKUP_RESTORE_OK: {len(source_counts)} tables, integrity={integrity}")


if __name__ == "__main__":
    main()
