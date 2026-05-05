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


class RsiReversalEvaluator:
    strategy_type = "rsi_reversal"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        timeframe = str(config.get("timeframe") or "5Min")
        lookback_minutes = int(config.get("lookback_minutes") or 240)
        rsi_period = int(config.get("rsi_period") or 14)
        return RequiredFeatures(
            timeframe=timeframe,
            lookback_minutes=lookback_minutes,
            rsi_periods=frozenset({rsi_period}),
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
        lookback_minutes = int(config.get("lookback_minutes") or 240)
        rsi_period = int(config.get("rsi_period") or 14)
        oversold_level = float(config.get("oversold_level") or 30)
        overbought_level = float(config.get("overbought_level") or 70)
        confirmation_mode = str(config.get("confirmation_mode") or "cross_back_inside").lower()

        rsi_values = indicators.rsi(rsi_period)
        latest_rsi = rsi_values[-1] if rsi_values else None
        previous_rsi = rsi_values[-2] if len(rsi_values) >= 2 else None
        if latest_rsi is None or previous_rsi is None:
            return None

        latest_close = float(latest.close)
        previous_close = float(previous.close)
        latest_open = float(latest.open)

        direction: str | None = None
        signal_type: str | None = None
        crossed_inside = False
        candle_confirmed = False

        if previous_rsi < oversold_level and latest_rsi >= oversold_level:
            direction = "bullish"
            signal_type = "rsi_oversold_recovery"
            crossed_inside = True
            candle_confirmed = latest_close > previous_close and latest_close >= latest_open
        elif previous_rsi > overbought_level and latest_rsi <= overbought_level:
            direction = "bearish"
            signal_type = "rsi_overbought_rejection"
            crossed_inside = True
            candle_confirmed = latest_close < previous_close and latest_close <= latest_open
        elif confirmation_mode == "reversal_candle":
            if previous_rsi < oversold_level and latest_rsi > previous_rsi:
                direction = "bullish"
                signal_type = "rsi_oversold_recovery"
                candle_confirmed = latest_close > previous_close and latest_close >= latest_open
            elif previous_rsi > overbought_level and latest_rsi < previous_rsi:
                direction = "bearish"
                signal_type = "rsi_overbought_rejection"
                candle_confirmed = latest_close < previous_close and latest_close <= latest_open

        configured_direction = config.get("direction")
        if configured_direction in {"bullish", "bearish"} and direction != configured_direction:
            return None
        if direction is None or signal_type is None:
            return None
        if _bool(config.get("require_price_confirmation"), default=True) and not candle_confirmed:
            return None

        short_average_window = _int_or_none(config.get("trend_average_window"))
        trend_average = None
        trend_conflict = False
        if short_average_window is not None:
            average_type = str(config.get("trend_average_type") or "ema").lower()
            average_values = indicators.sma(short_average_window) if average_type == "sma" else indicators.ema(short_average_window)
            trend_average = average_values[-1] if average_values else None
            if trend_average is not None:
                if direction == "bullish" and latest_close < trend_average:
                    trend_conflict = _bool(config.get("reject_trend_conflict"), default=False)
                if direction == "bearish" and latest_close > trend_average:
                    trend_conflict = _bool(config.get("reject_trend_conflict"), default=False)
        if trend_conflict:
            return None

        score = Decimal("0.50")
        if crossed_inside:
            score += Decimal("0.05")
        if candle_confirmed:
            score += Decimal("0.05")
        if trend_average is not None and not trend_conflict:
            score += Decimal("0.03")

        dedupe_minutes = int(config.get("dedupe_minutes") or 240)
        threshold = oversold_level if direction == "bullish" else overbought_level
        return SignalCandidate(
            symbol=symbol.upper(),
            strategy_type=self.strategy_type,
            signal_type=signal_type,
            direction=direction,
            confidence=confidence(score, maximum=Decimal("0.75")),
            rationale=(
                f"{symbol.upper()} RSI crossed back {'above' if direction == 'bullish' else 'below'} "
                f"{threshold:g} with {direction} price confirmation"
            ),
            features={
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
                "rsi_period": rsi_period,
                "previous_rsi": feature_decimal(previous_rsi),
                "latest_rsi": feature_decimal(latest_rsi),
                "oversold_level": feature_decimal(oversold_level),
                "overbought_level": feature_decimal(overbought_level),
                "confirmation_mode": confirmation_mode,
                "crossed_inside": crossed_inside,
                "candle_confirmed": candle_confirmed,
                "latest_close": str(latest.close),
                "previous_close": str(previous.close),
                "trend_average": feature_decimal(trend_average),
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


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
