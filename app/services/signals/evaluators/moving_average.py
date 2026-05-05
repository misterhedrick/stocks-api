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
from app.services.signals.indicators import IndicatorFrame, percent_change


class MovingAverageTrendEvaluator:
    strategy_type = "moving_average"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        timeframe = str(config.get("timeframe") or "5Min")
        lookback_minutes = int(config.get("lookback_minutes") or 1440)
        short_window = int(config.get("short_window") or 5)
        long_window = int(config.get("long_window") or 20)
        average_type = str(config.get("average_type") or "ema").lower()
        ema_periods = frozenset({short_window, long_window}) if average_type == "ema" else frozenset()
        sma_periods = frozenset({short_window, long_window}) if average_type == "sma" else frozenset()
        return RequiredFeatures(
            timeframe=timeframe,
            lookback_minutes=lookback_minutes,
            ema_periods=ema_periods,
            sma_periods=sma_periods,
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
        lookback_minutes = int(config.get("lookback_minutes") or 1440)
        short_window = int(config.get("short_window") or 5)
        long_window = int(config.get("long_window") or 20)
        average_type = str(config.get("average_type") or "ema").lower()
        trigger = str(config.get("trigger") or "trend_state").lower()

        short_average = _average(indicators, average_type, short_window)
        long_average = _average(indicators, average_type, long_window)
        latest_short = short_average[-1] if short_average else None
        previous_short = short_average[-2] if len(short_average) >= 2 else None
        latest_long = long_average[-1] if long_average else None
        previous_long = long_average[-2] if len(long_average) >= 2 else None

        if latest_short is None or previous_short is None or latest_long is None or previous_long is None:
            return None

        short_slope = latest_short - previous_short
        long_slope = latest_long - previous_long
        current_price = float(latest.close)
        previous_price = float(previous.close)
        recent_change = percent_change(current_price, previous_price)
        min_change_percent = float(config.get("min_change_percent") or 0.10)

        direction: str | None = None
        if _is_bullish_setup(
            trigger=trigger,
            current_price=current_price,
            latest_short=latest_short,
            previous_short=previous_short,
            latest_long=latest_long,
            previous_long=previous_long,
            short_slope=short_slope,
            recent_change=recent_change,
            min_change_percent=min_change_percent,
            config=config,
        ):
            direction = "bullish"
        elif _is_bearish_setup(
            trigger=trigger,
            current_price=current_price,
            latest_short=latest_short,
            previous_short=previous_short,
            latest_long=latest_long,
            previous_long=previous_long,
            short_slope=short_slope,
            recent_change=recent_change,
            min_change_percent=min_change_percent,
            config=config,
        ):
            direction = "bearish"

        configured_direction = config.get("direction")
        if configured_direction in {"bullish", "bearish"} and direction != configured_direction:
            return None
        if direction is None:
            return None

        separation_percent = abs(latest_short - latest_long) / latest_long * 100 if latest_long > 0 else None
        price_distance_percent = abs(current_price - latest_short) / latest_short * 100 if latest_short > 0 else None
        min_average_separation_percent = _float_or_none(config.get("min_average_separation_percent"))
        max_price_distance_percent = _float_or_none(config.get("max_price_distance_percent"))

        if min_average_separation_percent is not None:
            if separation_percent is None or separation_percent < min_average_separation_percent:
                return None
        if max_price_distance_percent is not None:
            if price_distance_percent is not None and price_distance_percent > max_price_distance_percent:
                return None

        score = Decimal("0.55")
        if short_slope > 0 and direction == "bullish":
            score += Decimal("0.05")
        if short_slope < 0 and direction == "bearish":
            score += Decimal("0.05")
        if (direction == "bullish" and current_price > latest_short) or (direction == "bearish" and current_price < latest_short):
            score += Decimal("0.05")
        if separation_percent is not None and separation_percent >= (min_average_separation_percent or 0.05):
            score += Decimal("0.05")
        if max_price_distance_percent is not None and price_distance_percent is not None:
            if price_distance_percent > max_price_distance_percent * 0.8:
                score -= Decimal("0.05")

        dedupe_minutes = int(config.get("dedupe_minutes") or 240)
        signal_type = str(config.get("signal_type") or "moving_average_setup")
        return SignalCandidate(
            symbol=symbol.upper(),
            strategy_type=self.strategy_type,
            signal_type=signal_type,
            direction=direction,
            confidence=confidence(score, maximum=Decimal("0.80")),
            rationale=(
                f"{symbol.upper()} {short_window} {average_type.upper()} is "
                f"{'above' if direction == 'bullish' else 'below'} {long_window} {average_type.upper()} "
                f"with {direction} price confirmation"
            ),
            features={
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "trigger": trigger,
                "average_type": average_type,
                "short_window": short_window,
                "long_window": long_window,
                "latest_close": str(latest.close),
                "previous_close": str(previous.close),
                "short_average": feature_decimal(latest_short),
                "long_average": feature_decimal(latest_long),
                "short_slope": feature_decimal(short_slope),
                "long_slope": feature_decimal(long_slope),
                "recent_percent_change": feature_decimal(recent_change),
                "average_separation_percent": feature_decimal(separation_percent),
                "price_distance_percent": feature_decimal(price_distance_percent),
                "dedupe_minutes": dedupe_minutes,
            },
            dedupe_key=f"{symbol.upper()}:{self.strategy_type}:{signal_type}:{direction}",
        )


def _is_bullish_setup(
    *,
    trigger: str,
    current_price: float,
    latest_short: float,
    previous_short: float,
    latest_long: float,
    previous_long: float,
    short_slope: float,
    recent_change: float | None,
    min_change_percent: float,
    config: dict[str, Any],
) -> bool:
    if trigger in {"bullish_cross", "crossover", "cross"}:
        if not (previous_short <= previous_long and latest_short > latest_long):
            return False
    else:
        if not (latest_short > latest_long):
            return False
    if _bool(config.get("require_price_confirmation"), default=True) and not (current_price > latest_short):
        return False
    if _bool(config.get("require_short_average_slope"), default=True) and not (short_slope > 0):
        return False
    if recent_change is not None and recent_change < min_change_percent:
        return False
    return True


def _is_bearish_setup(
    *,
    trigger: str,
    current_price: float,
    latest_short: float,
    previous_short: float,
    latest_long: float,
    previous_long: float,
    short_slope: float,
    recent_change: float | None,
    min_change_percent: float,
    config: dict[str, Any],
) -> bool:
    if trigger in {"bearish_cross", "crossover", "cross"}:
        if not (previous_short >= previous_long and latest_short < latest_long):
            return False
    else:
        if not (latest_short < latest_long):
            return False
    if _bool(config.get("require_price_confirmation"), default=True) and not (current_price < latest_short):
        return False
    if _bool(config.get("require_short_average_slope"), default=True) and not (short_slope < 0):
        return False
    if recent_change is not None and recent_change > -abs(min_change_percent):
        return False
    return True


def _average(indicators: IndicatorFrame, average_type: str, window: int) -> list[float | None]:
    if average_type == "sma":
        return indicators.sma(window)
    return indicators.ema(window)


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
