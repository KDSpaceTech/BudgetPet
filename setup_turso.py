"""One-time setup helper for a BudgetPet Turso account.

Required environment variables before running:
    TURSO_ORG
    TURSO_PLATFORM_TOKEN
    TURSO_GROUP=default
    TURSO_AUTH_DB_NAME=budgetpet-auth

The script creates/reuses the central auth database and prints the two
application secrets needed by BudgetPet: TURSO_DATABASE_URL and
TURSO_AUTH_TOKEN. Never commit the printed token.
"""
from __future__ import annotations

import os
import sys

from turso_http import TursoPlatformAPI


def main() -> int:
    org = os.environ.get("TURSO_ORG", "").strip()
    platform_token = os.environ.get("TURSO_PLATFORM_TOKEN", "").strip()
    group = os.environ.get("TURSO_GROUP", "default").strip() or "default"
    db_name = os.environ.get("TURSO_AUTH_DB_NAME", "budgetpet-auth").strip() or "budgetpet-auth"

    if not org or not platform_token:
        print("Thiếu TURSO_ORG hoặc TURSO_PLATFORM_TOKEN.")
        return 2

    api = TursoPlatformAPI(org, platform_token)
    try:
        try:
            db = api.get_database(db_name)
            print(f"Đã tìm thấy database auth: {db_name}")
        except Exception:
            db = api.create_database(db_name, group)
            print(f"Đã tạo database auth: {db_name}")
        host = db.get("Hostname") or db.get("hostname")
        token = api.create_database_token(db_name, expiration="30d")
        print("\nThêm các biến sau vào Render → Environment:")
        print(f"TURSO_DATABASE_URL=https://{host}")
        print(f"TURSO_AUTH_TOKEN={token}")
        print(f"TURSO_ORG={org}")
        print("TURSO_GROUP=" + group)
        print("TURSO_PLATFORM_TOKEN=<giữ bí mật, dùng để tạo database cho user>")
        print("\nKhông commit token vào GitHub.")
        return 0
    except Exception as exc:
        print(f"Setup Turso thất bại: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
