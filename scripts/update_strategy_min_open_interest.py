"""
Idempotent script to update strategy minimum open-interest filters.

This patches existing database strategy rows. It is intended for cases where the
code/templates have changed but already-seeded strategies still carry an older,
tighter open-interest floor such as 100.

Only patches:
- scanner.preview.min_open_interest

All other strategy config fields are left untouched.

Usage:
    python scripts/update_strategy_min_open_interest.py [--dry-run] [--all]

Options:
    --dry-run      Show what would change without writing to the database.
    --all          Include inactive strategies (default: active strategies only).
    --new-value    Target minimum open interest. Defaults to PAPER_STRATEGY_MIN_OPEN_INTEREST.

Manual SQL equivalent for 50 (run directly against Postgres if the script cannot connect):

    UPDATE strategies
    SET config = jsonb_set(
        config,
        '{scanner,preview,min_open_interest}',
        '50'::jsonb
    )
    WHERE is_active = true
      AND (
          NULLIF(config #>> '{scanner,preview,min_open_interest}', '')::integer > 50
          OR config #>> '{scanner,preview,min_open_interest}' IS NULL
      );

To run against Render Postgres:
    1. Open the Render dashboard and copy the external DATABASE_URL.
    2. Set DATABASE_URL in your local .env or shell environment.
    3. Run: python scripts/update_strategy_min_open_interest.py --dry-run
    4. If the dry-run output looks correct: python scripts/update_strategy_min_open_interest.py
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

from app.core.config import settings
from app.db.models import Strategy
from app.db.session import SessionLocal


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _should_patch(value: object, new_value: int) -> bool:
    current = _int_or_none(value)
    return current is None or current > new_value


def _patch_min_open_interest(
    config: dict,
    *,
    new_value: int,
) -> tuple[dict, bool]:
    """Return (patched_config, was_changed). Only touches scanner.preview.min_open_interest."""
    config = copy.deepcopy(config)
    scanner = config.get("scanner")
    if not isinstance(scanner, dict):
        return config, False

    preview = scanner.get("preview")
    if not isinstance(preview, dict):
        return config, False

    if not _should_patch(preview.get("min_open_interest"), new_value):
        return config, False

    preview["min_open_interest"] = new_value
    return config, True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update strategy minimum open-interest filters."
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
        "--new-value",
        type=int,
        default=settings.strategy_min_open_interest,
        help="Target minimum open interest. Defaults to PAPER_STRATEGY_MIN_OPEN_INTEREST.",
    )
    args = parser.parse_args()

    if args.new_value < 0:
        print("--new-value must be non-negative", file=sys.stderr)
        sys.exit(2)

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
            old_value = None
            scanner = (strategy.config or {}).get("scanner")
            if isinstance(scanner, dict):
                preview = scanner.get("preview")
                if isinstance(preview, dict):
                    old_value = preview.get("min_open_interest")

            new_config, changed = _patch_min_open_interest(
                strategy.config or {},
                new_value=args.new_value,
            )
            if not changed:
                skipped_no_match += 1
                print(
                    f"  skip  {strategy.name!r} — min_open_interest already <= {args.new_value}"
                )
                continue

            if args.dry_run:
                skipped_dry_run += 1
                print(
                    f"  [dry-run] would update {strategy.name!r}"
                    f" — scanner.preview.min_open_interest: {old_value!r} → {args.new_value}"
                )
            else:
                strategy.config = new_config
                db.add(strategy)
                updated += 1
                print(
                    f"  updated {strategy.name!r}"
                    f" — scanner.preview.min_open_interest: {old_value!r} → {args.new_value}"
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
