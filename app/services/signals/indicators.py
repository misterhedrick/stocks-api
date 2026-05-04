from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean
from typing import Sequence


@dataclass(frozen=True, slots=True)
class MacdSeries:
    line: list[float | None]
    signal: list[float | None]
    histogram: list[float | None]


@dataclass(frozen=True, slots=True)
class BollingerSeries:
    middle: list[float | None]
    upper: list[float | None]
    lower: list[float | None]


class IndicatorFrame:
    def __init__(
        self,
        *,
        close: Sequence[float],
        high: Sequence[float] | None = None,
        low: Sequence[float] | None = None,
        volume: Sequence[float | None] | None = None,
    ) -> None:
        self.close = list(close)
        self.high = list(high) if high is not None else list(close)
        self.low = list(low) if low is not None else list(close)
        self.volume = list(volume) if volume is not None else [None for _ in self.close]
        self._sma_cache: dict[int, list[float | None]] = {}
        self._ema_cache: dict[int, list[float | None]] = {}
        self._rsi_cache: dict[int, list[float | None]] = {}
        self._macd_cache: dict[tuple[int, int, int], MacdSeries] = {}
        self._bollinger_cache: dict[tuple[int, float], BollingerSeries] = {}
        self._atr_cache: dict[int, list[float | None]] = {}

    def sma(self, period: int) -> list[float | None]:
        if period not in self._sma_cache:
            self._sma_cache[period] = sma(self.close, period)
        return self._sma_cache[period]

    def ema(self, period: int) -> list[float | None]:
        if period not in self._ema_cache:
            self._ema_cache[period] = ema(self.close, period)
        return self._ema_cache[period]

    def rsi(self, period: int) -> list[float | None]:
        if period not in self._rsi_cache:
            self._rsi_cache[period] = rsi(self.close, period)
        return self._rsi_cache[period]

    def macd(self, fast_period: int, slow_period: int, signal_period: int) -> MacdSeries:
        key = (fast_period, slow_period, signal_period)
        if key not in self._macd_cache:
            self._macd_cache[key] = macd(self.close, fast_period, slow_period, signal_period)
        return self._macd_cache[key]

    def bollinger(self, period: int, stddev: float = 2.0) -> BollingerSeries:
        key = (period, stddev)
        if key not in self._bollinger_cache:
            self._bollinger_cache[key] = bollinger(self.close, period, stddev)
        return self._bollinger_cache[key]

    def atr(self, period: int) -> list[float | None]:
        if period not in self._atr_cache:
            self._atr_cache[period] = atr(self.high, self.low, self.close, period)
        return self._atr_cache[period]


def sma(values: Sequence[float], period: int) -> list[float | None]:
    _validate_period(period)
    result: list[float | None] = [None for _ in values]
    if len(values) < period:
        return result
    running_sum = sum(values[:period])
    result[period - 1] = running_sum / period
    for index in range(period, len(values)):
        running_sum += values[index] - values[index - period]
        result[index] = running_sum / period
    return result


def ema(values: Sequence[float], period: int) -> list[float | None]:
    _validate_period(period)
    result: list[float | None] = [None for _ in values]
    if len(values) < period:
        return result
    initial = mean(values[:period])
    result[period - 1] = initial
    multiplier = 2 / (period + 1)
    previous = initial
    for index in range(period, len(values)):
        current = (values[index] - previous) * multiplier + previous
        result[index] = current
        previous = current
    return result


def rsi(values: Sequence[float], period: int) -> list[float | None]:
    _validate_period(period)
    result: list[float | None] = [None for _ in values]
    if len(values) <= period:
        return result

    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    result[period] = _rsi_from_averages(avg_gain, avg_loss)

    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        gain = max(change, 0.0)
        loss = abs(min(change, 0.0))
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        result[index] = _rsi_from_averages(avg_gain, avg_loss)

    return result


def macd(
    values: Sequence[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> MacdSeries:
    _validate_period(fast_period)
    _validate_period(slow_period)
    _validate_period(signal_period)
    if fast_period >= slow_period:
        raise ValueError("fast_period must be less than slow_period")

    fast = ema(values, fast_period)
    slow = ema(values, slow_period)
    line: list[float | None] = []
    for fast_value, slow_value in zip(fast, slow, strict=True):
        if fast_value is None or slow_value is None:
            line.append(None)
        else:
            line.append(fast_value - slow_value)

    signal = ema_optional(line, signal_period)
    histogram: list[float | None] = []
    for line_value, signal_value in zip(line, signal, strict=True):
        if line_value is None or signal_value is None:
            histogram.append(None)
        else:
            histogram.append(line_value - signal_value)
    return MacdSeries(line=line, signal=signal, histogram=histogram)


def bollinger(values: Sequence[float], period: int, stddev: float = 2.0) -> BollingerSeries:
    _validate_period(period)
    middle = sma(values, period)
    upper: list[float | None] = [None for _ in values]
    lower: list[float | None] = [None for _ in values]
    for index in range(period - 1, len(values)):
        window = values[index - period + 1 : index + 1]
        window_mean = middle[index]
        if window_mean is None:
            continue
        variance = sum((value - window_mean) ** 2 for value in window) / period
        band_distance = sqrt(variance) * stddev
        upper[index] = window_mean + band_distance
        lower[index] = window_mean - band_distance
    return BollingerSeries(middle=middle, upper=upper, lower=lower)


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int,
) -> list[float | None]:
    _validate_period(period)
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows, and closes must have the same length")
    result: list[float | None] = [None for _ in closes]
    if len(closes) <= period:
        return result

    true_ranges = [0.0]
    for index in range(1, len(closes)):
        high = highs[index]
        low = lows[index]
        previous_close = closes[index - 1]
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))

    initial = sum(true_ranges[1 : period + 1]) / period
    result[period] = initial
    previous = initial
    for index in range(period + 1, len(closes)):
        current = ((previous * (period - 1)) + true_ranges[index]) / period
        result[index] = current
        previous = current
    return result


def ema_optional(values: Sequence[float | None], period: int) -> list[float | None]:
    _validate_period(period)
    result: list[float | None] = [None for _ in values]
    valid_values: list[float] = []
    start_index = None
    for index, value in enumerate(values):
        if value is None:
            continue
        valid_values.append(value)
        if len(valid_values) == period:
            start_index = index
            break
    if start_index is None:
        return result

    initial = mean(valid_values)
    result[start_index] = initial
    previous = initial
    multiplier = 2 / (period + 1)
    for index in range(start_index + 1, len(values)):
        value = values[index]
        if value is None:
            continue
        current = (value - previous) * multiplier + previous
        result[index] = current
        previous = current
    return result


def percent_change(current: float, reference: float) -> float | None:
    if reference == 0:
        return None
    return ((current - reference) / reference) * 100


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    relative_strength = avg_gain / avg_loss
    return 100 - (100 / (1 + relative_strength))


def _validate_period(period: int) -> None:
    if period <= 0:
        raise ValueError("period must be positive")
