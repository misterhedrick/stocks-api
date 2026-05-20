from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import AuditLog, JobRun, OptionSelectionDiagnostic, Signal


def build_retention_report(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    current_time = _utc_datetime(now)
    cutoffs = {
        "job_runs_before": current_time - timedelta(days=_setting_int("retention_job_run_days", 30)),
        "audit_logs_before": current_time - timedelta(days=_setting_int("retention_audit_log_days", 60)),
        "rejected_signals_before": current_time - timedelta(
            days=_setting_int("retention_rejected_signal_days", 30)
        ),
        "option_diagnostics_before": current_time - timedelta(
            days=_setting_int("retention_option_diagnostic_days", 60)
        ),
    }

    return {
        "generated_at": current_time.isoformat(),
        "mode": "report_only",
        "cutoffs": {key: value.isoformat() for key, value in cutoffs.items()},
        "eligible_counts": {
            "successful_job_runs": _count_successful_job_runs(
                db,
                before=cutoffs["job_runs_before"],
            ),
            "audit_logs": _count_audit_logs(db, before=cutoffs["audit_logs_before"]),
            "option_selection_diagnostics": _count_option_diagnostics(
                db,
                before=cutoffs["option_diagnostics_before"],
            ),
            "rejected_signals_without_order_intents": _count_rejected_signals_without_orders(
                db,
                before=cutoffs["rejected_signals_before"],
            ),
        },
        "always_preserved": [
            "broker_orders",
            "fills",
            "trade_cases",
            "ai_trade_reviews",
            "strategy_change_suggestions",
            "paper_review_snapshots",
            "failed_job_runs",
        ],
    }


def _count_successful_job_runs(db: Session, *, before: datetime) -> int:
    rows = db.scalars(
        select(JobRun.id)
        .where(JobRun.created_at < before)
        .where(JobRun.status != "failed")
    )
    return len(list(rows))


def _count_audit_logs(db: Session, *, before: datetime) -> int:
    rows = db.scalars(select(AuditLog.id).where(AuditLog.created_at < before))
    return len(list(rows))


def _count_option_diagnostics(db: Session, *, before: datetime) -> int:
    rows = db.scalars(
        select(OptionSelectionDiagnostic.id).where(
            OptionSelectionDiagnostic.created_at < before
        )
    )
    return len(list(rows))


def _count_rejected_signals_without_orders(db: Session, *, before: datetime) -> int:
    rows = db.scalars(
        select(Signal.id)
        .where(Signal.created_at < before)
        .where(Signal.status.in_(("stale", "rejected", "preview_rejected")))
        .where(~Signal.order_intents.any())
    )
    return len(list(rows))


def _setting_int(name: str, default: int) -> int:
    value = getattr(settings, name, default)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _utc_datetime(value: datetime | None) -> datetime:
    current_time = value or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        return current_time.replace(tzinfo=timezone.utc)
    return current_time.astimezone(timezone.utc)
