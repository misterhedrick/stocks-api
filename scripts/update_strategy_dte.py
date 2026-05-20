"""
Idempotent script to update strategy days-to-expiration windows.

Patches existing database strategy rows so the option-contract scanner searches
a wider expiry range. The default window (2-7 days) produces low/no open interest
on near-term contracts; expanding to 2-30 days surfaces weekly and monthly options
with adequate liquidity.

Only patches:
- scanner.preview.min_days_to_expiration
- scanner.preview.max_days_to_expiration

All other strategy config fields are left untouched.

Usage:
    python scripts/update_strategy_dte.py [--dry-run] [--all]
    python scripts/update_strategy_dte.py --min-dte 2 --max-dte 30

Options:
    --dry-run    Show what would change without writing to the database.
    --all        Include inactive strategies (default: active strategies only).
    --min-dte    Target minimum days to expiration (default: 2).
    --max-dte    Target maximum days to expiration (default: 30).

Manual SQL equivalent:
    UPDATE strategies
    SET config = jsonb_set(
        jsonb_set(
            config,
            '{scanner,preview,min_days_to_expiration}',
            '2'::jsonb
        ),
        '{scanner,preview,max_days_to_expiration}',
        '30'::jsonb
    )
    WHERE is_active = true
      AND config #> '{scanner,preview}' IS NOT NULL;

To run against Render Postgres:
    1. Open the Render dashboard and copy the external DATABASE_URL.
    2. Export it: export DATABASE_URL="postgresql+psycopg://..."
    3. Run: python scripts/update_strategy_dte.py --dry-run
    4. If output looks correct: python scripts/update_strategy_dte.py
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


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _patch_dte(
    config: dict,
    *,
    min_dte: int,
    max_dte: int,
) -> tuple[dict, bool, dict]:
    """Return (patched_config, was_changed, old_values). Only touches preview DTE fields."""
    config = copy.deepcopy(config)
    scanner = config.get("scanner")
    if not isinstance(scanner, dict):
        return config, False, {}

    preview = scanner.get("preview")
    if not isinstance(preview, dict):
        return config, False, {}

    old_min = _int_or_none(preview.get("min_days_to_expiration"))
    old_max = _int_or_none(preview.get("max_days_to_expiration"))

    changed = old_min != min_dte or old_max != max_dte
    if changed:
        preview["min_days_to_expiration"] = min_dte
        preview["max_days_to_expiration"] = max_dte

    return config, changed, {"min": old_min, "max": old_max}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update strategy days-to-expiration windows."
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
        "--min-dte",
        type=int,
        default=2,
        help="Target minimum days to expiration (default: 2).",
    )
    parser.add_argument(
        "--max-dte",
        type=int,
        default=30,
        help="Target maximum days to expiration (default: 30).",
    )
    args = parser.parse_args()

    if args.min_dte < 0:
        print("--min-dte must be non-negative", file=sys.stderr)
        sys.exit(2)
    if args.max_dte < args.min_dte:
        print("--max-dte must be >= --min-dte", file=sys.stderr)
        sys.exit(2)

    db = SessionLocal()
    try:
        query = select(Strategy)
        if not args.include_inactive:
            query = query.where(Strategy.is_active == True)  # noqa: E712
        strategies = list(db.scalars(query))

        updated = 0
        skipped_no_change = 0
        skipped_dry_run = 0

        for strategy in strategies:
            new_config, changed, old_vals = _patch_dte(
                strategy.config or {},
                min_dte=args.min_dte,
                max_dte=args.max_dte,
            )
            if not changed:
                skipped_no_change += 1
                continue

            label = (
                f"min_dte: {old_vals['min']!r} → {args.min_dte}, "
                f"max_dte: {old_vals['max']!r} → {args.max_dte}"
            )
            if args.dry_run:
                skipped_dry_run += 1
                print(f"  [dry-run] would update {strategy.name!r} — {label}")
            else:
                strategy.config = new_config
                db.add(strategy)
                updated += 1
                print(f"  updated {strategy.name!r} — {label}")

        if not args.dry_run and updated > 0:
            db.commit()
            print(f"\nCommitted. updated={updated} skipped={skipped_no_change}")
        elif args.dry_run:
            print(
                f"\nDry run complete. would_update={skipped_dry_run} skipped={skipped_no_change}"
            )
        else:
            print(f"\nNothing to update. skipped={skipped_no_change}")

    except SQLAlchemyError as exc:
        db.rollback()
        print(f"Database error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
