"""
Idempotent script to patch exit stop-loss config on existing strategies.

Patches scanner.exit.stop_loss_percent and scanner.exit.stop_loss_min_dollars
on all active strategies (or all strategies with --all). Strategies with exit
disabled or no exit config are skipped.

Usage:
    python scripts/update_strategy_stop_loss.py [--dry-run] [--all]
    python scripts/update_strategy_stop_loss.py --dry-run
    python scripts/update_strategy_stop_loss.py

Options:
    --dry-run               Show changes without writing to the database.
    --all                   Include inactive strategies (default: active only).
    --stop-loss-percent     Target stop loss percent. Defaults to PAPER_STRATEGY_STOP_LOSS_PERCENT.
    --stop-loss-min-dollars Target dollar floor. Defaults to PAPER_STRATEGY_STOP_LOSS_MIN_DOLLARS.

To run against Render Postgres:
    1. Open the Render dashboard and copy the external DATABASE_URL.
    2. Set DATABASE_URL in your local .env or shell environment.
    3. python scripts/update_strategy_stop_loss.py --dry-run
    4. python scripts/update_strategy_stop_loss.py
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


def _decimal_string(value: Decimal) -> str:
    return str(value)


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _patch_stop_loss(
    config: dict,
    *,
    stop_loss_percent: Decimal,
    stop_loss_min_dollars: Decimal,
) -> tuple[dict, list[str]]:
    config = copy.deepcopy(config)
    scanner = config.get("scanner")
    if not isinstance(scanner, dict):
        return config, []

    exit_config = scanner.get("exit")
    if not isinstance(exit_config, dict):
        return config, []

    if not exit_config.get("enabled"):
        return config, []

    changed_paths: list[str] = []

    current_pct = _decimal_or_none(exit_config.get("stop_loss_percent"))
    if current_pct != stop_loss_percent:
        exit_config["stop_loss_percent"] = _decimal_string(stop_loss_percent)
        changed_paths.append(
            f"scanner.exit.stop_loss_percent "
            f"{current_pct} → {stop_loss_percent}"
        )

    current_floor = _decimal_or_none(exit_config.get("stop_loss_min_dollars"))
    if current_floor != stop_loss_min_dollars:
        exit_config["stop_loss_min_dollars"] = _decimal_string(stop_loss_min_dollars)
        changed_paths.append(
            f"scanner.exit.stop_loss_min_dollars "
            f"{current_floor} → {stop_loss_min_dollars}"
        )

    return config, changed_paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Patch stop-loss exit config on strategies."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all", action="store_true", dest="include_inactive")
    parser.add_argument(
        "--stop-loss-percent",
        default=str(settings.strategy_stop_loss_percent),
    )
    parser.add_argument(
        "--stop-loss-min-dollars",
        default=str(settings.strategy_stop_loss_min_dollars),
    )
    args = parser.parse_args()

    try:
        stop_loss_percent = Decimal(args.stop_loss_percent)
        stop_loss_min_dollars = Decimal(args.stop_loss_min_dollars)
    except InvalidOperation as exc:
        print(f"Invalid argument: {exc}", file=sys.stderr)
        sys.exit(2)

    db = SessionLocal()
    try:
        query = select(Strategy)
        if not args.include_inactive:
            query = query.where(Strategy.is_active == True)  # noqa: E712
        strategies = list(db.scalars(query))

        updated = 0
        skipped = 0

        for strategy in strategies:
            new_config, changed_paths = _patch_stop_loss(
                strategy.config or {},
                stop_loss_percent=stop_loss_percent,
                stop_loss_min_dollars=stop_loss_min_dollars,
            )
            if not changed_paths:
                skipped += 1
                print(f"  skip  {strategy.name!r} — already up to date or no exit config")
                continue

            changes = ", ".join(changed_paths)
            if args.dry_run:
                print(f"  [dry-run] would update {strategy.name!r} — {changes}")
            else:
                strategy.config = new_config
                db.add(strategy)
                updated += 1
                print(f"  updated {strategy.name!r} — {changes}")

        if not args.dry_run and updated > 0:
            db.commit()
            print(f"\nCommitted. updated={updated} skipped={skipped}")
        elif args.dry_run:
            print(f"\nDry run complete. would_update={len(strategies) - skipped} skipped={skipped}")
        else:
            print(f"\nNothing to update. skipped={skipped}")

    except SQLAlchemyError as exc:
        db.rollback()
        print(f"Database error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
