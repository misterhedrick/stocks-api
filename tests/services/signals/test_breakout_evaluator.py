from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.breakout import BreakoutPriceThresholdEvaluator
from app.services.signals.indicators import IndicatorFrame


def _frame(closes: list[float], *, symbol: str = "SPY") -> CandleFrame:
    start = datetime(2026, 5, 4, 14, 0, tzinfo=timezone.utc)
    candles = []
    for index, close in enumerate(closes):
        open_price = closes[index - 1] if index > 0 else close
        high = max(open_price, close) + 0.1
        low = min(open_price, close) - 0.1
        candles.append(
            Candle(
                ts=start + timedelta(minutes=index),
                open=Decimal(str(open_price)),
                high=Decimal(str(high)),
                low=Decimal(str(low)),
                close=Decimal(str(close)),
                volume=Decimal("1000"),
            )
        )
    return CandleFrame(symbol=symbol, timeframe="5Min", candles=tuple(candles))


def _indicators(frame: CandleFrame) -> IndicatorFrame:
    return IndicatorFrame(
        close=frame.closes,
        high=frame.highs,
        low=frame.lows,
        volume=frame.volumes,
    )


def test_bullish_configured_price_breakout_signal() -> None:
    frame = _frame([99.0, 99.4, 99.8, 100.5])
    signal = BreakoutPriceThresholdEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "price_above": "100",
            "breakout_buffer_percent": "0.10",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.strategy_type == "breakout_price_threshold"
    assert signal.direction == "bullish"
    assert signal.signal_type == "price_breakout"
    assert signal.features["level_source"] == "configured_threshold"
    assert signal.features["threshold_crossed"] is True


def test_bearish_configured_price_breakdown_signal() -> None:
    frame = _frame([101.0, 100.6, 100.2, 99.4])
    signal = BreakoutPriceThresholdEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "price_below": "100",
            "breakout_buffer_percent": "0.10",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bearish"
    assert signal.signal_type == "price_breakdown"
    assert signal.features["level_source"] == "configured_threshold"


def test_bullish_recent_range_breakout_signal() -> None:
    frame = _frame([99.0, 99.4, 99.8, 99.6, 100.2])
    signal = BreakoutPriceThresholdEvaluator().evaluate(
        symbol="SPY",
        config={
            "range_lookback_candles": 3,
            "breakout_buffer_percent": "0.05",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bullish"
    assert signal.features["level_source"] == "recent_range"


def test_bearish_recent_range_breakdown_signal() -> None:
    frame = _frame([101.0, 100.6, 100.2, 100.4, 99.8])
    signal = BreakoutPriceThresholdEvaluator().evaluate(
        symbol="SPY",
        config={
            "range_lookback_candles": 3,
            "breakout_buffer_percent": "0.05",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bearish"
    assert signal.features["level_source"] == "recent_range"


def test_respects_configured_direction() -> None:
    frame = _frame([99.0, 99.4, 99.8, 100.5])
    signal = BreakoutPriceThresholdEvaluator().evaluate(
        symbol="SPY",
        config={
            "price_above": "100",
            "direction": "bearish",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_without_price_confirmation() -> None:
    frame = _frame([99.0, 99.4, 99.8, 100.1])
    signal = BreakoutPriceThresholdEvaluator().evaluate(
        symbol="SPY",
        config={
            "price_above": "100",
            "breakout_buffer_percent": "0",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_when_breakout_distance_too_large() -> None:
    frame = _frame([99.0, 99.4, 99.8, 105.0])
    signal = BreakoutPriceThresholdEvaluator().evaluate(
        symbol="SPY",
        config={
            "price_above": "100",
            "max_breakout_distance_percent": "2.0",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_when_no_threshold_or_range_configured() -> None:
    frame = _frame([99.0, 99.4, 99.8, 100.5])
    signal = BreakoutPriceThresholdEvaluator().evaluate(
        symbol="SPY",
        config={},
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None
