from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy.engine import make_url

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import settings
from app.db.session import SessionLocal, check_database_connection, check_database_schema
from app.services.trading_reset import (
    RESET_TRADING_DATA_CONFIRMATION,
    TradingDataResetConfirmationError,
    run_trading_data_reset,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Clear local paper-trading data after switching to a new Alpaca paper account. "
            "Strategies are preserved so the app can keep running with the same configuration."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete rows. Omit this for a dry run that only reports row counts.",
    )
    parser.add_argument(
        "--confirm",
        default=None,
        help=f"Required with --apply. Must be {RESET_TRADING_DATA_CONFIRMATION}.",
    )
    parser.add_argument(
        "--keep-history",
        action="store_true",
        help="Keep job_runs and audit_logs. Runtime trading tables are still cleared.",
    )
    parser.add_argument(
        "--force-live",
        action="store_true",
        help="Allow reset even when ALPACA_PAPER is false.",
    )
    args = parser.parse_args()

    if not settings.alpaca_paper and not args.force_live:
        raise SystemExit(
            "Refusing to reset while ALPACA_PAPER is false. "
            "Set ALPACA_PAPER=true or pass --force-live if this is intentional."
        )

    dry_run = not args.apply
    confirm = args.confirm if args.apply else None

    try:
        check_database_connection()
        check_database_schema()
        with SessionLocal() as db:
            result = run_trading_data_reset(
                db,
                dry_run=dry_run,
                include_history=not args.keep_history,
                confirm=confirm,
            )
    except TradingDataResetConfirmationError as exc:
        raise SystemExit(str(exc)) from exc

    print(
        json.dumps(
            {
                "database": _safe_database_url(settings.sqlalchemy_database_url),
                "alpaca_paper": settings.alpaca_paper,
                "applied": args.apply,
                "dry_run": result.dry_run,
                "include_history": result.include_history,
                "counts_before": result.counts_before,
                "deleted": result.deleted,
                "kept_tables": result.kept_tables,
                "confirmation_phrase": result.confirmation_phrase,
                "job_run": {
                    "id": str(result.job_run.id),
                    "status": result.job_run.status,
                    "started_at": _isoformat(result.job_run.started_at),
                    "finished_at": _isoformat(result.job_run.finished_at),
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


def _safe_database_url(database_url: str) -> str:
    url = make_url(database_url)
    if url.password is None:
        return url.render_as_string(hide_password=False)
    return url.render_as_string(hide_password=True)


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


if __name__ == "__main__":
    main()
