from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import JobRun
from app.services.audit_logs import record_audit_log
from app.services.market_cycle_helpers import (
    _elapsed_seconds,
    _error_category,
    _exit_alert_payload,
    _timeout_step,
)
from app.services.market_cycle_runner_types import MarketCycleResult


def _skipped_lock_result(
    db: Session,
    *,
    job_name: str,
    event_prefix: str,
    lock_key: int,
    symbol_filter: str | None,
) -> MarketCycleResult:
    skipped_at = datetime.now(timezone.utc)
    job_run = JobRun(
        job_name=job_name,
        status="skipped",
        started_at=skipped_at,
        finished_at=skipped_at,
        details={
            key: value
            for key, value in {"reason": "already_running", "symbol": symbol_filter}.items()
            if value is not None
        },
        error=None,
    )
    db.add(job_run)
    db.commit()
    db.refresh(job_run)
    return MarketCycleResult(
        job_run=job_run,
        symbol=symbol_filter,
        scan_enabled=False,
        reconcile_enabled=False,
        preview_enabled=False,
        exit_enabled=False,
        news_enabled=False,
        submit_enabled=False,
        scan=None,
        reconcile=None,
        preview=None,
        exits=None,
        news=None,
        submit=None,
        timings={"total_seconds": 0.0},
        phase_timeout_seconds=None,
        diagnostics={
            key: value
            for key, value in {
                "status": "skipped",
                "reason": "already_running",
                "symbol": symbol_filter,
            }.items()
            if value is not None
        },
    )


def _complete_market_cycle(
    db: Session,
    *,
    job_run: JobRun,
    event_prefix: str,
    final_status: str,
    details: dict[str, Any],
    exits: dict[str, Any] | None,
) -> MarketCycleResult:
    job_run.status = final_status
    job_run.finished_at = datetime.now(timezone.utc)
    job_run.details = details
    job_run.error = None
    db.add(job_run)
    exit_alert = _exit_alert_payload(exits)
    if exit_alert is not None:
        record_audit_log(
            db,
            event_type="market_cycle.exit_attention_required",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Market cycle exit evaluation needs attention",
            payload=exit_alert,
        )
    record_audit_log(
        db,
        event_type=f"{event_prefix}.{final_status}",
        entity_type="job_run",
        entity_id=job_run.id,
        message=f"{event_prefix.replace('_', ' ').title()} {final_status}",
        payload=details,
    )
    db.commit()
    db.refresh(job_run)
    return MarketCycleResult(job_run=job_run, **details)


def _fail_market_cycle(
    db: Session,
    *,
    job_run: JobRun,
    event_prefix: str,
    timings: dict[str, float],
    cycle_started: float,
    phase_timeout: int,
    exc: Exception,
) -> None:
    db.rollback()
    job_run.status = "failed"
    job_run.finished_at = datetime.now(timezone.utc)
    timings["total_seconds"] = _elapsed_seconds(cycle_started)
    job_run.details = {
        "timings": timings,
        "phase_timeout_seconds": phase_timeout,
        "diagnostics": {
            "status": "failed",
            "error_type": exc.__class__.__name__,
            "error_category": _error_category(str(exc)),
        },
    }
    job_run.error = f"{exc.__class__.__name__}: {exc}"
    db.add(job_run)
    record_audit_log(
        db,
        event_type=f"{event_prefix}.failed",
        entity_type="job_run",
        entity_id=job_run.id,
        message=f"{event_prefix.replace('_', ' ').title()} failed",
        payload={"error": job_run.error},
    )
    db.commit()
    db.refresh(job_run)


def _submit_skipped_result(
    *,
    submit_enabled: bool,
    submit_candidates_count: int,
    phase_timeout: int,
) -> dict[str, Any]:
    if submit_enabled:
        return {
            **_timeout_step("submit", phase_timeout),
            "candidates_seen": submit_candidates_count,
            "order_intents_seen": submit_candidates_count,
            "submitted": 0,
            "skipped": submit_candidates_count,
            "rejected": 0,
            "errors": ["submit skipped: runtime budget exceeded"]
            if submit_candidates_count
            else [],
            "skipped_reasons": (
                {"runtime_budget_exceeded": submit_candidates_count}
                if submit_candidates_count
                else {}
            ),
            "submitted_order_intent_ids": [],
            "broker_order_ids": [],
        }

    return {
        "status": "disabled",
        "reason": "submit disabled by global config",
        "candidates_seen": submit_candidates_count,
        "order_intents_seen": submit_candidates_count,
        "submitted": 0,
        "skipped": submit_candidates_count,
        "rejected": 0,
        "errors": ["submit disabled by global config"]
        if submit_candidates_count
        else [],
        "skipped_reasons": (
            {"submit disabled by global config": submit_candidates_count}
            if submit_candidates_count
            else {}
        ),
        "submitted_order_intent_ids": [],
        "broker_order_ids": [],
    }
