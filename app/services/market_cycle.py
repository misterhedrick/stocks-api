from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import JobRun
from app.services.audit_logs import record_audit_log
from app.services.broker_reconciliation import reconcile_broker_state
from app.services.signal_scanner import scan_signals


@dataclass(slots=True)
class MarketCycleResult:
    job_run: JobRun
    scan_enabled: bool
    reconcile_enabled: bool
    preview_enabled: bool
    submit_enabled: bool
    scan: dict[str, Any] | None
    reconcile: dict[str, Any] | None
    preview: dict[str, Any] | None
    submit: dict[str, Any] | None


def run_market_cycle(
    db: Session,
    *,
    scan_limit: int = 100,
    order_limit: int = 100,
    fill_page_size: int = 100,
) -> MarketCycleResult:
    started_at = datetime.now(timezone.utc)
    job_run = JobRun(
        job_name="market_cycle",
        status="running",
        started_at=started_at,
        details={},
    )
    db.add(job_run)
    db.flush()

    scan_enabled = settings.market_cycle_scan_enabled
    reconcile_enabled = settings.market_cycle_reconcile_enabled
    preview_enabled = settings.market_cycle_preview_enabled
    submit_enabled = settings.market_cycle_submit_enabled

    scan = None
    reconcile = None
    preview = _disabled_step("preview")
    submit = _disabled_step("submit")

    try:
        if scan_enabled:
            scan_result = scan_signals(db, limit=scan_limit)
            scan = {
                "job_run_id": str(scan_result.job_run.id),
                "strategies_seen": scan_result.strategies_seen,
                "strategies_scanned": scan_result.strategies_scanned,
                "signals_created": scan_result.signals_created,
                "signals_skipped": scan_result.signals_skipped,
                "errors": scan_result.errors,
            }
        else:
            scan = _disabled_step("scan")

        if reconcile_enabled:
            reconciliation_result = reconcile_broker_state(
                db,
                order_limit=order_limit,
                fill_page_size=fill_page_size,
            )
            reconcile = {
                "job_run_id": str(reconciliation_result.job_run.id),
                "orders_seen": reconciliation_result.orders_seen,
                "orders_created": reconciliation_result.orders_created,
                "orders_updated": reconciliation_result.orders_updated,
                "fills_seen": reconciliation_result.fills_seen,
                "fills_created": reconciliation_result.fills_created,
                "positions_seen": reconciliation_result.positions_seen,
                "position_snapshots_created": reconciliation_result.position_snapshots_created,
            }
        else:
            reconcile = _disabled_step("reconcile")

        if preview_enabled:
            preview = _not_implemented_step("preview")
        if submit_enabled:
            submit = _not_implemented_step("submit")

        details = {
            "scan_enabled": scan_enabled,
            "reconcile_enabled": reconcile_enabled,
            "preview_enabled": preview_enabled,
            "submit_enabled": submit_enabled,
            "scan": scan,
            "reconcile": reconcile,
            "preview": preview,
            "submit": submit,
        }
        job_run.status = "succeeded"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = details
        job_run.error = None
        db.add(job_run)
        record_audit_log(
            db,
            event_type="market_cycle.succeeded",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Market cycle succeeded",
            payload=details,
        )
        db.commit()
        db.refresh(job_run)

        return MarketCycleResult(job_run=job_run, **details)
    except Exception as exc:
        db.rollback()
        job_run.status = "failed"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = {}
        job_run.error = f"{exc.__class__.__name__}: {exc}"
        db.add(job_run)
        record_audit_log(
            db,
            event_type="market_cycle.failed",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Market cycle failed",
            payload={"error": job_run.error},
        )
        db.commit()
        db.refresh(job_run)
        raise


def _disabled_step(step_name: str) -> dict[str, Any]:
    return {"status": "disabled", "step": step_name}


def _not_implemented_step(step_name: str) -> dict[str, Any]:
    return {
        "status": "not_implemented",
        "step": step_name,
        "message": f"{step_name} automation is not implemented yet",
    }
