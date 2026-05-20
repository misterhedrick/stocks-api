from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.models import Strategy
from app.db.session import SessionLocal
from app.services.audit_logs import record_audit_log


DEFAULT_STRATEGY_NAMES = (
    "Paper QQQ downside put preview",
    "Paper SPY confirmed trend call preview",
    "Paper SPY momentum call preview",
    "Paper SPY moving average call preview",
    "Paper SPY upside call preview",
)

LEGACY_STRATEGY_NAMES = (
    "SPY Cron Paper Test",
    "SPY Paper Momentum",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare strategies for a higher-volume trading day."
    )
    parser.add_argument("--apply", action="store_true", help="Persist the strategy changes.")
    parser.add_argument("--max-orders-per-cycle", type=int, default=1)
    parser.add_argument("--max-orders-per-day", type=int, default=2)
    parser.add_argument("--max-open-contracts-per-symbol", type=int, default=1)
    parser.add_argument("--max-open-contracts-per-strategy", type=int, default=1)
    parser.add_argument("--trade-window-start", default="10:00")
    parser.add_argument("--trade-window-end", default="16:00")
    parser.add_argument(
        "--keep-legacy-active",
        action="store_true",
        help="Do not deactivate old manual/test strategies.",
    )
    parser.add_argument(
        "--strategy-name",
        action="append",
        dest="strategy_names",
        help="Strategy to prepare. May be passed more than once.",
    )
    args = parser.parse_args()

    strategy_names = tuple(args.strategy_names or DEFAULT_STRATEGY_NAMES)
    with SessionLocal() as db:
        strategies = list(
            db.scalars(
                select(Strategy)
                .where(Strategy.name.in_(strategy_names))
                .order_by(Strategy.name.asc())
            )
        )
        found_names = {strategy.name for strategy in strategies}
        missing_names = [name for name in strategy_names if name not in found_names]

        prepared: list[dict[str, Any]] = []
        deactivated: list[dict[str, Any]] = []
        for strategy in strategies:
            original_config = strategy.config if isinstance(strategy.config, dict) else {}
            config = deepcopy(original_config)
            scanner = config.get("scanner")
            if not isinstance(scanner, dict):
                scanner = {}
            preview = scanner.get("preview") if isinstance(scanner.get("preview"), dict) else {}
            existing_submit = (
                scanner.get("submit") if isinstance(scanner.get("submit"), dict) else {}
            )

            max_notional = (
                preview.get("max_estimated_notional")
                or existing_submit.get("max_notional_per_order")
                or "250.00"
            )
            submit_config = {
                "enabled": True,
                "max_orders_per_cycle": args.max_orders_per_cycle,
                "max_contracts_per_order": 1,
                "max_contracts_per_cycle": args.max_orders_per_cycle,
                "max_notional_per_order": str(max_notional),
                "max_open_contracts_per_symbol": args.max_open_contracts_per_symbol,
                "max_open_contracts_per_strategy": args.max_open_contracts_per_strategy,
                "max_orders_per_trading_day": args.max_orders_per_day,
                "trading_day_timezone": "America/New_York",
                "trade_windows": [
                    {
                        "timezone": "America/New_York",
                        "start": args.trade_window_start,
                        "end": args.trade_window_end,
                    }
                ],
                "allowed_sides": ["buy"],
            }

            scanner["submit"] = submit_config
            config["scanner"] = scanner
            prepared.append(
                {
                    "id": str(strategy.id),
                    "name": strategy.name,
                    "scanner_type": scanner.get("type"),
                    "symbols": scanner.get("symbols", []),
                    "submit": submit_config,
                }
            )

            if args.apply:
                strategy.config = config
                db.add(strategy)
                record_audit_log(
                    db,
                    event_type="strategy.updated",
                    entity_type="strategy",
                    entity_id=strategy.id,
                    message="Strategy prepared for trade data gathering",
                    payload={
                        "source": "prepare_paper_trade_day",
                        "submit_config": submit_config,
                    },
                )

        if not args.keep_legacy_active:
            legacy_strategies = list(
                db.scalars(
                    select(Strategy)
                    .where(Strategy.name.in_(LEGACY_STRATEGY_NAMES))
                    .order_by(Strategy.name.asc())
                )
            )
            for strategy in legacy_strategies:
                deactivated.append(
                    {
                        "id": str(strategy.id),
                        "name": strategy.name,
                        "was_active": strategy.is_active,
                    }
                )
                if args.apply and strategy.is_active:
                    strategy.is_active = False
                    db.add(strategy)
                    record_audit_log(
                        db,
                        event_type="strategy.updated",
                        entity_type="strategy",
                        entity_id=strategy.id,
                        message="Legacy test strategy deactivated for trade data gathering",
                        payload={
                            "source": "prepare_paper_trade_day",
                            "reason": "avoid always-on or stale test strategy signals",
                        },
                    )

        if args.apply:
            db.commit()

    print(
        json.dumps(
            {
                "applied": args.apply,
                "deactivated_legacy": deactivated,
                "prepared_count": len(prepared),
                "missing_strategy_names": missing_names,
                "prepared": prepared,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
