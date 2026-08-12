from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "database"))).expanduser()
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", str(DATA_DIR / "backups"))).expanduser()


def backup_sqlite(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source), timeout=30)
    try:
        dst = sqlite3.connect(str(destination))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def main() -> None:
    if not DATA_DIR.exists():
        raise SystemExit(f"Không tìm thấy DATA_DIR: {DATA_DIR}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / stamp
    target.mkdir(parents=True, exist_ok=True)

    backup_sqlite(DATA_DIR / "auth.db", target / "auth.db")

    users = DATA_DIR / "users"
    if users.exists():
        for db_file in users.glob("*.db"):
            backup_sqlite(db_file, target / "users" / db_file.name)

    print(f"Backup created: {target}")


if __name__ == "__main__":
    main()
