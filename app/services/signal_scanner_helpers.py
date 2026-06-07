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

from app.db.models import JobRun, Signal, Strategy
from app.integrations.alpaca import (
    AlpacaMarketDataClient,
)
from app.services.audit_logs import record_audit_log

DEFAULT_DEDUPE_MINUTES = 240
DEDUPE_STATUSES = ("new", "previewed", "submitted", "signal_only", "preview_disabled")

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
    original_symbols = list(clean_symbols)
    if symbol_filter is not None:
        if symbol_filter not in clean_symbols:
            no_signal_reasons.append(
                f"{strategy.name}: scanner does not include symbol {symbol_filter}"
            )
            return []
        clean_symbols = [symbol_filter]

    if scanner_type == "moving_average":
        return _moving_average_evaluator_signal_specs(
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
    if scanner_type in {
        "vwap_reclaim",
        "opening_range_breakout",
        "relative_strength",
        "time_series_momentum",
        "market_regime_filter",
        "pairs_relative_value",
        "options_spread_candidate",
    }:
        advanced_config = dict(scanner_config)
        context_symbols = clean_symbols
        if scanner_type in {
            "relative_strength",
            "market_regime_filter",
            "pairs_relative_value",
        }:
            context_symbols = original_symbols
            advanced_config["_emit_symbols"] = clean_symbols
        return _advanced_evaluator_signal_specs(
            strategy.name,
            advanced_config,
            context_symbols,
            market_data_client=market_data_client,
            no_signal_reasons=no_signal_reasons,
        )
    raise ValueError(
        "scanner.type must be moving_average, momentum_rate_of_change, "
        "rsi_reversal, macd_crossover, mean_reversion, breakout_price_threshold, "
        "volume_confirmed_breakout, volatility_squeeze, support_resistance, "
        "vwap_reclaim, opening_range_breakout, relative_strength, "
        "time_series_momentum, market_regime_filter, pairs_relative_value, "
        "or options_spread_candidate"
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


from app.services.signal_scanner_evaluator_specs import (
    _advanced_evaluator_signal_specs,
    _breakout_price_threshold_signal_specs,
    _macd_crossover_signal_specs,
    _mean_reversion_signal_specs,
    _momentum_rate_of_change_signal_specs,
    _moving_average_evaluator_signal_specs,
    _rsi_reversal_signal_specs,
    _support_resistance_signal_specs,
    _volatility_squeeze_signal_specs,
    _volume_confirmed_breakout_signal_specs,
)

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


def _positive_int(config: dict[str, Any], key: str, *, default: int) -> int:
    value = config.get(key, default)
    try:
        int_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"scanner.{key} must be an integer") from exc
    if int_value <= 0:
        raise ValueError(f"scanner.{key} must be greater than 0")
    return int_value


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
