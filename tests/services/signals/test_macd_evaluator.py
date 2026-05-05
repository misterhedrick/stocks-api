from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.macd import MacdCrossoverEvaluator
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


def test_bullish_macd_crossover_signal() -> None:
    frame = _frame([100, 99.8, 99.6, 99.4, 99.2, 99.0, 99.1, 99.3, 99.8, 100.4, 101.0])
    signal = MacdCrossoverEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "fast_period": 3,
            "slow_period": 6,
            "signal_period": 3,
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.strategy_type == "macd_crossover"
    assert signal.direction == "bullish"
    assert signal.signal_type == "macd_bullish_crossover"
    assert signal.features["current_histogram"] is not None


def test_bearish_macd_crossover_signal() -> None:
    frame = _frame([100, 100.2, 100.4, 100.6, 100.8, 101.0, 100.9, 100.7, 100.2, 99.6, 99.0])
    signal = MacdCrossoverEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "fast_period": 3,
            "slow_period": 6,
            "signal_period": 3,
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bearish"
    assert signal.signal_type == "macd_bearish_crossover"


def test_respects_configured_direction() -> None:
    frame = _frame([100, 99.8, 99.6, 99.4, 99.2, 99.0, 99.1, 99.3, 99.8, 100.4, 101.0])
    signal = MacdCrossoverEvaluator().evaluate(
        symbol="SPY",
        config={
            "fast_period": 3,
            "slow_period": 6,
            "signal_period": 3,
            "direction": "bearish",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_without_price_confirmation() -> None:
    frame = _frame([100, 99.8, 99.6, 99.4, 99.2, 99.0, 99.1, 99.3, 99.8, 100.4, 100.3])
    signal = MacdCrossoverEvaluator().evaluate(
        symbol="SPY",
        config={
            "fast_period": 3,
            "slow_period": 6,
            "signal_period": 3,
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_when_not_enough_indicator_values() -> None:
    frame = _frame([100, 100.1, 100.2])
    signal = MacdCrossoverEvaluator().evaluate(
        symbol="SPY",
        config={
            "fast_period": 3,
            "slow_period": 6,
            "signal_period": 3,
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_can_disable_histogram_confirmation() -> None:
    frame = _frame([100, 99.8, 99.6, 99.4, 99.2, 99.0, 99.1, 99.3, 99.8, 100.4, 101.0])
    signal = MacdCrossoverEvaluator().evaluate(
        symbol="SPY",
        config={
            "fast_period": 3,
            "slow_period": 6,
            "signal_period": 3,
            "require_histogram_confirmation": False,
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bullish"
