from __future__ import annotations

from decimal import Decimal
from statistics import median
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


class VwapReclaimEvaluator:
    strategy_type = "vwap_reclaim"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        atr_period = int(config.get("atr_period") or 14)
        return RequiredFeatures(
            timeframe=str(config.get("timeframe") or "5Min"),
            lookback_minutes=int(config.get("lookback_minutes") or 390),
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
        vwap_values = _vwap(indicators)
        latest_vwap = vwap_values[-1]
        previous_vwap = vwap_values[-2]
        if latest_vwap is None or previous_vwap is None:
            return None

        latest_close = float(latest.close)
        previous_close = float(previous.close)
        latest_open = float(latest.open)
        atr_period = int(config.get("atr_period") or 14)
        min_reclaim_percent = float(config.get("min_reclaim_percent") or 0.03)
        max_distance_percent = float(config.get("max_distance_percent") or 1.25)
        distance_percent = abs(latest_close - latest_vwap) / latest_vwap * 100
        if distance_percent > max_distance_percent:
            return None

        direction: str | None = None
        signal_type: str | None = None
        if (
            previous_close <= previous_vwap
            and latest_close > latest_vwap * (1 + min_reclaim_percent / 100)
            and latest_close >= latest_open
        ):
            direction = "bullish"
            signal_type = "vwap_reclaim"
        elif (
            previous_close >= previous_vwap
            and latest_close < latest_vwap * (1 - min_reclaim_percent / 100)
            and latest_close <= latest_open
        ):
            direction = "bearish"
            signal_type = "vwap_rejection"

        if direction is None or signal_type is None:
            return None

        score = Decimal("0.58")
        if distance_percent <= max_distance_percent * 0.5:
            score += Decimal("0.05")
        dedupe_minutes = int(config.get("dedupe_minutes") or 120)
        vwap_slope_percent = (
            ((latest_vwap - previous_vwap) / previous_vwap) * 100
            if previous_vwap
            else None
        )
        validation = {
            **price_action_features(candles, direction=direction),
            **atr_features(
                indicators,
                candles,
                period=atr_period,
                reference_price=latest_vwap,
                reference_label="vwap",
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
            rationale=f"{symbol.upper()} {signal_type.replace('_', ' ')} near VWAP",
            features={
                "timeframe": candles.timeframe,
                "lookback_minutes": int(config.get("lookback_minutes") or 390),
                "latest_close": str(latest.close),
                "previous_close": str(previous.close),
                "latest_vwap": feature_decimal(latest_vwap),
                "previous_vwap": feature_decimal(previous_vwap),
                "vwap_slope_percent": feature_decimal(vwap_slope_percent),
                "distance_percent": feature_decimal(distance_percent),
                "dedupe_minutes": dedupe_minutes,
                **validation,
            },
            dedupe_key=f"{symbol.upper()}:{self.strategy_type}:{signal_type}:{direction}",
        )


class OpeningRangeBreakoutEvaluator:
    strategy_type = "opening_range_breakout"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        atr_period = int(config.get("atr_period") or 14)
        return RequiredFeatures(
            timeframe=str(config.get("timeframe") or "5Min"),
            lookback_minutes=int(config.get("lookback_minutes") or 390),
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
        range_candles = int(config.get("range_candles") or 3)
        if len(candles.candles) <= range_candles + 1:
            return None

        opening = candles.candles[:range_candles]
        latest = candles.candles[-1]
        previous = candles.candles[-2]
        range_high = max(float(candle.high) for candle in opening)
        range_low = min(float(candle.low) for candle in opening)
        breakout_buffer_percent = float(config.get("breakout_buffer_percent") or 0.05)
        max_breakout_distance_percent = float(config.get("max_breakout_distance_percent") or 2.0)
        latest_close = float(latest.close)
        previous_close = float(previous.close)
        latest_open = float(latest.open)
        atr_period = int(config.get("atr_period") or 14)

        direction: str | None = None
        signal_type: str | None = None
        breakout_level: float | None = None
        if previous_close <= range_high and latest_close > range_high * (1 + breakout_buffer_percent / 100):
            direction = "bullish"
            signal_type = "opening_range_breakout"
            breakout_level = range_high
            if latest_close < latest_open:
                return None
        elif previous_close >= range_low and latest_close < range_low * (1 - breakout_buffer_percent / 100):
            direction = "bearish"
            signal_type = "opening_range_breakdown"
            breakout_level = range_low
            if latest_close > latest_open:
                return None

        if direction is None or signal_type is None or breakout_level is None:
            return None
        distance_percent = abs(latest_close - breakout_level) / breakout_level * 100
        if distance_percent > max_breakout_distance_percent:
            return None

        dedupe_minutes = int(config.get("dedupe_minutes") or 240)
        validation = {
            **price_action_features(candles, direction=direction),
            **atr_features(
                indicators,
                candles,
                period=atr_period,
                reference_price=breakout_level,
                reference_label="breakout",
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
            confidence=confidence(Decimal("0.63"), maximum=Decimal("0.85")),
            rationale=f"{symbol.upper()} broke {'above' if direction == 'bullish' else 'below'} its opening range",
            features={
                "timeframe": candles.timeframe,
                "range_candles": range_candles,
                "range_high": feature_decimal(range_high),
                "range_low": feature_decimal(range_low),
                "latest_close": str(latest.close),
                "breakout_buffer_percent": feature_decimal(breakout_buffer_percent),
                "distance_percent": feature_decimal(distance_percent),
                "dedupe_minutes": dedupe_minutes,
                **validation,
            },
            dedupe_key=f"{symbol.upper()}:{self.strategy_type}:{signal_type}:{direction}",
        )


class RelativeStrengthEvaluator:
    strategy_type = "relative_strength"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        atr_period = int(config.get("atr_period") or 14)
        return RequiredFeatures(
            timeframe=str(config.get("timeframe") or "5Min"),
            lookback_minutes=int(config.get("lookback_minutes") or 240),
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
        peer_returns = _peer_returns(market_regime)
        symbol_return = peer_returns.get(symbol.upper())
        if symbol_return is None or len(peer_returns) < 2:
            return None
        peer_median = median(peer_returns.values())
        edge = symbol_return - peer_median
        min_edge_percent = float(config.get("min_edge_percent") or 0.35)
        if edge >= min_edge_percent:
            direction = "bullish"
            signal_type = "relative_strength_leader"
        elif edge <= -min_edge_percent:
            direction = "bearish"
            signal_type = "relative_strength_laggard"
        else:
            return None

        dedupe_minutes = int(config.get("dedupe_minutes") or 240)
        atr_period = int(config.get("atr_period") or 14)
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
            confidence=confidence(Decimal("0.60") + Decimal(str(min(abs(edge), 1.5))) / Decimal("20"), maximum=Decimal("0.80")),
            rationale=f"{symbol.upper()} is a {direction} relative-strength outlier versus the trading universe",
            features={
                "timeframe": candles.timeframe,
                "lookback_minutes": int(config.get("lookback_minutes") or 240),
                "symbol_return_percent": feature_decimal(symbol_return),
                "peer_median_return_percent": feature_decimal(peer_median),
                "relative_edge_percent": feature_decimal(edge),
                "dedupe_minutes": dedupe_minutes,
                **validation,
            },
            dedupe_key=f"{symbol.upper()}:{self.strategy_type}:{signal_type}:{direction}",
        )


class TimeSeriesMomentumEvaluator:
    strategy_type = "time_series_momentum"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        average_window = int(config.get("trend_average_window") or 20)
        atr_period = int(config.get("atr_period") or 14)
        return RequiredFeatures(
            timeframe=str(config.get("timeframe") or "15Min"),
            lookback_minutes=int(config.get("lookback_minutes") or 1440),
            ema_periods=frozenset({average_window}),
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
        lookback_bars = int(config.get("lookback_bars") or min(26, len(candles.candles) - 1))
        if len(candles.candles) <= lookback_bars:
            return None
        latest = candles.candles[-1]
        reference = candles.candles[-1 - lookback_bars]
        trend_return = percent_change(float(latest.close), float(reference.close))
        if trend_return is None:
            return None
        min_trend_percent = float(config.get("min_trend_percent") or 1.0)
        average_window = int(config.get("trend_average_window") or 20)
        atr_period = int(config.get("atr_period") or 14)
        averages = indicators.ema(average_window)
        latest_average = averages[-1] if averages else None
        if latest_average is None:
            return None

        latest_close = float(latest.close)
        if trend_return >= min_trend_percent and latest_close > latest_average:
            direction = "bullish"
            signal_type = "time_series_momentum_uptrend"
        elif trend_return <= -min_trend_percent and latest_close < latest_average:
            direction = "bearish"
            signal_type = "time_series_momentum_downtrend"
        else:
            return None

        dedupe_minutes = int(config.get("dedupe_minutes") or 360)
        validation = {
            **price_action_features(candles, direction=direction),
            **atr_features(
                indicators,
                candles,
                period=atr_period,
                average_price=latest_average,
                average_label="trend_average",
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
            confidence=confidence(Decimal("0.62"), maximum=Decimal("0.84")),
            rationale=f"{symbol.upper()} has persistent {direction} time-series momentum",
            features={
                "timeframe": candles.timeframe,
                "lookback_bars": lookback_bars,
                "trend_return_percent": feature_decimal(trend_return),
                "trend_average_window": average_window,
                "trend_average": feature_decimal(latest_average),
                "dedupe_minutes": dedupe_minutes,
                **validation,
            },
            dedupe_key=f"{symbol.upper()}:{self.strategy_type}:{signal_type}:{direction}",
        )


class MarketRegimeFilterEvaluator:
    strategy_type = "market_regime_filter"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        atr_period = int(config.get("atr_period") or 14)
        return RequiredFeatures(
            timeframe=str(config.get("timeframe") or "5Min"),
            lookback_minutes=int(config.get("lookback_minutes") or 240),
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
        peer_returns = _peer_returns(market_regime)
        benchmark_symbols = [str(item).upper() for item in config.get("benchmark_symbols", ["SPY", "QQQ"])]
        benchmark_values = [peer_returns[item] for item in benchmark_symbols if item in peer_returns]
        symbol_return = peer_returns.get(symbol.upper())
        if symbol_return is None or not benchmark_values:
            return None
        benchmark_return = sum(benchmark_values) / len(benchmark_values)
        min_benchmark_percent = float(config.get("min_benchmark_percent") or 0.20)
        min_symbol_alignment_percent = float(config.get("min_symbol_alignment_percent") or 0.05)

        if benchmark_return >= min_benchmark_percent and symbol_return >= min_symbol_alignment_percent:
            direction = "bullish"
            signal_type = "risk_on_regime_alignment"
        elif benchmark_return <= -min_benchmark_percent and symbol_return <= -min_symbol_alignment_percent:
            direction = "bearish"
            signal_type = "risk_off_regime_alignment"
        else:
            return None

        dedupe_minutes = int(config.get("dedupe_minutes") or 360)
        atr_period = int(config.get("atr_period") or 14)
        validation = {
            **price_action_features(candles, direction=direction),
            **atr_features(indicators, candles, period=atr_period),
        }
        validation["validation_flags"] = validation_flags(validation)
        return SignalCandidate(
            symbol=symbol.upper(),
            strategy_type=self.strategy_type,
            signal_type=signal_type,
            direction=direction,
            confidence=confidence(Decimal("0.58"), maximum=Decimal("0.78")),
            rationale=f"{symbol.upper()} is aligned with the broad-market {direction} regime",
            features={
                "timeframe": candles.timeframe,
                "benchmark_symbols": benchmark_symbols,
                "benchmark_return_percent": feature_decimal(benchmark_return),
                "symbol_return_percent": feature_decimal(symbol_return),
                "dedupe_minutes": dedupe_minutes,
                **validation,
            },
            dedupe_key=f"{symbol.upper()}:{self.strategy_type}:{signal_type}:{direction}",
        )


class PairsRelativeValueEvaluator:
    strategy_type = "pairs_relative_value"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        atr_period = int(config.get("atr_period") or 14)
        return RequiredFeatures(
            timeframe=str(config.get("timeframe") or "5Min"),
            lookback_minutes=int(config.get("lookback_minutes") or 240),
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
        peer_returns = _peer_returns(market_regime)
        symbol_name = symbol.upper()
        benchmark = _pair_benchmark(symbol_name, config)
        symbol_return = peer_returns.get(symbol_name)
        benchmark_return = peer_returns.get(benchmark)
        if symbol_return is None or benchmark_return is None:
            return None
        spread = symbol_return - benchmark_return
        min_spread_percent = float(config.get("min_spread_percent") or 0.50)
        mode = str(config.get("mode") or "mean_reversion").lower()

        if mode == "trend_following":
            if spread >= min_spread_percent:
                direction = "bullish"
                signal_type = "pair_relative_strength_continuation"
            elif spread <= -min_spread_percent:
                direction = "bearish"
                signal_type = "pair_relative_weakness_continuation"
            else:
                return None
        else:
            if spread <= -min_spread_percent:
                direction = "bullish"
                signal_type = "pair_relative_value_recovery"
            elif spread >= min_spread_percent:
                direction = "bearish"
                signal_type = "pair_relative_value_fade"
            else:
                return None

        dedupe_minutes = int(config.get("dedupe_minutes") or 360)
        atr_period = int(config.get("atr_period") or 14)
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
            symbol=symbol_name,
            strategy_type=self.strategy_type,
            signal_type=signal_type,
            direction=direction,
            confidence=confidence(Decimal("0.59"), maximum=Decimal("0.80")),
            rationale=f"{symbol_name} pair spread versus {benchmark} reached {spread:.2f}%",
            features={
                "timeframe": candles.timeframe,
                "benchmark_symbol": benchmark,
                "symbol_return_percent": feature_decimal(symbol_return),
                "benchmark_return_percent": feature_decimal(benchmark_return),
                "spread_percent": feature_decimal(spread),
                "mode": mode,
                "execution_note": "signal_only_until_pair_execution_supported",
                "dedupe_minutes": dedupe_minutes,
                **validation,
            },
            dedupe_key=f"{symbol_name}:{self.strategy_type}:{signal_type}:{direction}:{benchmark}",
        )


class OptionsSpreadCandidateEvaluator:
    strategy_type = "options_spread_candidate"

    def required_features(self, config: dict[str, Any]) -> RequiredFeatures:
        return RequiredFeatures(
            timeframe=str(config.get("timeframe") or "5Min"),
            lookback_minutes=int(config.get("lookback_minutes") or 240),
            atr_periods=frozenset({int(config.get("atr_period") or 14)}),
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
        atr_period = int(config.get("atr_period") or 14)
        atr_values = indicators.atr(atr_period)
        latest_atr = atr_values[-1] if atr_values else None
        if latest_atr is None:
            return None
        latest = candles.candles[-1]
        reference = candles.candles[max(0, len(candles.candles) - 13)]
        move_percent = percent_change(float(latest.close), float(reference.close))
        if move_percent is None:
            return None
        atr_percent = latest_atr / float(latest.close) * 100 if latest.close > 0 else None
        min_move_percent = float(config.get("min_move_percent") or 0.50)
        min_atr_percent = float(config.get("min_atr_percent") or 0.35)
        if atr_percent is None or atr_percent < min_atr_percent:
            return None
        if move_percent >= min_move_percent:
            direction = "bullish"
            signal_type = "debit_call_spread_candidate"
        elif move_percent <= -min_move_percent:
            direction = "bearish"
            signal_type = "debit_put_spread_candidate"
        else:
            return None

        dedupe_minutes = int(config.get("dedupe_minutes") or 360)
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
            confidence=confidence(Decimal("0.57"), maximum=Decimal("0.78")),
            rationale=f"{symbol.upper()} has directional movement and volatility suitable for a debit spread candidate",
            features={
                "timeframe": candles.timeframe,
                "move_percent": feature_decimal(move_percent),
                "atr_period": atr_period,
                "atr_percent": feature_decimal(atr_percent),
                "execution_note": "signal_only_until_multileg_orders_are_supported",
                "dedupe_minutes": dedupe_minutes,
                **validation,
            },
            dedupe_key=f"{symbol.upper()}:{self.strategy_type}:{signal_type}:{direction}",
        )


def _vwap(indicators: IndicatorFrame) -> list[float | None]:
    cumulative_pv = 0.0
    cumulative_volume = 0.0
    values: list[float | None] = []
    for high, low, close, volume in zip(
        indicators.high,
        indicators.low,
        indicators.close,
        indicators.volume,
        strict=True,
    ):
        if volume is None or volume <= 0:
            values.append(values[-1] if values else None)
            continue
        typical = (high + low + close) / 3
        cumulative_pv += typical * volume
        cumulative_volume += volume
        values.append(cumulative_pv / cumulative_volume if cumulative_volume > 0 else None)
    return values


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


def _pair_benchmark(symbol: str, config: dict[str, Any]) -> str:
    pair_map = config.get("pair_benchmarks")
    if isinstance(pair_map, dict):
        raw_value = pair_map.get(symbol)
        if isinstance(raw_value, str) and raw_value.strip():
            return raw_value.strip().upper()
    raw_benchmark = config.get("benchmark_symbol")
    if isinstance(raw_benchmark, str) and raw_benchmark.strip():
        benchmark = raw_benchmark.strip().upper()
        if benchmark != symbol:
            return benchmark
    return "QQQ" if symbol == "SPY" else "SPY"
