from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.volatility_squeeze import VolatilitySqueezeEvaluator
from app.services.signals.indicators import IndicatorFrame


def _frame(closes: list[float], *, symbol: str = "SPY") -> CandleFrame:
    start = datetime(2026, 5, 4, 14, 0, tzinfo=timezone.utc)
    candles = []
    for index, close in enumerate(closes):
        open_price = closes[index - 1] if index > 0 else close
        high = max(open_price, close) + 0.05
        low = min(open_price, close) - 0.05
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


BULLISH_SQUEEZE_BREAKOUT = [
    100.0,
    101.0,
    99.0,
    100.8,
    99.2,
    100.1,
    100.0,
    99.95,
    100.05,
    100.0,
    100.8,
]

BEARISH_SQUEEZE_BREAKDOWN = [
    100.0,
    101.0,
    99.0,
    100.8,
    99.2,
    100.1,
    100.0,
    99.95,
    100.05,
    100.0,
    98.5,
]


def test_bullish_volatility_squeeze_breakout_signal() -> None:
    frame = _frame(BULLISH_SQUEEZE_BREAKOUT)
    signal = VolatilitySqueezeEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "bollinger_period": 3,
            "bollinger_stddev": 2.0,
            "squeeze_lookback_candles": 5,
            "range_lookback_candles": 5,
            "compression_ratio_threshold": "0.80",
            "breakout_buffer_percent": "0.05",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.strategy_type == "volatility_squeeze"
    assert signal.direction == "bullish"
    assert signal.signal_type == "volatility_squeeze_bullish_breakout"
    assert signal.features["compression_detected"] is True
    assert signal.features["threshold_crossed"] is True


def test_bearish_volatility_squeeze_breakdown_signal() -> None:
    frame = _frame(BEARISH_SQUEEZE_BREAKDOWN)
    signal = VolatilitySqueezeEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "bollinger_period": 3,
            "bollinger_stddev": 2.0,
            "squeeze_lookback_candles": 5,
            "range_lookback_candles": 5,
            "compression_ratio_threshold": "0.80",
            "breakout_buffer_percent": "0.05",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bearish"
    assert signal.signal_type == "volatility_squeeze_bearish_breakdown"


def test_respects_configured_direction() -> None:
    frame = _frame(BULLISH_SQUEEZE_BREAKOUT)
    signal = VolatilitySqueezeEvaluator().evaluate(
        symbol="SPY",
        config={
            "bollinger_period": 3,
            "squeeze_lookback_candles": 5,
            "range_lookback_candles": 5,
            "compression_ratio_threshold": "0.80",
            "direction": "bearish",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_without_compression() -> None:
    frame = _frame([100, 100.2, 100.4, 100.6, 100.8, 101.0, 101.2, 101.4, 101.6, 101.8, 102.5])
    signal = VolatilitySqueezeEvaluator().evaluate(
        symbol="SPY",
        config={
            "bollinger_period": 3,
            "squeeze_lookback_candles": 5,
            "range_lookback_candles": 5,
            "compression_ratio_threshold": "0.50",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_without_price_confirmation() -> None:
    frame = _frame([100.0, 101.0, 99.0, 100.8, 99.2, 100.1, 100.0, 99.95, 100.05, 100.0, 100.02])
    signal = VolatilitySqueezeEvaluator().evaluate(
        symbol="SPY",
        config={
            "bollinger_period": 3,
            "squeeze_lookback_candles": 5,
            "range_lookback_candles": 5,
            "compression_ratio_threshold": "0.80",
            "breakout_buffer_percent": "0",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_when_breakout_distance_too_large() -> None:
    frame = _frame([100.0, 101.0, 99.0, 100.8, 99.2, 100.1, 100.0, 99.95, 100.05, 100.0, 105.0])
    signal = VolatilitySqueezeEvaluator().evaluate(
        symbol="SPY",
        config={
            "bollinger_period": 3,
            "squeeze_lookback_candles": 5,
            "range_lookback_candles": 5,
            "compression_ratio_threshold": "0.80",
            "max_breakout_distance_percent": "2.0",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_when_not_enough_candles() -> None:
    frame = _frame([100, 100.1, 100.2])
    signal = VolatilitySqueezeEvaluator().evaluate(
        symbol="SPY",
        config={
            "bollinger_period": 3,
            "squeeze_lookback_candles": 5,
            "range_lookback_candles": 5,
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None
