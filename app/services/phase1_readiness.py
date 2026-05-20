from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import JobRun, ReviewSnapshot, Strategy, TradeCase

REQUIRED_RECENT_JOB_NAMES = (
    "market_entry_cycle",
    "market_cycle",
    "market_cycle_exits",
    "market_maintenance",
)


def build_phase1_readiness(db: Session) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []

    _check_safety_switches(blockers, warnings)
    active_strategy_count = _active_strategy_count(db)
    if active_strategy_count == 0:
        blockers.append("no active strategies found")

    recent_jobs = _latest_jobs(db)
    _check_recent_jobs(recent_jobs, warnings)

    latest_snapshot = _latest_review_snapshot(db)
    latest_trade_case_count = _recent_trade_case_count(db)

    if latest_snapshot is None:
        warnings.append("no review snapshot found yet")

    return {
        "ready": not blockers,
        "mode": _effective_mode(blockers),
        "blockers": blockers,
        "warnings": warnings,
        "safety": {
            "paper_mode": settings.alpaca_paper,
            "auto_submit_requires_paper": settings.auto_submit_requires_paper,
            "trading_automation_enabled": settings.trading_automation_enabled,
            "market_cycle_submit_enabled": settings.market_cycle_submit_enabled,
            "market_cycle_scan_enabled": settings.market_cycle_scan_enabled,
            "market_cycle_preview_enabled": settings.market_cycle_preview_enabled,
            "market_cycle_reconcile_enabled": settings.market_cycle_reconcile_enabled,
            "market_cycle_exit_enabled": settings.market_cycle_exit_enabled,
            "scheduled_jobs_enabled": getattr(settings, "scheduled_jobs_enabled", None),
        },
        "risk_caps": {
            "max_auto_orders_per_cycle": settings.max_auto_orders_per_cycle,
            "max_auto_orders_per_day": settings.max_auto_orders_per_day,
            "max_auto_orders_per_symbol_per_day": settings.max_auto_orders_per_symbol_per_day,
            "max_open_positions": settings.max_open_positions,
            "max_open_positions_per_symbol": settings.max_open_positions_per_symbol,
            "max_contracts_per_order": settings.max_contracts_per_order,
            "max_estimated_premium_per_order": str(settings.max_estimated_premium_per_order),
        },
        "active_strategy_count": active_strategy_count,
        "latest_jobs": recent_jobs,
        "latest_review_snapshot": latest_snapshot,
        "recent_trade_case_count": latest_trade_case_count,
    }


def _check_safety_switches(blockers: list[str], warnings: list[str]) -> None:
    if not settings.alpaca_paper:
        blockers.append("ALPACA_PAPER is false")
    if settings.auto_submit_requires_paper and not settings.alpaca_paper:
        blockers.append("AUTO_SUBMIT_REQUIRES_PAPER is true but ALPACA_PAPER is false")
    if not settings.market_cycle_scan_enabled:
        blockers.append("MARKET_CYCLE_SCAN_ENABLED is false")
    if not settings.market_cycle_preview_enabled:
        blockers.append("MARKET_CYCLE_PREVIEW_ENABLED is false")
    if not settings.market_cycle_reconcile_enabled:
        warnings.append("MARKET_CYCLE_RECONCILE_ENABLED is false")
    if not settings.market_cycle_exit_enabled:
        warnings.append("MARKET_CYCLE_EXIT_ENABLED is false")
    if not settings.market_cycle_submit_enabled:
        warnings.append("MARKET_CYCLE_SUBMIT_ENABLED is false; entries will not auto-submit")
    if not settings.trading_automation_enabled:
        warnings.append("TRADING_AUTOMATION_ENABLED is false; entries will not auto-submit")


def _effective_mode(blockers: list[str]) -> str:
    if blockers:
        return "blocked"
    if settings.market_cycle_submit_enabled and settings.trading_automation_enabled:
        return "paper_auto_submit" if settings.alpaca_paper else "live_auto_submit"
    if settings.market_cycle_preview_enabled:
        return "paper_preview_only" if settings.alpaca_paper else "live_preview_only"
    return "watch_only"


def _active_strategy_count(db: Session) -> int:
    strategies = db.scalars(
        select(Strategy.id).where(Strategy.is_active == True)  # noqa: E712
    )
    return len(list(strategies))


def _latest_jobs(db: Session) -> dict[str, dict[str, Any] | None]:
    return {
        job_name: _job_read_item(_latest_job(db, job_name))
        for job_name in REQUIRED_RECENT_JOB_NAMES
    }


def _latest_job(db: Session, job_name: str) -> JobRun | None:
    return db.scalar(
        select(JobRun)
        .where(JobRun.job_name == job_name)
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )


def _job_read_item(job_run: JobRun | None) -> dict[str, Any] | None:
    if job_run is None:
        return None
    return {
        "id": str(job_run.id),
        "job_name": job_run.job_name,
        "status": job_run.status,
        "started_at": job_run.started_at.isoformat(),
        "finished_at": job_run.finished_at.isoformat() if job_run.finished_at else None,
        "error": job_run.error,
    }


def _check_recent_jobs(
    recent_jobs: dict[str, dict[str, Any] | None],
    warnings: list[str],
) -> None:
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    for job_name, job in recent_jobs.items():
        if job is None:
            warnings.append(f"no recent {job_name} job found")
            continue
        if job["status"] == "failed":
            warnings.append(f"latest {job_name} job failed")
            continue
        try:
            started_at = datetime.fromisoformat(str(job["started_at"]))
        except ValueError:
            continue
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if started_at < stale_cutoff:
            warnings.append(f"latest {job_name} job is older than 3 days")


def _latest_review_snapshot(db: Session) -> dict[str, Any] | None:
    snapshot = db.scalar(
        select(ReviewSnapshot)
        .order_by(ReviewSnapshot.generated_at.desc())
        .limit(1)
    )
    if snapshot is None:
        return None
    return {
        "id": str(snapshot.id),
        "review_date": snapshot.review_date.isoformat(),
        "review_type": snapshot.review_type,
        "status": snapshot.status,
        "generated_at": snapshot.generated_at.isoformat(),
    }


def _recent_trade_case_count(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    trade_cases = db.scalars(
        select(TradeCase.id).where(TradeCase.created_at >= cutoff)
    )
    return len(list(trade_cases))
