"""
Repair the Render Alembic revision after the no-op 0012 migration incident.

This is an operational script for the specific Render state where the database
schema still uses paper_review_snapshots, migration 0012 is intentionally a
no-op, and PgBouncer/server-side zombie transactions may be blocking Alembic's
version-table update during app startup.

Usage:
    python scripts/repair_render_alembic_0012.py
    python scripts/repair_render_alembic_0012.py --apply --confirm STAMP_ALEMBIC_0012
    python scripts/repair_render_alembic_0012.py --apply --skip-terminate --confirm STAMP_ALEMBIC_0012

To run against Render Postgres:
    1. Prefer Render's direct/external PostgreSQL URL over PgBouncer.
    2. Set DATABASE_URL in your local .env or shell environment.
    3. Run the dry-run command first.
    4. Run the --apply command only if the dry-run shows alembic_version=0011.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.core.config import settings

CURRENT_REVISION = "0011_strategy_tuning_decisions"
TARGET_REVISION = "0012_rename_paper_review_snapshots"
CONFIRMATION_PHRASE = "STAMP_ALEMBIC_0012"


def _connect_args() -> dict[str, int]:
    if settings.sqlalchemy_database_url.startswith("postgresql"):
        return {"connect_timeout": settings.database_connect_timeout_seconds}
    return {}


def _engine():
    return create_engine(
        settings.sqlalchemy_database_url,
        connect_args=_connect_args(),
        poolclass=NullPool,
    )


def _scalar(connection: Any, sql: str, **params: Any) -> Any:
    return connection.execute(text(sql), params).scalar_one_or_none()


def _print_state(connection: Any) -> str | None:
    current_revision = _scalar(connection, "SELECT version_num FROM alembic_version")
    idle_tx_count = _scalar(
        connection,
        """
        SELECT count(*)
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND pid <> pg_backend_pid()
          AND state IN ('idle in transaction', 'idle in transaction (aborted)')
        """,
    )
    print(f"alembic_version={current_revision}")
    print(f"idle_transaction_backends={idle_tx_count}")
    return current_revision


def _terminate_idle_transactions(connection: Any) -> int:
    rows = connection.execute(
        text(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
              AND state IN ('idle in transaction', 'idle in transaction (aborted)')
            """
        )
    ).all()
    return sum(1 for row in rows if row[0])


def _stamp_revision(connection: Any) -> int:
    result = connection.execute(
        text(
            """
            UPDATE alembic_version
               SET version_num = :target_revision
             WHERE version_num = :current_revision
            """
        ),
        {
            "current_revision": CURRENT_REVISION,
            "target_revision": TARGET_REVISION,
        },
    )
    return int(result.rowcount or 0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stamp Render Alembic revision 0012 after the no-op migration incident."
    )
    parser.add_argument("--apply", action="store_true", help="Write the revision stamp.")
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Required with --apply: {CONFIRMATION_PHRASE}",
    )
    parser.add_argument(
        "--skip-terminate",
        action="store_true",
        help="Do not terminate idle/aborted transaction backends before stamping.",
    )
    parser.add_argument(
        "--lock-timeout-ms",
        type=int,
        default=5000,
        help="PostgreSQL lock timeout for the repair transaction.",
    )
    parser.add_argument(
        "--statement-timeout-ms",
        type=int,
        default=15000,
        help="PostgreSQL statement timeout for the repair transaction.",
    )
    args = parser.parse_args()

    if args.apply and args.confirm != CONFIRMATION_PHRASE:
        print(
            f"Refusing to write. Re-run with --confirm {CONFIRMATION_PHRASE}",
            file=sys.stderr,
        )
        sys.exit(2)

    engine = _engine()
    try:
        with engine.begin() as connection:
            connection.execute(text(f"SET LOCAL lock_timeout = '{args.lock_timeout_ms}ms'"))
            connection.execute(
                text(f"SET LOCAL statement_timeout = '{args.statement_timeout_ms}ms'")
            )

            print("Before repair:")
            current_revision = _print_state(connection)

            if not args.apply:
                print("\nDry run only. No database changes made.")
                return

            if current_revision == TARGET_REVISION:
                print("\nAlready stamped to target revision. Nothing to do.")
                return

            if current_revision != CURRENT_REVISION:
                print(
                    f"\nRefusing to stamp unexpected revision {current_revision!r}; "
                    f"expected {CURRENT_REVISION!r}.",
                    file=sys.stderr,
                )
                sys.exit(3)

            if not args.skip_terminate:
                try:
                    terminated = _terminate_idle_transactions(connection)
                    print(f"\nTerminated idle transaction backends: {terminated}")
                except SQLAlchemyError as exc:
                    print(
                        "\nCould not terminate idle transaction backends. "
                        "You can retry with --skip-terminate if the version row is not locked.",
                        file=sys.stderr,
                    )
                    print(f"Terminate error: {exc}", file=sys.stderr)
                    raise

            updated = _stamp_revision(connection)
            if updated != 1:
                print(f"\nExpected to stamp 1 row, stamped {updated}.", file=sys.stderr)
                sys.exit(4)

            print("\nAfter repair:")
            _print_state(connection)
            print("\nCommitted Alembic revision stamp.")
    except SQLAlchemyError as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
