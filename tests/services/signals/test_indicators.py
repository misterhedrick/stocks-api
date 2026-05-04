from __future__ import annotations

import pytest

from app.services.signals.indicators import atr, bollinger, ema, macd, percent_change, rsi, sma


def test_sma_uses_rolling_window() -> None:
    assert sma([1, 2, 3, 4], 3) == [None, None, 2.0, 3.0]


def test_ema_initializes_with_sma_then_smooths() -> None:
    values = ema([1, 2, 3, 4], 3)
    assert values[0] is None
    assert values[1] is None
    assert values[2] == 2.0
    assert values[3] == 3.0


def test_rsi_handles_all_gains_as_100() -> None:
    values = rsi([1, 2, 3, 4, 5, 6], 3)
    assert values[3] == 100.0
    assert values[-1] == 100.0


def test_rsi_handles_flat_data_as_50() -> None:
    values = rsi([5, 5, 5, 5, 5], 3)
    assert values[3] == 50.0


def test_macd_returns_line_signal_and_histogram() -> None:
    values = [float(index) for index in range(1, 60)]
    series = macd(values, 12, 26, 9)
    assert len(series.line) == len(values)
    assert len(series.signal) == len(values)
    assert len(series.histogram) == len(values)
    assert series.line[-1] is not None
    assert series.signal[-1] is not None
    assert series.histogram[-1] is not None


def test_macd_rejects_invalid_periods() -> None:
    with pytest.raises(ValueError):
        macd([1, 2, 3], 26, 12, 9)


def test_bollinger_returns_bands_after_period() -> None:
    series = bollinger([1, 2, 3, 4, 5], 3, 2.0)
    assert series.middle[:2] == [None, None]
    assert series.middle[2] == 2.0
    assert series.upper[2] is not None
    assert series.lower[2] is not None


def test_atr_uses_true_range() -> None:
    values = atr(
        highs=[10, 12, 13, 14, 15],
        lows=[9, 10, 11, 12, 13],
        closes=[9.5, 11, 12, 13, 14],
        period=3,
    )
    assert values[0] is None
    assert values[3] is not None
    assert values[4] is not None


def test_percent_change() -> None:
    assert percent_change(110, 100) == 10.0
    assert percent_change(90, 100) == -10.0
    assert percent_change(90, 0) is None
