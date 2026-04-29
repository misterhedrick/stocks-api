from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BrokerOrder, Fill, OrderIntent, PositionSnapshot
from app.services.performance_review import get_paper_performance_review
from app.services.position_exits import (
    _latest_active_exit_order,
    _latest_position_snapshots,
    _option_expiration_date,
    _position_recommendation,
    _underlying_from_position,
    _exit_config_for_strategy,
    resolve_position_ownership,
)


@dataclass(slots=True)
class TradeLifecycleResult:
    generated_at: datetime
    positions_seen: int
    managed_positions: int
    unmanaged_positions: int
    positions: list[dict[str, Any]]


@dataclass(slots=True)
class TradeCasesResult:
    generated_at: datetime
    fills_seen: int
    matched_round_trips: int
    open_positions: list[dict[str, Any]]
    recent_round_trips: list[dict[str, Any]]
    totals: dict[str, Any]
    by_strategy: list[dict[str, Any]]
    by_symbol: list[dict[str, Any]]


def get_trade_lifecycle(
    db: Session,
    *,
    limit: int = 100,
) -> TradeLifecycleResult:
    positions = _latest_position_snapshots(db, limit=limit)
    lifecycle_positions = [_position_lifecycle(db, position) for position in positions]
    managed_positions = sum(
        1
        for position in lifecycle_positions
        if position["ownership"].get("managed") is True
    )
    return TradeLifecycleResult(
        generated_at=datetime.now(timezone.utc),
        positions_seen=len(lifecycle_positions),
        managed_positions=managed_positions,
        unmanaged_positions=len(lifecycle_positions) - managed_positions,
        positions=lifecycle_positions,
    )


def get_trade_cases(
    db: Session,
    *,
    limit: int = 500,
) -> TradeCasesResult:
    review = get_paper_performance_review(db, limit=limit)
    return TradeCasesResult(
        generated_at=review.generated_at,
        fills_seen=review.fills_seen,
        matched_round_trips=review.matched_round_trips,
        open_positions=review.open_positions,
        recent_round_trips=review.recent_round_trips,
        totals=review.totals,
        by_strategy=review.by_strategy,
        by_symbol=review.by_symbol,
    )


def _position_lifecycle(
    db: Session,
    position: PositionSnapshot,
) -> dict[str, Any]:
    ownership = resolve_position_ownership(db, position)
    exit_config = (
        _exit_config_for_strategy(ownership.strategy)
        if ownership.strategy is not None
        else None
    )
    active_exit_order = _latest_active_exit_order(db, position.symbol)
    recommended_action, reason = _position_recommendation(
        position,
        ownership,
        exit_config,
        active_exit_order,
    )
    entry_order_intent = (
        db.get(OrderIntent, ownership.order_intent_id)
        if ownership.order_intent_id is not None
        else None
    )
    entry_broker_orders = (
        _broker_orders_for_intent(db, entry_order_intent.id, side="buy")
        if entry_order_intent is not None
        else []
    )
    entry_fills = _fills_for_broker_orders(db, [order["id"] for order in entry_broker_orders])

    return {
        "symbol": position.symbol,
        "underlying_symbol": _underlying_from_position(position),
        "option_expiration_date": (
            _option_expiration_date(position.symbol).isoformat()
            if _option_expiration_date(position.symbol) is not None
            else None
        ),
        "quantity": _decimal_string(position.quantity),
        "market_value": _optional_decimal_string(position.market_value),
        "cost_basis": _optional_decimal_string(position.cost_basis),
        "unrealized_pl": _optional_decimal_string(position.unrealized_pl),
        "unrealized_pl_percent": _optional_decimal_string(
            _unrealized_pl_percent(position)
        ),
        "captured_at": position.captured_at.isoformat(),
        "ownership": ownership.as_dict(),
        "entry_order_intent": _order_intent_summary(entry_order_intent),
        "entry_broker_orders": entry_broker_orders,
        "entry_fills": entry_fills,
        "entry_fill_summary": _fill_summary(entry_fills),
        "exit_config_enabled": exit_config is not None,
        "active_exit_order": active_exit_order,
        "recommended_action": recommended_action,
        "reason": reason,
    }


def _broker_orders_for_intent(
    db: Session,
    order_intent_id: uuid.UUID,
    *,
    side: str,
) -> list[dict[str, Any]]:
    statement = (
        select(BrokerOrder)
        .where(BrokerOrder.order_intent_id == order_intent_id)
        .where(BrokerOrder.side == side)
        .order_by(BrokerOrder.submitted_at.desc().nullslast(), BrokerOrder.created_at.desc())
    )
    return [_broker_order_summary(order) for order in db.scalars(statement)]


def _fills_for_broker_orders(
    db: Session,
    broker_order_ids: list[uuid.UUID],
) -> list[dict[str, Any]]:
    if not broker_order_ids:
        return []
    statement = (
        select(Fill)
        .where(Fill.broker_order_id.in_(broker_order_ids))
        .order_by(Fill.filled_at.asc())
    )
    return [_fill_detail(fill) for fill in db.scalars(statement)]


def _order_intent_summary(order_intent: OrderIntent | None) -> dict[str, Any] | None:
    if order_intent is None:
        return None
    return {
        "id": str(order_intent.id),
        "strategy_id": str(order_intent.strategy_id)
        if order_intent.strategy_id is not None
        else None,
        "signal_id": str(order_intent.signal_id)
        if order_intent.signal_id is not None
        else None,
        "underlying_symbol": order_intent.underlying_symbol,
        "option_symbol": order_intent.option_symbol,
        "side": order_intent.side,
        "quantity": order_intent.quantity,
        "order_type": order_intent.order_type,
        "limit_price": _optional_decimal_string(order_intent.limit_price),
        "status": order_intent.status,
        "submitted_at": order_intent.submitted_at.isoformat()
        if order_intent.submitted_at is not None
        else None,
        "rationale": order_intent.rationale,
    }


def _broker_order_summary(order: BrokerOrder) -> dict[str, Any]:
    return {
        "id": order.id,
        "alpaca_order_id": order.alpaca_order_id,
        "symbol": order.symbol,
        "side": order.side,
        "quantity": _decimal_string(order.quantity),
        "order_type": order.order_type,
        "limit_price": _optional_decimal_string(order.limit_price),
        "status": order.status,
        "submitted_at": order.submitted_at.isoformat()
        if order.submitted_at is not None
        else None,
        "filled_at": order.filled_at.isoformat()
        if order.filled_at is not None
        else None,
    }


def _fill_detail(fill: Fill) -> dict[str, Any]:
    return {
        "id": str(fill.id),
        "broker_order_id": str(fill.broker_order_id)
        if fill.broker_order_id is not None
        else None,
        "alpaca_fill_id": fill.alpaca_fill_id,
        "symbol": fill.symbol,
        "side": fill.side,
        "quantity": _decimal_string(fill.quantity),
        "price": _decimal_string(fill.price),
        "notional": _decimal_string(fill.quantity * fill.price * Decimal("100")),
        "filled_at": fill.filled_at.isoformat(),
    }


def _fill_summary(fills: list[dict[str, Any]]) -> dict[str, Any]:
    total_quantity = sum(
        (Decimal(str(fill["quantity"])) for fill in fills),
        Decimal("0"),
    )
    total_notional = sum(
        (Decimal(str(fill["notional"])) for fill in fills),
        Decimal("0"),
    )
    average_price = (
        total_notional / total_quantity / Decimal("100")
        if total_quantity > 0
        else Decimal("0")
    )
    return {
        "fills_seen": len(fills),
        "filled_quantity": _decimal_string(total_quantity),
        "filled_notional": _decimal_string(total_notional),
        "average_fill_price": _decimal_string(average_price),
    }


def _unrealized_pl_percent(position: PositionSnapshot) -> Decimal | None:
    if position.unrealized_pl is None or position.cost_basis in {None, Decimal("0")}:
        return None
    return Decimal(position.unrealized_pl) / abs(Decimal(position.cost_basis)) * Decimal("100")


def _optional_decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return _decimal_string(value)


def _decimal_string(value: Decimal) -> str:
    normalized = Decimal(str(value)).normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f")
