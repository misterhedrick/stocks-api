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


BULLISH_CROSS_CLOSES = [
    100,
    99.7577041233068,
    99.79481420046872,
    99.7841830476994,
    99.87243437716285,
    99.45540170883189,
    99.24891920689157,
    99.1018997513621,
    98.95777500678071,
    98.69418245718987,
    98.64894967755845,
    101.20704045353115,
]

BEARISH_CROSS_CLOSES = [
    100,
    100.30793190296691,
    100.78002439766175,
    100.92071487516952,
    100.90232434820594,
    101.20769991324669,
    101.15720078711166,
    101.48449212537011,
    101.53409723091359,
    101.8911811986045,
    102.19075666987278,
    100.4537614535399,
]


def test_bullish_macd_crossover_signal() -> None:
    frame = _frame(BULLISH_CROSS_CLOSES)
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
    frame = _frame(BEARISH_CROSS_CLOSES)
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
    frame = _frame(BULLISH_CROSS_CLOSES)
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
    frame = _frame(
        [
            100,
            100.53280451002044,
            100.55133767183129,
            102.23700876915007,
            100.36538675680373,
            100.81751281306839,
            102.35945614617215,
            103.68063483541728,
            104.62739204213482,
            105.78656419452177,
            105.36270692014725,
            105.65909889194583,
        ]
    )
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
    frame = _frame(BULLISH_CROSS_CLOSES)
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
