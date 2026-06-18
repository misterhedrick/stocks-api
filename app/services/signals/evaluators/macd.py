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
from app.services.signals.indicators import IndicatorFrame


class MacdCrossoverEvaluator:
    strategy_type = "macd_crossover"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        timeframe = str(config.get("timeframe") or "5Min")
        lookback_minutes = int(config.get("lookback_minutes") or 480)
        fast_period = int(config.get("fast_period") or 12)
        slow_period = int(config.get("slow_period") or 26)
        signal_period = int(config.get("signal_period") or 9)
        atr_period = int(config.get("atr_period") or 14)
        return RequiredFeatures(
            timeframe=timeframe,
            lookback_minutes=lookback_minutes,
            macd_periods=frozenset({(fast_period, slow_period, signal_period)}),
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
        if len(candles.candles) < 2:
            return None

        latest = candles.candles[-1]
        previous = candles.candles[-2]
        timeframe = str(config.get("timeframe") or candles.timeframe)
        lookback_minutes = int(config.get("lookback_minutes") or 480)
        fast_period = int(config.get("fast_period") or 12)
        slow_period = int(config.get("slow_period") or 26)
        signal_period = int(config.get("signal_period") or 9)
        atr_period = int(config.get("atr_period") or 14)

        macd = indicators.macd(fast_period, slow_period, signal_period)
        current_macd = macd.line[-1] if macd.line else None
        previous_macd = macd.line[-2] if len(macd.line) >= 2 else None
        current_signal = macd.signal[-1] if macd.signal else None
        previous_signal = macd.signal[-2] if len(macd.signal) >= 2 else None
        current_histogram = macd.histogram[-1] if macd.histogram else None
        previous_histogram = macd.histogram[-2] if len(macd.histogram) >= 2 else None

        if (
            current_macd is None
            or previous_macd is None
            or current_signal is None
            or previous_signal is None
            or current_histogram is None
        ):
            return None

        latest_close = float(latest.close)
        previous_close = float(previous.close)
        require_price_confirmation = _bool(config.get("require_price_confirmation"), default=True)
        require_histogram_confirmation = _bool(config.get("require_histogram_confirmation"), default=True)

        direction: str | None = None
        signal_type: str | None = None
        if previous_macd <= previous_signal and current_macd > current_signal:
            direction = "bullish"
            signal_type = "macd_bullish_crossover"
            if require_price_confirmation and latest_close <= previous_close:
                return None
            if require_histogram_confirmation and current_histogram <= 0:
                return None
        elif previous_macd >= previous_signal and current_macd < current_signal:
            direction = "bearish"
            signal_type = "macd_bearish_crossover"
            if require_price_confirmation and latest_close >= previous_close:
                return None
            if require_histogram_confirmation and current_histogram >= 0:
                return None

        configured_direction = config.get("direction")
        if configured_direction in {"bullish", "bearish"} and direction != configured_direction:
            return None
        if direction is None or signal_type is None:
            return None

        histogram_expanding = False
        if previous_histogram is not None:
            if direction == "bullish":
                histogram_expanding = current_histogram > previous_histogram
            else:
                histogram_expanding = current_histogram < previous_histogram

        score = Decimal("0.55")
        if require_price_confirmation:
            score += Decimal("0.05")
        if histogram_expanding:
            score += Decimal("0.05")
        if direction == "bullish" and current_macd > 0:
            score += Decimal("0.03")
        if direction == "bearish" and current_macd < 0:
            score += Decimal("0.03")

        dedupe_minutes = int(config.get("dedupe_minutes") or 240)
        validation = {
            **price_action_features(candles, direction=direction),
            **atr_features(indicators, candles, period=atr_period),
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
                f"{symbol.upper()} MACD crossed {'above' if direction == 'bullish' else 'below'} "
                f"the signal line with {direction} price confirmation"
            ),
            features={
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "fast_period": fast_period,
                "slow_period": slow_period,
                "signal_period": signal_period,
                "previous_macd": feature_decimal(previous_macd),
                "current_macd": feature_decimal(current_macd),
                "previous_signal": feature_decimal(previous_signal),
                "current_signal": feature_decimal(current_signal),
                "previous_histogram": feature_decimal(previous_histogram),
                "current_histogram": feature_decimal(current_histogram),
                "histogram_expanding": histogram_expanding,
                "latest_close": str(latest.close),
                "previous_close": str(previous.close),
                "dedupe_minutes": dedupe_minutes,
                **validation,
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
