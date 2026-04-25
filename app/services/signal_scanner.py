from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobRun, Signal, Strategy
from app.services.audit_logs import record_audit_log


@dataclass(slots=True)
class SignalScanResult:
    job_run: JobRun
    strategies_seen: int
    strategies_scanned: int
    signals_created: int
    signals_skipped: int
    errors: list[str]


def scan_signals(
    db: Session,
    *,
    limit: int = 100,
) -> SignalScanResult:
    started_at = datetime.now(timezone.utc)
    job_run = JobRun(
        job_name="scan_signals",
        status="running",
        started_at=started_at,
        details={},
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
        errors: list[str] = []

        for strategy in strategies:
            signal_specs = _signal_specs_from_strategy(strategy)
            if not signal_specs:
                continue

            strategies_scanned += 1
            for index, signal_spec in enumerate(signal_specs):
                try:
                    signal = _signal_from_spec(strategy, signal_spec)
                except ValueError as exc:
                    signals_skipped += 1
                    errors.append(f"{strategy.name}[{index}]: {exc}")
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

        details = {
            "strategies_seen": len(strategies),
            "strategies_scanned": strategies_scanned,
            "signals_created": signals_created,
            "signals_skipped": signals_skipped,
            "errors": errors,
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
