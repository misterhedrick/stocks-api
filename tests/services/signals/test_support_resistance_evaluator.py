from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.support_resistance import SupportResistanceEvaluator
from app.services.signals.indicators import IndicatorFrame


def _custom_frame(candles: list[tuple[float, float, float, float]], *, symbol: str = "SPY") -> CandleFrame:
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
                volume=Decimal("1000"),
            )
            for index, (open_price, high, low, close) in enumerate(candles)
        ),
    )


def _indicators(frame: CandleFrame) -> IndicatorFrame:
    return IndicatorFrame(
        close=frame.closes,
        high=frame.highs,
        low=frame.lows,
        volume=frame.volumes,
    )


def test_manual_resistance_breakout_signal() -> None:
    frame = _custom_frame(
        [
            (99.0, 99.5, 98.8, 99.2),
            (99.2, 99.8, 99.0, 99.7),
            (99.7, 101.0, 99.6, 100.8),
        ]
    )
    signal = SupportResistanceEvaluator().evaluate(
        symbol="SPY",
        config={
            "mode": "breakout",
            "resistance_levels": [100],
            "breakout_buffer_percent": "0.10",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.strategy_type == "support_resistance"
    assert signal.direction == "bullish"
    assert signal.signal_type == "resistance_breakout"
    assert signal.features["level_kind"] == "resistance"
    assert signal.features["level_source"] == "manual"
    assert signal.features["threshold_crossed"] is True


def test_manual_support_breakdown_signal() -> None:
    frame = _custom_frame(
        [
            (101.0, 101.2, 100.6, 100.9),
            (100.9, 101.0, 100.1, 100.2),
            (100.2, 100.3, 99.0, 99.2),
        ]
    )
    signal = SupportResistanceEvaluator().evaluate(
        symbol="SPY",
        config={
            "mode": "breakout",
            "support_levels": "100",
            "breakout_buffer_percent": "0.10",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bearish"
    assert signal.signal_type == "support_breakdown"
    assert signal.features["level_kind"] == "support"


def test_manual_support_bounce_signal() -> None:
    frame = _custom_frame(
        [
            (101.0, 101.2, 100.6, 100.9),
            (100.9, 101.0, 100.0, 100.2),
            (100.0, 100.8, 99.8, 100.6),
        ]
    )
    signal = SupportResistanceEvaluator().evaluate(
        symbol="SPY",
        config={
            "mode": "bounce",
            "support_levels": [100],
            "level_tolerance_percent": "0.20",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bullish"
    assert signal.signal_type == "support_bounce"
    assert signal.features["threshold_crossed"] is False
    assert signal.features["candle_confirmed"] is True


def test_manual_resistance_rejection_signal() -> None:
    frame = _custom_frame(
        [
            (99.0, 99.6, 98.8, 99.3),
            (99.3, 100.0, 99.2, 99.8),
            (100.1, 100.3, 99.2, 99.5),
        ]
    )
    signal = SupportResistanceEvaluator().evaluate(
        symbol="SPY",
        config={
            "mode": "rejection",
            "resistance_levels": [100],
            "level_tolerance_percent": "0.20",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bearish"
    assert signal.signal_type == "resistance_rejection"
    assert signal.features["level_kind"] == "resistance"


def test_swing_resistance_breakout_signal() -> None:
    frame = _custom_frame(
        [
            (99.0, 99.2, 98.8, 99.0),
            (99.0, 100.0, 98.9, 99.8),
            (99.8, 99.5, 99.0, 99.2),
            (99.2, 100.0, 99.0, 99.7),
            (99.7, 99.6, 99.1, 99.3),
            (99.3, 100.8, 99.2, 100.6),
        ]
    )
    signal = SupportResistanceEvaluator().evaluate(
        symbol="SPY",
        config={
            "mode": "breakout",
            "lookback_candles": 6,
            "swing_window": 1,
            "min_touches": 2,
            "level_tolerance_percent": "0.15",
            "breakout_buffer_percent": "0.10",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is not None
    assert signal.direction == "bullish"
    assert signal.features["level_source"] == "swing"
    assert signal.features["level_touches"] == 2


def test_respects_configured_direction() -> None:
    frame = _custom_frame(
        [
            (99.0, 99.5, 98.8, 99.2),
            (99.2, 99.8, 99.0, 99.7),
            (99.7, 101.0, 99.6, 100.8),
        ]
    )
    signal = SupportResistanceEvaluator().evaluate(
        symbol="SPY",
        config={
            "mode": "breakout",
            "resistance_levels": [100],
            "direction": "bearish",
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_without_candle_confirmation() -> None:
    frame = _custom_frame(
        [
            (99.0, 99.5, 98.8, 99.2),
            (99.2, 99.8, 99.0, 99.7),
            (101.0, 101.2, 100.2, 100.5),
        ]
    )
    signal = SupportResistanceEvaluator().evaluate(
        symbol="SPY",
        config={
            "mode": "breakout",
            "resistance_levels": [100],
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None


def test_rejects_without_levels() -> None:
    frame = _custom_frame(
        [
            (100.0, 100.2, 99.8, 100.0),
            (100.0, 100.1, 99.9, 100.0),
            (100.0, 100.2, 99.8, 100.1),
        ]
    )
    signal = SupportResistanceEvaluator().evaluate(
        symbol="SPY",
        config={
            "mode": "breakout",
            "lookback_candles": 3,
            "swing_window": 1,
            "min_touches": 2,
        },
        candles=frame,
        indicators=_indicators(frame),
    )

    assert signal is None
