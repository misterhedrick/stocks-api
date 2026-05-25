from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import BrokerOrder, OrderIntent, Signal, Strategy
from app.schemas.options import OptionContractSelectionCreate
from app.schemas.order_intents import OrderIntentPreviewCreate
from app.services.market_cycle_runner_types import EXPOSURE_BROKER_ORDER_STATUSES


def _preview_payload_for_signal(
    signal: Signal,
    strategy: Strategy,
) -> OrderIntentPreviewCreate:
    preview_config = _preview_config_for_strategy(strategy)

    option_symbol = preview_config.get("option_symbol")
    contract_selection = None
    if option_symbol is None:
        contract_selection = _contract_selection_for_signal(signal, preview_config)

    return OrderIntentPreviewCreate(
        signal_id=signal.id,
        option_symbol=option_symbol if isinstance(option_symbol, str) else None,
        contract_selection=contract_selection,
        side=_string_config(preview_config, "side", default="buy"),
        quantity=_int_config(preview_config, "quantity", default=1),
        order_type=_string_config(preview_config, "order_type", default="limit"),
        limit_price=preview_config.get("limit_price"),
        time_in_force=_string_config(preview_config, "time_in_force", default="day"),
        rationale=preview_config.get("rationale")
        if isinstance(preview_config.get("rationale"), str)
        else signal.rationale,
        data_feed=_string_config(preview_config, "data_feed", default="indicative"),
        max_estimated_notional=preview_config.get("max_estimated_notional"),
        max_spread=preview_config.get("max_spread"),
        max_spread_percent=preview_config.get("max_spread_percent"),
    )


def _preview_config_for_strategy(strategy: Strategy) -> dict[str, Any]:
    scanner_config = strategy.config.get("scanner")
    if not isinstance(scanner_config, dict):
        raise ValueError("strategy scanner config is required for auto-preview")

    preview_config = scanner_config.get("preview")
    if not isinstance(preview_config, dict):
        raise ValueError("scanner.preview config is required")
    if preview_config.get("enabled") is not True:
        raise ValueError("scanner.preview.enabled must be true")
    return preview_config


def _submit_config_for_order_intent(
    strategy: Strategy,
    order_intent: OrderIntent,
) -> dict[str, Any]:
    preview = order_intent.preview if isinstance(order_intent.preview, dict) else {}
    if (
        order_intent.side.lower() == "sell"
        and preview.get("source") == "position_exit_evaluator"
    ):
        exit_config = _exit_config_for_strategy(strategy)
        submit_config = exit_config.get("submit")
        if not isinstance(submit_config, dict):
            raise ValueError("scanner.exit.submit config is required")
        if submit_config.get("enabled") is not True:
            raise ValueError("scanner.exit.submit.enabled must be true")
        return submit_config

    return _submit_config_for_strategy(strategy)


def _submit_config_for_strategy(strategy: Strategy) -> dict[str, Any]:
    scanner_config = strategy.config.get("scanner")
    if not isinstance(scanner_config, dict):
        raise ValueError("strategy scanner config is required for auto-submit")

    submit_config = scanner_config.get("submit")
    if not isinstance(submit_config, dict):
        raise ValueError("scanner.submit config is required")
    if submit_config.get("enabled") is not True:
        raise ValueError("scanner.submit.enabled must be true")
    return submit_config


def _exit_config_for_strategy(strategy: Strategy) -> dict[str, Any]:
    scanner_config = strategy.config.get("scanner")
    if not isinstance(scanner_config, dict):
        raise ValueError("strategy scanner config is required for exit submit")

    exit_config = scanner_config.get("exit")
    if not isinstance(exit_config, dict):
        raise ValueError("scanner.exit config is required")
    if exit_config.get("enabled") is not True:
        raise ValueError("scanner.exit.enabled must be true")
    return exit_config


def _validate_submit_limits(
    db: Session,
    order_intent: OrderIntent,
    strategy_id: uuid.UUID,
    submit_config: dict[str, Any],
    submitted_for_strategy: int,
    contracts_submitted_for_strategy: int,
    contracts_submitted_for_strategy_symbol: int,
    now: datetime,
) -> None:
    _validate_trade_windows(submit_config, now=now)

    allowed_sides = submit_config.get("allowed_sides", ["buy"])
    if not isinstance(allowed_sides, list) or not allowed_sides:
        raise ValueError("scanner.submit.allowed_sides must be a non-empty list")
    clean_allowed_sides = {
        value.strip().lower()
        for value in allowed_sides
        if isinstance(value, str) and value.strip()
    }
    if order_intent.side.lower() not in clean_allowed_sides:
        raise ValueError("order side is not allowed by scanner.submit.allowed_sides")


def _contract_selection_for_signal(
    signal: Signal,
    preview_config: dict[str, Any],
) -> OptionContractSelectionCreate:
    underlying_symbol = preview_config.get("underlying_symbol")
    if not isinstance(underlying_symbol, str) or not underlying_symbol.strip():
        underlying_symbol = signal.underlying_symbol or signal.symbol
    option_type = preview_config.get("option_type")
    if not isinstance(option_type, str) or not option_type.strip():
        option_type = _option_type_for_signal(signal)

    return OptionContractSelectionCreate(
        underlying_symbol=underlying_symbol,
        option_type=option_type,
        side=_string_config(preview_config, "side", default="buy"),
        expiration_date=preview_config.get("expiration_date"),
        expiration_date_gte=preview_config.get("expiration_date_gte"),
        expiration_date_lte=preview_config.get("expiration_date_lte"),
        min_days_to_expiration=preview_config.get("min_days_to_expiration"),
        max_days_to_expiration=preview_config.get("max_days_to_expiration"),
        target_strike=preview_config.get("target_strike"),
        underlying_price=preview_config.get("underlying_price"),
        max_estimated_notional=preview_config.get("max_estimated_notional"),
        max_spread=preview_config.get("max_spread"),
        max_spread_percent=preview_config.get("max_spread_percent"),
        min_open_interest=preview_config.get("min_open_interest"),
        min_quote_size=preview_config.get("min_quote_size"),
        preview_profile=_preview_profile(preview_config),
        data_feed=_string_config(preview_config, "data_feed", default="indicative"),
        limit=_options_candidate_limit(),
    )


def _option_type_for_signal(signal: Signal) -> str:
    direction = signal.direction.strip().lower()
    if direction == "bullish":
        return "call"
    if direction == "bearish":
        return "put"
    raise ValueError("signal.direction must be bullish or bearish when scanner.preview.option_type is omitted")


def _preview_profile(preview_config: dict[str, Any]) -> str | None:
    profile = preview_config.get("preview_profile") or preview_config.get("profile")
    if isinstance(profile, str) and profile.strip():
        return profile.strip()
    return None


def _options_candidate_limit() -> int:
    try:
        return max(int(settings.options_max_candidates), 1)
    except (TypeError, ValueError):
        return 100


def _string_config(
    config: dict[str, Any],
    key: str,
    *,
    default: str | None = None,
    label_prefix: str = "scanner.preview",
) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label_prefix}.{key} must be a non-empty string")
    return value.strip()


def _int_config(
    config: dict[str, Any],
    key: str,
    *,
    default: int,
    label_prefix: str = "scanner.preview",
) -> int:
    value = config.get(key, default)
    try:
        int_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label_prefix}.{key} must be an integer") from exc
    if int_value <= 0:
        raise ValueError(f"{label_prefix}.{key} must be greater than 0")
    return int_value


def _validate_trade_windows(
    submit_config: dict[str, Any],
    *,
    now: datetime,
) -> None:
    windows = submit_config.get("trade_windows")
    if windows is None:
        return
    if not isinstance(windows, list) or not windows:
        raise ValueError("scanner.submit.trade_windows must be a non-empty list")

    window_errors: list[str] = []
    for window in windows:
        try:
            if _is_inside_trade_window(window, now=now):
                return
        except ValueError as exc:
            window_errors.append(str(exc))

    if window_errors:
        raise ValueError(window_errors[0])
    raise ValueError("current time is outside scanner.submit.trade_windows")


def _is_inside_trade_window(window: object, *, now: datetime) -> bool:
    if not isinstance(window, dict):
        raise ValueError("scanner.submit.trade_windows entries must be objects")

    timezone_name = _string_config(
        window,
        "timezone",
        default="America/New_York",
        label_prefix="scanner.submit.trade_windows",
    )
    try:
        window_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            "scanner.submit.trade_windows.timezone must be a valid timezone"
        ) from exc

    start = _time_config(window, "start")
    end = _time_config(window, "end")
    current_time = (
        now.astimezone(window_timezone).time().replace(second=0, microsecond=0)
    )

    if start <= end:
        return start <= current_time <= end
    return current_time >= start or current_time <= end


def _time_config(config: dict[str, Any], key: str) -> time:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"scanner.submit.trade_windows.{key} must be HH:MM")
    try:
        parsed_time = time.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"scanner.submit.trade_windows.{key} must be HH:MM") from exc
    return parsed_time.replace(second=0, microsecond=0)


def _optional_int_config(
    config: dict[str, Any],
    key: str,
    *,
    label_prefix: str,
) -> int | None:
    if config.get(key) is None:
        return None
    return _int_config(config, key, default=1, label_prefix=label_prefix)


def _optional_decimal_config(
    config: dict[str, Any],
    key: str,
    *,
    label_prefix: str,
) -> Decimal | None:
    value = config.get(key)
    if value is None:
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label_prefix}.{key} must be a decimal") from exc
    if decimal_value <= Decimal("0"):
        raise ValueError(f"{label_prefix}.{key} must be greater than 0")
    return decimal_value


def _order_intent_notional(order_intent: OrderIntent) -> Decimal | None:
    if order_intent.limit_price is not None:
        return order_intent.limit_price * Decimal(order_intent.quantity) * Decimal("100")

    preview = order_intent.preview if isinstance(order_intent.preview, dict) else {}
    quote_preview = preview.get("quote")
    if isinstance(quote_preview, dict):
        notional = _decimal_from_preview(quote_preview.get("estimated_notional"))
        if notional is not None:
            return notional

    selection_preview = preview.get("selection")
    if isinstance(selection_preview, dict):
        selection_quote = selection_preview.get("quote")
        if isinstance(selection_quote, dict):
            return _decimal_from_preview(selection_quote.get("estimated_notional"))

    return None


def _decimal_from_preview(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _existing_contract_exposure(
    db: Session,
    *,
    strategy_id: uuid.UUID,
    option_symbol: str | None = None,
) -> Decimal:
    signed_quantity = case(
        (func.lower(BrokerOrder.side) == "sell", -BrokerOrder.quantity),
        else_=BrokerOrder.quantity,
    )
    statement = (
        select(func.coalesce(func.sum(signed_quantity), 0))
        .select_from(BrokerOrder)
        .join(OrderIntent, BrokerOrder.order_intent_id == OrderIntent.id)
        .where(OrderIntent.strategy_id == strategy_id)
        .where(BrokerOrder.status.in_(EXPOSURE_BROKER_ORDER_STATUSES))
    )
    if option_symbol is not None:
        statement = statement.where(BrokerOrder.symbol == option_symbol)

    value = db.scalar(statement)
    if value is None:
        return Decimal("0")
    try:
        exposure = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    return max(exposure, Decimal("0"))


def _submitted_orders_for_trading_day(
    db: Session,
    *,
    strategy_id: uuid.UUID,
    submit_config: dict[str, Any],
    now: datetime,
) -> int:
    day_timezone = _trading_day_timezone(submit_config)
    current_local = now.astimezone(day_timezone)
    day_start_local = datetime.combine(
        current_local.date(),
        time.min,
        tzinfo=day_timezone,
    )
    day_end_local = datetime.combine(
        current_local.date(),
        time.max,
        tzinfo=day_timezone,
    )
    statement = (
        select(func.count(BrokerOrder.id))
        .select_from(BrokerOrder)
        .join(OrderIntent, BrokerOrder.order_intent_id == OrderIntent.id)
        .where(OrderIntent.strategy_id == strategy_id)
        .where(BrokerOrder.submitted_at.is_not(None))
        .where(BrokerOrder.submitted_at >= day_start_local.astimezone(timezone.utc))
        .where(BrokerOrder.submitted_at <= day_end_local.astimezone(timezone.utc))
    )
    value = db.scalar(statement)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _trading_day_timezone(submit_config: dict[str, Any]) -> ZoneInfo:
    timezone_name = submit_config.get("trading_day_timezone", "America/New_York")
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ValueError("scanner.submit.trading_day_timezone must be a non-empty string")
    try:
        return ZoneInfo(timezone_name.strip())
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            "scanner.submit.trading_day_timezone must be a valid timezone"
        ) from exc
