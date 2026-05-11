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
