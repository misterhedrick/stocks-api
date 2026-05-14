from __future__ import annotations

from datetime import date, datetime, timezone

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from typing import Any

from zoneinfo import ZoneInfo

from sqlalchemy import select

from sqlalchemy.orm import Session

from app.db.models import PositionSnapshot

from app.integrations.alpaca import AlpacaMarketDataClient

from app.services.order_intents import _decimal_from_preview

from app.services.position_exit_lookup import _entry_fill_time

from app.services.position_exit_types import OPTION_EXPIRATION_PATTERN, PositionOwnership

def _exit_trigger_reason(
    position: PositionSnapshot,
    exit_config: dict[str, Any],
    *,
    today: date,
    entry_time: datetime | None = None,
    peak_unrealized_pl_percent: Decimal | None = None,
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

    trailing_activation_percent = _optional_positive_decimal(
        exit_config.get("trailing_profit_activation_percent")
    )
    trailing_giveback_percent = _optional_positive_decimal(
        exit_config.get("trailing_profit_giveback_percent")
    )
    if (
        pnl_percent is not None
        and peak_unrealized_pl_percent is not None
        and trailing_activation_percent is not None
        and trailing_giveback_percent is not None
        and peak_unrealized_pl_percent >= trailing_activation_percent
        and peak_unrealized_pl_percent - pnl_percent >= trailing_giveback_percent
    ):
        return (
            "trailing_profit_giveback_percent triggered at "
            f"{pnl_percent}% after peak {peak_unrealized_pl_percent}%"
        )

    max_days_to_expiration = _optional_int(exit_config.get("max_days_to_expiration"))
    expiration_date = _option_expiration_date(position.symbol)
    if max_days_to_expiration is not None and expiration_date is not None:
        days_to_expiration = (expiration_date - today).days
        # <= is intentional: exit when N or fewer calendar days remain,
        # including the expiration day itself (days_to_expiration == 0).
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


def _exit_rule_diagnostics(
    position: PositionSnapshot,
    exit_config: dict[str, Any] | None,
    *,
    today: date,
    entry_time: datetime | None = None,
    peak_unrealized_pl_percent: Decimal | None = None,
) -> dict[str, Any]:
    pnl_percent = _unrealized_pl_percent(position)
    expiration_date = _option_expiration_date(position.symbol)
    days_to_expiration = (
        (expiration_date - today).days if expiration_date is not None else None
    )
    hours_held = None
    if isinstance(entry_time, datetime):
        entry_utc = (
            entry_time.astimezone(timezone.utc)
            if entry_time.tzinfo is not None
            else entry_time.replace(tzinfo=timezone.utc)
        )
        hours_held = (datetime.now(timezone.utc) - entry_utc).total_seconds() / 3600

    config = exit_config if isinstance(exit_config, dict) else {}
    thresholds = {
        "profit_target_percent": _string_or_none(config.get("profit_target_percent")),
        "stop_loss_percent": _string_or_none(config.get("stop_loss_percent")),
        "trailing_profit_activation_percent": _string_or_none(
            config.get("trailing_profit_activation_percent")
        ),
        "trailing_profit_giveback_percent": _string_or_none(
            config.get("trailing_profit_giveback_percent")
        ),
        "max_days_to_expiration": _string_or_none(config.get("max_days_to_expiration")),
        "max_hold_hours": _string_or_none(config.get("max_hold_hours")),
        "max_spread": _string_or_none(config.get("max_spread")),
        "limit_price_source": _string_or_none(config.get("limit_price_source")),
    }
    return {
        "unrealized_pl_percent": str(pnl_percent) if pnl_percent is not None else None,
        "peak_unrealized_pl_percent": str(peak_unrealized_pl_percent)
        if peak_unrealized_pl_percent is not None
        else None,
        "expiration_date": expiration_date.isoformat()
        if expiration_date is not None
        else None,
        "days_to_expiration": days_to_expiration,
        "entry_time": entry_time.isoformat() if isinstance(entry_time, datetime) else None,
        "hours_held": round(hours_held, 4) if hours_held is not None else None,
        "thresholds": thresholds,
    }

def _default_unmanaged_exit_config() -> dict[str, Any]:
    return {
        "order_type": "limit",
        "limit_price_source": "bid",
        "time_in_force": "day",
        "data_feed": "indicative",
    }

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
    peak_unrealized_pl_percent = _peak_unrealized_pl_percent(
        db,
        position,
        entry_time=entry_time,
    )
    trigger_reason = _exit_trigger_reason(
        position,
        exit_config,
        today=datetime.now(ZoneInfo("America/New_York")).date(),
        entry_time=entry_time,
        peak_unrealized_pl_percent=peak_unrealized_pl_percent,
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

def _peak_unrealized_pl_percent(
    db: Session,
    position: PositionSnapshot,
    *,
    entry_time: datetime | None,
) -> Decimal | None:
    statement = select(PositionSnapshot).where(PositionSnapshot.symbol == position.symbol)
    if isinstance(entry_time, datetime):
        entry_utc = (
            entry_time.astimezone(timezone.utc)
            if entry_time.tzinfo is not None
            else entry_time.replace(tzinfo=timezone.utc)
        )
        statement = statement.where(PositionSnapshot.captured_at >= entry_utc)
    snapshots = [
        snapshot
        for snapshot in db.scalars(statement)
        if isinstance(snapshot, PositionSnapshot)
    ]
    values = [
        value
        for value in (
            _unrealized_pl_percent(snapshot)
            for snapshot in [position, *snapshots]
        )
        if value is not None
    ]
    return max(values) if values else None

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


def _string_or_none(value: object) -> str | None:
    return str(value) if value is not None else None
