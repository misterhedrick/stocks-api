from __future__ import annotations

from datetime import datetime

from typing import Any

from sqlalchemy import select

from sqlalchemy.orm import Session

from app.db.models import OrderIntent, Signal

def cleanup_stale_trading_state(
    db: Session,
    *,
    stale_before: datetime,
    source: str,
    limit: int = 1000,
) -> dict[str, Any]:
    stale_signals = list(
        db.scalars(
            select(Signal)
            .where(Signal.status == "new")
            .where(Signal.created_at < stale_before)
            .order_by(Signal.created_at.asc())
            .limit(limit)
        )
    )
    stale_order_intents = list(
        db.scalars(
            select(OrderIntent)
            .where(OrderIntent.status == "previewed")
            .where(OrderIntent.submitted_at.is_(None))
            .where(OrderIntent.created_at < stale_before)
            .order_by(OrderIntent.created_at.asc())
            .limit(limit)
        )
    )

    reason = f"Marked stale by {source} before {stale_before.isoformat()}"
    for signal in stale_signals:
        signal.status = "stale"
        signal.rejected_reason = reason
        db.add(signal)

    for order_intent in stale_order_intents:
        order_intent.status = "stale"
        order_intent.rejection_reason = reason
        db.add(order_intent)

    return {
        "stale_before": stale_before.isoformat(),
        "signals_marked_stale": len(stale_signals),
        "order_intents_marked_stale": len(stale_order_intents),
        "oldest_stale_signal_created_at": _oldest_created_at(stale_signals),
        "oldest_stale_order_intent_created_at": _oldest_created_at(stale_order_intents),
        "signals_by_strategy_id": _counts_by_strategy_id(stale_signals),
        "order_intents_by_strategy_id": _counts_by_strategy_id(stale_order_intents),
        "signal_ids": [str(signal.id) for signal in stale_signals],
        "order_intent_ids": [str(order_intent.id) for order_intent in stale_order_intents],
    }

def _oldest_created_at(rows: list[object]) -> str | None:
    timestamps = [
        getattr(row, "created_at", None)
        for row in rows
        if getattr(row, "created_at", None) is not None
    ]
    if not timestamps:
        return None
    return min(timestamps).isoformat()

def _counts_by_strategy_id(rows: list[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        strategy_id = getattr(row, "strategy_id", None)
        key = str(strategy_id) if strategy_id is not None else "none"
        counts[key] = counts.get(key, 0) + 1
    return counts
