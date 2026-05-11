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
from app.services.signals.indicators import IndicatorFrame

DEFAULT_DEDUPE_MINUTES = 240
DEDUPE_STATUSES = ("new", "previewed", "submitted")

def _mean_reversion_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping mean_reversion evaluator"
        )
        return []
    if not settings.mean_reversion_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: MEAN_REVERSION_EVALUATOR_ENABLED=false, skipping mean_reversion evaluator"
        )
        return []

    evaluator = get_evaluator("mean_reversion")
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

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for mean_reversion evaluator"
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
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: mean_reversion evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _breakout_price_threshold_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping breakout_price_threshold evaluator"
        )
        return []
    if not settings.breakout_price_threshold_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: BREAKOUT_PRICE_THRESHOLD_EVALUATOR_ENABLED=false, skipping breakout_price_threshold evaluator"
        )
        return []

    evaluator = get_evaluator("breakout_price_threshold")
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

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for breakout_price_threshold evaluator"
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
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: breakout_price_threshold evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _volume_confirmed_breakout_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping volume_confirmed_breakout evaluator"
        )
        return []
    if not settings.volume_confirmed_breakout_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: VOLUME_CONFIRMED_BREAKOUT_EVALUATOR_ENABLED=false, skipping volume_confirmed_breakout evaluator"
        )
        return []

    evaluator = get_evaluator("volume_confirmed_breakout")
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

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for volume_confirmed_breakout evaluator"
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
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: volume_confirmed_breakout evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _volatility_squeeze_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping volatility_squeeze evaluator"
        )
        return []
    if not settings.volatility_squeeze_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: VOLATILITY_SQUEEZE_EVALUATOR_ENABLED=false, skipping volatility_squeeze evaluator"
        )
        return []

    evaluator = get_evaluator("volatility_squeeze")
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

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for volatility_squeeze evaluator"
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
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: volatility_squeeze evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _support_resistance_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping support_resistance evaluator"
        )
        return []
    if not settings.support_resistance_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SUPPORT_RESISTANCE_EVALUATOR_ENABLED=false, skipping support_resistance evaluator"
        )
        return []

    evaluator = get_evaluator("support_resistance")
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

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for support_resistance evaluator"
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
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: support_resistance evaluator produced no signal"
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


