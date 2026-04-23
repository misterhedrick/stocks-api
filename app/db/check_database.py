from __future__ import annotations

import sys

from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db.session import check_database_connection


def describe_database_target() -> str:
    url = make_url(settings.sqlalchemy_database_url)
    host = url.host or "localhost"
    port = url.port or "default"
    database = url.database or ""
    return f"{host}:{port}/{database}"


def main() -> int:
    print(f"Database target: {describe_database_target()}")

    try:
        check_database_connection()
    except Exception as exc:
        print(
            f"Database connection failed: {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    print("Database connection OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
