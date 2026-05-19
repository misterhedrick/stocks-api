from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.core.config import settings
from app.db.models import JobRun, Signal, Strategy
from app.integrations.alpaca import (
    AlpacaMarketDataClient,
)
from app.services.audit_logs import record_audit_log
from app.services import signal_scanner_evaluator_breakout as _breakout_evaluators
from app.services import signal_scanner_evaluator_trend as _trend_evaluators
from app.services import signal_scanner_evaluator_advanced as _advanced_evaluators
from app.services.signal_scanner_helpers import (
    _filter_signal_specs_for_symbol,
    _has_recent_duplicate_signal,
    _normalize_symbol,
    _signal_from_spec,
    _signal_specs_from_scanner,
    _signal_specs_from_strategy,
)


def _sync_evaluator_settings() -> None:
    _trend_evaluators.settings = settings
    _breakout_evaluators.settings = settings
    _advanced_evaluators.settings = settings


def _moving_average_evaluator_signal_specs(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    _sync_evaluator_settings()
    return _trend_evaluators._moving_average_evaluator_signal_specs(*args, **kwargs)


def _momentum_rate_of_change_signal_specs(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    _sync_evaluator_settings()
    return _trend_evaluators._momentum_rate_of_change_signal_specs(*args, **kwargs)


def _rsi_reversal_signal_specs(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    _sync_evaluator_settings()
    return _trend_evaluators._rsi_reversal_signal_specs(*args, **kwargs)


def _macd_crossover_signal_specs(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    _sync_evaluator_settings()
    return _trend_evaluators._macd_crossover_signal_specs(*args, **kwargs)


def _mean_reversion_signal_specs(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    _sync_evaluator_settings()
    return _breakout_evaluators._mean_reversion_signal_specs(*args, **kwargs)


def _breakout_price_threshold_signal_specs(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    _sync_evaluator_settings()
    return _breakout_evaluators._breakout_price_threshold_signal_specs(*args, **kwargs)


def _volume_confirmed_breakout_signal_specs(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    _sync_evaluator_settings()
    return _breakout_evaluators._volume_confirmed_breakout_signal_specs(*args, **kwargs)


def _volatility_squeeze_signal_specs(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    _sync_evaluator_settings()
    return _breakout_evaluators._volatility_squeeze_signal_specs(*args, **kwargs)


def _support_resistance_signal_specs(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    _sync_evaluator_settings()
    return _breakout_evaluators._support_resistance_signal_specs(*args, **kwargs)


def _advanced_evaluator_signal_specs(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    _sync_evaluator_settings()
    return _advanced_evaluators._advanced_evaluator_signal_specs(*args, **kwargs)


_candle_frame_from_stock_bars = _trend_evaluators._candle_frame_from_stock_bars
_signal_spec_from_candidate = _trend_evaluators._signal_spec_from_candidate


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
    _sync_evaluator_settings()
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
