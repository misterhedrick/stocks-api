from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.volume_breakout import VolumeConfirmedBreakoutEvaluator
from app.services.signals.indicators import IndicatorFrame


def _custom_frame(candles: list[tuple[float, float, float, float, float]], *, symbol: str = "SPY") -> CandleFrame:
    start = datetime(2026, 5, 4, 14, 0, tzinfo=timezone.utc)
    return CandleFrame(
        symbol=symbol,
        timeframe="5Min",
        candles=tuple(
            Candle(
                ts=start + timedelta(minutes=index),
                open=Decimal(str(open_price)),
                high=Decimal(str(high)),
                low=Decimal(str(low)),
                close=Decimal(str(close)),
                volume=Decimal(str(volume)),
            )
            for index, (open_price, high, low, close, volume) in enumerate(candles)
        ),
    )


def _indicators(frame: CandleFrame) -> IndicatorFrame:
    return IndicatorFrame(
        close=frame.closes,
        high=frame.highs,
        low=frame.lows,
        volume=frame.volumes,
    )


def _base_bullish_frame(*, latest_volume: float = 2500, latest_close: float = 100.8) -> CandleFrame:
    return _custom_frame(
        [
            (99.0, 99.4, 98.8, 99.1, 1000),
            (99.1, 99.6, 99.0, 99.4, 1000),
            (99.4, 99.9, 99.2, 99.8, 1000),
            (99.8, 100.0, 99.5, 99.9, 1000),
            (100.0, 101.0, 99.9, latest_close, latest_volume),
        ]
    )


def _base_bearish_frame(*, latest_volume: float = 2500, latest_close: float = 99.2) -> CandleFrame:
    return _custom_frame(
        [
            (101.0, 101.2, 100.6, 100.9, 1000),
            (100.9, 101.0, 100.4, 100.6, 1000),
            (100.6, 100.8, 100.1, 100.2, 1000),
            (100.2, 100.5, 100.0, 100.1, 1000),
            (100.0, 100.1, 99.0, latest_close, latest_volume),
        ]
    )


def test_bullish_configured_volume_breakout_signal() -> None:
    frame = _base_bullish_frame()
    signal = VolumeConfirmedBreakoutEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "price_above": "100",
            "breakout_buffer_percent": "0.10",
            "volume_lookback_candles": 4,
            "min_relative_volume": "1.5",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.strategy_type == "volume_confirmed_breakout"
    assert signal.direction == "bullish"
    assert signal.signal_type == "volume_confirmed_price_breakout"
    assert signal.features["level_source"] == "configured_threshold"
    assert signal.features["relative_volume"] == "2.5000"
    assert signal.features["threshold_crossed"] is True
    assert signal.features["candle_confirmed"] is True


def test_bearish_configured_volume_breakdown_signal() -> None:
    frame = _base_bearish_frame()
    signal = VolumeConfirmedBreakoutEvaluator().evaluate(
        symbol="SPY",
        config={
            "timeframe": "5Min",
            "price_below": "100",
            "breakout_buffer_percent": "0.10",
            "volume_lookback_candles": 4,
            "min_relative_volume": "1.5",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bearish"
    assert signal.signal_type == "volume_confirmed_price_breakdown"
    assert signal.features["level_source"] == "configured_threshold"


def test_bullish_recent_range_volume_breakout_signal() -> None:
    frame = _base_bullish_frame()
    signal = VolumeConfirmedBreakoutEvaluator().evaluate(
        symbol="SPY",
        config={
            "range_lookback_candles": 3,
            "breakout_buffer_percent": "0.05",
            "volume_lookback_candles": 4,
            "min_relative_volume": "1.5",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bullish"
    assert signal.features["level_source"] == "recent_range"


def test_bearish_recent_range_volume_breakdown_signal() -> None:
    frame = _base_bearish_frame()
    signal = VolumeConfirmedBreakoutEvaluator().evaluate(
        symbol="SPY",
        config={
            "range_lookback_candles": 3,
            "breakout_buffer_percent": "0.05",
            "volume_lookback_candles": 4,
            "min_relative_volume": "1.5",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bearish"
    assert signal.features["level_source"] == "recent_range"


def test_rejects_when_relative_volume_is_too_low() -> None:
    frame = _base_bullish_frame(latest_volume=1200)
    signal = VolumeConfirmedBreakoutEvaluator().evaluate(
        symbol="SPY",
        config={
            "price_above": "100",
            "volume_lookback_candles": 4,
            "min_relative_volume": "1.5",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_without_candle_confirmation() -> None:
    frame = _custom_frame(
        [
            (99.0, 99.4, 98.8, 99.1, 1000),
            (99.1, 99.6, 99.0, 99.4, 1000),
            (99.4, 99.9, 99.2, 99.8, 1000),
            (99.8, 100.0, 99.5, 99.9, 1000),
            (101.0, 101.1, 100.0, 100.2, 2500),
        ]
    )
    signal = VolumeConfirmedBreakoutEvaluator().evaluate(
        symbol="SPY",
        config={
            "price_above": "100",
            "volume_lookback_candles": 4,
            "min_relative_volume": "1.5",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_respects_configured_direction() -> None:
    frame = _base_bullish_frame()
    signal = VolumeConfirmedBreakoutEvaluator().evaluate(
        symbol="SPY",
        config={
            "price_above": "100",
            "direction": "bearish",
            "volume_lookback_candles": 4,
            "min_relative_volume": "1.5",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_when_no_threshold_or_range_configured() -> None:
    frame = _base_bullish_frame()
    signal = VolumeConfirmedBreakoutEvaluator().evaluate(
        symbol="SPY",
        config={
            "volume_lookback_candles": 4,
            "min_relative_volume": "1.5",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None
