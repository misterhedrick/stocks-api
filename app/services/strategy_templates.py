from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.core.config import settings


LIQUID_OPTIONS_UNIVERSE = (
    "SPY",
    "QQQ",
    "NVDA",
    "TSLA",
    "IWM",
    "AMZN",
    "MSFT",
    "GOOGL",
    "META",
    "AAPL",
)


def build_preview_first_strategy_payloads(
    *,
    prices: dict[str, Decimal],
) -> list[dict[str, Any]]:
    """Build JSON-safe paper strategy payloads from current underlying prices."""
    return [
        _price_threshold_payload(
            name="Paper SPY upside call preview",
            description=(
                "SPY call strategy that watches for a small "
                "upside price break and auto-submits orders."
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
                "QQQ put strategy that watches for a small "
                "downside price break and auto-submits orders."
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
                "SPY call strategy that watches short-term "
                "positive momentum and auto-submits orders."
            ),
            symbol="SPY",
            target_strike=_whole_dollar(prices["SPY"]),
        ),
        build_moving_average_strategy_payload(
            symbol="SPY",
            target_strike=_whole_dollar(prices["SPY"]),
        ),
        build_trend_confirmation_strategy_payload(
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
    trigger: str = "bullish_cross",
    short_window: int = 5,
    long_window: int = 20,
    lookback_minutes: int = 1440,
    timeframe: str = "5Min",
    confidence: str = "0.6200",
    min_change_percent: str | None = None,
) -> dict[str, Any]:
    clean_symbol = symbol.strip().upper()
    direction = "bearish" if trigger.startswith("bearish") else "bullish"
    if name is None:
        name = f"Paper {clean_symbol} moving average {option_type} preview"

    return {
        "name": name,
        "description": (
            f"{clean_symbol} {option_type} strategy that watches "
            "a short/long moving-average setup and auto-submits orders."
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
                "min_change_percent": min_change_percent
                or _decimal_string(settings.paper_strategy_min_change_percent),
                "require_short_average_slope": True,
                "require_price_confirmation": True,
                "market_regime": _market_regime_config(direction),
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
                    rationale=f"{name}: auto-submit enabled.",
                ),
                "exit": _exit_config(),
                "submit": _submit_config(),
            }
        },
    }


def build_trend_confirmation_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    direction: str = "bullish",
    short_window: int = 8,
    long_window: int = 21,
    lookback_minutes: int = 1440,
    timeframe: str = "5Min",
    min_change_percent: str | None = None,
    confidence: str = "0.6800",
) -> dict[str, Any]:
    clean_symbol = symbol.strip().upper()
    if name is None:
        name = f"Paper {clean_symbol} confirmed trend {option_type} preview"

    return {
        "name": name,
        "description": (
            f"{clean_symbol} {option_type} strategy that requires "
            "moving-average alignment plus price momentum confirmation "
            "and auto-submits orders."
        ),
        "is_active": True,
        "config": {
            "scanner": {
                "type": "trend_confirmation",
                "symbols": [clean_symbol],
                "short_window": short_window,
                "long_window": long_window,
                "lookback_minutes": lookback_minutes,
                "timeframe": timeframe,
                "min_change_percent": min_change_percent
                or _decimal_string(settings.paper_strategy_trend_min_change_percent),
                "signal_type": "confirmed_trend",
                "direction": direction,
                "confidence": confidence,
                "require_short_average_slope": True,
                "require_price_above_short_average": direction == "bullish",
                "require_price_below_short_average": direction == "bearish",
                "market_regime": _market_regime_config(direction),
                "rationale": (
                    f"{clean_symbol} confirmed trend scanner found "
                    f"{direction} MA alignment and momentum"
                ),
                "data_feed": "iex",
                "dedupe_minutes": 360,
                "preview": _preview_config(
                    symbol=clean_symbol,
                    option_type=option_type,
                    target_strike=target_strike,
                    rationale=f"{name}: auto-submit enabled.",
                    max_estimated_notional="2500.00",
                    max_spread=None,
                ),
                "exit": _exit_config(
                    profit_target_percent=None,
                    stop_loss_percent=None,
                    max_spread="0.25",
                ),
                "submit": _submit_config(max_notional_per_order="200.00"),
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
                    rationale=f"{name}: auto-submit enabled.",
                ),
                "exit": _exit_config(),
                "submit": _submit_config(),
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
                    rationale=f"{name}: auto-submit enabled.",
                ),
                "exit": _exit_config(),
                "submit": _submit_config(),
            }
        },
    }


def _preview_config(
    *,
    symbol: str,
    option_type: str,
    target_strike: Decimal,
    rationale: str,
    max_estimated_notional: str = "2500.00",
    max_spread: str | None = None,
    max_spread_percent: str | None = None,
    min_open_interest: int = 100,
    min_quote_size: int = 1,
    min_days_to_expiration: int = 2,
    max_days_to_expiration: int = 7,
) -> dict[str, Any]:
    return {
        "enabled": True,
        "underlying_symbol": symbol,
        "option_type": option_type,
        "min_days_to_expiration": min_days_to_expiration,
        "max_days_to_expiration": max_days_to_expiration,
        "target_strike": _decimal_string(target_strike),
        "side": "buy",
        "quantity": 1,
        "order_type": "limit",
        "time_in_force": "day",
        "data_feed": "indicative",
        "max_estimated_notional": max_estimated_notional,
        "max_spread": max_spread or _decimal_string(settings.paper_strategy_max_spread),
        "max_spread_percent": max_spread_percent
        or _decimal_string(settings.paper_strategy_max_spread_percent),
        "min_open_interest": min_open_interest,
        "min_quote_size": min_quote_size,
        "limit": 20,
        "rationale": rationale,
    }


def _submit_config(*, max_notional_per_order: str = "2500.00") -> dict[str, Any]:
    return {
        "enabled": True,
        "max_orders_per_cycle": 1,
        "max_contracts_per_order": 1,
        "max_contracts_per_cycle": 1,
        "max_notional_per_order": max_notional_per_order,
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


def _exit_config(
    *,
    profit_target_percent: str | None = None,
    stop_loss_percent: str | None = None,
    max_spread: str = "0.25",
) -> dict[str, Any]:
    return {
        "enabled": True,
        "profit_target_percent": profit_target_percent
        or _decimal_string(settings.paper_strategy_profit_target_percent),
        "stop_loss_percent": stop_loss_percent
        or _decimal_string(settings.paper_strategy_stop_loss_percent),
        "max_days_to_expiration": 1,
        "max_contracts_per_exit": 1,
        "order_type": "limit",
        "limit_price_source": "bid",
        "time_in_force": "day",
        "data_feed": "indicative",
        "max_spread": max_spread,
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


def _market_regime_config(direction: str) -> dict[str, Any]:
    return {
        "enabled": True,
        "symbols": ["SPY", "QQQ"],
        "bullish_min_change_percent": "0.05",
        "bearish_max_change_percent": "-0.05",
        "direction": direction,
    }
