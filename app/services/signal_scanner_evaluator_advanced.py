from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings
from app.integrations.alpaca import AlpacaMarketDataClient, AlpacaStockBars
from app.services.signal_scanner_evaluator_trend import (
    _candle_frame_from_stock_bars,
    _scanner_string,
    _signal_spec_from_candidate,
)
from app.services.signals.candles import CandleFrame
from app.services.signals.evaluators.registry import get_evaluator
from app.services.signals.indicators import IndicatorFrame, percent_change


ADVANCED_EVALUATOR_FLAGS = {
    "vwap_reclaim": "vwap_reclaim_evaluator_enabled",
    "opening_range_breakout": "opening_range_breakout_evaluator_enabled",
    "relative_strength": "relative_strength_evaluator_enabled",
    "time_series_momentum": "time_series_momentum_evaluator_enabled",
    "market_regime_filter": "market_regime_filter_evaluator_enabled",
    "pairs_relative_value": "pairs_relative_value_evaluator_enabled",
    "options_spread_candidate": "options_spread_candidate_evaluator_enabled",
}


def _advanced_evaluator_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    scanner_type = str(scanner_config.get("type") or "")
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping {scanner_type} evaluator"
        )
        return []
    flag_name = ADVANCED_EVALUATOR_FLAGS.get(scanner_type)
    if flag_name is None:
        raise ValueError(f"unsupported advanced scanner type: {scanner_type}")
    if not bool(getattr(settings, flag_name)):
        no_signal_reasons.append(
            f"{strategy_name}: {flag_name.upper()}=false, skipping {scanner_type} evaluator"
        )
        return []

    evaluator = get_evaluator(scanner_type)
    if evaluator is None:
        return []

    features = evaluator.required_features(scanner_config)
    timeframe = features.timeframe
    lookback_minutes = features.lookback_minutes
    feed = _scanner_string(scanner_config, "data_feed", default="iex")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes + 5)

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    bars_by_symbol = client.get_stock_bars(
        symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=feed,
        limit=max(lookback_minutes + 10, 20),
    )

    frames_by_symbol = _frames_by_symbol(bars_by_symbol, timeframe)
    peer_returns = _peer_returns(frames_by_symbol)
    emit_symbols = _emit_symbols(scanner_config, symbols)
    signal_specs: list[dict[str, Any]] = []
    for symbol in emit_symbols:
        candle_frame = frames_by_symbol.get(symbol)
        if candle_frame is None or len(candle_frame.candles) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for {scanner_type} evaluator"
            )
            continue

        indicator_frame = IndicatorFrame(
            close=candle_frame.closes,
            high=candle_frame.highs,
            low=candle_frame.lows,
            volume=candle_frame.volumes,
        )
        candidate = evaluator.evaluate(
            symbol=symbol,
            config=scanner_config,
            candles=candle_frame,
            indicators=indicator_frame,
            market_regime={"peer_returns": peer_returns},
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: {scanner_type} evaluator produced no signal"
            )
            continue
        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _emit_symbols(scanner_config: dict[str, Any], symbols: list[str]) -> list[str]:
    raw_emit_symbols = scanner_config.get("_emit_symbols")
    if not isinstance(raw_emit_symbols, list):
        return symbols
    clean_symbols = []
    available = {symbol.upper() for symbol in symbols}
    for raw_symbol in raw_emit_symbols:
        if not isinstance(raw_symbol, str):
            continue
        symbol = raw_symbol.strip().upper()
        if symbol and symbol in available:
            clean_symbols.append(symbol)
    return clean_symbols


def _frames_by_symbol(
    bars_by_symbol: dict[str, AlpacaStockBars],
    timeframe: str,
) -> dict[str, CandleFrame]:
    frames: dict[str, CandleFrame] = {}
    for symbol, stock_bars in bars_by_symbol.items():
        if stock_bars is None or len(stock_bars.bars) < 2:
            continue
        frames[symbol.upper()] = _candle_frame_from_stock_bars(stock_bars, timeframe)
    return frames


def _peer_returns(frames_by_symbol: dict[str, CandleFrame]) -> dict[str, float]:
    returns: dict[str, float] = {}
    for symbol, frame in frames_by_symbol.items():
        if len(frame.candles) < 2:
            continue
        pct = percent_change(float(frame.candles[-1].close), float(frame.candles[0].close))
        if pct is not None:
            returns[symbol] = pct
    return returns
