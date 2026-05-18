from __future__ import annotations

from decimal import Decimal

from typing import Any

from app.core.config import settings
from app.services.strategy_template_common import (
    _decimal_string,
    _exit_config,
    _market_regime_config,
    _preview_config,
    _submit_config,
)

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
    change_above_percent: str = "0.25",
    change_below_percent: str = "-0.25",
    short_average_type: str = "ema",
    short_average_window: int = 9,
    max_extension_percent: str | None = None,
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
                "max_extension_percent": max_extension_percent
                or _decimal_string(settings.paper_strategy_momentum_max_extension_percent),
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
                "require_histogram_confirmation": True,
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
