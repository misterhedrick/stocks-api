from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def build_preview_first_strategy_payloads(
    *,
    prices: dict[str, Decimal],
) -> list[dict[str, Any]]:
    """Build JSON-safe paper strategy payloads from current underlying prices."""
    return [
        _price_threshold_payload(
            name="Paper SPY upside call preview",
            description=(
                "Preview-first SPY call strategy that watches for a small "
                "upside price break. Auto-submit stays disabled."
            ),
            symbol="SPY",
            direction="bullish",
            option_type="call",
            threshold_key="price_above",
            threshold=_percent_from_price(prices["SPY"], Decimal("1.005")),
            target_strike=_whole_dollar(prices["SPY"]),
            signal_type="price_breakout",
            rationale="SPY quote midpoint crossed the upside paper threshold",
        ),
        _price_threshold_payload(
            name="Paper QQQ downside put preview",
            description=(
                "Preview-first QQQ put strategy that watches for a small "
                "downside price break. Auto-submit stays disabled."
            ),
            symbol="QQQ",
            direction="bearish",
            option_type="put",
            threshold_key="price_below",
            threshold=_percent_from_price(prices["QQQ"], Decimal("0.995")),
            target_strike=_whole_dollar(prices["QQQ"]),
            signal_type="price_breakdown",
            rationale="QQQ quote midpoint crossed the downside paper threshold",
        ),
        _percent_change_payload(
            name="Paper SPY momentum call preview",
            description=(
                "Preview-first SPY call strategy that watches short-term "
                "positive momentum. Auto-submit stays disabled."
            ),
            symbol="SPY",
            target_strike=_whole_dollar(prices["SPY"]),
        ),
        build_moving_average_strategy_payload(
            symbol="SPY",
            target_strike=_whole_dollar(prices["SPY"]),
        ),
    ]


def required_template_symbols() -> list[str]:
    return ["SPY", "QQQ"]


def build_moving_average_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    trigger: str = "bullish_trend",
    short_window: int = 5,
    long_window: int = 20,
    lookback_minutes: int = 60,
    timeframe: str = "1Min",
    confidence: str = "0.6200",
) -> dict[str, Any]:
    clean_symbol = symbol.strip().upper()
    direction = "bearish" if trigger.startswith("bearish") else "bullish"
    if name is None:
        name = f"Paper {clean_symbol} moving average {option_type} preview"

    return {
        "name": name,
        "description": (
            f"Preview-first {clean_symbol} {option_type} strategy that watches "
            "a short/long moving-average setup. Auto-submit stays disabled."
        ),
        "is_active": True,
        "config": {
            "scanner": {
                "type": "moving_average",
                "symbols": [clean_symbol],
                "short_window": short_window,
                "long_window": long_window,
                "lookback_minutes": lookback_minutes,
                "timeframe": timeframe,
                "trigger": trigger,
                "signal_type": "moving_average_setup",
                "direction": direction,
                "confidence": confidence,
                "rationale": (
                    f"{clean_symbol} moving average scanner triggered "
                    f"{trigger}"
                ),
                "data_feed": "iex",
                "dedupe_minutes": 240,
                "preview": _preview_config(
                    symbol=clean_symbol,
                    option_type=option_type,
                    target_strike=target_strike,
                    rationale=f"{name}: preview only; does not submit.",
                ),
                "exit": _exit_config(),
                "submit": _disabled_submit_config(),
            }
        },
    }


def _price_threshold_payload(
    *,
    name: str,
    description: str,
    symbol: str,
    direction: str,
    option_type: str,
    threshold_key: str,
    threshold: Decimal,
    target_strike: Decimal,
    signal_type: str,
    rationale: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "is_active": True,
        "config": {
            "scanner": {
                "type": "price_threshold",
                "symbols": [symbol],
                "signal_type": signal_type,
                "direction": direction,
                threshold_key: _decimal_string(threshold),
                "confidence": "0.6500",
                "rationale": rationale,
                "data_feed": "iex",
                "dedupe_minutes": 240,
                "preview": _preview_config(
                    symbol=symbol,
                    option_type=option_type,
                    target_strike=target_strike,
                    rationale=f"{name}: preview only; does not submit.",
                ),
                "exit": _exit_config(),
                "submit": _disabled_submit_config(),
            }
        },
    }


def _percent_change_payload(
    *,
    name: str,
    description: str,
    symbol: str,
    target_strike: Decimal,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "is_active": True,
        "config": {
            "scanner": {
                "type": "percent_change",
                "symbols": [symbol],
                "lookback_minutes": 30,
                "timeframe": "1Min",
                "change_above_percent": "0.35",
                "signal_type": "momentum_breakout",
                "direction": "bullish",
                "confidence": "0.6000",
                "rationale": "SPY rose at least 0.35% over the lookback window",
                "data_feed": "iex",
                "dedupe_minutes": 240,
                "preview": _preview_config(
                    symbol=symbol,
                    option_type="call",
                    target_strike=target_strike,
                    rationale=f"{name}: preview only; does not submit.",
                ),
                "exit": _exit_config(),
                "submit": _disabled_submit_config(),
            }
        },
    }


def _preview_config(
    *,
    symbol: str,
    option_type: str,
    target_strike: Decimal,
    rationale: str,
) -> dict[str, Any]:
    return {
        "enabled": True,
        "underlying_symbol": symbol,
        "option_type": option_type,
        "target_strike": _decimal_string(target_strike),
        "side": "buy",
        "quantity": 1,
        "order_type": "limit",
        "time_in_force": "day",
        "data_feed": "indicative",
        "max_estimated_notional": "250.00",
        "max_spread": "0.25",
        "limit": 100,
        "rationale": rationale,
    }


def _disabled_submit_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "max_orders_per_cycle": 1,
        "max_contracts_per_order": 1,
        "max_contracts_per_cycle": 1,
        "max_notional_per_order": "250.00",
        "max_open_contracts_per_symbol": 1,
        "max_open_contracts_per_strategy": 2,
        "max_orders_per_trading_day": 1,
        "trading_day_timezone": "America/New_York",
        "trade_windows": [
            {
                "timezone": "America/New_York",
                "start": "09:45",
                "end": "15:45",
            }
        ],
        "allowed_sides": ["buy"],
    }


def _exit_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "profit_target_percent": "30",
        "stop_loss_percent": "20",
        "max_days_to_expiration": 1,
        "max_contracts_per_exit": 1,
        "order_type": "limit",
        "limit_price_source": "bid",
        "time_in_force": "day",
        "data_feed": "indicative",
        "max_spread": "0.30",
        "submit": {
            "enabled": True,
            "max_orders_per_cycle": 1,
            "max_contracts_per_order": 1,
            "max_contracts_per_cycle": 1,
            "max_notional_per_order": "100000.00",
            "max_orders_per_trading_day": 3,
            "trading_day_timezone": "America/New_York",
            "trade_windows": [
                {
                    "timezone": "America/New_York",
                    "start": "09:45",
                    "end": "15:45",
                }
            ],
            "allowed_sides": ["sell"],
        },
    }


def _percent_from_price(price: Decimal, multiplier: Decimal) -> Decimal:
    return (price * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _whole_dollar(price: Decimal) -> Decimal:
    return price.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _decimal_string(value: Decimal) -> str:
    return str(value)
