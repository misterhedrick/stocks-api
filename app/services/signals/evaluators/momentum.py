from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.signals.candles import CandleFrame
from app.services.signals.evaluators.base import (
    RequiredFeatures,
    SignalCandidate,
    atr_features,
    confidence,
    feature_decimal,
    price_action_features,
    regime_alignment_features,
    validation_flags,
)
from app.services.signals.indicators import IndicatorFrame, percent_change


class MomentumRateOfChangeEvaluator:
    strategy_type = "momentum_rate_of_change"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        timeframe = str(config.get("timeframe") or "1Min")
        lookback_minutes = int(config.get("lookback_minutes") or 45)
        short_average_window = int(config.get("short_average_window") or 9)
        average_type = str(config.get("short_average_type") or "ema").lower()
        atr_period = int(config.get("atr_period") or 14)
        ema_periods = frozenset({short_average_window}) if average_type == "ema" else frozenset()
        sma_periods = frozenset({short_average_window}) if average_type == "sma" else frozenset()
        return RequiredFeatures(
            timeframe=timeframe,
            lookback_minutes=lookback_minutes,
            ema_periods=ema_periods,
            sma_periods=sma_periods,
            atr_periods=frozenset({atr_period}),
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
        timeframe = str(config.get("timeframe") or candles.timeframe)
        lookback_minutes = int(config.get("lookback_minutes") or 45)
        atr_period = int(config.get("atr_period") or 14)
        offset = _candles_for_minutes(lookback_minutes, timeframe)
        if len(candles.candles) <= offset or len(candles.candles) < 2:
            return None

        latest = candles.candles[-1]
        previous = candles.candles[-2]
        reference = candles.candles[-1 - offset]
        pct = percent_change(float(latest.close), float(reference.close))
        if pct is None:
            return None

        change_above = float(config.get("change_above_percent") or 0.50)
        change_below = float(config.get("change_below_percent") or -0.50)

        if pct >= change_above:
            direction = "bullish"
            signal_type = "momentum_breakout"
        elif pct <= change_below:
            direction = "bearish"
            signal_type = "momentum_breakdown"
        else:
            return None

        if _bool(config.get("require_latest_candle_confirmation"), default=True):
            if direction == "bullish" and not (latest.close > previous.close and latest.close >= latest.open):
                return None
            if direction == "bearish" and not (latest.close < previous.close and latest.close <= latest.open):
                return None

        average_type = str(config.get("short_average_type") or "ema").lower()
        short_average_window = int(config.get("short_average_window") or 9)
        short_average = _average(indicators, average_type, short_average_window)
        latest_average = short_average[-1] if short_average else None
        extension_percent: float | None = None
        max_extension_percent = _float_or_none(config.get("max_extension_percent"))
        if latest_average is not None and latest_average > 0:
            extension_percent = abs(float(latest.close) - latest_average) / latest_average * 100
            if max_extension_percent is not None and extension_percent > max_extension_percent:
                return None
            if direction == "bullish" and float(latest.close) < latest_average:
                return None
            if direction == "bearish" and float(latest.close) > latest_average:
                return None

        score = Decimal("0.55")
        threshold = change_above if direction == "bullish" else abs(change_below)
        if abs(pct) >= threshold * 1.25:
            score += Decimal("0.05")
        if latest_average is not None:
            score += Decimal("0.05")
        if extension_percent is not None and max_extension_percent is not None and extension_percent > max_extension_percent * 0.8:
            score -= Decimal("0.05")

        dedupe_minutes = int(config.get("dedupe_minutes") or 120)
        validation = {
            **price_action_features(candles, direction=direction),
            **atr_features(
                indicators,
                candles,
                period=atr_period,
                average_price=latest_average,
                average_label="short_average",
            ),
            **regime_alignment_features(
                symbol=symbol,
                direction=direction,
                market_regime=market_regime,
            ),
        }
        validation["validation_flags"] = validation_flags(validation)
        return SignalCandidate(
            symbol=symbol.upper(),
            strategy_type=self.strategy_type,
            signal_type=signal_type,
            direction=direction,
            confidence=confidence(score, maximum=Decimal("0.82")),
            rationale=(
                f"{symbol.upper()} moved {pct:.2f}% over {lookback_minutes} minutes "
                f"with {direction} candle confirmation"
            ),
            features={
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "reference_close": str(reference.close),
                "latest_close": str(latest.close),
                "percent_change": feature_decimal(pct),
                "short_average_type": average_type,
                "short_average_window": short_average_window,
                "short_average": feature_decimal(latest_average),
                "extension_percent": feature_decimal(extension_percent),
                "dedupe_minutes": dedupe_minutes,
                **validation,
            },
            dedupe_key=f"{symbol.upper()}:{self.strategy_type}:{signal_type}:{direction}",
        )


def _average(indicators: IndicatorFrame, average_type: str, window: int) -> list[float | None]:
    if average_type == "sma":
        return indicators.sma(window)
    return indicators.ema(window)


def _candles_for_minutes(lookback_minutes: int, timeframe: str) -> int:
    timeframe_minutes = _timeframe_minutes(timeframe)
    return max(1, lookback_minutes // timeframe_minutes)


def _timeframe_minutes(timeframe: str) -> int:
    value = timeframe.strip().lower()
    if value.endswith("min"):
        return int(value[:-3])
    if value.endswith("m"):
        return int(value[:-1])
    if value.endswith("hour"):
        return int(value[:-4]) * 60
    if value.endswith("h"):
        return int(value[:-1]) * 60
    raise ValueError(f"Unsupported timeframe: {timeframe}")


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
