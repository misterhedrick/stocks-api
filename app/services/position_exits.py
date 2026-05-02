from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
import re
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.utils import current_trading_day_start_utc
from app.db.models import BrokerOrder, Fill, JobRun, OrderIntent, PositionSnapshot, Strategy
from app.integrations.alpaca import AlpacaMarketDataClient
from app.services.audit_logs import record_audit_log
from app.services.order_intents import (
    _build_quote_preview,
    _decimal_from_preview,
)


@dataclass(slots=True)
class ExitEvaluationResult:
    positions_seen: int
    positions_evaluated: int
    exits_created: int
    exits_skipped: int
    errors: list[str]
    no_exit_reasons: list[str]
    position_ownership: list[dict[str, Any]]
    order_intent_ids: list[uuid.UUID]


@dataclass(slots=True)
class PositionOwnership:
    symbol: str
    managed: bool
    reason: str
    strategy: Strategy | None = None
    strategy_id: uuid.UUID | None = None
    strategy_name: str | None = None
    order_intent_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "managed": self.managed,
            "reason": self.reason,
            "strategy_id": str(self.strategy_id) if self.strategy_id else None,
            "strategy_name": self.strategy_name,
            "order_intent_id": str(self.order_intent_id)
            if self.order_intent_id
            else None,
        }


@dataclass(slots=True)
class PositionManagementStatus:
    symbol: str
    quantity: str
    market_value: str | None
    cost_basis: str | None
    unrealized_pl: str | None
    captured_at: str
    ownership: dict[str, Any]
    exit_config_enabled: bool
    active_exit_order: dict[str, Any] | None
    recommended_action: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "market_value": self.market_value,
            "cost_basis": self.cost_basis,
            "unrealized_pl": self.unrealized_pl,
            "captured_at": self.captured_at,
            "ownership": self.ownership,
            "exit_config_enabled": self.exit_config_enabled,
            "active_exit_order": self.active_exit_order,
            "recommended_action": self.recommended_action,
            "reason": self.reason,
        }


ACTIVE_EXIT_ORDER_STATUSES = {
    "previewed",
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "submitted",
}
BROKER_ACTIVE_EXIT_ORDER_STATUSES = ACTIVE_EXIT_ORDER_STATUSES - {"previewed"}
ENTRY_BROKER_ORDER_STATUSES = {
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "filled",
    "submitted",
}

OPTION_EXPIRATION_PATTERN = re.compile(r"(\d{6})([CP])\d{8}$")


def evaluate_position_exits(
    db: Session,
    *,
    limit: int = 100,
    market_data_client: AlpacaMarketDataClient | None = None,
) -> ExitEvaluationResult:
    positions = _latest_position_snapshots(db, limit=limit)
    client = market_data_client or AlpacaMarketDataClient.from_settings()

    positions_evaluated = 0
    exits_created = 0
    exits_skipped = 0
    errors: list[str] = []
    no_exit_reasons: list[str] = []
    position_ownership: list[dict[str, Any]] = []
    order_intent_ids: list[uuid.UUID] = []

    for position in positions:
        if position.quantity <= 0:
            no_exit_reasons.append(f"{position.symbol}: quantity is not long")
            continue

        ownership = resolve_position_ownership(db, position)
        position_ownership.append(ownership.as_dict())
        if not ownership.managed or ownership.strategy is None:
            no_exit_reasons.append(f"{position.symbol}: {ownership.reason}")
            continue

        strategy = ownership.strategy
        exit_config = _exit_config_for_strategy(strategy)
        if exit_config is None:
            no_exit_reasons.append(
                f"{position.symbol}: linked strategy '{strategy.name}' scanner.exit is not enabled"
            )
            continue

        positions_evaluated += 1
        entry_time = _entry_fill_time(db, ownership)
        trigger_reason = _exit_trigger_reason(
            position,
            exit_config,
            today=datetime.now(ZoneInfo("America/New_York")).date(),
            entry_time=entry_time,
        )
        if trigger_reason is None:
            no_exit_reasons.append(f"{position.symbol}: no exit rule triggered")
            continue

        if _has_active_exit_order(db, position.symbol):
            exits_skipped += 1
            no_exit_reasons.append(f"{position.symbol}: active exit order already exists")
            continue

        try:
            order_intent = _create_exit_order_intent(
                db,
                position,
                strategy,
                exit_config,
                trigger_reason=trigger_reason,
                market_data_client=client,
            )
        except Exception as exc:
            exits_skipped += 1
            errors.append(f"{position.symbol}: {exc.__class__.__name__}: {exc}")
            continue

        exits_created += 1
        order_intent_ids.append(order_intent.id)

    return ExitEvaluationResult(
        positions_seen=len(positions),
        positions_evaluated=positions_evaluated,
        exits_created=exits_created,
        exits_skipped=exits_skipped,
        errors=errors,
        no_exit_reasons=no_exit_reasons,
        position_ownership=position_ownership,
        order_intent_ids=order_intent_ids,
    )


def preview_unmanaged_position_exits(
    db: Session,
    *,
    symbol: str | None = None,
    limit: int = 100,
    market_data_client: AlpacaMarketDataClient | None = None,
) -> ExitEvaluationResult:
    positions = _latest_position_snapshots(db, limit=limit)
    if symbol is not None:
        normalized_symbol = symbol.strip().upper()
        positions = [
            position
            for position in positions
            if position.symbol.upper() == normalized_symbol
        ]

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    positions_evaluated = 0
    exits_created = 0
    exits_skipped = 0
    errors: list[str] = []
    no_exit_reasons: list[str] = []
    position_ownership: list[dict[str, Any]] = []
    order_intent_ids: list[uuid.UUID] = []

    for position in positions:
        if position.quantity <= 0:
            no_exit_reasons.append(f"{position.symbol}: quantity is not long")
            continue

        ownership = resolve_position_ownership(db, position)
        position_ownership.append(ownership.as_dict())
        if ownership.managed:
            no_exit_reasons.append(f"{position.symbol}: position is already managed")
            continue

        positions_evaluated += 1
        if _has_active_exit_order(db, position.symbol):
            exits_skipped += 1
            no_exit_reasons.append(f"{position.symbol}: active exit order already exists")
            continue

        try:
            order_intent = _create_exit_order_intent(
                db,
                position,
                None,
                _default_unmanaged_exit_config(),
                trigger_reason=f"manual unmanaged exit preview: {ownership.reason}",
                market_data_client=client,
            )
        except Exception as exc:
            exits_skipped += 1
            errors.append(f"{position.symbol}: {exc.__class__.__name__}: {exc}")
            continue

        exits_created += 1
        order_intent_ids.append(order_intent.id)

    return ExitEvaluationResult(
        positions_seen=len(positions),
        positions_evaluated=positions_evaluated,
        exits_created=exits_created,
        exits_skipped=exits_skipped,
        errors=errors,
        no_exit_reasons=no_exit_reasons,
        position_ownership=position_ownership,
        order_intent_ids=order_intent_ids,
    )


def get_position_management_statuses(
    db: Session,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for position in _latest_position_snapshots(db, limit=limit):
        ownership = resolve_position_ownership(db, position)
        exit_config = (
            _exit_config_for_strategy(ownership.strategy)
            if ownership.strategy is not None
            else None
        )
        active_exit_order = _latest_active_exit_order(db, position.symbol)
        recommended_action, reason = _position_recommendation(
            db,
            position,
            ownership,
            exit_config,
            active_exit_order,
        )
        statuses.append(
            PositionManagementStatus(
                symbol=position.symbol,
                quantity=str(position.quantity),
                market_value=str(position.market_value)
                if position.market_value is not None
                else None,
                cost_basis=str(position.cost_basis)
                if position.cost_basis is not None
                else None,
                unrealized_pl=str(position.unrealized_pl)
                if position.unrealized_pl is not None
                else None,
                captured_at=position.captured_at.isoformat(),
                ownership=ownership.as_dict(),
                exit_config_enabled=exit_config is not None,
                active_exit_order=active_exit_order,
                recommended_action=recommended_action,
                reason=reason,
            ).as_dict()
        )
    return statuses


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
    order_intent = _latest_entry_order_intent_for_position(db, position.symbol)
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


def _exit_config_for_strategy(strategy: Strategy) -> dict[str, Any] | None:
    scanner_config = strategy.config.get("scanner")
    if not isinstance(scanner_config, dict):
        return None

    exit_config = scanner_config.get("exit")
    if not isinstance(exit_config, dict) or exit_config.get("enabled") is not True:
        return None
    return exit_config


def _exit_trigger_reason(
    position: PositionSnapshot,
    exit_config: dict[str, Any],
    *,
    today: date,
    entry_time: datetime | None = None,
) -> str | None:
    pnl_percent = _unrealized_pl_percent(position)
    stop_loss_percent = _optional_positive_decimal(exit_config.get("stop_loss_percent"))
    if (
        pnl_percent is not None
        and stop_loss_percent is not None
        and pnl_percent <= -stop_loss_percent
    ):
        return f"stop_loss_percent triggered at {pnl_percent}%"

    profit_target_percent = _optional_positive_decimal(
        exit_config.get("profit_target_percent")
    )
    if (
        pnl_percent is not None
        and profit_target_percent is not None
        and pnl_percent >= profit_target_percent
    ):
        return f"profit_target_percent triggered at {pnl_percent}%"

    max_days_to_expiration = _optional_int(exit_config.get("max_days_to_expiration"))
    expiration_date = _option_expiration_date(position.symbol)
    if max_days_to_expiration is not None and expiration_date is not None:
        days_to_expiration = (expiration_date - today).days
        if days_to_expiration <= max_days_to_expiration:
            return f"max_days_to_expiration triggered with {days_to_expiration} days left"

    max_hold_hours = _optional_int(exit_config.get("max_hold_hours"))
    if max_hold_hours is not None and entry_time is not None:
        entry_utc = (
            entry_time.astimezone(timezone.utc)
            if entry_time.tzinfo is not None
            else entry_time.replace(tzinfo=timezone.utc)
        )
        hours_held = (datetime.now(timezone.utc) - entry_utc).total_seconds() / 3600
        if hours_held >= max_hold_hours:
            return f"max_hold_hours triggered after {hours_held:.1f}h"

    return None


def _create_exit_order_intent(
    db: Session,
    position: PositionSnapshot,
    strategy: Strategy | None,
    exit_config: dict[str, Any],
    *,
    trigger_reason: str,
    market_data_client: AlpacaMarketDataClient,
) -> OrderIntent:
    quantity = min(
        int(position.quantity),
        _optional_int(exit_config.get("max_contracts_per_exit")) or int(position.quantity),
    )
    if quantity <= 0:
        raise ValueError("exit quantity must be greater than 0")

    data_feed = _string_config(exit_config, "data_feed", default="indicative")
    latest_quote = _latest_quote_for_position(
        market_data_client,
        position.symbol,
        data_feed=data_feed,
    )
    quote_preview = _build_quote_preview(
        latest_quote,
        side="sell",
        quantity=quantity,
        supplied_limit_price=None,
    )

    max_spread = _optional_positive_decimal(exit_config.get("max_spread"))
    spread = _decimal_from_preview(quote_preview.get("spread"))
    if max_spread is not None and spread is not None and spread > max_spread:
        raise ValueError(f"quote spread {spread} exceeds scanner.exit.max_spread")

    order_type = _string_config(exit_config, "order_type", default="limit")
    if order_type not in {"limit", "market"}:
        raise ValueError("scanner.exit.order_type must be limit or market")

    limit_price = None
    if order_type == "limit":
        limit_price = _exit_limit_price(quote_preview, exit_config)
        if limit_price is None:
            raise ValueError("unable to derive exit limit price from quote")

    order_intent = OrderIntent(
        strategy_id=strategy.id if strategy is not None else None,
        signal_id=None,
        underlying_symbol=_underlying_from_position(position),
        option_symbol=position.symbol,
        side="sell",
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        time_in_force=_string_config(exit_config, "time_in_force", default="day"),
        status="previewed",
        rationale=f"Exit {position.symbol}: {trigger_reason}",
        preview={
            "source": "position_exit_evaluator",
            "data_feed": data_feed,
            "trigger_reason": trigger_reason,
            "position": {
                "symbol": position.symbol,
                "quantity": str(position.quantity),
                "market_value": str(position.market_value)
                if position.market_value is not None
                else None,
                "cost_basis": str(position.cost_basis)
                if position.cost_basis is not None
                else None,
                "unrealized_pl": str(position.unrealized_pl)
                if position.unrealized_pl is not None
                else None,
                "captured_at": position.captured_at.isoformat(),
            },
            "position_ownership": {
                "strategy_id": str(strategy.id) if strategy is not None else None,
                "strategy_name": strategy.name if strategy is not None else None,
            },
            "quote": quote_preview,
        },
    )

    db.add(order_intent)
    db.flush()
    record_audit_log(
        db,
        event_type="order_intent.exit_previewed",
        entity_type="order_intent",
        entity_id=order_intent.id,
        message="Exit order intent preview generated from current position",
        payload={
            "strategy_id": str(strategy.id) if strategy is not None else None,
            "option_symbol": order_intent.option_symbol,
            "side": order_intent.side,
            "quantity": order_intent.quantity,
            "order_type": order_intent.order_type,
            "limit_price": str(order_intent.limit_price)
            if order_intent.limit_price is not None
            else None,
            "trigger_reason": trigger_reason,
        },
    )
    db.commit()
    db.refresh(order_intent)
    return order_intent


def _default_unmanaged_exit_config() -> dict[str, Any]:
    return {
        "order_type": "limit",
        "limit_price_source": "bid",
        "time_in_force": "day",
        "data_feed": "indicative",
    }


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


def _position_recommendation(
    db: Session,
    position: PositionSnapshot,
    ownership: PositionOwnership,
    exit_config: dict[str, Any] | None,
    active_exit_order: dict[str, Any] | None,
) -> tuple[str, str]:
    if active_exit_order is not None:
        return (
            "exit_pending",
            f"active exit order intent {active_exit_order['order_intent_id']} is {active_exit_order['status']}",
        )

    if not ownership.managed:
        return ("preview_unmanaged_exit", ownership.reason)

    if exit_config is None:
        return ("add_exit_config", "linked strategy does not have scanner.exit enabled")

    entry_time = _entry_fill_time(db, ownership)
    trigger_reason = _exit_trigger_reason(
        position,
        exit_config,
        today=datetime.now(ZoneInfo("America/New_York")).date(),
        entry_time=entry_time,
    )
    if trigger_reason is not None:
        return ("exit_rule_triggered", trigger_reason)

    return ("hold", "no exit rule triggered")


def _exit_limit_price(
    quote_preview: dict[str, object],
    exit_config: dict[str, Any],
) -> Decimal | None:
    source = _string_config(exit_config, "limit_price_source", default="bid")
    if source not in {"bid", "midpoint"}:
        raise ValueError("scanner.exit.limit_price_source must be bid or midpoint")

    key = "bid_price" if source == "bid" else "midpoint"
    value = _decimal_from_preview(quote_preview.get(key))
    if value is None:
        return None
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _unrealized_pl_percent(position: PositionSnapshot) -> Decimal | None:
    if position.unrealized_pl is None or position.cost_basis in {None, Decimal("0")}:
        return None
    try:
        return (
            Decimal(position.unrealized_pl)
            / abs(Decimal(position.cost_basis))
            * Decimal("100")
        )
    except (InvalidOperation, ZeroDivisionError):
        return None


def _option_expiration_date(symbol: str) -> date | None:
    match = OPTION_EXPIRATION_PATTERN.search(symbol)
    if match is None:
        return None
    raw_date = match.group(1)
    try:
        return datetime.strptime(raw_date, "%y%m%d").date()
    except ValueError:
        return None


def _underlying_from_position(position: PositionSnapshot) -> str:
    raw_position = position.raw_position if isinstance(position.raw_position, dict) else {}
    underlying = raw_position.get("underlying_symbol")
    if isinstance(underlying, str) and underlying.strip():
        return underlying.strip().upper()

    symbol = position.symbol
    match = OPTION_EXPIRATION_PATTERN.search(symbol)
    if match is not None:
        return symbol[: match.start()].strip().upper()
    return symbol.strip().upper()


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


def _latest_quote_for_position(
    market_data_client: AlpacaMarketDataClient,
    symbol: str,
    *,
    data_feed: str,
) -> object:
    if _option_expiration_date(symbol) is not None:
        return market_data_client.get_latest_option_quote(symbol, feed=data_feed)

    stock_quotes = market_data_client.get_latest_stock_quotes([symbol], feed="iex")
    quote = stock_quotes.get(symbol)
    if quote is None:
        raise ValueError(f"no latest stock quote returned for {symbol}")
    return quote


def _optional_positive_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("exit decimal config values must be decimals") from exc
    if decimal_value <= Decimal("0"):
        raise ValueError("exit decimal config values must be greater than 0")
    return decimal_value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        int_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("exit integer config values must be integers") from exc
    if int_value < 0:
        raise ValueError("exit integer config values must be greater than or equal to 0")
    return int_value


def _string_config(
    config: dict[str, Any],
    key: str,
    *,
    default: str,
) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"scanner.exit.{key} must be a non-empty string")
    return value.strip().lower()
