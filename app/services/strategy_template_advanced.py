from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.strategy_template_common import (
    _exit_config,
    _preview_config,
    _submit_config,
)


def build_vwap_reclaim_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    timeframe: str = "5Min",
    lookback_minutes: int = 390,
    min_reclaim_percent: str = "0.08",
    max_distance_percent: str = "0.75",
    confidence: str = "0.6800",
    dedupe_minutes: int = 180,
) -> dict[str, Any]:
    return _advanced_payload(
        symbol=symbol,
        target_strike=target_strike,
        name=name or f"{symbol.strip().upper()} VWAP reclaim {option_type} preview",
        option_type=option_type,
        scanner={
            "type": "vwap_reclaim",
            "timeframe": timeframe,
            "lookback_minutes": lookback_minutes,
            "min_reclaim_percent": min_reclaim_percent,
            "max_distance_percent": max_distance_percent,
            "confidence": confidence,
            "dedupe_minutes": dedupe_minutes,
        },
        description="VWAP reclaim/rejection strategy that watches intraday price relative to volume-weighted average price.",
    )


def build_opening_range_breakout_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    timeframe: str = "5Min",
    lookback_minutes: int = 390,
    range_candles: int = 3,
    breakout_buffer_percent: str = "0.10",
    max_breakout_distance_percent: str = "1.5",
    confidence: str = "0.7000",
    dedupe_minutes: int = 360,
) -> dict[str, Any]:
    return _advanced_payload(
        symbol=symbol,
        target_strike=target_strike,
        name=name or f"{symbol.strip().upper()} opening range breakout {option_type} preview",
        option_type=option_type,
        scanner={
            "type": "opening_range_breakout",
            "timeframe": timeframe,
            "lookback_minutes": lookback_minutes,
            "range_candles": range_candles,
            "breakout_buffer_percent": breakout_buffer_percent,
            "max_breakout_distance_percent": max_breakout_distance_percent,
            "confidence": confidence,
            "dedupe_minutes": dedupe_minutes,
        },
        description="Opening-range breakout strategy keyed off the first configured candles of the session.",
    )


def build_relative_strength_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    timeframe: str = "5Min",
    lookback_minutes: int = 240,
    min_edge_percent: str = "0.60",
    confidence: str = "0.6800",
    dedupe_minutes: int = 360,
) -> dict[str, Any]:
    return _advanced_payload(
        symbol=symbol,
        target_strike=target_strike,
        name=name or f"{symbol.strip().upper()} relative strength {option_type} preview",
        option_type=option_type,
        scanner={
            "type": "relative_strength",
            "timeframe": timeframe,
            "lookback_minutes": lookback_minutes,
            "min_edge_percent": min_edge_percent,
            "confidence": confidence,
            "dedupe_minutes": dedupe_minutes,
        },
        description="Cross-sectional relative-strength strategy comparing each symbol against the active trading universe.",
    )


def build_time_series_momentum_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    timeframe: str = "15Min",
    lookback_minutes: int = 1440,
    lookback_bars: int = 26,
    min_trend_percent: str = "1.5",
    trend_average_window: int = 20,
    confidence: str = "0.7000",
    dedupe_minutes: int = 480,
) -> dict[str, Any]:
    return _advanced_payload(
        symbol=symbol,
        target_strike=target_strike,
        name=name or f"{symbol.strip().upper()} time-series momentum {option_type} preview",
        option_type=option_type,
        scanner={
            "type": "time_series_momentum",
            "timeframe": timeframe,
            "lookback_minutes": lookback_minutes,
            "lookback_bars": lookback_bars,
            "min_trend_percent": min_trend_percent,
            "trend_average_window": trend_average_window,
            "confidence": confidence,
            "dedupe_minutes": dedupe_minutes,
        },
        description="Longer-horizon time-series momentum strategy for persistent trend states.",
    )


def build_market_regime_filter_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    timeframe: str = "5Min",
    lookback_minutes: int = 240,
    confidence: str = "0.6600",
    dedupe_minutes: int = 480,
) -> dict[str, Any]:
    return _mark_signal_only_payload(
        _advanced_payload(
            symbol=symbol,
            target_strike=target_strike,
            name=name
            or f"{symbol.strip().upper()} market regime filter {option_type} signal",
            option_type=option_type,
            scanner={
                "type": "market_regime_filter",
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "benchmark_symbols": ["SPY", "QQQ"],
                "min_benchmark_percent": "0.35",
                "min_symbol_alignment_percent": "0.15",
                "confidence": confidence,
                "dedupe_minutes": dedupe_minutes,
            },
            description="Market-regime alignment strategy using broad-market direction as a scanner-level gate.",
        )
    )


def build_pairs_relative_value_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    timeframe: str = "5Min",
    lookback_minutes: int = 240,
    confidence: str = "0.6700",
    dedupe_minutes: int = 480,
) -> dict[str, Any]:
    return _mark_signal_only_payload(
        _advanced_payload(
            symbol=symbol,
            target_strike=target_strike,
            name=name
            or f"{symbol.strip().upper()} pairs relative value {option_type} signal",
            option_type=option_type,
            scanner={
                "type": "pairs_relative_value",
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "benchmark_symbol": "SPY",
                "pair_benchmarks": {"SPY": "QQQ"},
                "min_spread_percent": "0.75",
                "mode": "mean_reversion",
                "confidence": confidence,
                "dedupe_minutes": dedupe_minutes,
            },
            description="Pairs/relative-value strategy comparing each symbol against a benchmark peer while paired execution is unavailable.",
        )
    )


def build_options_spread_candidate_strategy_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str | None = None,
    option_type: str = "call",
    timeframe: str = "5Min",
    lookback_minutes: int = 240,
    confidence: str = "0.6600",
    dedupe_minutes: int = 480,
) -> dict[str, Any]:
    return _mark_signal_only_payload(
        _advanced_payload(
            symbol=symbol,
            target_strike=target_strike,
            name=name
            or f"{symbol.strip().upper()} options spread candidate {option_type} signal",
            option_type=option_type,
            scanner={
                "type": "options_spread_candidate",
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "atr_period": 14,
                "min_move_percent": "0.75",
                "min_atr_percent": "0.50",
                "execution_mode": "signal_only_until_multileg_supported",
                "confidence": confidence,
                "dedupe_minutes": dedupe_minutes,
            },
            description="Options-spread candidate scanner. It flags spread-worthy setups while multi-leg execution is unavailable.",
        )
    )


def _advanced_payload(
    *,
    symbol: str,
    target_strike: Decimal,
    name: str,
    option_type: str,
    scanner: dict[str, Any],
    description: str,
) -> dict[str, Any]:
    clean_symbol = symbol.strip().upper()
    scanner = dict(scanner)
    scanner.update(
        {
            "symbols": [clean_symbol],
            "rationale": f"{clean_symbol} {scanner['type']} scanner triggered",
            "data_feed": "iex",
            "preview": _preview_config(
                symbol=clean_symbol,
                option_type=option_type,
                target_strike=target_strike,
                rationale=f"{name}: auto-submit enabled.",
            ),
            "exit": _exit_config(),
            "submit": _submit_config(),
        }
    )
    return {
        "name": name,
        "description": f"{clean_symbol} {description}",
        "is_active": True,
        "config": {"scanner": scanner},
    }


def _mark_signal_only_payload(payload: dict[str, Any]) -> dict[str, Any]:
    scanner = payload["config"]["scanner"]
    preview = scanner.get("preview")
    if isinstance(preview, dict):
        preview["enabled"] = False
        preview["rationale"] = f"{payload['name']}: signal-only scanner."
    submit = scanner.get("submit")
    if isinstance(submit, dict):
        submit["enabled"] = False
    payload["description"] = f"{payload['description']} Signals are review-only and do not create entry previews."
    return payload
