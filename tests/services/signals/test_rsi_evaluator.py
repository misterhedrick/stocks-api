from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.rsi import RsiReversalEvaluator
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


def test_bullish_rsi_oversold_recovery_signal() -> None:
    frame = _frame([100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 92.5, 93.5])
    signal = RsiReversalEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "rsi_period": 5,
            "oversold_level": 30,
            "overbought_level": 70,
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.strategy_type == "rsi_reversal"
    assert signal.direction == "bullish"
    assert signal.signal_type == "rsi_oversold_recovery"
    assert signal.features["previous_rsi"] is not None
    assert signal.features["latest_rsi"] is not None
    assert signal.features["crossed_inside"] is True


def test_bearish_rsi_overbought_rejection_signal() -> None:
    frame = _frame([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 107.5, 106.5])
    signal = RsiReversalEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "rsi_period": 5,
            "oversold_level": 30,
            "overbought_level": 70,
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bearish"
    assert signal.signal_type == "rsi_overbought_rejection"
    assert signal.features["crossed_inside"] is True


def test_respects_configured_direction() -> None:
    frame = _frame([100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 92.5, 93.5])
    signal = RsiReversalEvaluator().evaluate(
        symbol="SPY",
        config={
            "rsi_period": 5,
            "direction": "bearish",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_without_price_confirmation() -> None:
    frame = _frame([100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 92.5, 92.0])
    signal = RsiReversalEvaluator().evaluate(
        symbol="SPY",
        config={
            "rsi_period": 5,
            "oversold_level": 30,
            "overbought_level": 70,
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_when_not_enough_rsi_values() -> None:
    frame = _frame([100, 99, 98, 97])
    signal = RsiReversalEvaluator().evaluate(
        symbol="SPY",
        config={"rsi_period": 5},
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_reversal_candle_mode_allows_rsi_turn_without_cross() -> None:
    frame = _frame([100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 91.5, 92.0])
    signal = RsiReversalEvaluator().evaluate(
        symbol="SPY",
        config={
            "rsi_period": 5,
            "oversold_level": 35,
            "confirmation_mode": "reversal_candle",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bullish"
    assert signal.features["crossed_inside"] is False
    assert signal.features["candle_confirmed"] is True
