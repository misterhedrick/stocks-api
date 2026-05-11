"""
Inspect and update strategy entry submit trade windows to 10:00-16:00 ET.

The automated entry gate reads scanner.submit.trade_windows from each strategy's
JSON config in Postgres. Updating code/templates does not automatically update
existing live strategy rows, so this script is the safe operational patch.

Usage:
    python scripts/update_strategy_trade_windows.py [--dry-run] [--all] [--force]

Options:
    --dry-run   Show what would change without writing to the database.
    --all       Include inactive strategies (default: active strategies only).
    --force     Replace any existing scanner.submit.trade_windows value with
                [{"timezone": "America/New_York", "start": "10:00", "end": "16:00"}].
                Without --force, only known old 09:45 windows are patched.

Manual SQL equivalent for force mode:

    UPDATE strategies
    SET config = jsonb_set(
        config,
        '{scanner,submit,trade_windows}',
        '[{"timezone": "America/New_York", "start": "10:00", "end": "16:00"}]'::jsonb,
        true
    )
    WHERE is_active = true
      AND config ? 'scanner';

To run against Render Postgres:
    1. Open the Render dashboard and copy the external DATABASE_URL.
    2. Set DATABASE_URL in your local .env or shell environment.
    3. Inspect current active strategy windows:
       python scripts/update_strategy_trade_windows.py --dry-run --force
    4. If the dry-run output looks correct:
       python scripts/update_strategy_trade_windows.py --force
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import Strategy
from app.db.session import SessionLocal

OLD_WINDOWS = {
    ("09:45", "15:30"),
    ("09:45", "15:45"),
}
NEW_START = "10:00"
NEW_END = "16:00"
TARGET_TIMEZONE = "America/New_York"
TARGET_WINDOWS = [
    {"timezone": TARGET_TIMEZONE, "start": NEW_START, "end": NEW_END},
]


def _scanner_submit(config: dict[str, Any]) -> dict[str, Any] | None:
    scanner = config.get("scanner")
    if not isinstance(scanner, dict):
        return None
    submit = scanner.get("submit")
    if not isinstance(submit, dict):
        return None
    return submit


def _current_windows(config: dict[str, Any]) -> Any:
    submit = _scanner_submit(config)
    if submit is None:
        return None
    return submit.get("trade_windows")


def _patch_submit_trade_windows(
    config: dict[str, Any],
    *,
    force: bool,
) -> tuple[dict[str, Any], bool, Any, Any]:
    """Return (patched_config, was_changed, before_windows, after_windows)."""
    config = copy.deepcopy(config)
    scanner = config.get("scanner")
    if not isinstance(scanner, dict):
        return config, False, None, None

    submit = scanner.get("submit")
    if not isinstance(submit, dict):
        submit = {}
        scanner["submit"] = submit

    before = copy.deepcopy(submit.get("trade_windows"))

    if force:
        if before == TARGET_WINDOWS:
            return config, False, before, before
        submit["trade_windows"] = copy.deepcopy(TARGET_WINDOWS)
        return config, True, before, copy.deepcopy(TARGET_WINDOWS)

    windows = submit.get("trade_windows")
    if not isinstance(windows, list):
        return config, False, before, before

    changed = False
    for window in windows:
        if not isinstance(window, dict):
            continue
        if (
            window.get("timezone") == TARGET_TIMEZONE
            and (window.get("start"), window.get("end")) in OLD_WINDOWS
        ):
            window["start"] = NEW_START
            window["end"] = NEW_END
            changed = True

    after = copy.deepcopy(submit.get("trade_windows"))
    return config, changed, before, after


def _format_windows(windows: Any) -> str:
    if windows is None:
        return "<missing>"
    return json.dumps(windows, sort_keys=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect/update strategy entry submit trade windows to 10:00-16:00 ET."
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace any existing scanner.submit.trade_windows with the target 10:00-16:00 ET window.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = select(Strategy)
        if not args.include_inactive:
            query = query.where(Strategy.is_active == True)  # noqa: E712
        strategies = list(db.scalars(query))

        updated = 0
        unchanged = 0
        skipped_no_scanner = 0
        dry_run_updates = 0

        print(
            f"Target scanner.submit.trade_windows: {_format_windows(TARGET_WINDOWS)}"
            f" | mode={'force' if args.force else 'legacy-only'}"
            f" | scope={'all strategies' if args.include_inactive else 'active strategies'}"
        )

        for strategy in strategies:
            config = strategy.config or {}
            before_windows = _current_windows(config)
            new_config, changed, before, after = _patch_submit_trade_windows(
                config,
                force=args.force,
            )

            if before is None and after is None and "scanner" not in config:
                skipped_no_scanner += 1
                print(f"  skip  {strategy.name!r} — no scanner config")
                continue

            if not changed:
                unchanged += 1
                print(
                    f"  ok    {strategy.name!r} — scanner.submit.trade_windows="
                    f"{_format_windows(before_windows)}"
                )
                continue

            if args.dry_run:
                dry_run_updates += 1
                print(
                    f"  [dry-run] would update {strategy.name!r}"
                    f" — {_format_windows(before)} → {_format_windows(after)}"
                )
            else:
                strategy.config = new_config
                db.add(strategy)
                updated += 1
                print(
                    f"  updated {strategy.name!r}"
                    f" — {_format_windows(before)} → {_format_windows(after)}"
                )

        if not args.dry_run and updated > 0:
            db.commit()
            print(
                f"\nCommitted. updated={updated} unchanged={unchanged} "
                f"skipped_no_scanner={skipped_no_scanner}"
            )
        elif args.dry_run:
            print(
                f"\nDry run complete. would_update={dry_run_updates} unchanged={unchanged} "
                f"skipped_no_scanner={skipped_no_scanner}"
            )
        else:
            print(
                f"\nNothing to update. unchanged={unchanged} "
                f"skipped_no_scanner={skipped_no_scanner}"
            )

    except SQLAlchemyError as exc:
        db.rollback()
        print(f"Database error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
