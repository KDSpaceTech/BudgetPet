"""Optional one-time migration from the previous local multi-user SQLite layout to Turso.

Expected local layout:
    database/auth.db
    database/users/*.db

Environment:
    TURSO_DATABASE_URL, TURSO_AUTH_TOKEN, TURSO_ORG, TURSO_PLATFORM_TOKEN,
    TURSO_GROUP=default

Existing passwords are preserved; sessions are intentionally not migrated.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from turso_http import TursoPlatformAPI, connect_turso
from app import init_auth_db, init_user_database

BASE = Path(__file__).resolve().parent
LOCAL_DATA = Path(os.environ.get("LOCAL_DATA_DIR", str(BASE / "database"))).expanduser()
LOCAL_AUTH = LOCAL_DATA / "auth.db"


def main() -> int:
    if not LOCAL_AUTH.exists():
        print(f"Không tìm thấy {LOCAL_AUTH}. Đặt LOCAL_DATA_DIR nếu database cũ nằm chỗ khác.")
        return 2
    org=os.environ.get("TURSO_ORG","").strip(); platform_token=os.environ.get("TURSO_PLATFORM_TOKEN","").strip()
    if not org or not platform_token:
        print("Thiếu TURSO_ORG/TURSO_PLATFORM_TOKEN.")
        return 2

    init_auth_db()
    platform=TursoPlatformAPI(org,platform_token)
    auth_local=sqlite3.connect(LOCAL_AUTH); auth_local.row_factory=sqlite3.Row
    rows=auth_local.execute("SELECT * FROM users ORDER BY id").fetchall()
    auth_remote=__import__('app').get_auth_connection()
    try:
        for row in rows:
            exists=auth_remote.execute("SELECT id FROM users WHERE username=? COLLATE NOCASE",(row['username'],)).fetchone()
            if exists:
                print(f"SKIP {row['username']}: đã tồn tại trên Turso")
                continue
            db_name=f"budgetpet-user-{os.urandom(10).hex()}"
            db=platform.create_database(db_name, os.environ.get('TURSO_GROUP','default'))
            host=db.get('Hostname') or db.get('hostname')
            token=platform.create_database_token(db_name, expiration="30d")
            init_user_database((host,token))
            src_path=Path(row['user_db'])
            if not src_path.is_absolute(): src_path=(BASE/src_path).resolve()
            if src_path.exists():
                src=sqlite3.connect(src_path); src.row_factory=sqlite3.Row
                dst=connect_turso(host,token)
                tables=[r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
                for table in tables:
                    cols=[r[1] for r in src.execute(f'PRAGMA table_info("{table}")').fetchall()]
                    if not cols: continue
                    placeholders=','.join('?' for _ in cols); quoted=','.join('"'+c.replace('"','""')+'"' for c in cols)
                    for rec in src.execute(f'SELECT {quoted} FROM "{table}"'):
                        dst.execute(f'INSERT OR REPLACE INTO "{table}" ({quoted}) VALUES ({placeholders})', tuple(rec[c] for c in cols))
                dst.commit(); dst.close(); src.close()
            auth_remote.execute("INSERT INTO users(username,display_name,password_hash,password_salt,user_db,user_host) VALUES(?,?,?,?,?,?)",(row['username'],row['display_name'],row['password_hash'],row['password_salt'],db_name,host))
            auth_remote.commit()
            print(f"MIGRATED {row['username']} -> {db_name}")
        return 0
    finally:
        auth_remote.close(); auth_local.close()


if __name__ == '__main__':
    raise SystemExit(main())
