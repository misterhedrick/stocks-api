from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.moving_average import MovingAverageTrendEvaluator
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


def test_bullish_moving_average_trend_signal() -> None:
    frame = _frame([100, 100.1, 100.2, 100.3, 100.45, 100.65, 100.85, 101.05])
    signal = MovingAverageTrendEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "short_window": 3,
            "long_window": 5,
            "average_type": "ema",
            "trigger": "bullish_trend",
            "min_change_percent": "0.05",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.strategy_type == "moving_average"
    assert signal.direction == "bullish"
    assert signal.signal_type == "moving_average_setup"
    assert signal.features["short_average"] is not None
    assert signal.features["long_average"] is not None


def test_bearish_moving_average_trend_signal() -> None:
    frame = _frame([101.0, 100.8, 100.6, 100.4, 100.2, 100.0, 99.8, 99.6])
    signal = MovingAverageTrendEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "short_window": 3,
            "long_window": 5,
            "average_type": "ema",
            "trigger": "bearish_trend",
            "min_change_percent": "0.05",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bearish"
    assert signal.signal_type == "moving_average_setup"


def test_respects_configured_direction() -> None:
    frame = _frame([100, 100.1, 100.2, 100.3, 100.45, 100.65, 100.85, 101.05])
    signal = MovingAverageTrendEvaluator().evaluate(
        symbol="SPY",
        config={
            "short_window": 3,
            "long_window": 5,
            "trigger": "bullish_trend",
            "direction": "bearish",
            "min_change_percent": "0.05",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_no_signal_when_price_confirmation_fails() -> None:
    frame = _frame([100, 100.5, 101.0, 101.5, 102.0, 101.4, 101.2, 101.0])
    signal = MovingAverageTrendEvaluator().evaluate(
        symbol="SPY",
        config={
            "short_window": 3,
            "long_window": 5,
            "trigger": "bullish_trend",
            "min_change_percent": "0.05",
            "require_short_average_slope": False,
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_bullish_crossover_signal() -> None:
    frame = _frame([100, 99.8, 99.6, 99.7, 99.9, 100.2, 100.8, 101.4])
    signal = MovingAverageTrendEvaluator().evaluate(
        symbol="SPY",
        config={
            "short_window": 2,
            "long_window": 4,
            "average_type": "sma",
            "trigger": "bullish_cross",
            "min_change_percent": "0.05",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bullish"


def test_rejects_when_not_enough_indicator_values() -> None:
    frame = _frame([100, 100.1, 100.2])
    signal = MovingAverageTrendEvaluator().evaluate(
        symbol="SPY",
        config={"short_window": 3, "long_window": 5},
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None
