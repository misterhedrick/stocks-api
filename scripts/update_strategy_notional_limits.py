"""
Idempotent script to update paper strategy entry notional limits.

This patches existing database strategy rows. It is intended for cases where the
code/templates have changed but already-seeded strategies still carry an older,
tighter notional cap such as 250.00.

Only patches:
- scanner.preview.max_estimated_notional
- scanner.submit.max_notional_per_order

All other strategy config fields are left untouched.

Usage:
    python scripts/update_strategy_notional_limits.py [--dry-run] [--all]

Options:
    --dry-run      Show what would change without writing to the database.
    --all          Include inactive strategies (default: active strategies only).
    --new-value    Target notional value. Defaults to PAPER_STRATEGY_MAX_ESTIMATED_NOTIONAL.

Manual SQL equivalent for 3000.00 (run directly against Postgres if the script cannot connect):

    UPDATE strategies
    SET config = jsonb_set(
        jsonb_set(
            config,
            '{scanner,preview,max_estimated_notional}',
            '"3000.00"'::jsonb
        ),
        '{scanner,submit,max_notional_per_order}',
        '"3000.00"'::jsonb
    )
    WHERE is_active = true
      AND (
          NULLIF(config #>> '{scanner,preview,max_estimated_notional}', '')::numeric < 3000
          OR NULLIF(config #>> '{scanner,submit,max_notional_per_order}', '')::numeric < 3000
      );

To run against Render Postgres:
    1. Open the Render dashboard and copy the external DATABASE_URL.
    2. Set DATABASE_URL in your local .env or shell environment.
    3. Run: python scripts/update_strategy_notional_limits.py --dry-run
    4. If the dry-run output looks correct: python scripts/update_strategy_notional_limits.py
"""
from __future__ import annotations

import argparse
import copy
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.db.models import Strategy
from app.db.session import SessionLocal


def _money_string(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _should_patch(value: object, new_value: Decimal) -> bool:
    current = _decimal_or_none(value)
    return current is None or current < new_value


def _patch_notional_limits(
    config: dict,
    *,
    new_value: Decimal,
) -> tuple[dict, list[str]]:
    """Return (patched_config, changed_paths). Only touches scanner preview/submit caps."""
    config = copy.deepcopy(config)
    scanner = config.get("scanner")
    if not isinstance(scanner, dict):
        return config, []

    changed_paths: list[str] = []
    new_value_text = _money_string(new_value)

    preview = scanner.get("preview")
    if isinstance(preview, dict) and _should_patch(
        preview.get("max_estimated_notional"), new_value
    ):
        preview["max_estimated_notional"] = new_value_text
        changed_paths.append("scanner.preview.max_estimated_notional")

    submit = scanner.get("submit")
    if isinstance(submit, dict) and _should_patch(
        submit.get("max_notional_per_order"), new_value
    ):
        submit["max_notional_per_order"] = new_value_text
        changed_paths.append("scanner.submit.max_notional_per_order")

    return config, changed_paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update paper strategy entry notional limits."
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
        default=str(settings.paper_strategy_max_estimated_notional),
        help="Target notional value. Defaults to PAPER_STRATEGY_MAX_ESTIMATED_NOTIONAL.",
    )
    args = parser.parse_args()

    try:
        new_value = Decimal(str(args.new_value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        print(f"Invalid --new-value: {args.new_value!r}", file=sys.stderr)
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
            new_config, changed_paths = _patch_notional_limits(
                strategy.config or {},
                new_value=new_value,
            )
            if not changed_paths:
                skipped_no_match += 1
                print(
                    f"  skip  {strategy.name!r} — no notional limit below "
                    f"{_money_string(new_value)} found"
                )
                continue

            paths = ", ".join(changed_paths)
            if args.dry_run:
                skipped_dry_run += 1
                print(
                    f"  [dry-run] would update {strategy.name!r}"
                    f" — {paths} → {_money_string(new_value)}"
                )
            else:
                strategy.config = new_config
                db.add(strategy)
                updated += 1
                print(
                    f"  updated {strategy.name!r}"
                    f" — {paths} → {_money_string(new_value)}"
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
