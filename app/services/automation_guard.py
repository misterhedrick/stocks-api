from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import BrokerOrder, JobRun, OrderIntent, PositionSnapshot


@dataclass(slots=True)
class AutomationDecision:
    allowed: bool
    reasons: list[str]
    limits_snapshot: dict[str, Any]


def can_auto_submit_order_intent(
    db: Session,
    order_intent: OrderIntent,
    *,
    cycle_id: str | None = None,
) -> AutomationDecision:
    reasons: list[str] = []
    price = _order_intent_price(order_intent)
    estimated_premium = (
        price * Decimal(order_intent.quantity) * Decimal("100")
        if price is not None
        else None
    )
    submitted_today = _submitted_orders_today(db)
    submitted_today_for_symbol = _submitted_orders_today(
        db,
        underlying_symbol=order_intent.underlying_symbol,
    )
    submitted_this_cycle = _submitted_orders_for_cycle(db, cycle_id=cycle_id)
    open_positions = _open_positions_count(db)
    open_positions_for_symbol = _open_positions_count(
        db,
        underlying_symbol=order_intent.underlying_symbol,
    )
    has_broker_order = _has_broker_order(db, order_intent)

    limits_snapshot = {
        "trading_automation_enabled": settings.trading_automation_enabled,
        "market_cycle_submit_enabled": settings.market_cycle_submit_enabled,
        "auto_submit_requires_paper": settings.auto_submit_requires_paper,
        "paper_mode": settings.alpaca_paper,
        "max_auto_orders_per_cycle": settings.max_auto_orders_per_cycle,
        "max_auto_orders_per_day": settings.max_auto_orders_per_day,
        "max_auto_orders_per_symbol_per_day": settings.max_auto_orders_per_symbol_per_day,
        "max_open_positions": settings.max_open_positions,
        "max_open_positions_per_symbol": settings.max_open_positions_per_symbol,
        "max_contracts_per_order": settings.max_contracts_per_order,
        "max_estimated_premium_per_order": str(
            settings.max_estimated_premium_per_order
        ),
        "cycle_id": cycle_id,
        "submitted_auto_orders_today": submitted_today,
        "submitted_auto_orders_today_for_symbol": submitted_today_for_symbol,
        "submitted_auto_orders_this_cycle": submitted_this_cycle,
        "open_positions": open_positions,
        "open_positions_for_symbol": open_positions_for_symbol,
        "order_intent_status": order_intent.status,
        "order_quantity": order_intent.quantity,
        "order_price": str(price) if price is not None else None,
        "estimated_premium": str(estimated_premium)
        if estimated_premium is not None
        else None,
        "price_available": price is not None,
        "has_broker_order": has_broker_order,
    }

    if not settings.trading_automation_enabled:
        reasons.append("TRADING_AUTOMATION_ENABLED is false")
    if not settings.market_cycle_submit_enabled:
        reasons.append("MARKET_CYCLE_SUBMIT_ENABLED is false")
    if settings.auto_submit_requires_paper and not settings.alpaca_paper:
        reasons.append("AUTO_SUBMIT_REQUIRES_PAPER is true and ALPACA_PAPER is false")
    if order_intent.status != "previewed":
        reasons.append("order intent status is not previewed")
    if has_broker_order:
        reasons.append("order intent already has a broker_order")
    if order_intent.quantity > settings.max_contracts_per_order:
        reasons.append("order intent quantity exceeds MAX_CONTRACTS_PER_ORDER")
    if (
        estimated_premium is not None
        and estimated_premium > settings.max_estimated_premium_per_order
    ):
        reasons.append(
            "estimated premium exceeds MAX_ESTIMATED_PREMIUM_PER_ORDER"
        )
    if submitted_today >= settings.max_auto_orders_per_day:
        reasons.append("MAX_AUTO_ORDERS_PER_DAY reached")
    if submitted_today_for_symbol >= settings.max_auto_orders_per_symbol_per_day:
        reasons.append("MAX_AUTO_ORDERS_PER_SYMBOL_PER_DAY reached")
    if submitted_this_cycle >= settings.max_auto_orders_per_cycle:
        reasons.append("MAX_AUTO_ORDERS_PER_CYCLE reached")
    if open_positions >= settings.max_open_positions:
        reasons.append("MAX_OPEN_POSITIONS reached")
    if open_positions_for_symbol >= settings.max_open_positions_per_symbol:
        reasons.append("MAX_OPEN_POSITIONS_PER_SYMBOL reached")

    return AutomationDecision(
        allowed=not reasons,
        reasons=reasons,
        limits_snapshot=limits_snapshot,
    )


def _has_broker_order(db: Session, order_intent: OrderIntent) -> bool:
    broker_orders = getattr(order_intent, "broker_orders", None)
    if broker_orders is not None and len(broker_orders) > 0:
        return True

    statement = select(func.count(BrokerOrder.id)).where(
        BrokerOrder.order_intent_id == order_intent.id
    )
    return _int_scalar(db, statement) > 0


def _submitted_orders_today(
    db: Session,
    *,
    underlying_symbol: str | None = None,
) -> int:
    trading_tz = ZoneInfo("America/New_York")
    local_now = datetime.now(timezone.utc).astimezone(trading_tz)
    day_start = datetime.combine(local_now.date(), time.min, tzinfo=trading_tz).astimezone(timezone.utc)
    day_end = datetime.combine(local_now.date(), time.max, tzinfo=trading_tz).astimezone(timezone.utc)
    statement = (
        select(func.count(BrokerOrder.id))
        .where(BrokerOrder.submitted_at.is_not(None))
        .where(BrokerOrder.submitted_at >= day_start)
        .where(BrokerOrder.submitted_at <= day_end)
    )
    if underlying_symbol is not None:
        normalized = underlying_symbol.strip().upper()
        if not normalized:
            return 0
        statement = (
            statement.join(OrderIntent, BrokerOrder.order_intent_id == OrderIntent.id)
            .where(func.upper(OrderIntent.underlying_symbol) == normalized)
        )
    return _int_scalar(db, statement)


def _submitted_orders_for_cycle(db: Session, *, cycle_id: str | None) -> int:
    if cycle_id is None:
        return 0
    try:
        job_run_id = uuid.UUID(str(cycle_id))
    except ValueError:
        return 0
    job_run = db.get(JobRun, job_run_id)
    if job_run is None:
        return 0
    statement = (
        select(func.count(BrokerOrder.id))
        .where(BrokerOrder.submitted_at.is_not(None))
        .where(BrokerOrder.submitted_at >= job_run.started_at)
    )
    # Only apply an upper bound when the job has finished. While still running,
    # any order submitted after the cycle started belongs to this cycle.
    if job_run.finished_at is not None:
        statement = statement.where(BrokerOrder.submitted_at <= job_run.finished_at)
    return _int_scalar(db, statement)


def _open_positions_count(
    db: Session,
    *,
    underlying_symbol: str | None = None,
) -> int:
    latest_captured_at = (
        select(
            PositionSnapshot.symbol.label("symbol"),
            func.max(PositionSnapshot.captured_at).label("captured_at"),
        )
        .group_by(PositionSnapshot.symbol)
        .subquery()
    )
    open_positions = (
        select(PositionSnapshot.symbol)
        .join(
            latest_captured_at,
            (PositionSnapshot.symbol == latest_captured_at.c.symbol)
            & (PositionSnapshot.captured_at == latest_captured_at.c.captured_at),
        )
        .where(PositionSnapshot.quantity > 0)
    )
    if underlying_symbol is not None:
        normalized = underlying_symbol.strip().upper()
        if not normalized:
            return 0
        # Match the exact stock symbol OR option contracts whose symbol starts
        # with the underlying immediately followed by a 6-digit expiration date
        # (e.g. "SPY240315C00500000"). The [0-9] anchor prevents "SPY" from
        # matching unrelated tickers like "SPYG" or "SPYD".
        open_positions = open_positions.where(
            or_(
                func.upper(PositionSnapshot.symbol) == normalized,
                PositionSnapshot.symbol.regexp_match(
                    f"^{re.escape(normalized)}[0-9]", flags="i"
                ),
            )
        )

    statement = select(func.count()).select_from(open_positions.subquery())
    return _int_scalar(db, statement)


def _order_intent_price(order_intent: OrderIntent) -> Decimal | None:
    if order_intent.limit_price is not None:
        return Decimal(str(order_intent.limit_price))

    preview = order_intent.preview if isinstance(order_intent.preview, dict) else {}
    for quote in _quote_candidates(preview):
        for key in ("estimated_price", "suggested_limit_price", "midpoint"):
            price = _decimal_or_none(quote.get(key))
            if price is not None:
                return price
    return None


def _quote_candidates(preview: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    quote = preview.get("quote")
    if isinstance(quote, dict):
        candidates.append(quote)

    selection = preview.get("selection")
    if isinstance(selection, dict):
        selection_quote = selection.get("quote")
        if isinstance(selection_quote, dict):
            candidates.append(selection_quote)
    return candidates


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _int_scalar(db: Session, statement: object) -> int:
    value = db.scalar(statement)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
