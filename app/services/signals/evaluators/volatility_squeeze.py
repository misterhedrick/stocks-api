from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.signals.candles import CandleFrame
from app.services.signals.evaluators.base import (
    RequiredFeatures,
    SignalCandidate,
    confidence,
    feature_decimal,
)
from app.services.signals.indicators import IndicatorFrame


class VolatilitySqueezeEvaluator:
    strategy_type = "volatility_squeeze"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        timeframe = str(config.get("timeframe") or "5Min")
        lookback_minutes = int(config.get("lookback_minutes") or 720)
        bollinger_period = int(config.get("bollinger_period") or 20)
        bollinger_stddev = float(config.get("bollinger_stddev") or 2.0)
        return RequiredFeatures(
            timeframe=timeframe,
            lookback_minutes=lookback_minutes,
            bollinger_periods=frozenset({(bollinger_period, bollinger_stddev)}),
        )

    def evaluate(
        self,
        *,
        symbol: str,
        config: dict[str, Any],
        candles: CandleFrame,
        indicators: IndicatorFrame,
        market_regime: Any | None = None,
    ) -> SignalCandidate | None:
        if len(candles.candles) < 3:
            return None

        latest = candles.candles[-1]
        previous = candles.candles[-2]
        timeframe = str(config.get("timeframe") or candles.timeframe)
        lookback_minutes = int(config.get("lookback_minutes") or 720)
        bollinger_period = int(config.get("bollinger_period") or 20)
        bollinger_stddev = float(config.get("bollinger_stddev") or 2.0)
        squeeze_lookback_candles = int(config.get("squeeze_lookback_candles") or 20)
        range_lookback_candles = int(config.get("range_lookback_candles") or squeeze_lookback_candles)
        breakout_buffer_percent = float(config.get("breakout_buffer_percent") or 0)
        dedupe_minutes = int(config.get("dedupe_minutes") or 240)

        if squeeze_lookback_candles <= 0 or range_lookback_candles <= 0:
            return None
        if len(candles.candles) <= max(squeeze_lookback_candles, range_lookback_candles):
            return None

        bands = indicators.bollinger(bollinger_period, bollinger_stddev)
        latest_middle = bands.middle[-1] if bands.middle else None
        latest_upper = bands.upper[-1] if bands.upper else None
        latest_lower = bands.lower[-1] if bands.lower else None
        if latest_middle is None or latest_upper is None or latest_lower is None:
            return None

        band_widths = _band_widths(
            middle=bands.middle,
            upper=bands.upper,
            lower=bands.lower,
        )
        recent_widths = [width for width in band_widths[-squeeze_lookback_candles - 1 : -1] if width is not None]
        if len(recent_widths) < squeeze_lookback_candles:
            return None

        latest_width = band_widths[-1]
        if latest_width is None:
            return None

        min_recent_width = min(recent_widths)
        avg_recent_width = sum(recent_widths) / len(recent_widths)
        max_band_width_percent = _float_or_none(config.get("max_band_width_percent"))
        compression_ratio_threshold = float(config.get("compression_ratio_threshold") or 0.75)

        compression_detected = False
        if max_band_width_percent is not None and min_recent_width <= max_band_width_percent:
            compression_detected = True
        if avg_recent_width > 0 and min_recent_width <= avg_recent_width * compression_ratio_threshold:
            compression_detected = True
        if not compression_detected:
            return None

        range_candles = candles.candles[-range_lookback_candles - 1 : -1]
        range_high = max(float(candle.high) for candle in range_candles)
        range_low = min(float(candle.low) for candle in range_candles)

        latest_close = float(latest.close)
        previous_close = float(previous.close)
        latest_open = float(latest.open)

        direction: str | None = None
        signal_type: str | None = None
        breakout_level: float | None = None
        threshold_crossed = False
        candle_confirmed = False

        bullish_buffered_level = range_high * (1 + breakout_buffer_percent / 100)
        bearish_buffered_level = range_low * (1 - breakout_buffer_percent / 100)

        if previous_close <= range_high and latest_close > bullish_buffered_level:
            direction = "bullish"
            signal_type = "volatility_squeeze_bullish_breakout"
            breakout_level = range_high
            threshold_crossed = True
            candle_confirmed = latest_close >= latest_open and latest_close > previous_close
        elif previous_close >= range_low and latest_close < bearish_buffered_level:
            direction = "bearish"
            signal_type = "volatility_squeeze_bearish_breakdown"
            breakout_level = range_low
            threshold_crossed = True
            candle_confirmed = latest_close <= latest_open and latest_close < previous_close

        configured_direction = config.get("direction")
        if configured_direction in {"bullish", "bearish"} and direction != configured_direction:
            return None
        if direction is None or signal_type is None or breakout_level is None:
            return None
        if _bool(config.get("require_price_confirmation"), default=True) and not candle_confirmed:
            return None

        distance_percent = None
        if breakout_level > 0:
            distance_percent = abs(latest_close - breakout_level) / breakout_level * 100
        max_breakout_distance_percent = _float_or_none(config.get("max_breakout_distance_percent"))
        if max_breakout_distance_percent is not None and distance_percent is not None:
            if distance_percent > max_breakout_distance_percent:
                return None

        width_expanding = latest_width > min_recent_width
        score = Decimal("0.56")
        if threshold_crossed:
            score += Decimal("0.05")
        if candle_confirmed:
            score += Decimal("0.05")
        if width_expanding:
            score += Decimal("0.04")
        if max_band_width_percent is not None and min_recent_width <= max_band_width_percent:
            score += Decimal("0.03")
        if max_breakout_distance_percent is not None and distance_percent is not None:
            if distance_percent > max_breakout_distance_percent * 0.8:
                score -= Decimal("0.05")

        configured_signal_type = config.get("signal_type")
        if isinstance(configured_signal_type, str) and configured_signal_type:
            signal_type = configured_signal_type

        return SignalCandidate(
            symbol=symbol.upper(),
            strategy_type=self.strategy_type,
            signal_type=signal_type,
            direction=direction,
            confidence=confidence(score, maximum=Decimal("0.84")),
            rationale=(
                f"{symbol.upper()} broke {'above' if direction == 'bullish' else 'below'} "
                f"a recent squeeze range after Bollinger Band compression"
            ),
            features={
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "bollinger_period": bollinger_period,
                "bollinger_stddev": bollinger_stddev,
                "squeeze_lookback_candles": squeeze_lookback_candles,
                "range_lookback_candles": range_lookback_candles,
                "breakout_buffer_percent": feature_decimal(breakout_buffer_percent),
                "range_high": feature_decimal(range_high),
                "range_low": feature_decimal(range_low),
                "breakout_level": feature_decimal(breakout_level),
                "latest_band_width_percent": feature_decimal(latest_width),
                "min_recent_band_width_percent": feature_decimal(min_recent_width),
                "avg_recent_band_width_percent": feature_decimal(avg_recent_width),
                "max_band_width_percent": feature_decimal(max_band_width_percent),
                "compression_ratio_threshold": feature_decimal(compression_ratio_threshold),
                "compression_detected": compression_detected,
                "width_expanding": width_expanding,
                "latest_close": str(latest.close),
                "previous_close": str(previous.close),
                "threshold_crossed": threshold_crossed,
                "candle_confirmed": candle_confirmed,
                "distance_percent": feature_decimal(distance_percent),
                "dedupe_minutes": dedupe_minutes,
            },
            dedupe_key=f"{symbol.upper()}:{self.strategy_type}:{signal_type}:{direction}",
        )


def _band_widths(
    *,
    middle: list[float | None],
    upper: list[float | None],
    lower: list[float | None],
) -> list[float | None]:
    widths: list[float | None] = []
    for middle_value, upper_value, lower_value in zip(middle, upper, lower, strict=True):
        if middle_value is None or upper_value is None or lower_value is None or middle_value == 0:
            widths.append(None)
        else:
            widths.append(((upper_value - lower_value) / middle_value) * 100)
    return widths


def _bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
