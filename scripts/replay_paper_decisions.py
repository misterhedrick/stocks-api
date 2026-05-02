from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.models import OrderIntent, Signal, Strategy
from app.db.session import SessionLocal
from app.services.automation_guard import can_auto_submit_order_intent
from app.services.market_cycle import _entry_preview_delay_reason


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay recorded signal/order-intent decisions without submitting."
    )
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    with SessionLocal() as db:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "signals": _signal_replay(db, limit=args.limit),
            "order_intents": _order_intent_replay(db, limit=args.limit),
        }

    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _signal_replay(db, *, limit: int) -> list[dict[str, Any]]:
    statement = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
    rows = []
    for signal in db.scalars(statement):
        strategy = db.get(Strategy, signal.strategy_id) if signal.strategy_id else None
        rows.append(
            {
                "signal_id": str(signal.id),
                "strategy_name": strategy.name if strategy is not None else None,
                "symbol": signal.symbol,
                "signal_type": signal.signal_type,
                "direction": signal.direction,
                "status": signal.status,
                "would_preview": _would_preview(strategy),
                "preview_delay_reason": _entry_preview_delay_reason(strategy)
                if strategy is not None
                else "signal has no strategy",
            }
        )
    return rows


def _order_intent_replay(db, *, limit: int) -> list[dict[str, Any]]:
    statement = select(OrderIntent).order_by(OrderIntent.created_at.desc()).limit(limit)
    rows = []
    for order_intent in db.scalars(statement):
        decision = can_auto_submit_order_intent(
            db,
            order_intent,
            cycle_id="paper-decision-replay",
        )
        rows.append(
            {
                "order_intent_id": str(order_intent.id),
                "strategy_id": str(order_intent.strategy_id)
                if order_intent.strategy_id
                else None,
                "underlying_symbol": order_intent.underlying_symbol,
                "option_symbol": order_intent.option_symbol,
                "side": order_intent.side,
                "status": order_intent.status,
                "would_submit": decision.allowed,
                "submit_reasons": decision.reasons,
                "limits_snapshot": decision.limits_snapshot,
            }
        )
    return rows


def _would_preview(strategy: Strategy | None) -> bool:
    if strategy is None:
        return False
    config = strategy.config if isinstance(strategy.config, dict) else {}
    scanner = config.get("scanner") if isinstance(config.get("scanner"), dict) else {}
    preview = scanner.get("preview") if isinstance(scanner.get("preview"), dict) else {}
    return preview.get("enabled") is True


if __name__ == "__main__":
    main()
