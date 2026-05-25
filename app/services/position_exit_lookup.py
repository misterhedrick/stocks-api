from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from typing import Any
import uuid

from sqlalchemy import and_, func, or_, select

from sqlalchemy.orm import Session

from app.core.utils import current_trading_day_start_utc

from app.db.models import BrokerOrder, Fill, JobRun, OrderIntent, PositionSnapshot, Strategy

from app.services.position_exit_types import (
    BROKER_ACTIVE_EXIT_ORDER_STATUSES,
    ENTRY_BROKER_ORDER_STATUSES,
    PositionOwnership,
)

def _latest_position_snapshots(db: Session, *, limit: int) -> list[PositionSnapshot]:
    latest_reconciliation = db.scalar(
        select(JobRun)
        .where(JobRun.job_name == "reconcile_broker")
        .where(JobRun.status == "succeeded")
        .where(JobRun.finished_at.is_not(None))
        .order_by(JobRun.finished_at.desc())
        .limit(1)
    )
    if (
        latest_reconciliation is not None
        and latest_reconciliation.started_at is not None
        and latest_reconciliation.finished_at is not None
    ):
        statement = (
            select(PositionSnapshot)
            .where(PositionSnapshot.captured_at >= latest_reconciliation.started_at)
            .where(PositionSnapshot.captured_at <= latest_reconciliation.finished_at)
            .where(PositionSnapshot.quantity > 0)
            .order_by(PositionSnapshot.captured_at.desc())
            .limit(limit)
        )
        return list(db.scalars(statement))

    latest_captured_at = (
        select(
            PositionSnapshot.symbol.label("symbol"),
            func.max(PositionSnapshot.captured_at).label("captured_at"),
        )
        .group_by(PositionSnapshot.symbol)
        .subquery()
    )
    statement = (
        select(PositionSnapshot)
        .join(
            latest_captured_at,
            and_(
                PositionSnapshot.symbol == latest_captured_at.c.symbol,
                PositionSnapshot.captured_at == latest_captured_at.c.captured_at,
            ),
        )
        .where(PositionSnapshot.quantity > 0)
        .order_by(PositionSnapshot.captured_at.desc())
        .limit(limit)
    )
    return list(db.scalars(statement))

def resolve_position_ownership(
    db: Session,
    position: PositionSnapshot,
) -> PositionOwnership:
    open_lot = _latest_open_entry_lot_for_position(db, position.symbol)
    if open_lot is not None:
        order_intent = open_lot.get("order_intent")
        open_quantity = open_lot.get("remaining_quantity")
        if order_intent is None:
            return PositionOwnership(
                symbol=position.symbol,
                managed=False,
                reason="no open strategy-linked entry lot found",
            )
    else:
        order_intent = _latest_entry_order_intent_for_position(db, position.symbol)
        open_quantity = None
    if order_intent is None:
        return PositionOwnership(
            symbol=position.symbol,
            managed=False,
            reason="no linked entry order intent found",
        )

    if order_intent.strategy_id is None:
        return PositionOwnership(
            symbol=position.symbol,
            managed=False,
            reason="linked order intent has no strategy",
            order_intent_id=order_intent.id,
        )

    strategy = db.get(Strategy, order_intent.strategy_id)
    if strategy is None:
        return PositionOwnership(
            symbol=position.symbol,
            managed=False,
            reason="linked strategy was not found",
            strategy_id=order_intent.strategy_id,
            order_intent_id=order_intent.id,
        )

    if not strategy.is_active:
        return PositionOwnership(
            symbol=position.symbol,
            managed=False,
            reason=f"linked strategy '{strategy.name}' is inactive",
            strategy=strategy,
            strategy_id=strategy.id,
            strategy_name=strategy.name,
            order_intent_id=order_intent.id,
        )

    return PositionOwnership(
        symbol=position.symbol,
        managed=True,
        reason=f"linked to active strategy '{strategy.name}'",
        strategy=strategy,
        strategy_id=strategy.id,
        strategy_name=strategy.name,
        order_intent_id=order_intent.id,
        open_quantity=open_quantity if isinstance(open_quantity, Decimal) else None,
    )

def _latest_entry_order_intent_for_position(
    db: Session,
    symbol: str,
) -> OrderIntent | None:
    statement = (
        select(OrderIntent)
        .select_from(BrokerOrder)
        .join(OrderIntent, BrokerOrder.order_intent_id == OrderIntent.id)
        .where(BrokerOrder.symbol == symbol)
        .where(OrderIntent.option_symbol == symbol)
        .where(func.lower(OrderIntent.side) == "buy")
        .where(func.lower(BrokerOrder.side) == "buy")
        .where(BrokerOrder.status.in_(ENTRY_BROKER_ORDER_STATUSES))
        .order_by(BrokerOrder.submitted_at.desc().nullslast(), BrokerOrder.created_at.desc())
        .limit(1)
    )
    return db.scalar(statement)


def _latest_open_entry_lot_for_position(
    db: Session,
    symbol: str,
) -> dict[str, Any] | None:
    try:
        rows = list(db.execute(_strategy_fill_statement_for_symbol(symbol)))
    except Exception:
        return None

    open_lots: dict[uuid.UUID, list[dict[str, Any]]] = {}
    saw_fill = False
    for row in rows:
        values = tuple(row)
        side = str(values[2]).lower()
        strategy_id = values[6]
        order_intent_id = values[5]
        if strategy_id is None or order_intent_id is None:
            continue
        saw_fill = True
        quantity = Decimal(str(values[3]))
        if side == "buy":
            open_lots.setdefault(strategy_id, []).append(
                {
                    "strategy_id": strategy_id,
                    "order_intent_id": order_intent_id,
                    "filled_at": values[1],
                    "remaining_quantity": quantity,
                }
            )
            continue
        if side != "sell":
            continue

        remaining_sell_quantity = quantity
        lots = open_lots.get(strategy_id, [])
        while remaining_sell_quantity > 0 and lots:
            lot = lots[0]
            matched_quantity = min(remaining_sell_quantity, lot["remaining_quantity"])
            lot["remaining_quantity"] -= matched_quantity
            remaining_sell_quantity -= matched_quantity
            if lot["remaining_quantity"] <= 0:
                lots.pop(0)

    if not saw_fill:
        return None

    remaining_lots = [
        lot
        for lots in open_lots.values()
        for lot in lots
        if lot["remaining_quantity"] > 0
    ]
    if not remaining_lots:
        return {}

    latest_lot = max(
        remaining_lots,
        key=lambda item: item["filled_at"] or datetime.min.replace(tzinfo=timezone.utc),
    )
    order_intent = db.get(OrderIntent, latest_lot["order_intent_id"])
    if order_intent is None:
        return {}
    return {
        "order_intent": order_intent,
        "remaining_quantity": latest_lot["remaining_quantity"],
    }


def _strategy_fill_statement_for_symbol(symbol: str) -> object:
    return (
        select(
            Fill.id,
            Fill.filled_at,
            Fill.side,
            Fill.quantity,
            Fill.price,
            OrderIntent.id,
            OrderIntent.strategy_id,
        )
        .select_from(Fill)
        .join(BrokerOrder, Fill.broker_order_id == BrokerOrder.id)
        .join(OrderIntent, BrokerOrder.order_intent_id == OrderIntent.id)
        .where(Fill.symbol == symbol)
        .where(OrderIntent.option_symbol == symbol)
        .where(OrderIntent.strategy_id.is_not(None))
        .order_by(Fill.filled_at.asc(), Fill.created_at.asc())
    )

def _exit_config_for_strategy(strategy: Strategy) -> dict[str, Any] | None:
    scanner_config = strategy.config.get("scanner")
    if not isinstance(scanner_config, dict):
        return None

    exit_config = scanner_config.get("exit")
    if not isinstance(exit_config, dict) or exit_config.get("enabled") is not True:
        return None
    return exit_config

def _has_active_exit_order(db: Session, symbol: str) -> bool:
    statement = (
        select(func.count(OrderIntent.id))
        .where(OrderIntent.option_symbol == symbol)
        .where(func.lower(OrderIntent.side) == "sell")
        .where(_active_exit_order_status_filter())
    )
    value = db.scalar(statement)
    try:
        return int(value or 0) > 0
    except (TypeError, ValueError):
        return False

def _latest_active_exit_order(
    db: Session,
    symbol: str,
) -> dict[str, Any] | None:
    order_intent = db.scalar(
        select(OrderIntent)
        .where(OrderIntent.option_symbol == symbol)
        .where(func.lower(OrderIntent.side) == "sell")
        .where(_active_exit_order_status_filter())
        .order_by(OrderIntent.created_at.desc())
        .limit(1)
    )
    if order_intent is None:
        return None
    return {
        "order_intent_id": str(order_intent.id),
        "status": order_intent.status,
        "quantity": order_intent.quantity,
        "order_type": order_intent.order_type,
        "limit_price": str(order_intent.limit_price)
        if order_intent.limit_price is not None
        else None,
        "created_at": order_intent.created_at.isoformat()
        if order_intent.created_at is not None
        else None,
    }

def _active_exit_order_status_filter() -> object:
    return or_(
        OrderIntent.status.in_(BROKER_ACTIVE_EXIT_ORDER_STATUSES),
        and_(
            OrderIntent.status == "previewed",
            OrderIntent.created_at >= current_trading_day_start_utc(),
        ),
    )

def _entry_fill_time(db: Session, ownership: PositionOwnership) -> datetime | None:
    if ownership.order_intent_id is None:
        return None
    statement = (
        select(func.min(Fill.filled_at))
        .select_from(Fill)
        .join(BrokerOrder, Fill.broker_order_id == BrokerOrder.id)
        .where(BrokerOrder.order_intent_id == ownership.order_intent_id)
        .where(func.lower(Fill.side) == "buy")
    )
    return db.scalar(statement)
