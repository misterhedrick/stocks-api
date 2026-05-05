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


class BreakoutPriceThresholdEvaluator:
    strategy_type = "breakout_price_threshold"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        timeframe = str(config.get("timeframe") or "5Min")
        lookback_minutes = int(config.get("lookback_minutes") or 480)
        return RequiredFeatures(
            timeframe=timeframe,
            lookback_minutes=lookback_minutes,
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
        breakout_buffer_percent = float(config.get("breakout_buffer_percent") or 0)
        dedupe_minutes = int(config.get("dedupe_minutes") or 240)

        latest_close = float(latest.close)
        previous_close = float(previous.close)
        latest_open = float(latest.open)

        bullish_level = _float_or_none(config.get("price_above"))
        bearish_level = _float_or_none(config.get("price_below"))
        level_source = "configured_threshold"

        if bullish_level is None and bearish_level is None:
            range_lookback = int(config.get("range_lookback_candles") or 0)
            if range_lookback <= 0:
                return None
            if len(candles.candles) <= range_lookback:
                return None
            range_candles = candles.candles[-range_lookback - 1 : -1]
            bullish_level = max(float(candle.high) for candle in range_candles)
            bearish_level = min(float(candle.low) for candle in range_candles)
            level_source = "recent_range"

        direction: str | None = None
        signal_type: str | None = None
        breakout_level: float | None = None
        threshold_crossed = False
        candle_confirmed = False

        if bullish_level is not None:
            buffered_level = bullish_level * (1 + breakout_buffer_percent / 100)
            if previous_close <= bullish_level and latest_close > buffered_level:
                direction = "bullish"
                signal_type = "price_breakout"
                breakout_level = bullish_level
                threshold_crossed = True
                candle_confirmed = latest_close >= latest_open and latest_close > previous_close

        if direction is None and bearish_level is not None:
            buffered_level = bearish_level * (1 - breakout_buffer_percent / 100)
            if previous_close >= bearish_level and latest_close < buffered_level:
                direction = "bearish"
                signal_type = "price_breakdown"
                breakout_level = bearish_level
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

        score = Decimal("0.55")
        if threshold_crossed:
            score += Decimal("0.05")
        if candle_confirmed:
            score += Decimal("0.05")
        if distance_percent is not None and distance_percent >= breakout_buffer_percent:
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
            confidence=confidence(score, maximum=Decimal("0.82")),
            rationale=(
                f"{symbol.upper()} closed {'above' if direction == 'bullish' else 'below'} "
                f"breakout level {breakout_level:g} with {direction} candle confirmation"
            ),
            features={
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "level_source": level_source,
                "breakout_level": feature_decimal(breakout_level),
                "price_above": feature_decimal(bullish_level),
                "price_below": feature_decimal(bearish_level),
                "breakout_buffer_percent": feature_decimal(breakout_buffer_percent),
                "latest_close": str(latest.close),
                "previous_close": str(previous.close),
                "threshold_crossed": threshold_crossed,
                "candle_confirmed": candle_confirmed,
                "distance_percent": feature_decimal(distance_percent),
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
