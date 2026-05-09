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
    """Build JSON-safe evaluator-backed paper strategy payloads from current prices."""
    return [
        build_moving_average_strategy_payload(
            symbol="SPY",
            target_strike=_whole_dollar(prices["SPY"]),
        ),
        build_momentum_rate_of_change_strategy_payload(
            symbol="SPY",
            target_strike=_whole_dollar(prices["SPY"]),
        ),
        build_breakout_price_threshold_strategy_payload(
            symbol="SPY",
            target_strike=_whole_dollar(prices["SPY"]),
        ),
        build_rsi_reversal_strategy_payload(
            symbol="QQQ",
            target_strike=_whole_dollar(prices["QQQ"]),
        ),
        build_macd_crossover_strategy_payload(
            symbol="QQQ",
            target_strike=_whole_dollar(prices["QQQ"]),
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


def build_momentum_rate_of_change_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    direction: str = "bullish",
    timeframe: str = "1Min",
    lookback_minutes: int = 30,
    change_above_percent: str = "0.175",
    change_below_percent: str = "-0.175",
    short_average_type: str = "ema",
    short_average_window: int = 9,
    confidence: str = "0.6500",
    dedupe_minutes: int = 60,
) -> dict[str, Any]:
    clean_symbol = symbol.strip().upper()
    if name is None:
        name = f"Paper {clean_symbol} momentum rate-of-change {option_type} preview"

    return {
        "name": name,
        "description": (
            f"{clean_symbol} {option_type} strategy that watches "
            "short-term price momentum and candle confirmation."
        ),
        "is_active": True,
        "config": {
            "scanner": {
                "type": "momentum_rate_of_change",
                "symbols": [clean_symbol],
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "change_above_percent": change_above_percent,
                "change_below_percent": change_below_percent,
                "short_average_type": short_average_type,
                "short_average_window": short_average_window,
                "require_latest_candle_confirmation": True,
                "direction": direction,
                "confidence": confidence,
                "rationale": (
                    f"{clean_symbol} momentum rate-of-change scanner triggered {direction}"
                ),
                "data_feed": "iex",
                "dedupe_minutes": dedupe_minutes,
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


def build_rsi_reversal_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    direction: str = "bullish",
    timeframe: str = "5Min",
    lookback_minutes: int = 480,
    rsi_period: int = 14,
    oversold_level: str = "35",
    overbought_level: str = "65",
    confirmation_mode: str = "cross_back_inside",
    confidence: str = "0.6000",
    dedupe_minutes: int = 60,
) -> dict[str, Any]:
    clean_symbol = symbol.strip().upper()
    if name is None:
        name = f"Paper {clean_symbol} RSI reversal {option_type} preview"

    return {
        "name": name,
        "description": (
            f"{clean_symbol} {option_type} strategy that watches for RSI "
            "oversold/overbought reversals and auto-submits orders."
        ),
        "is_active": True,
        "config": {
            "scanner": {
                "type": "rsi_reversal",
                "symbols": [clean_symbol],
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "rsi_period": rsi_period,
                "oversold_level": oversold_level,
                "overbought_level": overbought_level,
                "confirmation_mode": confirmation_mode,
                "require_price_confirmation": True,
                "direction": direction,
                "confidence": confidence,
                "rationale": (
                    f"{clean_symbol} RSI reversal scanner triggered {direction}"
                ),
                "data_feed": "iex",
                "dedupe_minutes": dedupe_minutes,
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


def build_macd_crossover_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    direction: str = "bullish",
    timeframe: str = "5Min",
    lookback_minutes: int = 720,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    confidence: str = "0.6200",
    dedupe_minutes: int = 60,
) -> dict[str, Any]:
    clean_symbol = symbol.strip().upper()
    if name is None:
        name = f"Paper {clean_symbol} MACD crossover {option_type} preview"

    return {
        "name": name,
        "description": (
            f"{clean_symbol} {option_type} strategy that watches for MACD "
            "signal-line crossovers with price confirmation and auto-submits orders."
        ),
        "is_active": True,
        "config": {
            "scanner": {
                "type": "macd_crossover",
                "symbols": [clean_symbol],
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "fast_period": fast_period,
                "slow_period": slow_period,
                "signal_period": signal_period,
                "require_histogram_confirmation": False,
                "require_price_confirmation": True,
                "direction": direction,
                "confidence": confidence,
                "rationale": (
                    f"{clean_symbol} MACD crossover scanner triggered {direction}"
                ),
                "data_feed": "iex",
                "dedupe_minutes": dedupe_minutes,
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


def build_mean_reversion_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    direction: str = "bullish",
    timeframe: str = "5Min",
    lookback_minutes: int = 720,
    bollinger_period: int = 20,
    bollinger_stddev: str = "2.0",
    confidence: str = "0.6200",
    max_distance_to_middle_percent: str = "2.0",
    dedupe_minutes: int = 60,
) -> dict[str, Any]:
    clean_symbol = symbol.strip().upper()
    if name is None:
        name = f"Paper {clean_symbol} mean reversion {option_type} preview"

    return {
        "name": name,
        "description": (
            f"{clean_symbol} {option_type} strategy that watches for Bollinger Band "
            "mean-reversion setups and auto-submits orders."
        ),
        "is_active": True,
        "config": {
            "scanner": {
                "type": "mean_reversion",
                "symbols": [clean_symbol],
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "bollinger_period": bollinger_period,
                "bollinger_stddev": bollinger_stddev,
                "max_distance_to_middle_percent": max_distance_to_middle_percent,
                "require_price_confirmation": True,
                "direction": direction,
                "confidence": confidence,
                "rationale": (
                    f"{clean_symbol} mean reversion scanner triggered {direction}"
                ),
                "data_feed": "iex",
                "dedupe_minutes": dedupe_minutes,
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


def build_breakout_price_threshold_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    direction: str = "bullish",
    timeframe: str = "5Min",
    lookback_minutes: int = 480,
    range_lookback_candles: int = 20,
    breakout_buffer_percent: str = "0.05",
    max_breakout_distance_percent: str = "3.0",
    confidence: str = "0.6200",
    dedupe_minutes: int = 60,
) -> dict[str, Any]:
    clean_symbol = symbol.strip().upper()
    if name is None:
        name = f"Paper {clean_symbol} breakout price threshold {option_type} preview"

    return {
        "name": name,
        "description": (
            f"{clean_symbol} {option_type} strategy that watches for price breakouts "
            "above or below recent range extremes and auto-submits orders."
        ),
        "is_active": True,
        "config": {
            "scanner": {
                "type": "breakout_price_threshold",
                "symbols": [clean_symbol],
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "range_lookback_candles": range_lookback_candles,
                "breakout_buffer_percent": breakout_buffer_percent,
                "max_breakout_distance_percent": max_breakout_distance_percent,
                "require_price_confirmation": True,
                "direction": direction,
                "confidence": confidence,
                "rationale": (
                    f"{clean_symbol} breakout price threshold scanner triggered {direction}"
                ),
                "data_feed": "iex",
                "dedupe_minutes": dedupe_minutes,
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


def build_volume_confirmed_breakout_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    direction: str = "bullish",
    timeframe: str = "5Min",
    lookback_minutes: int = 480,
    range_lookback_candles: int = 20,
    volume_lookback_candles: int = 20,
    min_relative_volume: str = "1.25",
    breakout_buffer_percent: str = "0.05",
    max_breakout_distance_percent: str = "3.0",
    confidence: str = "0.6500",
    dedupe_minutes: int = 60,
) -> dict[str, Any]:
    clean_symbol = symbol.strip().upper()
    if name is None:
        name = f"Paper {clean_symbol} volume confirmed breakout {option_type} preview"

    return {
        "name": name,
        "description": (
            f"{clean_symbol} {option_type} strategy that watches for price breakouts "
            "confirmed by elevated volume and auto-submits orders."
        ),
        "is_active": True,
        "config": {
            "scanner": {
                "type": "volume_confirmed_breakout",
                "symbols": [clean_symbol],
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "range_lookback_candles": range_lookback_candles,
                "volume_lookback_candles": volume_lookback_candles,
                "min_relative_volume": min_relative_volume,
                "breakout_buffer_percent": breakout_buffer_percent,
                "max_breakout_distance_percent": max_breakout_distance_percent,
                "require_candle_confirmation": True,
                "direction": direction,
                "confidence": confidence,
                "rationale": (
                    f"{clean_symbol} volume confirmed breakout scanner triggered {direction}"
                ),
                "data_feed": "iex",
                "dedupe_minutes": dedupe_minutes,
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


def build_volatility_squeeze_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    direction: str = "bullish",
    timeframe: str = "5Min",
    lookback_minutes: int = 720,
    bollinger_period: int = 20,
    bollinger_stddev: str = "2.0",
    squeeze_lookback_candles: int = 20,
    range_lookback_candles: int = 20,
    compression_ratio_threshold: str = "0.90",
    breakout_buffer_percent: str = "0.05",
    max_breakout_distance_percent: str = "4.0",
    confidence: str = "0.6500",
    dedupe_minutes: int = 60,
) -> dict[str, Any]:
    clean_symbol = symbol.strip().upper()
    if name is None:
        name = f"Paper {clean_symbol} volatility squeeze {option_type} preview"

    return {
        "name": name,
        "description": (
            f"{clean_symbol} {option_type} strategy that watches for Bollinger Band "
            "squeeze compression followed by a breakout and auto-submits orders."
        ),
        "is_active": True,
        "config": {
            "scanner": {
                "type": "volatility_squeeze",
                "symbols": [clean_symbol],
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "bollinger_period": bollinger_period,
                "bollinger_stddev": bollinger_stddev,
                "squeeze_lookback_candles": squeeze_lookback_candles,
                "range_lookback_candles": range_lookback_candles,
                "compression_ratio_threshold": compression_ratio_threshold,
                "breakout_buffer_percent": breakout_buffer_percent,
                "max_breakout_distance_percent": max_breakout_distance_percent,
                "require_price_confirmation": True,
                "direction": direction,
                "confidence": confidence,
                "rationale": (
                    f"{clean_symbol} volatility squeeze scanner triggered {direction}"
                ),
                "data_feed": "iex",
                "dedupe_minutes": dedupe_minutes,
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


def build_support_resistance_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    direction: str = "bullish",
    timeframe: str = "5Min",
    lookback_minutes: int = 720,
    mode: str = "both",
    swing_window: int = 3,
    lookback_candles: int = 60,
    min_touches: int = 2,
    level_tolerance_percent: str = "0.20",
    breakout_buffer_percent: str = "0.075",
    max_distance_percent: str = "1.0",
    confidence: str = "0.6000",
    dedupe_minutes: int = 60,
) -> dict[str, Any]:
    clean_symbol = symbol.strip().upper()
    if name is None:
        name = f"Paper {clean_symbol} support resistance {option_type} preview"

    return {
        "name": name,
        "description": (
            f"{clean_symbol} {option_type} strategy that detects swing-based "
            "support and resistance levels and auto-submits orders on bounces or breakouts."
        ),
        "is_active": True,
        "config": {
            "scanner": {
                "type": "support_resistance",
                "symbols": [clean_symbol],
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "mode": mode,
                "swing_window": swing_window,
                "lookback_candles": lookback_candles,
                "min_touches": min_touches,
                "level_tolerance_percent": level_tolerance_percent,
                "breakout_buffer_percent": breakout_buffer_percent,
                "max_distance_percent": max_distance_percent,
                "require_candle_confirmation": True,
                "direction": direction,
                "confidence": confidence,
                "rationale": (
                    f"{clean_symbol} support/resistance scanner triggered {direction}"
                ),
                "data_feed": "iex",
                "dedupe_minutes": dedupe_minutes,
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


def _preview_config(
    *,
    symbol: str,
    option_type: str,
    target_strike: Decimal,
    rationale: str,
    max_estimated_notional: str | None = None,
    max_spread: str | None = None,
    max_spread_percent: str | None = None,
    min_open_interest: int | None = None,
    min_quote_size: int = 1,
    min_days_to_expiration: int = 2,
    max_days_to_expiration: int = 30,
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
        "max_estimated_notional": max_estimated_notional
        or _decimal_string(settings.paper_strategy_max_estimated_notional),
        "max_spread": max_spread or _decimal_string(settings.paper_strategy_max_spread),
        "max_spread_percent": max_spread_percent
        or _decimal_string(settings.paper_strategy_max_spread_percent),
        "min_open_interest": min_open_interest
        if min_open_interest is not None
        else settings.paper_strategy_min_open_interest,
        "min_quote_size": min_quote_size,
        "limit": 20,
        "rationale": rationale,
    }


def _submit_config(*, max_notional_per_order: str | None = None) -> dict[str, Any]:
    return {
        "enabled": True,
        "max_orders_per_cycle": 1,
        "max_contracts_per_order": 1,
        "max_contracts_per_cycle": 1,
        "max_notional_per_order": max_notional_per_order
        or _decimal_string(settings.paper_strategy_max_estimated_notional),
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


def _market_regime_config(direction: str) -> dict[str, Any]:
    return {
        "enabled": True,
        "symbols": ["SPY", "QQQ"],
        "bullish_min_change_percent": "0.025",
        "bearish_max_change_percent": "-0.025",
        "direction": direction,
    }


def _whole_dollar(price: Decimal) -> Decimal:
    return price.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _decimal_string(value: Decimal) -> str:
    return str(value)
