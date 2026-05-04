from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.momentum import MomentumRateOfChangeEvaluator
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
    return CandleFrame(symbol=symbol, timeframe="1Min", candles=tuple(candles))


def _indicators(frame: CandleFrame) -> IndicatorFrame:
    return IndicatorFrame(
        close=frame.closes,
        high=frame.highs,
        low=frame.lows,
        volume=frame.volumes,
    )


def test_bullish_momentum_signal() -> None:
    frame = _frame([100, 100.05, 100.10, 100.20, 100.35, 100.50])
    signal = MomentumRateOfChangeEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "1Min",
            "lookback_minutes": 5,
            "change_above_percent": "0.35",
            "change_below_percent": "-0.35",
            "short_average_window": 3,
            "max_extension_percent": "5",
        },
        candles=frame,
        indicators=_indicators(frame),
    )
    assert signal is not None
    assert signal.direction == "bullish"
    assert signal.signal_type == "momentum_breakout"
    assert signal.features["percent_change"] is not None


def test_bearish_momentum_signal() -> None:
    frame = _frame([100, 99.95, 99.90, 99.75, 99.60, 99.45])
    signal = MomentumRateOfChangeEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "1Min",
            "lookback_minutes": 5,
            "change_above_percent": "0.35",
            "change_below_percent": "-0.35",
            "short_average_window": 3,
            "max_extension_percent": "5",
        },
        candles=frame,
        indicators=_indicators(frame),
    )
    assert signal is not None
    assert signal.direction == "bearish"
    assert signal.signal_type == "momentum_breakdown"


def test_no_signal_when_threshold_not_met() -> None:
    frame = _frame([100, 100.01, 100.02, 100.03, 100.04, 100.05])
    signal = MomentumRateOfChangeEvaluator().evaluate(
        symbol="SPY",
        config={"timeframe": "1Min", "lookback_minutes": 5},
        candles=frame,
        indicators=_indicators(frame),
    )
    assert signal is None


def test_rejects_latest_candle_reversal() -> None:
    frame = _frame([100, 100.2, 100.4, 100.6, 101.0, 100.8])
    signal = MomentumRateOfChangeEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "1Min",
            "lookback_minutes": 5,
            "change_above_percent": "0.35",
            "short_average_window": 3,
            "max_extension_percent": "5",
        },
        candles=frame,
        indicators=_indicators(frame),
    )
    assert signal is None


def test_rejects_when_not_enough_candles() -> None:
    frame = _frame([100, 100.5])
    signal = MomentumRateOfChangeEvaluator().evaluate(
        symbol="SPY",
        config={"timeframe": "1Min", "lookback_minutes": 5},
        candles=frame,
        indicators=_indicators(frame),
    )
    assert signal is None
