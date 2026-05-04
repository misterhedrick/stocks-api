"""
Idempotent script to add preview_profile metadata to existing strategy rows.

This does not delete or rebuild strategies. It only patches:
- scanner.preview.preview_profile

By default the profile is inferred from scanner.type, e.g.:
- price_threshold
- percent_change
- moving_average
- trend_confirmation

Usage:
    python scripts/update_strategy_preview_profiles.py [--dry-run] [--all]

Options:
    --dry-run   Show what would change without writing to the database.
    --all       Include inactive strategies (default: active strategies only).
    --force     Replace an existing preview_profile if it differs from scanner.type.
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


def _patch_preview_profile(
    config: dict,
    *,
    force: bool,
) -> tuple[dict, bool, str | None, str | None]:
    config = copy.deepcopy(config)
    scanner = config.get("scanner")
    if not isinstance(scanner, dict):
        return config, False, None, None

    scanner_type = scanner.get("type")
    if not isinstance(scanner_type, str) or not scanner_type.strip():
        return config, False, None, None

    profile = scanner_type.strip()
    preview = scanner.setdefault("preview", {})
    if not isinstance(preview, dict):
        return config, False, None, profile

    old_profile = preview.get("preview_profile") or preview.get("profile")
    if old_profile == profile:
        return config, False, old_profile, profile
    if old_profile is not None and not force:
        return config, False, str(old_profile), profile

    preview["preview_profile"] = profile
    return config, True, str(old_profile) if old_profile is not None else None, profile


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add scanner.preview.preview_profile metadata to strategies."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--all",
        action="store_true",
        dest="include_inactive",
        help="Include inactive strategies (default: active only).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing preview_profile values if they differ from scanner.type.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = select(Strategy)
        if not args.include_inactive:
            query = query.where(Strategy.is_active == True)  # noqa: E712
        strategies = list(db.scalars(query))

        updated = 0
        skipped = 0
        dry_run_updates = 0

        for strategy in strategies:
            new_config, changed, old_profile, new_profile = _patch_preview_profile(
                strategy.config or {},
                force=args.force,
            )
            if not changed:
                skipped += 1
                print(
                    f"  skip  {strategy.name!r} — preview_profile={old_profile!r}, target={new_profile!r}"
                )
                continue

            if args.dry_run:
                dry_run_updates += 1
                print(
                    f"  [dry-run] would update {strategy.name!r}"
                    f" — scanner.preview.preview_profile: {old_profile!r} → {new_profile!r}"
                )
            else:
                strategy.config = new_config
                db.add(strategy)
                updated += 1
                print(
                    f"  updated {strategy.name!r}"
                    f" — scanner.preview.preview_profile: {old_profile!r} → {new_profile!r}"
                )

        if not args.dry_run and updated > 0:
            db.commit()
            print(f"\nCommitted. updated={updated} skipped={skipped}")
        elif args.dry_run:
            print(f"\nDry run complete. would_update={dry_run_updates} skipped={skipped}")
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
