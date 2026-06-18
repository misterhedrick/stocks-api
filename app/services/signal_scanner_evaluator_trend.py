from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.core.config import settings
from app.db.models import JobRun, Signal, Strategy
from app.integrations.alpaca import (
    AlpacaMarketDataClient,
    AlpacaStockBars,
)
from app.services.audit_logs import record_audit_log
from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.base import SignalCandidate
from app.services.signals.evaluators.registry import get_evaluator
from app.services.signals.indicators import IndicatorFrame, percent_change

DEFAULT_DEDUPE_MINUTES = 240


def _moving_average_evaluator_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping moving average evaluator"
        )
        return []
    if not settings.moving_average_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: MOVING_AVERAGE_EVALUATOR_ENABLED=false, skipping moving average evaluator"
        )
        return []

    short_window = _positive_int(scanner_config, "short_window", default=5)
    long_window = _positive_int(scanner_config, "long_window", default=20)
    if short_window >= long_window:
        raise ValueError("scanner.short_window must be less than scanner.long_window")

    evaluator = get_evaluator("moving_average")
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
    market_regime = _market_regime_from_stock_bars(bars_by_symbol)

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 3:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for moving average evaluator"
            )
            continue

        candle_frame = _candle_frame_from_stock_bars(stock_bars, timeframe)
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
            market_regime=market_regime,
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: moving average evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _momentum_rate_of_change_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping momentum evaluator"
        )
        return []
    if not settings.momentum_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: MOMENTUM_EVALUATOR_ENABLED=false, skipping momentum evaluator"
        )
        return []

    evaluator = get_evaluator("momentum_rate_of_change")
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
    market_regime = _market_regime_from_stock_bars(bars_by_symbol)

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for momentum evaluator"
            )
            continue

        candle_frame = _candle_frame_from_stock_bars(stock_bars, timeframe)
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
            market_regime=market_regime,
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: momentum evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _rsi_reversal_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping rsi_reversal evaluator"
        )
        return []
    if not settings.rsi_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: RSI_EVALUATOR_ENABLED=false, skipping rsi_reversal evaluator"
        )
        return []

    evaluator = get_evaluator("rsi_reversal")
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
    market_regime = _market_regime_from_stock_bars(bars_by_symbol)

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for rsi_reversal evaluator"
            )
            continue

        candle_frame = _candle_frame_from_stock_bars(stock_bars, timeframe)
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
            market_regime=market_regime,
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: rsi_reversal evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _macd_crossover_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping macd_crossover evaluator"
        )
        return []
    if not settings.macd_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: MACD_EVALUATOR_ENABLED=false, skipping macd_crossover evaluator"
        )
        return []

    evaluator = get_evaluator("macd_crossover")
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
    market_regime = _market_regime_from_stock_bars(bars_by_symbol)

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for macd_crossover evaluator"
            )
            continue

        candle_frame = _candle_frame_from_stock_bars(stock_bars, timeframe)
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
            market_regime=market_regime,
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: macd_crossover evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _candle_frame_from_stock_bars(stock_bars: AlpacaStockBars, timeframe: str) -> CandleFrame:
    candles = tuple(
        Candle(
            ts=bar.timestamp,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        )
        for bar in stock_bars.bars
    )
    return CandleFrame(symbol=stock_bars.symbol, timeframe=timeframe, candles=candles)


def _signal_spec_from_candidate(
    candidate: SignalCandidate,
    scanner_config: dict[str, Any],
) -> dict[str, Any]:
    dedupe_minutes = int(
        candidate.features.get("dedupe_minutes")
        or scanner_config.get("dedupe_minutes")
        or DEFAULT_DEDUPE_MINUTES
    )
    return {
        "symbol": candidate.symbol,
        "underlying_symbol": candidate.symbol,
        "signal_type": candidate.signal_type,
        "direction": candidate.direction,
        "confidence": candidate.confidence,
        "rationale": candidate.rationale,
        "market_context": {
            "source": f"evaluator.{candidate.strategy_type}",
            "strategy_type": candidate.strategy_type,
            "dedupe_key": candidate.dedupe_key,
            **candidate.features,
        },
        "dedupe_minutes": dedupe_minutes,
    }


def _market_regime_from_stock_bars(
    bars_by_symbol: dict[str, AlpacaStockBars],
) -> dict[str, dict[str, float]] | None:
    peer_returns: dict[str, float] = {}
    for symbol, stock_bars in bars_by_symbol.items():
        if stock_bars is None or len(stock_bars.bars) < 2:
            continue
        first_close = float(stock_bars.bars[0].close)
        latest_close = float(stock_bars.bars[-1].close)
        pct = percent_change(latest_close, first_close)
        if pct is not None:
            peer_returns[symbol.upper()] = pct
    if not peer_returns:
        return None
    return {"peer_returns": peer_returns}


def _positive_int(config: dict[str, Any], key: str, *, default: int) -> int:
    raw_value = config.get(key, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"scanner.{key} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"scanner.{key} must be a positive integer")
    return value


def _scanner_string(
    config: dict[str, Any],
    key: str,
    *,
    default: str,
) -> str:
    raw_value = config.get(key, default)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError(f"scanner.{key} must be a non-empty string")
    return raw_value.strip()


