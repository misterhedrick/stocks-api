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


class MeanReversionEvaluator:
    strategy_type = "mean_reversion"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        timeframe = str(config.get("timeframe") or "5Min")
        lookback_minutes = int(config.get("lookback_minutes") or 480)
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
        if len(candles.candles) < 2:
            return None

        latest = candles.candles[-1]
        previous = candles.candles[-2]
        timeframe = str(config.get("timeframe") or candles.timeframe)
        lookback_minutes = int(config.get("lookback_minutes") or 480)
        bollinger_period = int(config.get("bollinger_period") or 20)
        bollinger_stddev = float(config.get("bollinger_stddev") or 2.0)

        bands = indicators.bollinger(bollinger_period, bollinger_stddev)
        middle = bands.middle[-1] if bands.middle else None
        upper = bands.upper[-1] if bands.upper else None
        lower = bands.lower[-1] if bands.lower else None
        if middle is None or upper is None or lower is None:
            return None

        latest_close = float(latest.close)
        previous_close = float(previous.close)
        latest_open = float(latest.open)
        latest_high = float(latest.high)
        latest_low = float(latest.low)

        direction: str | None = None
        signal_type: str | None = None
        band_touch = False
        candle_confirmed = False

        if latest_low < lower and latest_close > lower:
            direction = "bullish"
            signal_type = "mean_reversion_lower_band_recovery"
            band_touch = True
            candle_confirmed = latest_close > previous_close or latest_close >= latest_open
        elif latest_high > upper and latest_close < upper:
            direction = "bearish"
            signal_type = "mean_reversion_upper_band_rejection"
            band_touch = True
            candle_confirmed = latest_close < previous_close or latest_close <= latest_open

        configured_direction = config.get("direction")
        if configured_direction in {"bullish", "bearish"} and direction != configured_direction:
            return None
        if direction is None or signal_type is None:
            return None
        if _bool(config.get("require_price_confirmation"), default=True) and not candle_confirmed:
            return None

        distance_to_middle_percent = None
        if middle > 0:
            distance_to_middle_percent = abs(latest_close - middle) / middle * 100
        max_distance_to_middle_percent = _float_or_none(config.get("max_distance_to_middle_percent"))
        if max_distance_to_middle_percent is not None and distance_to_middle_percent is not None:
            if distance_to_middle_percent > max_distance_to_middle_percent:
                return None

        score = Decimal("0.52")
        if band_touch:
            score += Decimal("0.05")
        if candle_confirmed:
            score += Decimal("0.05")
        if distance_to_middle_percent is not None and distance_to_middle_percent <= 1.0:
            score += Decimal("0.03")

        dedupe_minutes = int(config.get("dedupe_minutes") or 240)
        return SignalCandidate(
            symbol=symbol.upper(),
            strategy_type=self.strategy_type,
            signal_type=signal_type,
            direction=direction,
            confidence=confidence(score, maximum=Decimal("0.75")),
            rationale=(
                f"{symbol.upper()} touched the {'lower' if direction == 'bullish' else 'upper'} "
                f"Bollinger Band and closed back inside with {direction} confirmation"
            ),
            features={
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "bollinger_period": bollinger_period,
                "bollinger_stddev": bollinger_stddev,
                "middle_band": feature_decimal(middle),
                "upper_band": feature_decimal(upper),
                "lower_band": feature_decimal(lower),
                "latest_close": str(latest.close),
                "previous_close": str(previous.close),
                "latest_high": str(latest.high),
                "latest_low": str(latest.low),
                "band_touch": band_touch,
                "candle_confirmed": candle_confirmed,
                "distance_to_middle_percent": feature_decimal(distance_to_middle_percent),
                "dedupe_minutes": dedupe_minutes,
            },
            dedupe_key=f"{symbol.upper()}:{self.strategy_type}:{signal_type}:{direction}",
        )


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
