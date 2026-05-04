"""
Idempotent script to update strategy entry submit trade windows from 09:45-15:45 to 10:00-16:00.

Only patches scanner.submit.trade_windows. All other strategy config fields
(scanner, preview, exit, risk settings) are left untouched.

Usage:
    python scripts/update_strategy_trade_windows.py [--dry-run] [--all]

Options:
    --dry-run   Show what would change without writing to the database.
    --all       Include inactive strategies (default: active strategies only).

Manual SQL equivalent (run directly against Postgres if the script cannot connect):

    UPDATE strategies
    SET config = jsonb_set(
        config,
        '{scanner,submit,trade_windows}',
        '[{"timezone": "America/New_York", "start": "10:00", "end": "16:00"}]'::jsonb
    )
    WHERE config #>> '{scanner,submit,trade_windows,0,start}' = '09:45'
      AND config #>> '{scanner,submit,trade_windows,0,end}'   = '15:45';

To run against Render Postgres:
    1. Open the Render dashboard and copy the external DATABASE_URL.
    2. Set DATABASE_URL in your local .env or shell environment.
    3. Run: python scripts/update_strategy_trade_windows.py --dry-run
    4. If the dry-run output looks correct: python scripts/update_strategy_trade_windows.py
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import Strategy
from app.db.session import SessionLocal

OLD_START = "09:45"
OLD_END = "15:45"
NEW_START = "10:00"
NEW_END = "16:00"
TARGET_TIMEZONE = "America/New_York"


def _patch_submit_trade_windows(config: dict) -> tuple[dict, bool]:
    """Return (patched_config, was_changed). Only touches scanner.submit.trade_windows."""
    config = copy.deepcopy(config)
    scanner = config.get("scanner")
    if not isinstance(scanner, dict):
        return config, False

    submit = scanner.get("submit")
    if not isinstance(submit, dict):
        return config, False

    windows = submit.get("trade_windows")
    if not isinstance(windows, list):
        return config, False

    changed = False
    for window in windows:
        if not isinstance(window, dict):
            continue
        if (
            window.get("timezone") == TARGET_TIMEZONE
            and window.get("start") == OLD_START
            and window.get("end") == OLD_END
        ):
            window["start"] = NEW_START
            window["end"] = NEW_END
            changed = True

    return config, changed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update strategy entry submit trade windows to 10:00-16:00 ET."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show changes without writing to the database.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="include_inactive",
        help="Include inactive strategies (default: active only).",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = select(Strategy)
        if not args.include_inactive:
            query = query.where(Strategy.is_active == True)  # noqa: E712
        strategies = list(db.scalars(query))

        updated = 0
        skipped_no_match = 0
        skipped_dry_run = 0

        for strategy in strategies:
            new_config, changed = _patch_submit_trade_windows(strategy.config or {})
            if not changed:
                skipped_no_match += 1
                print(f"  skip  {strategy.name!r} — no matching 09:45-15:45 window found")
                continue

            if args.dry_run:
                skipped_dry_run += 1
                print(
                    f"  [dry-run] would update {strategy.name!r}"
                    f" — scanner.submit.trade_windows: {OLD_START}-{OLD_END} → {NEW_START}-{NEW_END}"
                )
            else:
                strategy.config = new_config
                db.add(strategy)
                updated += 1
                print(
                    f"  updated {strategy.name!r}"
                    f" — scanner.submit.trade_windows: {OLD_START}-{OLD_END} → {NEW_START}-{NEW_END}"
                )

        if not args.dry_run and updated > 0:
            db.commit()
            print(f"\nCommitted. updated={updated} skipped={skipped_no_match}")
        elif args.dry_run:
            print(f"\nDry run complete. would_update={skipped_dry_run} skipped={skipped_no_match}")
        else:
            print(f"\nNothing to update. skipped={skipped_no_match}")

    except SQLAlchemyError as exc:
        db.rollback()
        print(f"Database error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
