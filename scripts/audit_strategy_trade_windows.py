"""Read-only audit for strategy scanner.submit.trade_windows configuration."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import Strategy
from app.db.session import SessionLocal

TARGET_TIMEZONE = "America/New_York"
TARGET_START = "10:00"
TARGET_END = "16:00"
TARGET_KEY = f"{TARGET_START}-{TARGET_END} {TARGET_TIMEZONE}"


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


def _window_key(windows: Any) -> str:
    if not isinstance(windows, list) or not windows:
        return "<missing>" if windows is None else json.dumps(windows, sort_keys=True)
    parts: list[str] = []
    for window in windows:
        if not isinstance(window, dict):
            parts.append(json.dumps(window, sort_keys=True))
            continue
        parts.append(
            f"{window.get('start', '?')}-{window.get('end', '?')} "
            f"{window.get('timezone', '?')}"
        )
    return ", ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit strategy entry trade windows without modifying database rows."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="include_inactive",
        help="Include inactive strategies; default is active strategies only.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print machine-readable JSON instead of human-readable text.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = select(Strategy)
        if not args.include_inactive:
            query = query.where(Strategy.is_active == True)  # noqa: E712
        strategies = list(db.scalars(query))

        distribution: Counter[str] = Counter()
        missing_scanner: list[str] = []
        mismatched: list[dict[str, str]] = []

        for strategy in strategies:
            config = strategy.config or {}
            windows = _current_windows(config)
            key = _window_key(windows)
            distribution[key] += 1
            if _scanner_submit(config) is None:
                missing_scanner.append(strategy.name)
            elif key != TARGET_KEY:
                mismatched.append({"name": strategy.name, "trade_windows": key})

        result = {
            "scope": "all" if args.include_inactive else "active",
            "strategies_inspected": len(strategies),
            "target_window": TARGET_KEY,
            "trade_window_distribution": dict(sorted(distribution.items())),
            "missing_scanner_config_count": len(missing_scanner),
            "missing_scanner_config": missing_scanner,
            "mismatched_trade_window_count": len(mismatched),
            "mismatched_trade_windows": mismatched,
            "ready": len(mismatched) == 0,
        }

        if args.json_output:
            print(json.dumps(result, indent=2, sort_keys=True))
            return

        print("Strategy trade-window audit")
        print(f"  scope={result['scope']}")
        print(f"  strategies_inspected={result['strategies_inspected']}")
        print(f"  target_window={result['target_window']}")
        print("\nTrade-window distribution:")
        for key, count in sorted(distribution.items(), key=lambda item: (-item[1], item[0])):
            print(f"  {key}: {count}")

        print("\nOperational summary:")
        print(f"  missing_scanner_config_count={len(missing_scanner)}")
        print(f"  mismatched_trade_window_count={len(mismatched)}")
        print(f"  ready={result['ready']}")

        if missing_scanner:
            print("\nStrategies missing scanner config:")
            for name in missing_scanner:
                print(f"  {name}")

        if mismatched:
            print("\nStrategies with non-target trade windows:")
            for item in mismatched:
                print(f"  {item['name']}: {item['trade_windows']}")

    except SQLAlchemyError as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
