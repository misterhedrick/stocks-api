from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.core.config import settings
from app.db.models import JobRun, Signal, Strategy
from app.integrations.alpaca import (
    AlpacaLatestStockQuote,
    AlpacaMarketDataClient,
    AlpacaStockBars,
)
from app.services.audit_logs import record_audit_log
from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.base import SignalCandidate
from app.services.signals.evaluators.registry import get_evaluator
from app.services.signals.indicators import IndicatorFrame

DEFAULT_DEDUPE_MINUTES = 240
DEDUPE_STATUSES = ("new", "previewed", "submitted")


@dataclass(slots=True)
class SignalScanResult:
    job_run: JobRun
    strategies_seen: int
    strategies_scanned: int
    signals_created: int
    signals_skipped: int
    errors: list[str]
    no_signal_reasons: list[str]
    created_signal_ids: list[uuid.UUID]


def scan_signals(
    db: Session,
    *,
    limit: int = 100,
    symbol: str | None = None,
    market_data_client: AlpacaMarketDataClient | None = None,
) -> SignalScanResult:
    started_at = datetime.now(timezone.utc)
    symbol_filter = _normalize_symbol(symbol)
    job_run = JobRun(
        job_name="scan_signals",
        status="running",
        started_at=started_at,
        details={"symbol": symbol_filter} if symbol_filter else {},
    )
    db.add(job_run)
    db.flush()

    try:
        strategies = list(
            db.scalars(
                select(Strategy)
                .where(Strategy.is_active == True)  # noqa: E712
                .order_by(Strategy.created_at.asc())
                .limit(limit)
            )
        )

        strategies_scanned = 0
        signals_created = 0
        signals_skipped = 0
        created_signal_ids: list[uuid.UUID] = []
        errors: list[str] = []
        no_signal_reasons: list[str] = []

        for strategy in strategies:
            signal_specs = _signal_specs_from_strategy(strategy)
            signal_specs = _filter_signal_specs_for_symbol(signal_specs, symbol_filter)
            try:
                signal_specs.extend(
                    _signal_specs_from_scanner(
                        strategy,
                        symbol_filter=symbol_filter,
                        market_data_client=market_data_client,
                        no_signal_reasons=no_signal_reasons,
                    )
                )
            except ValueError as exc:
                signals_skipped += 1
                errors.append(f"{strategy.name}.scanner: {exc}")
                logger.warning("Signal scanner config error for strategy %r: %s", strategy.name, exc)
            except Exception as exc:
                signals_skipped += 1
                errors.append(f"{strategy.name}.scanner: {exc.__class__.__name__}: {exc}")
                logger.error(
                    "Signal scanner unexpected error for strategy %r: %s: %s",
                    strategy.name,
                    exc.__class__.__name__,
                    exc,
                )

            if not signal_specs:
                if "scanner" not in strategy.config and "scan_signals" not in strategy.config:
                    no_signal_reasons.append(
                        f"{strategy.name}: no scan_signals or scanner config"
                    )
                continue

            strategies_scanned += 1
            for index, signal_spec in enumerate(signal_specs):
                try:
                    signal = _signal_from_spec(strategy, signal_spec)
                except ValueError as exc:
                    signals_skipped += 1
                    errors.append(f"{strategy.name}[{index}]: {exc}")
                    continue

                if _has_recent_duplicate_signal(db, signal, signal_spec):
                    signals_skipped += 1
                    errors.append(
                        f"{strategy.name}[{index}]: duplicate signal suppressed for "
                        f"{signal.symbol} {signal.signal_type} {signal.direction}"
                    )
                    continue

                db.add(signal)
                db.flush()
                record_audit_log(
                    db,
                    event_type="signal.created",
                    entity_type="signal",
                    entity_id=signal.id,
                    message="Signal created by scanner",
                    payload={
                        "strategy_id": str(strategy.id),
                        "strategy_name": strategy.name,
                        "symbol": signal.symbol,
                        "underlying_symbol": signal.underlying_symbol,
                        "signal_type": signal.signal_type,
                        "direction": signal.direction,
                        "confidence": str(signal.confidence)
                        if signal.confidence is not None
                        else None,
                        "source": "scan_signals",
                    },
                )
                signals_created += 1
                created_signal_ids.append(signal.id)

        details = {
            "symbol": symbol_filter,
            "strategies_seen": len(strategies),
            "strategies_scanned": strategies_scanned,
            "signals_created": signals_created,
            "signals_skipped": signals_skipped,
            "errors": errors,
            "no_signal_reasons": no_signal_reasons,
            "created_signal_ids": [str(signal_id) for signal_id in created_signal_ids],
        }
        job_run.status = "succeeded"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = details
        job_run.error = None
        db.add(job_run)
        record_audit_log(
            db,
            event_type="signal_scan.succeeded",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Signal scan succeeded",
            payload=details,
        )
        db.commit()
        db.refresh(job_run)

        return SignalScanResult(
            job_run=job_run,
            strategies_seen=len(strategies),
            strategies_scanned=strategies_scanned,
            signals_created=signals_created,
            signals_skipped=signals_skipped,
            errors=errors,
            no_signal_reasons=no_signal_reasons,
            created_signal_ids=created_signal_ids,
        )
    except Exception as exc:
        db.rollback()
        job_run.status = "failed"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = {}
        job_run.error = f"{exc.__class__.__name__}: {exc}"
        db.add(job_run)
        record_audit_log(
            db,
            event_type="signal_scan.failed",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Signal scan failed",
            payload={"error": job_run.error},
        )
        db.commit()
        db.refresh(job_run)
        raise


def _signal_specs_from_strategy(strategy: Strategy) -> list[dict[str, Any]]:
    signal_specs = strategy.config.get("scan_signals")
    if isinstance(signal_specs, list):
        return [item for item in signal_specs if isinstance(item, dict)]
    return []


def _signal_specs_from_scanner(
    strategy: Strategy,
    *,
    symbol_filter: str | None = None,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    scanner_config = strategy.config.get("scanner")
    if scanner_config is None:
        return []
    if not isinstance(scanner_config, dict):
        raise ValueError("scanner must be an object")

    scanner_type = scanner_config.get("type")
    symbols = scanner_config.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("scanner.symbols must be a non-empty list")

    clean_symbols = []
    for raw_symbol in symbols:
        if not isinstance(raw_symbol, str) or not raw_symbol.strip():
            raise ValueError("scanner.symbols must contain only non-empty strings")
        clean_symbols.append(raw_symbol.strip().upper())
    if symbol_filter is not None:
        if symbol_filter not in clean_symbols:
            no_signal_reasons.append(
                f"{strategy.name}: scanner does not include symbol {symbol_filter}"
            )
            return []
        clean_symbols = [symbol_filter]

    if scanner_type == "price_threshold":
        return _price_threshold_signal_specs(
            strategy.name,
            scanner_config,
            clean_symbols,
            market_data_client=market_data_client,
            no_signal_reasons=no_signal_reasons,
        )
    if scanner_type == "percent_change":
        return _percent_change_signal_specs(
            strategy.name,
            scanner_config,
            clean_symbols,
            market_data_client=market_data_client,
            no_signal_reasons=no_signal_reasons,
        )
    if scanner_type == "moving_average":
        return _moving_average_evaluator_signal_specs(
            strategy.name,
            scanner_config,
            clean_symbols,
            market_data_client=market_data_client,
            no_signal_reasons=no_signal_reasons,
        )
    if scanner_type == "trend_confirmation":
        return _trend_confirmation_signal_specs(
            strategy.name,
            scanner_config,
            clean_symbols,
            market_data_client=market_data_client,
            no_signal_reasons=no_signal_reasons,
        )
    if scanner_type == "momentum_rate_of_change":
        return _momentum_rate_of_change_signal_specs(
            strategy.name,
            scanner_config,
            clean_symbols,
            market_data_client=market_data_client,
            no_signal_reasons=no_signal_reasons,
        )
    if scanner_type == "rsi_reversal":
        return _rsi_reversal_signal_specs(
            strategy.name,
            scanner_config,
            clean_symbols,
            market_data_client=market_data_client,
            no_signal_reasons=no_signal_reasons,
        )
    if scanner_type == "macd_crossover":
        return _macd_crossover_signal_specs(
            strategy.name,
            scanner_config,
            clean_symbols,
            market_data_client=market_data_client,
            no_signal_reasons=no_signal_reasons,
        )
    if scanner_type == "mean_reversion":
        return _mean_reversion_signal_specs(
            strategy.name,
            scanner_config,
            clean_symbols,
            market_data_client=market_data_client,
            no_signal_reasons=no_signal_reasons,
        )
    if scanner_type == "breakout_price_threshold":
        return _breakout_price_threshold_signal_specs(
            strategy.name,
            scanner_config,
            clean_symbols,
            market_data_client=market_data_client,
            no_signal_reasons=no_signal_reasons,
        )
    if scanner_type == "volume_confirmed_breakout":
        return _volume_confirmed_breakout_signal_specs(
            strategy.name,
            scanner_config,
            clean_symbols,
            market_data_client=market_data_client,
            no_signal_reasons=no_signal_reasons,
        )
    if scanner_type == "volatility_squeeze":
        return _volatility_squeeze_signal_specs(
            strategy.name,
            scanner_config,
            clean_symbols,
            market_data_client=market_data_client,
            no_signal_reasons=no_signal_reasons,
        )
    if scanner_type == "support_resistance":
        return _support_resistance_signal_specs(
            strategy.name,
            scanner_config,
            clean_symbols,
            market_data_client=market_data_client,
            no_signal_reasons=no_signal_reasons,
        )
    raise ValueError(
        "scanner.type must be price_threshold, percent_change, moving_average, "
        "trend_confirmation, momentum_rate_of_change, rsi_reversal, macd_crossover, "
        "mean_reversion, breakout_price_threshold, volume_confirmed_breakout, "
        "volatility_squeeze, or support_resistance"
    )


def _normalize_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    normalized = symbol.strip().upper()
    return normalized or None


def _filter_signal_specs_for_symbol(
    signal_specs: list[dict[str, Any]],
    symbol: str | None,
) -> list[dict[str, Any]]:
    if symbol is None:
        return signal_specs
    filtered = []
    for signal_spec in signal_specs:
        spec_symbol = signal_spec.get("underlying_symbol") or signal_spec.get("symbol")
        if isinstance(spec_symbol, str) and spec_symbol.strip().upper() == symbol:
            filtered.append(signal_spec)
    return filtered


def _price_threshold_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    price_above = _optional_decimal(scanner_config, "price_above")
    price_below = _optional_decimal(scanner_config, "price_below")
    if price_above is None and price_below is None:
        raise ValueError("scanner requires price_above or price_below")

    feed = scanner_config.get("data_feed", "iex")
    if not isinstance(feed, str) or not feed.strip():
        raise ValueError("scanner.data_feed must be a non-empty string")

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    quotes = client.get_latest_stock_quotes(symbols, feed=feed.strip())
    signal_specs: list[dict[str, Any]] = []

    for symbol in symbols:
        latest_quote = quotes.get(symbol)
        if latest_quote is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no latest stock quote returned"
            )
            continue

        price = _price_from_quote(latest_quote)
        if price is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: stock quote had no usable bid or ask price"
            )
            continue

        triggered = (
            (price_above is not None and price >= price_above)
            or (price_below is not None and price <= price_below)
        )
        if not triggered:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: price {price} did not cross configured threshold"
            )
            continue

        signal_specs.append(
            {
                "symbol": symbol,
                "underlying_symbol": symbol,
                "signal_type": _scanner_string(
                    scanner_config,
                    "signal_type",
                    default="price_threshold",
                ),
                "direction": _scanner_string(
                    scanner_config,
                    "direction",
                    default="bullish" if price_above is not None else "bearish",
                ),
                "confidence": scanner_config.get("confidence"),
                "rationale": _scanner_string(
                    scanner_config,
                    "rationale",
                    default="Price threshold scanner triggered",
                ),
                "market_context": {
                    "source": "scanner.price_threshold",
                    "price": str(price),
                    "price_above": str(price_above) if price_above is not None else None,
                    "price_below": str(price_below) if price_below is not None else None,
                    "data_feed": feed.strip(),
                    "quote": _stock_quote_context(latest_quote),
                },
                "dedupe_minutes": scanner_config.get(
                    "dedupe_minutes",
                    DEFAULT_DEDUPE_MINUTES,
                ),
            }
        )

    return signal_specs


def _percent_change_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    change_above = _optional_decimal(scanner_config, "change_above_percent")
    change_below = _optional_decimal(scanner_config, "change_below_percent")
    if change_above is None and change_below is None:
        raise ValueError(
            "scanner requires change_above_percent or change_below_percent"
        )

    lookback_minutes = _positive_int(scanner_config, "lookback_minutes", default=30)
    timeframe = _scanner_string(scanner_config, "timeframe", default="1Min")
    feed = _scanner_string(scanner_config, "data_feed", default="iex")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes)

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    bars_by_symbol = client.get_stock_bars(
        symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=feed,
        limit=max(lookback_minutes + 5, 10),
    )
    signal_specs: list[dict[str, Any]] = []

    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no stock bars returned for lookback window"
            )
            continue
        if len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: fewer than 2 stock bars returned for lookback window"
            )
            continue

        first_bar = stock_bars.bars[0]
        last_bar = stock_bars.bars[-1]
        if first_bar.close == Decimal("0"):
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: first close was 0, percent change unavailable"
            )
            continue

        change_percent = (
            (last_bar.close - first_bar.close) / first_bar.close
        ) * Decimal("100")
        triggered = (
            (change_above is not None and change_percent >= change_above)
            or (change_below is not None and change_percent <= change_below)
        )
        if not triggered:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: percent change {change_percent} did not cross configured threshold"
            )
            continue

        signal_specs.append(
            {
                "symbol": symbol,
                "underlying_symbol": symbol,
                "signal_type": _scanner_string(
                    scanner_config,
                    "signal_type",
                    default="percent_change",
                ),
                "direction": _scanner_string(
                    scanner_config,
                    "direction",
                    default="bullish" if change_above is not None else "bearish",
                ),
                "confidence": scanner_config.get("confidence"),
                "rationale": _scanner_string(
                    scanner_config,
                    "rationale",
                    default="Percent change scanner triggered",
                ),
                "market_context": {
                    "source": "scanner.percent_change",
                    "lookback_minutes": lookback_minutes,
                    "timeframe": timeframe,
                    "data_feed": feed,
                    "first_close": str(first_bar.close),
                    "last_close": str(last_bar.close),
                    "change_percent": str(change_percent),
                    "change_above_percent": str(change_above)
                    if change_above is not None
                    else None,
                    "change_below_percent": str(change_below)
                    if change_below is not None
                    else None,
                    "bars": _stock_bars_context(stock_bars),
                },
                "dedupe_minutes": scanner_config.get(
                    "dedupe_minutes",
                    DEFAULT_DEDUPE_MINUTES,
                ),
            }
        )

    return signal_specs


def _moving_average_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    short_window = _positive_int(scanner_config, "short_window", default=5)
    long_window = _positive_int(scanner_config, "long_window", default=20)
    if short_window >= long_window:
        raise ValueError("scanner.short_window must be less than scanner.long_window")

    trigger = _scanner_string(scanner_config, "trigger", default="bullish_cross")
    if trigger not in {"bullish_cross", "bearish_cross", "bullish_trend", "bearish_trend"}:
        raise ValueError(
            "scanner.trigger must be bullish_cross, bearish_cross, bullish_trend, or bearish_trend"
        )

    lookback_minutes = _positive_int(
        scanner_config,
        "lookback_minutes",
        default=max(long_window + 5, 30),
    )
    timeframe = _scanner_string(scanner_config, "timeframe", default="1Min")
    feed = _scanner_string(scanner_config, "data_feed", default="iex")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes)

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    market_regime = _market_regime_config(scanner_config)
    requested_symbols = _unique_symbols([*symbols, *market_regime.get("symbols", [])])
    bars_by_symbol = client.get_stock_bars(
        requested_symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=feed,
        limit=max(lookback_minutes + 5, long_window + 2),
    )
    signal_specs: list[dict[str, Any]] = []

    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no stock bars returned for moving average window"
            )
            continue
        if len(stock_bars.bars) < long_window + 1:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: fewer than {long_window + 1} stock bars returned for moving average window"
            )
            continue

        closes = [bar.close for bar in stock_bars.bars]
        # Compute averages for the current bar and the bar immediately before it
        # (windows shifted by 1). The one-bar shift is intentional: comparing
        # current vs previous detects crossovers (e.g. short crosses above long).
        previous_short = _average(closes[-short_window - 1 : -1])
        previous_long = _average(closes[-long_window - 1 : -1])
        current_short = _average(closes[-short_window:])
        current_long = _average(closes[-long_window:])

        first_bar = stock_bars.bars[0]
        last_bar = stock_bars.bars[-1]
        change_percent = _change_percent(first_bar.close, last_bar.close)

        triggered = (
            (
                trigger == "bullish_cross"
                and previous_short <= previous_long
                and current_short > current_long
            )
            or (
                trigger == "bearish_cross"
                and previous_short >= previous_long
                and current_short < current_long
            )
            or (trigger == "bullish_trend" and current_short > current_long)
            or (trigger == "bearish_trend" and current_short < current_long)
        )
        if not triggered:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: moving average trigger {trigger} was not met"
            )
            continue

        bullish = trigger.startswith("bullish")
        if _bool_config(scanner_config, "require_short_average_slope", default=False):
            slope_confirmed = (
                current_short > previous_short if bullish else current_short < previous_short
            )
            if not slope_confirmed:
                no_signal_reasons.append(
                    f"{strategy_name}.{symbol}: short moving average slope did not confirm {trigger}"
                )
                continue

        if _bool_config(scanner_config, "require_price_confirmation", default=False):
            price_confirmed = (
                last_bar.close > current_short if bullish else last_bar.close < current_short
            )
            if not price_confirmed:
                no_signal_reasons.append(
                    f"{strategy_name}.{symbol}: latest close did not confirm {trigger}"
                )
                continue

        min_change_percent = _optional_decimal(scanner_config, "min_change_percent")
        if min_change_percent is not None:
            momentum_confirmed = (
                change_percent >= min_change_percent
                if bullish
                else change_percent <= -min_change_percent
            )
            if not momentum_confirmed:
                no_signal_reasons.append(
                    f"{strategy_name}.{symbol}: percent change {change_percent} did not confirm {trigger}"
                )
                continue

        market_regime_context = _market_regime_context(
            bars_by_symbol,
            market_regime,
            direction="bullish" if bullish else "bearish",
        )
        if market_regime_context["enabled"] and not market_regime_context["passed"]:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: market regime did not confirm {trigger}: "
                f"{market_regime_context['reason']}"
            )
            continue

        signal_specs.append(
            {
                "symbol": symbol,
                "underlying_symbol": symbol,
                "signal_type": _scanner_string(
                    scanner_config,
                    "signal_type",
                    default="moving_average",
                ),
                "direction": _scanner_string(
                    scanner_config,
                    "direction",
                    default="bullish" if bullish else "bearish",
                ),
                "confidence": scanner_config.get("confidence"),
                "rationale": _scanner_string(
                    scanner_config,
                    "rationale",
                    default="Moving average scanner triggered",
                ),
                "market_context": {
                    "source": "scanner.moving_average",
                    "trigger": trigger,
                    "short_window": short_window,
                    "long_window": long_window,
                    "timeframe": timeframe,
                    "data_feed": feed,
                    "previous_short_average": str(previous_short),
                    "previous_long_average": str(previous_long),
                    "current_short_average": str(current_short),
                    "current_long_average": str(current_long),
                    "short_average_slope": str(current_short - previous_short),
                    "first_close": str(first_bar.close),
                    "last_close": str(last_bar.close),
                    "change_percent": str(change_percent),
                    "min_change_percent": str(min_change_percent)
                    if min_change_percent is not None
                    else None,
                    "market_regime": market_regime_context,
                    "bars": _stock_bars_context(stock_bars),
                },
                "dedupe_minutes": scanner_config.get(
                    "dedupe_minutes",
                    DEFAULT_DEDUPE_MINUTES,
                ),
            }
        )

    return signal_specs


def _moving_average_evaluator_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping moving average evaluator"
        )
        return []
    if not settings.moving_average_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: MOVING_AVERAGE_EVALUATOR_ENABLED=false, skipping moving average evaluator"
        )
        return []

    short_window = _positive_int(scanner_config, "short_window", default=5)
    long_window = _positive_int(scanner_config, "long_window", default=20)
    if short_window >= long_window:
        raise ValueError("scanner.short_window must be less than scanner.long_window")

    evaluator = get_evaluator("moving_average")
    if evaluator is None:
        return []

    features = evaluator.required_features(scanner_config)
    timeframe = features.timeframe
    lookback_minutes = features.lookback_minutes
    feed = _scanner_string(scanner_config, "data_feed", default="iex")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes + 5)

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    bars_by_symbol = client.get_stock_bars(
        symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=feed,
        limit=max(lookback_minutes + 10, 20),
    )

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 3:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for moving average evaluator"
            )
            continue

        candle_frame = _candle_frame_from_stock_bars(stock_bars, timeframe)
        indicator_frame = IndicatorFrame(
            close=candle_frame.closes,
            high=candle_frame.highs,
            low=candle_frame.lows,
            volume=candle_frame.volumes,
        )
        candidate = evaluator.evaluate(
            symbol=symbol,
            config=scanner_config,
            candles=candle_frame,
            indicators=indicator_frame,
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: moving average evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _trend_confirmation_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    direction = _scanner_string(scanner_config, "direction", default="bullish")
    if direction not in {"bullish", "bearish"}:
        raise ValueError("scanner.direction must be bullish or bearish")

    short_window = _positive_int(scanner_config, "short_window", default=5)
    long_window = _positive_int(scanner_config, "long_window", default=20)
    if short_window >= long_window:
        raise ValueError("scanner.short_window must be less than scanner.long_window")

    min_change_percent = _optional_decimal(scanner_config, "min_change_percent")
    if min_change_percent is None:
        min_change_percent = Decimal("0.25")
    if min_change_percent <= Decimal("0"):
        raise ValueError("scanner.min_change_percent must be greater than 0")

    lookback_minutes = _positive_int(scanner_config, "lookback_minutes", default=1440)
    timeframe = _scanner_string(scanner_config, "timeframe", default="5Min")
    feed = _scanner_string(scanner_config, "data_feed", default="iex")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes)

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    market_regime = _market_regime_config(scanner_config)
    requested_symbols = _unique_symbols([*symbols, *market_regime.get("symbols", [])])
    bars_by_symbol = client.get_stock_bars(
        requested_symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=feed,
        limit=max(lookback_minutes + 5, long_window + 2),
    )
    signal_specs: list[dict[str, Any]] = []

    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no stock bars returned for trend confirmation window"
            )
            continue
        if len(stock_bars.bars) < long_window + 1:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: fewer than {long_window + 1} stock bars returned for trend confirmation window"
            )
            continue

        first_bar = stock_bars.bars[0]
        last_bar = stock_bars.bars[-1]
        if first_bar.close == Decimal("0"):
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: first close was 0, trend confirmation unavailable"
            )
            continue

        closes = [bar.close for bar in stock_bars.bars]
        previous_short_average = _average(closes[-short_window - 1 : -1])
        short_average = _average(closes[-short_window:])
        long_average = _average(closes[-long_window:])
        change_percent = _change_percent(first_bar.close, last_bar.close)

        if direction == "bullish":
            aligned = (
                short_average > long_average
                and last_bar.close > long_average
                and (
                    not _bool_config(
                        scanner_config,
                        "require_price_above_short_average",
                        default=False,
                    )
                    or last_bar.close > short_average
                )
            )
            momentum_confirmed = change_percent >= min_change_percent
        else:
            aligned = (
                short_average < long_average
                and last_bar.close < long_average
                and (
                    not _bool_config(
                        scanner_config,
                        "require_price_below_short_average",
                        default=False,
                    )
                    or last_bar.close < short_average
                )
            )
            momentum_confirmed = change_percent <= -min_change_percent

        if not aligned:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: moving averages were not aligned for {direction} trend confirmation"
            )
            continue
        if not momentum_confirmed:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: percent change {change_percent} did not confirm {direction} trend"
            )
            continue

        if _bool_config(scanner_config, "require_short_average_slope", default=False):
            slope_confirmed = (
                short_average > previous_short_average
                if direction == "bullish"
                else short_average < previous_short_average
            )
            if not slope_confirmed:
                no_signal_reasons.append(
                    f"{strategy_name}.{symbol}: short moving average slope did not confirm {direction} trend"
                )
                continue

        market_regime_context = _market_regime_context(
            bars_by_symbol,
            market_regime,
            direction=direction,
        )
        if market_regime_context["enabled"] and not market_regime_context["passed"]:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: market regime did not confirm {direction} trend: "
                f"{market_regime_context['reason']}"
            )
            continue

        signal_specs.append(
            {
                "symbol": symbol,
                "underlying_symbol": symbol,
                "signal_type": _scanner_string(
                    scanner_config,
                    "signal_type",
                    default="trend_confirmation",
                ),
                "direction": direction,
                "confidence": scanner_config.get("confidence"),
                "rationale": _scanner_string(
                    scanner_config,
                    "rationale",
                    default="Trend confirmation scanner triggered",
                ),
                "market_context": {
                    "source": "scanner.trend_confirmation",
                    "direction": direction,
                    "short_window": short_window,
                    "long_window": long_window,
                    "lookback_minutes": lookback_minutes,
                    "timeframe": timeframe,
                    "data_feed": feed,
                    "first_close": str(first_bar.close),
                    "last_close": str(last_bar.close),
                    "change_percent": str(change_percent),
                    "min_change_percent": str(min_change_percent),
                    "previous_short_average": str(previous_short_average),
                    "short_average": str(short_average),
                    "long_average": str(long_average),
                    "short_average_slope": str(short_average - previous_short_average),
                    "market_regime": market_regime_context,
                    "bars": _stock_bars_context(stock_bars),
                },
                "dedupe_minutes": scanner_config.get(
                    "dedupe_minutes",
                    DEFAULT_DEDUPE_MINUTES,
                ),
            }
        )

    return signal_specs


def _momentum_rate_of_change_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping momentum evaluator"
        )
        return []
    if not settings.momentum_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: MOMENTUM_EVALUATOR_ENABLED=false, skipping momentum evaluator"
        )
        return []

    evaluator = get_evaluator("momentum_rate_of_change")
    if evaluator is None:
        return []

    features = evaluator.required_features(scanner_config)
    timeframe = features.timeframe
    lookback_minutes = features.lookback_minutes
    feed = _scanner_string(scanner_config, "data_feed", default="iex")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes + 5)

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    bars_by_symbol = client.get_stock_bars(
        symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=feed,
        limit=max(lookback_minutes + 10, 20),
    )

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for momentum evaluator"
            )
            continue

        candle_frame = _candle_frame_from_stock_bars(stock_bars, timeframe)
        indicator_frame = IndicatorFrame(
            close=candle_frame.closes,
            high=candle_frame.highs,
            low=candle_frame.lows,
            volume=candle_frame.volumes,
        )
        candidate = evaluator.evaluate(
            symbol=symbol,
            config=scanner_config,
            candles=candle_frame,
            indicators=indicator_frame,
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: momentum evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _rsi_reversal_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping rsi_reversal evaluator"
        )
        return []
    if not settings.rsi_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: RSI_EVALUATOR_ENABLED=false, skipping rsi_reversal evaluator"
        )
        return []

    evaluator = get_evaluator("rsi_reversal")
    if evaluator is None:
        return []

    features = evaluator.required_features(scanner_config)
    timeframe = features.timeframe
    lookback_minutes = features.lookback_minutes
    feed = _scanner_string(scanner_config, "data_feed", default="iex")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes + 5)

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    bars_by_symbol = client.get_stock_bars(
        symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=feed,
        limit=max(lookback_minutes + 10, 20),
    )

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for rsi_reversal evaluator"
            )
            continue

        candle_frame = _candle_frame_from_stock_bars(stock_bars, timeframe)
        indicator_frame = IndicatorFrame(
            close=candle_frame.closes,
            high=candle_frame.highs,
            low=candle_frame.lows,
            volume=candle_frame.volumes,
        )
        candidate = evaluator.evaluate(
            symbol=symbol,
            config=scanner_config,
            candles=candle_frame,
            indicators=indicator_frame,
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: rsi_reversal evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _macd_crossover_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping macd_crossover evaluator"
        )
        return []
    if not settings.macd_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: MACD_EVALUATOR_ENABLED=false, skipping macd_crossover evaluator"
        )
        return []

    evaluator = get_evaluator("macd_crossover")
    if evaluator is None:
        return []

    features = evaluator.required_features(scanner_config)
    timeframe = features.timeframe
    lookback_minutes = features.lookback_minutes
    feed = _scanner_string(scanner_config, "data_feed", default="iex")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes + 5)

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    bars_by_symbol = client.get_stock_bars(
        symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=feed,
        limit=max(lookback_minutes + 10, 20),
    )

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for macd_crossover evaluator"
            )
            continue

        candle_frame = _candle_frame_from_stock_bars(stock_bars, timeframe)
        indicator_frame = IndicatorFrame(
            close=candle_frame.closes,
            high=candle_frame.highs,
            low=candle_frame.lows,
            volume=candle_frame.volumes,
        )
        candidate = evaluator.evaluate(
            symbol=symbol,
            config=scanner_config,
            candles=candle_frame,
            indicators=indicator_frame,
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: macd_crossover evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _mean_reversion_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping mean_reversion evaluator"
        )
        return []
    if not settings.mean_reversion_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: MEAN_REVERSION_EVALUATOR_ENABLED=false, skipping mean_reversion evaluator"
        )
        return []

    evaluator = get_evaluator("mean_reversion")
    if evaluator is None:
        return []

    features = evaluator.required_features(scanner_config)
    timeframe = features.timeframe
    lookback_minutes = features.lookback_minutes
    feed = _scanner_string(scanner_config, "data_feed", default="iex")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes + 5)

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    bars_by_symbol = client.get_stock_bars(
        symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=feed,
        limit=max(lookback_minutes + 10, 20),
    )

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for mean_reversion evaluator"
            )
            continue

        candle_frame = _candle_frame_from_stock_bars(stock_bars, timeframe)
        indicator_frame = IndicatorFrame(
            close=candle_frame.closes,
            high=candle_frame.highs,
            low=candle_frame.lows,
            volume=candle_frame.volumes,
        )
        candidate = evaluator.evaluate(
            symbol=symbol,
            config=scanner_config,
            candles=candle_frame,
            indicators=indicator_frame,
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: mean_reversion evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _breakout_price_threshold_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping breakout_price_threshold evaluator"
        )
        return []
    if not settings.breakout_price_threshold_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: BREAKOUT_PRICE_THRESHOLD_EVALUATOR_ENABLED=false, skipping breakout_price_threshold evaluator"
        )
        return []

    evaluator = get_evaluator("breakout_price_threshold")
    if evaluator is None:
        return []

    features = evaluator.required_features(scanner_config)
    timeframe = features.timeframe
    lookback_minutes = features.lookback_minutes
    feed = _scanner_string(scanner_config, "data_feed", default="iex")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes + 5)

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    bars_by_symbol = client.get_stock_bars(
        symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=feed,
        limit=max(lookback_minutes + 10, 20),
    )

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for breakout_price_threshold evaluator"
            )
            continue

        candle_frame = _candle_frame_from_stock_bars(stock_bars, timeframe)
        indicator_frame = IndicatorFrame(
            close=candle_frame.closes,
            high=candle_frame.highs,
            low=candle_frame.lows,
            volume=candle_frame.volumes,
        )
        candidate = evaluator.evaluate(
            symbol=symbol,
            config=scanner_config,
            candles=candle_frame,
            indicators=indicator_frame,
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: breakout_price_threshold evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _volume_confirmed_breakout_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping volume_confirmed_breakout evaluator"
        )
        return []
    if not settings.volume_confirmed_breakout_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: VOLUME_CONFIRMED_BREAKOUT_EVALUATOR_ENABLED=false, skipping volume_confirmed_breakout evaluator"
        )
        return []

    evaluator = get_evaluator("volume_confirmed_breakout")
    if evaluator is None:
        return []

    features = evaluator.required_features(scanner_config)
    timeframe = features.timeframe
    lookback_minutes = features.lookback_minutes
    feed = _scanner_string(scanner_config, "data_feed", default="iex")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes + 5)

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    bars_by_symbol = client.get_stock_bars(
        symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=feed,
        limit=max(lookback_minutes + 10, 20),
    )

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for volume_confirmed_breakout evaluator"
            )
            continue

        candle_frame = _candle_frame_from_stock_bars(stock_bars, timeframe)
        indicator_frame = IndicatorFrame(
            close=candle_frame.closes,
            high=candle_frame.highs,
            low=candle_frame.lows,
            volume=candle_frame.volumes,
        )
        candidate = evaluator.evaluate(
            symbol=symbol,
            config=scanner_config,
            candles=candle_frame,
            indicators=indicator_frame,
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: volume_confirmed_breakout evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _volatility_squeeze_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping volatility_squeeze evaluator"
        )
        return []
    if not settings.volatility_squeeze_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: VOLATILITY_SQUEEZE_EVALUATOR_ENABLED=false, skipping volatility_squeeze evaluator"
        )
        return []

    evaluator = get_evaluator("volatility_squeeze")
    if evaluator is None:
        return []

    features = evaluator.required_features(scanner_config)
    timeframe = features.timeframe
    lookback_minutes = features.lookback_minutes
    feed = _scanner_string(scanner_config, "data_feed", default="iex")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes + 5)

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    bars_by_symbol = client.get_stock_bars(
        symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=feed,
        limit=max(lookback_minutes + 10, 20),
    )

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for volatility_squeeze evaluator"
            )
            continue

        candle_frame = _candle_frame_from_stock_bars(stock_bars, timeframe)
        indicator_frame = IndicatorFrame(
            close=candle_frame.closes,
            high=candle_frame.highs,
            low=candle_frame.lows,
            volume=candle_frame.volumes,
        )
        candidate = evaluator.evaluate(
            symbol=symbol,
            config=scanner_config,
            candles=candle_frame,
            indicators=indicator_frame,
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: volatility_squeeze evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _support_resistance_signal_specs(
    strategy_name: str,
    scanner_config: dict[str, Any],
    symbols: list[str],
    *,
    market_data_client: AlpacaMarketDataClient | None,
    no_signal_reasons: list[str],
) -> list[dict[str, Any]]:
    if not settings.signal_evaluators_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SIGNAL_EVALUATORS_ENABLED=false, skipping support_resistance evaluator"
        )
        return []
    if not settings.support_resistance_evaluator_enabled:
        no_signal_reasons.append(
            f"{strategy_name}: SUPPORT_RESISTANCE_EVALUATOR_ENABLED=false, skipping support_resistance evaluator"
        )
        return []

    evaluator = get_evaluator("support_resistance")
    if evaluator is None:
        return []

    features = evaluator.required_features(scanner_config)
    timeframe = features.timeframe
    lookback_minutes = features.lookback_minutes
    feed = _scanner_string(scanner_config, "data_feed", default="iex")
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes + 5)

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    bars_by_symbol = client.get_stock_bars(
        symbols,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=feed,
        limit=max(lookback_minutes + 10, 20),
    )

    signal_specs: list[dict[str, Any]] = []
    for symbol in symbols:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: no usable bars for support_resistance evaluator"
            )
            continue

        candle_frame = _candle_frame_from_stock_bars(stock_bars, timeframe)
        indicator_frame = IndicatorFrame(
            close=candle_frame.closes,
            high=candle_frame.highs,
            low=candle_frame.lows,
            volume=candle_frame.volumes,
        )
        candidate = evaluator.evaluate(
            symbol=symbol,
            config=scanner_config,
            candles=candle_frame,
            indicators=indicator_frame,
        )
        if candidate is None:
            no_signal_reasons.append(
                f"{strategy_name}.{symbol}: support_resistance evaluator produced no signal"
            )
            continue

        signal_specs.append(_signal_spec_from_candidate(candidate, scanner_config))

    return signal_specs


def _candle_frame_from_stock_bars(stock_bars: AlpacaStockBars, timeframe: str) -> CandleFrame:
    candles = tuple(
        Candle(
            ts=bar.timestamp,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        )
        for bar in stock_bars.bars
    )
    return CandleFrame(symbol=stock_bars.symbol, timeframe=timeframe, candles=candles)


def _signal_spec_from_candidate(
    candidate: SignalCandidate,
    scanner_config: dict[str, Any],
) -> dict[str, Any]:
    dedupe_minutes = int(
        candidate.features.get("dedupe_minutes")
        or scanner_config.get("dedupe_minutes")
        or DEFAULT_DEDUPE_MINUTES
    )
    return {
        "symbol": candidate.symbol,
        "underlying_symbol": candidate.symbol,
        "signal_type": candidate.signal_type,
        "direction": candidate.direction,
        "confidence": candidate.confidence,
        "rationale": candidate.rationale,
        "market_context": {
            "source": f"evaluator.{candidate.strategy_type}",
            "strategy_type": candidate.strategy_type,
            "dedupe_key": candidate.dedupe_key,
            **candidate.features,
        },
        "dedupe_minutes": dedupe_minutes,
    }


def _signal_from_spec(strategy: Strategy, signal_spec: dict[str, Any]) -> Signal:
    symbol = _required_string(signal_spec, "symbol")
    signal_type = _required_string(signal_spec, "signal_type")
    direction = _required_string(signal_spec, "direction")

    return Signal(
        strategy_id=strategy.id,
        symbol=symbol,
        underlying_symbol=_optional_string(signal_spec, "underlying_symbol"),
        signal_type=signal_type,
        direction=direction,
        confidence=_optional_confidence(signal_spec),
        rationale=_optional_string(signal_spec, "rationale"),
        market_context=signal_spec.get("market_context")
        if isinstance(signal_spec.get("market_context"), dict)
        else {},
        status="new",
    )


def _required_string(signal_spec: dict[str, Any], key: str) -> str:
    value = signal_spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _optional_string(signal_spec: dict[str, Any], key: str) -> str | None:
    value = signal_spec.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_confidence(signal_spec: dict[str, Any]) -> Decimal | None:
    value = signal_spec.get("confidence")
    if value is None:
        return None
    try:
        confidence = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("confidence must be a decimal between 0 and 1") from exc
    if confidence < Decimal("0") or confidence > Decimal("1"):
        raise ValueError("confidence must be between 0 and 1")
    return confidence


def _has_recent_duplicate_signal(
    db: Session,
    signal: Signal,
    signal_spec: dict[str, Any],
) -> bool:
    dedupe_minutes = _dedupe_minutes(signal_spec)
    if dedupe_minutes <= 0:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=dedupe_minutes)
    statement = (
        select(Signal)
        .where(Signal.strategy_id == signal.strategy_id)
        .where(Signal.symbol == signal.symbol)
        .where(Signal.signal_type == signal.signal_type)
        .where(Signal.direction == signal.direction)
        .where(Signal.status.in_(DEDUPE_STATUSES))
        .where(Signal.created_at >= cutoff)
        .limit(1)
    )
    return db.scalar(statement) is not None


def _dedupe_minutes(signal_spec: dict[str, Any]) -> int:
    value = signal_spec.get("dedupe_minutes", DEFAULT_DEDUPE_MINUTES)
    try:
        dedupe_minutes = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("dedupe_minutes must be an integer") from exc
    if dedupe_minutes < 0:
        raise ValueError("dedupe_minutes must be greater than or equal to 0")
    return dedupe_minutes


def _optional_decimal(config: dict[str, Any], key: str) -> Decimal | None:
    value = config.get(key)
    if value is None:
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"scanner.{key} must be a decimal") from exc
    return decimal_value


def _positive_int(config: dict[str, Any], key: str, *, default: int) -> int:
    value = config.get(key, default)
    try:
        int_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"scanner.{key} must be an integer") from exc
    if int_value <= 0:
        raise ValueError(f"scanner.{key} must be greater than 0")
    return int_value


def _bool_config(config: dict[str, Any], key: str, *, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"scanner.{key} must be a boolean")


def _scanner_string(
    scanner_config: dict[str, Any],
    key: str,
    *,
    default: str,
) -> str:
    value = scanner_config.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"scanner.{key} must be a non-empty string")
    return value.strip()


def _average(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _change_percent(first_close: Decimal, last_close: Decimal) -> Decimal:
    if first_close == Decimal("0"):
        return Decimal("0")
    return ((last_close - first_close) / first_close) * Decimal("100")


def _market_regime_config(scanner_config: dict[str, Any]) -> dict[str, Any]:
    raw_config = scanner_config.get("market_regime")
    if raw_config is None:
        return {"enabled": False, "symbols": []}
    if not isinstance(raw_config, dict):
        raise ValueError("scanner.market_regime must be an object")

    enabled = _bool_config(raw_config, "enabled", default=False)
    raw_symbols = raw_config.get("symbols", ["SPY", "QQQ"])
    if not isinstance(raw_symbols, list) or not raw_symbols:
        raise ValueError("scanner.market_regime.symbols must be a non-empty list")

    symbols = []
    for raw_symbol in raw_symbols:
        if not isinstance(raw_symbol, str) or not raw_symbol.strip():
            raise ValueError("scanner.market_regime.symbols must contain strings")
        symbols.append(raw_symbol.strip().upper())

    return {
        "enabled": enabled,
        "symbols": _unique_symbols(symbols) if enabled else [],
        "bullish_min_change_percent": _optional_decimal(
            raw_config,
            "bullish_min_change_percent",
        ),
        "bearish_max_change_percent": _optional_decimal(
            raw_config,
            "bearish_max_change_percent",
        ),
    }


def _market_regime_context(
    bars_by_symbol: dict[str, AlpacaStockBars],
    market_regime: dict[str, Any],
    *,
    direction: str,
) -> dict[str, Any]:
    if market_regime.get("enabled") is not True:
        return {"enabled": False, "passed": True, "reason": "disabled"}

    symbol_contexts = []
    changes = []
    for symbol in market_regime["symbols"]:
        stock_bars = bars_by_symbol.get(symbol)
        if stock_bars is None or len(stock_bars.bars) < 2:
            symbol_contexts.append(
                {
                    "symbol": symbol,
                    "bars_seen": 0 if stock_bars is None else len(stock_bars.bars),
                    "change_percent": None,
                }
            )
            continue
        first_bar = stock_bars.bars[0]
        last_bar = stock_bars.bars[-1]
        change_percent = _change_percent(first_bar.close, last_bar.close)
        changes.append(change_percent)
        symbol_contexts.append(
            {
                "symbol": symbol,
                "bars_seen": len(stock_bars.bars),
                "first_close": str(first_bar.close),
                "last_close": str(last_bar.close),
                "change_percent": str(change_percent),
            }
        )

    if not changes:
        return {
            "enabled": True,
            "passed": False,
            "reason": "no usable market regime bars",
            "symbols": symbol_contexts,
        }

    average_change = _average(changes)
    if direction == "bullish":
        threshold = market_regime.get("bullish_min_change_percent")
        passed = threshold is None or average_change >= threshold
        reason = (
            f"average market change {average_change} below bullish minimum {threshold}"
            if not passed
            else "bullish market regime confirmed"
        )
    else:
        threshold = market_regime.get("bearish_max_change_percent")
        passed = threshold is None or average_change <= threshold
        reason = (
            f"average market change {average_change} above bearish maximum {threshold}"
            if not passed
            else "bearish market regime confirmed"
        )

    return {
        "enabled": True,
        "passed": passed,
        "reason": reason,
        "average_change_percent": str(average_change),
        "bullish_min_change_percent": str(
            market_regime.get("bullish_min_change_percent")
        )
        if market_regime.get("bullish_min_change_percent") is not None
        else None,
        "bearish_max_change_percent": str(
            market_regime.get("bearish_max_change_percent")
        )
        if market_regime.get("bearish_max_change_percent") is not None
        else None,
        "symbols": symbol_contexts,
    }


def _unique_symbols(symbols: list[str]) -> list[str]:
    unique = []
    for symbol in symbols:
        clean_symbol = symbol.strip().upper()
        if clean_symbol and clean_symbol not in unique:
            unique.append(clean_symbol)
    return unique


def _price_from_quote(latest_quote: AlpacaLatestStockQuote) -> Decimal | None:
    bid_price = _usable_quote_price(latest_quote.quote.bid_price)
    ask_price = _usable_quote_price(latest_quote.quote.ask_price)
    if bid_price is not None and ask_price is not None:
        return (bid_price + ask_price) / Decimal("2")
    return ask_price or bid_price


def _usable_quote_price(value: Decimal | None) -> Decimal | None:
    if value is None or value <= Decimal("0"):
        return None
    return value


def _stock_quote_context(latest_quote: AlpacaLatestStockQuote) -> dict[str, object]:
    quote = latest_quote.quote
    return {
        "symbol": latest_quote.symbol,
        "bid_price": str(quote.bid_price) if quote.bid_price is not None else None,
        "bid_size": str(quote.bid_size) if quote.bid_size is not None else None,
        "ask_price": str(quote.ask_price) if quote.ask_price is not None else None,
        "ask_size": str(quote.ask_size) if quote.ask_size is not None else None,
        "quote_timestamp": quote.timestamp.isoformat()
        if quote.timestamp is not None
        else None,
        "raw_quote": latest_quote.raw_response,
    }


def _stock_bars_context(stock_bars: AlpacaStockBars) -> dict[str, object]:
    return {
        "symbol": stock_bars.symbol,
        "bars_seen": len(stock_bars.bars),
        "first_timestamp": stock_bars.bars[0].timestamp.isoformat(),
        "last_timestamp": stock_bars.bars[-1].timestamp.isoformat(),
    }
