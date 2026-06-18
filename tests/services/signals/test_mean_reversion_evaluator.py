from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.mean_reversion import MeanReversionEvaluator
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


BULLISH_LOWER_BAND_CLOSES = [100, 100.2, 100.1, 100.3, 100.2, 100.1, 100.0, 99.9, 99.8, 93.7, 98.7]
BEARISH_UPPER_BAND_CLOSES = [100, 99.8, 99.9, 99.7, 99.8, 99.9, 100, 100.1, 100.2, 100.2, 100.1]


def test_bullish_lower_band_recovery_signal() -> None:
    frame = _frame(BULLISH_LOWER_BAND_CLOSES)
    signal = MeanReversionEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "bollinger_period": 5,
            "bollinger_stddev": 2.0,
            "atr_period": 3,
        },
        candles=frame,
        indicators=_indicators(frame),
        market_regime={"peer_returns": {"SPY": -0.6, "QQQ": -0.4}},
    )

    assert signal is not None
    assert signal.strategy_type == "mean_reversion"
    assert signal.direction == "bullish"
    assert signal.signal_type == "mean_reversion_lower_band_recovery"
    assert signal.features["lower_band"] is not None
    assert signal.features["band_touch"] is True
    assert signal.features["band_excursion_percent"] is not None
    assert signal.features["distance_to_middle_atr"] is not None
    assert signal.features["market_regime_alignment"] == "conflict"


def test_bearish_upper_band_rejection_signal() -> None:
    frame = _frame(BEARISH_UPPER_BAND_CLOSES)
    signal = MeanReversionEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "bollinger_period": 5,
            "bollinger_stddev": 2.0,
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bearish"
    assert signal.signal_type == "mean_reversion_upper_band_rejection"
    assert signal.features["upper_band"] is not None


def test_respects_configured_direction() -> None:
    frame = _frame(BULLISH_LOWER_BAND_CLOSES)
    signal = MeanReversionEvaluator().evaluate(
        symbol="SPY",
        config={
            "bollinger_period": 5,
            "direction": "bearish",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_without_price_confirmation() -> None:
    frame = _frame([100, 100.2, 100.1, 100.3, 100.2, 100.1, 100.0, 99.9, 99.8, 93.7, 93.6])
    signal = MeanReversionEvaluator().evaluate(
        symbol="SPY",
        config={
            "bollinger_period": 5,
            "bollinger_stddev": 2.0,
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_when_not_enough_indicator_values() -> None:
    frame = _frame([100, 100.1, 100.2])
    signal = MeanReversionEvaluator().evaluate(
        symbol="SPY",
        config={"bollinger_period": 5},
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_when_too_far_from_middle_band() -> None:
    frame = _frame(BULLISH_LOWER_BAND_CLOSES)
    signal = MeanReversionEvaluator().evaluate(
        symbol="SPY",
        config={
            "bollinger_period": 5,
            "bollinger_stddev": 2.0,
            "max_distance_to_middle_percent": "0.01",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None
