from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

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
from app.services.signals.indicators import IndicatorFrame


@dataclass(frozen=True)
class PriceLevel:
    kind: str
    value: float
    source: str
    touches: int = 1


class SupportResistanceEvaluator:
    strategy_type = "support_resistance"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        timeframe = str(config.get("timeframe") or "5Min")
        lookback_minutes = int(config.get("lookback_minutes") or 720)
        atr_period = int(config.get("atr_period") or 14)
        return RequiredFeatures(
            timeframe=timeframe,
            lookback_minutes=lookback_minutes,
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
        if len(candles.candles) < 3:
            return None

        latest = candles.candles[-1]
        previous = candles.candles[-2]
        timeframe = str(config.get("timeframe") or candles.timeframe)
        lookback_minutes = int(config.get("lookback_minutes") or 720)
        atr_period = int(config.get("atr_period") or 14)
        mode = str(config.get("mode") or "breakout_or_rejection").lower()
        level_tolerance_percent = float(config.get("level_tolerance_percent") or 0.10)
        breakout_buffer_percent = float(config.get("breakout_buffer_percent") or level_tolerance_percent)
        min_touches = int(config.get("min_touches") or 1)
        dedupe_minutes = int(config.get("dedupe_minutes") or 240)

        support_levels = _levels_from_config(config.get("support_levels"), kind="support")
        resistance_levels = _levels_from_config(config.get("resistance_levels"), kind="resistance")
        if not support_levels and not resistance_levels:
            support_levels, resistance_levels = _swing_levels(
                candles,
                swing_window=int(config.get("swing_window") or 2),
                lookback_candles=int(config.get("lookback_candles") or 60),
                tolerance_percent=level_tolerance_percent,
            )

        levels = [level for level in [*support_levels, *resistance_levels] if level.touches >= min_touches]
        if not levels:
            return None

        latest_close = float(latest.close)
        previous_close = float(previous.close)
        latest_open = float(latest.open)
        latest_high = float(latest.high)
        latest_low = float(latest.low)

        candidate = _find_signal_candidate(
            levels=levels,
            mode=mode,
            latest_close=latest_close,
            previous_close=previous_close,
            latest_open=latest_open,
            latest_high=latest_high,
            latest_low=latest_low,
            tolerance_percent=level_tolerance_percent,
            breakout_buffer_percent=breakout_buffer_percent,
        )
        if candidate is None:
            return None

        direction, signal_type, level, candle_confirmed, threshold_crossed = candidate
        configured_direction = config.get("direction")
        if configured_direction in {"bullish", "bearish"} and direction != configured_direction:
            return None
        if _bool(config.get("require_candle_confirmation"), default=True) and not candle_confirmed:
            return None

        distance_percent = abs(latest_close - level.value) / level.value * 100 if level.value > 0 else None
        max_distance_percent = _float_or_none(config.get("max_distance_percent"))
        if max_distance_percent is not None and distance_percent is not None:
            if distance_percent > max_distance_percent:
                return None

        score = Decimal("0.55")
        if level.touches >= 2:
            score += Decimal("0.05")
        if candle_confirmed:
            score += Decimal("0.05")
        if threshold_crossed:
            score += Decimal("0.03")
        if level.source == "manual":
            score += Decimal("0.03")
        if max_distance_percent is not None and distance_percent is not None:
            if distance_percent > max_distance_percent * 0.8:
                score -= Decimal("0.05")

        configured_signal_type = config.get("signal_type")
        if isinstance(configured_signal_type, str) and configured_signal_type:
            signal_type = configured_signal_type

        validation = {
            **price_action_features(candles, direction=direction),
            **atr_features(
                indicators,
                candles,
                period=atr_period,
                reference_price=level.value,
                reference_label="level",
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
                f"{symbol.upper()} generated a {direction} support/resistance signal "
                f"around {level.kind} level {level.value:g}"
            ),
            features={
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "mode": mode,
                "level_kind": level.kind,
                "level_value": feature_decimal(level.value),
                "level_source": level.source,
                "level_touches": level.touches,
                "level_tolerance_percent": feature_decimal(level_tolerance_percent),
                "breakout_buffer_percent": feature_decimal(breakout_buffer_percent),
                "latest_close": str(latest.close),
                "previous_close": str(previous.close),
                "latest_high": str(latest.high),
                "latest_low": str(latest.low),
                "threshold_crossed": threshold_crossed,
                "candle_confirmed": candle_confirmed,
                "distance_percent": feature_decimal(distance_percent),
                "dedupe_minutes": dedupe_minutes,
                **validation,
            },
            dedupe_key=f"{symbol.upper()}:{self.strategy_type}:{signal_type}:{direction}:{round(level.value, 2)}",
        )


def _find_signal_candidate(
    *,
    levels: list[PriceLevel],
    mode: str,
    latest_close: float,
    previous_close: float,
    latest_open: float,
    latest_high: float,
    latest_low: float,
    tolerance_percent: float,
    breakout_buffer_percent: float,
) -> tuple[str, str, PriceLevel, bool, bool] | None:
    ordered_levels = sorted(
        levels,
        key=lambda level: abs(latest_close - level.value),
    )
    allow_breakout = mode in {"breakout", "breakout_or_rejection", "both"}
    allow_rejection = mode in {"rejection", "bounce", "bounce_or_rejection", "breakout_or_rejection", "both"}

    for level in ordered_levels:
        tolerance = level.value * (tolerance_percent / 100)
        breakout_buffer = level.value * (breakout_buffer_percent / 100)

        if allow_breakout and level.kind == "resistance":
            if previous_close <= level.value and latest_close > level.value + breakout_buffer:
                return (
                    "bullish",
                    "resistance_breakout",
                    level,
                    latest_close >= latest_open and latest_close > previous_close,
                    True,
                )

        if allow_breakout and level.kind == "support":
            if previous_close >= level.value and latest_close < level.value - breakout_buffer:
                return (
                    "bearish",
                    "support_breakdown",
                    level,
                    latest_close <= latest_open and latest_close < previous_close,
                    True,
                )

        if allow_rejection and level.kind == "support":
            if latest_low <= level.value + tolerance and latest_close > level.value:
                return (
                    "bullish",
                    "support_bounce",
                    level,
                    latest_close >= latest_open or latest_close > previous_close,
                    False,
                )

        if allow_rejection and level.kind == "resistance":
            if latest_high >= level.value - tolerance and latest_close < level.value:
                return (
                    "bearish",
                    "resistance_rejection",
                    level,
                    latest_close <= latest_open or latest_close < previous_close,
                    False,
                )

    return None


def _levels_from_config(value: object, *, kind: str) -> list[PriceLevel]:
    levels: list[PriceLevel] = []
    for level in _iter_level_values(value):
        parsed = _float_or_none(level)
        if parsed is not None:
            levels.append(PriceLevel(kind=kind, value=parsed, source="manual", touches=1))
    return levels


def _iter_level_values(value: object) -> Iterable[object]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return value
    return [value]


def _swing_levels(
    candles: CandleFrame,
    *,
    swing_window: int,
    lookback_candles: int,
    tolerance_percent: float,
) -> tuple[list[PriceLevel], list[PriceLevel]]:
    if swing_window <= 0:
        return [], []
    usable = list(candles.candles[-lookback_candles:]) if lookback_candles > 0 else list(candles.candles)
    if len(usable) < swing_window * 2 + 1:
        return [], []

    support_values: list[float] = []
    resistance_values: list[float] = []
    for index in range(swing_window, len(usable) - swing_window):
        candle = usable[index]
        left = usable[index - swing_window : index]
        right = usable[index + 1 : index + 1 + swing_window]
        high = float(candle.high)
        low = float(candle.low)
        if all(high > float(other.high) for other in [*left, *right]):
            resistance_values.append(high)
        if all(low < float(other.low) for other in [*left, *right]):
            support_values.append(low)

    return (
        _cluster_levels(support_values, kind="support", tolerance_percent=tolerance_percent),
        _cluster_levels(resistance_values, kind="resistance", tolerance_percent=tolerance_percent),
    )


def _cluster_levels(values: list[float], *, kind: str, tolerance_percent: float) -> list[PriceLevel]:
    levels: list[PriceLevel] = []
    for value in sorted(values):
        matched_index = None
        for index, level in enumerate(levels):
            tolerance = level.value * (tolerance_percent / 100)
            if abs(value - level.value) <= tolerance:
                matched_index = index
                break
        if matched_index is None:
            levels.append(PriceLevel(kind=kind, value=value, source="swing", touches=1))
        else:
            existing = levels[matched_index]
            touches = existing.touches + 1
            averaged_value = ((existing.value * existing.touches) + value) / touches
            levels[matched_index] = PriceLevel(
                kind=kind,
                value=averaged_value,
                source="swing",
                touches=touches,
            )
    return levels


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
