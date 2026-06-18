from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from statistics import mean
from typing import Any, Literal, Protocol, Sequence

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


def price_action_features(
    candles: CandleFrame,
    *,
    direction: SignalDirection,
) -> dict[str, Any]:
    if not candles.candles:
        return {}

    latest = candles.candles[-1]
    open_price = float(latest.open)
    high = float(latest.high)
    low = float(latest.low)
    close = float(latest.close)
    candle_range = high - low
    body = abs(close - open_price)
    close_position = 0.5
    body_percent = None
    upper_wick_percent = None
    lower_wick_percent = None
    if candle_range > 0:
        close_position = (close - low) / candle_range
        body_percent = (body / candle_range) * 100
        upper_wick_percent = ((high - max(open_price, close)) / candle_range) * 100
        lower_wick_percent = ((min(open_price, close) - low) / candle_range) * 100

    directional_close_position = (
        close_position if direction == "bullish" else 1 - close_position
    )
    directional_wick_percent = (
        lower_wick_percent if direction == "bullish" else upper_wick_percent
    )
    opposing_wick_percent = (
        upper_wick_percent if direction == "bullish" else lower_wick_percent
    )

    return {
        "candle_body_percent": feature_decimal(body_percent),
        "close_position_in_range": feature_decimal(close_position),
        "directional_close_position": feature_decimal(directional_close_position),
        "upper_wick_percent": feature_decimal(upper_wick_percent),
        "lower_wick_percent": feature_decimal(lower_wick_percent),
        "directional_wick_percent": feature_decimal(directional_wick_percent),
        "opposing_wick_percent": feature_decimal(opposing_wick_percent),
        "directional_candle": (
            close >= open_price if direction == "bullish" else close <= open_price
        ),
    }


def atr_features(
    indicators: IndicatorFrame,
    candles: CandleFrame,
    *,
    period: int = 14,
    reference_price: float | None = None,
    reference_label: str = "reference",
    average_price: float | None = None,
    average_label: str = "average",
) -> dict[str, Any]:
    if not candles.candles:
        return {"atr_period": period}

    latest = candles.candles[-1]
    latest_close = float(latest.close)
    atr_values = indicators.atr(period)
    latest_atr = atr_values[-1] if atr_values else None
    features: dict[str, Any] = {
        "atr_period": period,
        "latest_atr": feature_decimal(latest_atr),
        "atr_percent": (
            feature_decimal((latest_atr / latest_close) * 100)
            if latest_atr is not None and latest_close > 0
            else None
        ),
    }

    if latest_atr is not None and latest_atr > 0 and len(candles.candles) >= 2:
        previous_close = float(candles.candles[-2].close)
        move = latest_close - previous_close
        features["move_from_previous_atr"] = feature_decimal(abs(move) / latest_atr)
        features["signed_move_from_previous_atr"] = feature_decimal(move / latest_atr)

    if latest_atr is not None and latest_atr > 0 and reference_price is not None:
        distance = abs(latest_close - reference_price) / latest_atr
        features[f"{reference_label}_distance_atr"] = feature_decimal(distance)

    if latest_atr is not None and latest_atr > 0 and average_price is not None:
        extension = abs(latest_close - average_price) / latest_atr
        features[f"{average_label}_extension_atr"] = feature_decimal(extension)

    return features


def regime_alignment_features(
    *,
    symbol: str,
    direction: SignalDirection,
    market_regime: Any | None,
    benchmark_symbols: Sequence[str] = ("SPY", "QQQ"),
) -> dict[str, Any]:
    peer_returns = _peer_returns(market_regime)
    symbol_name = symbol.upper()
    symbol_return = peer_returns.get(symbol_name)
    benchmark_values = [
        peer_returns[item.upper()]
        for item in benchmark_symbols
        if item.upper() in peer_returns
    ]
    benchmark_return = mean(benchmark_values) if benchmark_values else None
    alignment = _alignment(
        direction=direction,
        symbol_return=symbol_return,
        benchmark_return=benchmark_return,
    )

    return {
        "market_regime_symbol_return_percent": feature_decimal(symbol_return),
        "market_regime_benchmark_return_percent": feature_decimal(benchmark_return),
        "market_regime_direction": _return_direction(benchmark_return),
        "market_regime_alignment": alignment,
        "market_regime_aligned": alignment == "aligned",
        "market_regime_conflict": alignment == "conflict",
        "market_regime_score": {
            "aligned": "1.0000",
            "mixed": "0.0000",
            "unknown": None,
            "conflict": "-1.0000",
        }[alignment],
    }


def validation_flags(features: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if features.get("market_regime_aligned") is True:
        flags.append("market_regime_aligned")
    if features.get("market_regime_conflict") is True:
        flags.append("market_regime_conflict")
    directional_close = _float_or_none(features.get("directional_close_position"))
    if directional_close is not None:
        if directional_close >= 0.70:
            flags.append("strong_directional_close")
        elif directional_close < 0.50:
            flags.append("weak_directional_close")
    body_percent = _float_or_none(features.get("candle_body_percent"))
    if body_percent is not None and body_percent >= 60:
        flags.append("wide_body_candle")
    atr_percent = _float_or_none(features.get("atr_percent"))
    if atr_percent is not None and atr_percent >= 1:
        flags.append("high_intraday_atr")
    return flags


def _peer_returns(market_regime: Any | None) -> dict[str, float]:
    if not isinstance(market_regime, dict):
        return {}
    raw_returns = market_regime.get("peer_returns")
    if not isinstance(raw_returns, dict):
        return {}
    returns: dict[str, float] = {}
    for raw_symbol, raw_value in raw_returns.items():
        try:
            returns[str(raw_symbol).upper()] = float(raw_value)
        except (TypeError, ValueError):
            continue
    return returns


def _alignment(
    *,
    direction: SignalDirection,
    symbol_return: float | None,
    benchmark_return: float | None,
) -> str:
    if symbol_return is None and benchmark_return is None:
        return "unknown"
    desired_positive = direction == "bullish"
    values = [value for value in (symbol_return, benchmark_return) if value is not None]
    if not values:
        return "unknown"
    aligned = all(value >= 0 for value in values) if desired_positive else all(value <= 0 for value in values)
    conflict = all(value < 0 for value in values) if desired_positive else all(value > 0 for value in values)
    if aligned:
        return "aligned"
    if conflict:
        return "conflict"
    return "mixed"


def _return_direction(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value > 0:
        return "bullish"
    if value < 0:
        return "bearish"
    return "flat"


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
