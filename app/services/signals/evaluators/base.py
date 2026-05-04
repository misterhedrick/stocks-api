from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol

from app.services.signals.candles import CandleFrame
from app.services.signals.indicators import IndicatorFrame

SignalDirection = Literal["bullish", "bearish"]


@dataclass(frozen=True, slots=True)
class RequiredFeatures:
    timeframe: str
    lookback_minutes: int
    sma_periods: frozenset[int] = frozenset()
    ema_periods: frozenset[int] = frozenset()
    rsi_periods: frozenset[int] = frozenset()
    macd_periods: frozenset[tuple[int, int, int]] = frozenset()
    bollinger_periods: frozenset[tuple[int, float]] = frozenset()
    atr_periods: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True)
class SignalCandidate:
    symbol: str
    strategy_type: str
    signal_type: str
    direction: SignalDirection
    confidence: Decimal
    rationale: str
    features: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str | None = None


class SignalEvaluator(Protocol):
    strategy_type: str

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        ...

    def evaluate(
        self,
        *,
        symbol: str,
        config: dict[str, Any],
        candles: CandleFrame,
        indicators: IndicatorFrame,
        market_regime: Any | None = None,
    ) -> SignalCandidate | None:
        ...


def confidence(value: str | float | Decimal, *, minimum: Decimal = Decimal("0"), maximum: Decimal = Decimal("1")) -> Decimal:
    decimal_value = Decimal(str(value))
    if decimal_value < minimum:
        return minimum
    if decimal_value > maximum:
        return maximum
    return decimal_value.quantize(Decimal("0.01"))


def feature_decimal(value: float | Decimal | None, places: str = "0.0001") -> str | None:
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(Decimal(places)))
