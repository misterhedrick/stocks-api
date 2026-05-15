from __future__ import annotations

import logging

from datetime import datetime, timedelta, timezone

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings

from app.services.ai_trade_review import write_ai_trade_reviews_from_paper_evidence

from app.services.audit_logs import record_audit_log

from app.services.broker_reconciliation import reconcile_broker_state

from app.services.market_maintenance_cleanup import cleanup_stale_trading_state, _counts_by_strategy_id, _oldest_created_at

from app.services.market_maintenance_dte import _json_safe_value, patch_strategy_dte

from app.services.market_maintenance_support import (
    _disabled_step,
    _fail_job_run,
    _finish_job_run,
    _news_summary,
    _performance_summary,
    _readiness_summary,
    _reconciliation_summary,
    _settings_snapshot,
    _start_job_run,
)

from app.services.market_maintenance_types import (
    AUTO_POST_MARKET_START_HOUR_UTC,
    MarketMaintenanceResult,
    PatchStrategyDteResult,
)

from app.services.news_scanner import scan_market_news

from app.services.paper_review_snapshots import (
    create_or_update_post_market_paper_review_snapshot,
    prune_old_paper_review_snapshots,
)
from app.services.performance_review import PerformanceReviewResult

from app.services.performance_review import get_paper_performance_review

from app.services.trade_cases import populate_trade_cases_from_closed_round_trips

logger = logging.getLogger(__name__)

def run_market_maintenance(
    db: Session,
    *,
    phase: str = "auto",
    now: datetime | None = None,
    order_limit: int | None = None,
    fill_page_size: int | None = None,
    stale_after_hours: int | None = None,
    news_enabled: bool = True,
) -> MarketMaintenanceResult:
    if order_limit is not None and order_limit < 1:
        raise ValueError("order_limit must be >= 1")
    if fill_page_size is not None and fill_page_size < 1:
        raise ValueError("fill_page_size must be >= 1")
    if stale_after_hours is not None and stale_after_hours < 0:
        raise ValueError("stale_after_hours must be >= 0")
    selected_phase = resolve_market_maintenance_phase(phase, now=now)
    if selected_phase == "pre_market":
        return run_pre_market_maintenance(
            db,
            order_limit=100 if order_limit is None else order_limit,
            fill_page_size=100 if fill_page_size is None else fill_page_size,
            stale_after_hours=12 if stale_after_hours is None else stale_after_hours,
            news_enabled=news_enabled,
        )

    return run_post_market_maintenance(
        db,
        order_limit=500 if order_limit is None else order_limit,
        fill_page_size=100 if fill_page_size is None else fill_page_size,
        stale_after_hours=0 if stale_after_hours is None else stale_after_hours,
    )

def resolve_market_maintenance_phase(
    phase: str = "auto",
    *,
    now: datetime | None = None,
) -> str:
    normalized = phase.strip().lower().replace("-", "_")
    if normalized in {"pre", "pre_market"}:
        return "pre_market"
    if normalized in {"post", "post_market"}:
        return "post_market"
    if normalized != "auto":
        raise ValueError("phase must be auto, pre_market, or post_market")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_utc = current_time.astimezone(timezone.utc)
    if current_utc.hour < AUTO_POST_MARKET_START_HOUR_UTC:
        return "pre_market"
    return "post_market"

def run_pre_market_maintenance(
    db: Session,
    *,
    order_limit: int = 100,
    fill_page_size: int = 100,
    stale_after_hours: int = 12,
    news_enabled: bool = True,
) -> MarketMaintenanceResult:
    logger.info("Running pre-market maintenance (order_limit=%d, stale_after_hours=%d)", order_limit, stale_after_hours)
    started_at = datetime.now(timezone.utc)
    job_run = _start_job_run(db, "pre_market_maintenance", started_at=started_at)

    try:
        cleanup = cleanup_stale_trading_state(
            db,
            stale_before=started_at - timedelta(hours=stale_after_hours),
            source="pre_market_maintenance",
        )
        reconciliation_result = reconcile_broker_state(
            db,
            order_limit=order_limit,
            fill_page_size=fill_page_size,
        )
        reconcile = _reconciliation_summary(reconciliation_result)
        news = _news_summary(scan_market_news(db)) if news_enabled else _disabled_step("news")
        readiness = _readiness_summary(db)
        settings_snapshot = _settings_snapshot()

        details = {
            "phase": "pre_market",
            "cleanup": cleanup,
            "reconcile": reconcile,
            "news": news,
            "performance": None,
            "readiness": readiness,
            "settings_snapshot": settings_snapshot,
        }
        _finish_job_run(db, job_run, details=details, event_type="market_maintenance.pre_market.succeeded")
        return MarketMaintenanceResult(
            job_run=job_run,
            phase="pre_market",
            cleanup=cleanup,
            reconcile=reconcile,
            news=news,
            performance=None,
            readiness=readiness,
            settings_snapshot=settings_snapshot,
        )
    except Exception as exc:
        _fail_job_run(db, job_run, exc, event_type="market_maintenance.pre_market.failed")
        raise

def run_post_market_maintenance(
    db: Session,
    *,
    order_limit: int = 500,
    fill_page_size: int = 100,
    stale_after_hours: int = 0,
) -> MarketMaintenanceResult:
    logger.info("Running post-market maintenance (order_limit=%d, stale_after_hours=%d)", order_limit, stale_after_hours)
    started_at = datetime.now(timezone.utc)
    job_run = _start_job_run(db, "post_market_maintenance", started_at=started_at)

    try:
        reconciliation_result = reconcile_broker_state(
            db,
            order_limit=order_limit,
            fill_page_size=fill_page_size,
        )
        reconcile = _reconciliation_summary(reconciliation_result)
        cleanup = cleanup_stale_trading_state(
            db,
            stale_before=started_at - timedelta(hours=stale_after_hours),
            source="post_market_maintenance",
        )
        perf_result = get_paper_performance_review(db, limit=500)
        performance = _performance_summary(perf_result)
        readiness = _readiness_summary(db)
        settings_snapshot = _settings_snapshot()

        details = {
            "phase": "post_market",
            "cleanup": cleanup,
            "reconcile": reconcile,
            "news": None,
            "performance": performance,
            "readiness": readiness,
            "settings_snapshot": settings_snapshot,
        }
        _finish_job_run(db, job_run, details=details, event_type="market_maintenance.post_market.succeeded")
    except Exception as exc:
        _fail_job_run(db, job_run, exc, event_type="market_maintenance.post_market.failed")
        raise

    # Populate trade cases after the maintenance job_run is safely committed.
    # Runs in its own transaction so a failure here never rolls back the maintenance record.
    trade_cases = _populate_trade_cases_safely(db, limit=500)
    _write_trade_cases_audit_log(db, maintenance_job_run=job_run, trade_cases=trade_cases)
    paper_review_snapshot = _paper_review_snapshot_safely(
        db,
        maintenance_job_run=job_run,
        generated_at=started_at,
        limit=500,
        performance=perf_result,
    )
    ai_trade_reviews = _ai_trade_reviews_safely(
        db,
        maintenance_job_run=job_run,
        limit=100,
    )
    paper_review_snapshot_retention = _paper_review_snapshot_retention_safely(
        db,
        maintenance_job_run=job_run,
        generated_at=started_at,
    )

    return MarketMaintenanceResult(
        job_run=job_run,
        phase="post_market",
        cleanup=cleanup,
        reconcile=reconcile,
        news=None,
        performance=performance,
        readiness=readiness,
        settings_snapshot=settings_snapshot,
        trade_cases=trade_cases,
        paper_review_snapshot=paper_review_snapshot,
        ai_trade_reviews=ai_trade_reviews,
        paper_review_snapshot_retention=paper_review_snapshot_retention,
    )

def _populate_trade_cases_safely(db: Session, *, limit: int) -> dict[str, Any]:
    try:
        result = populate_trade_cases_from_closed_round_trips(db, limit=limit)
        return {
            "job_run_id": str(result.job_run.id),
            "round_trips_seen": result.round_trips_seen,
            "inserted": result.inserted,
            "updated": result.updated,
            "skipped": result.skipped,
            "errors": result.errors,
        }
    except Exception as exc:
        logger.error(
            "Trade case population failed during post-market maintenance: %s: %s",
            exc.__class__.__name__,
            exc,
        )
        return {"status": "failed", "error": f"{exc.__class__.__name__}: {exc}"}

def _write_trade_cases_audit_log(
    db: Session,
    *,
    maintenance_job_run: JobRun,
    trade_cases: dict[str, Any],
) -> None:
    failed = "error" in trade_cases
    event_type = (
        "market_maintenance.post_market.trade_cases.failed"
        if failed
        else "market_maintenance.post_market.trade_cases.succeeded"
    )
    payload: dict[str, Any] = {"maintenance_job_run_id": str(maintenance_job_run.id)}
    if "job_run_id" in trade_cases:
        payload["trade_case_population_job_run_id"] = trade_cases["job_run_id"]
    payload.update({k: v for k, v in trade_cases.items() if k != "job_run_id"})
    try:
        record_audit_log(
            db,
            event_type=event_type,
            entity_type="job_run",
            entity_id=maintenance_job_run.id,
            message=(
                "Post-market trade case population succeeded"
                if not failed
                else "Post-market trade case population failed"
            ),
            payload=payload,
        )
        db.commit()
    except Exception as exc:
        logger.error(
            "Failed to write trade-cases audit log: %s: %s",
            exc.__class__.__name__,
            exc,
        )

def _paper_review_snapshot_safely(
    db: Session,
    *,
    maintenance_job_run: JobRun,
    generated_at: datetime,
    limit: int,
    performance: PerformanceReviewResult | None = None,
) -> dict[str, Any]:
    try:
        result = create_or_update_post_market_paper_review_snapshot(
            db,
            generated_at=generated_at,
            limit=limit,
            performance=performance,
        )
        payload = {
            "snapshot_id": str(result.snapshot.id),
            "created": result.created,
            "review_date": result.review_date.isoformat(),
            "review_type": result.review_type,
            "signal_count": result.signal_count,
            "order_intent_count": result.order_intent_count,
            "fill_count": result.fill_count,
            "diagnostic_count": result.diagnostic_count,
            "rejected_shadow_outcome_count": result.rejected_shadow_outcome_count,
            "refinement_candidate_count": result.refinement_candidate_count,
            "learning_report_saved": True,
            "learning_report_path": "paper_review_snapshots.raw_payload.learning_report",
        }
        _write_paper_review_snapshot_audit_log(
            db,
            maintenance_job_run=maintenance_job_run,
            snapshot=payload,
        )
        return payload
    except Exception as exc:
        logger.error(
            "Paper review snapshot failed during post-market maintenance: %s: %s",
            exc.__class__.__name__,
            exc,
        )
        payload = {"status": "failed", "error": f"{exc.__class__.__name__}: {exc}"}
        _write_paper_review_snapshot_audit_log(
            db,
            maintenance_job_run=maintenance_job_run,
            snapshot=payload,
        )
        return payload

def _paper_review_snapshot_retention_safely(
    db: Session,
    *,
    maintenance_job_run: JobRun,
    generated_at: datetime,
) -> dict[str, Any]:
    try:
        retention_days = max(1, int(settings.paper_review_snapshot_retention_days))
        cutoff_date = generated_at.astimezone(timezone.utc).date() - timedelta(
            days=retention_days,
        )
        payload = prune_old_paper_review_snapshots(db, before_date=cutoff_date)
        _write_paper_review_snapshot_retention_audit_log(
            db,
            maintenance_job_run=maintenance_job_run,
            retention=payload,
        )
        return payload
    except Exception as exc:
        logger.error(
            "Paper review snapshot retention failed during post-market maintenance: %s: %s",
            exc.__class__.__name__,
            exc,
        )
        payload = {"status": "failed", "error": f"{exc.__class__.__name__}: {exc}"}
        _write_paper_review_snapshot_retention_audit_log(
            db,
            maintenance_job_run=maintenance_job_run,
            retention=payload,
        )
        return payload

def _write_paper_review_snapshot_retention_audit_log(
    db: Session,
    *,
    maintenance_job_run: JobRun,
    retention: dict[str, Any],
) -> None:
    failed = "error" in retention
    event_type = (
        "market_maintenance.post_market.paper_review_snapshot_retention.failed"
        if failed
        else "market_maintenance.post_market.paper_review_snapshot_retention.succeeded"
    )
    try:
        record_audit_log(
            db,
            event_type=event_type,
            entity_type="job_run",
            entity_id=maintenance_job_run.id,
            message=(
                "Post-market paper review snapshot retention succeeded"
                if not failed
                else "Post-market paper review snapshot retention failed"
            ),
            payload={
                "maintenance_job_run_id": str(maintenance_job_run.id),
                **retention,
            },
        )
        db.commit()
    except Exception as exc:
        logger.error(
            "Failed to write paper-review snapshot retention audit log: %s: %s",
            exc.__class__.__name__,
            exc,
        )

def _write_paper_review_snapshot_audit_log(
    db: Session,
    *,
    maintenance_job_run: JobRun,
    snapshot: dict[str, Any],
) -> None:
    failed = "error" in snapshot
    event_type = (
        "market_maintenance.post_market.paper_review_snapshot.failed"
        if failed
        else "market_maintenance.post_market.paper_review_snapshot.succeeded"
    )
    try:
        record_audit_log(
            db,
            event_type=event_type,
            entity_type="job_run",
            entity_id=maintenance_job_run.id,
            message=(
                "Post-market paper review snapshot succeeded"
                if not failed
                else "Post-market paper review snapshot failed"
            ),
            payload={
                "maintenance_job_run_id": str(maintenance_job_run.id),
                **snapshot,
            },
        )
        db.commit()
    except Exception as exc:
        logger.error(
            "Failed to write paper-review snapshot audit log: %s: %s",
            exc.__class__.__name__,
            exc,
        )

def _ai_trade_reviews_safely(
    db: Session,
    *,
    maintenance_job_run: JobRun,
    limit: int,
) -> dict[str, Any]:
    try:
        result = write_ai_trade_reviews_from_paper_evidence(db, limit=limit)
        payload = {
            "job_run_id": str(result.job_run.id),
            "trade_cases_seen": result.trade_cases_seen,
            "reviews_created": result.reviews_created,
            "reviews_skipped": result.reviews_skipped,
            "suggestions_created": result.suggestions_created,
            "errors": result.errors,
        }
        _write_ai_trade_reviews_audit_log(
            db,
            maintenance_job_run=maintenance_job_run,
            ai_trade_reviews=payload,
        )
        return payload
    except Exception as exc:
        logger.error(
            "AI trade review writer failed during post-market maintenance: %s: %s",
            exc.__class__.__name__,
            exc,
        )
        payload = {"status": "failed", "error": f"{exc.__class__.__name__}: {exc}"}
        _write_ai_trade_reviews_audit_log(
            db,
            maintenance_job_run=maintenance_job_run,
            ai_trade_reviews=payload,
        )
        return payload

def _write_ai_trade_reviews_audit_log(
    db: Session,
    *,
    maintenance_job_run: JobRun,
    ai_trade_reviews: dict[str, Any],
) -> None:
    failed = "error" in ai_trade_reviews
    event_type = (
        "market_maintenance.post_market.ai_trade_reviews.failed"
        if failed
        else "market_maintenance.post_market.ai_trade_reviews.succeeded"
    )
    try:
        record_audit_log(
            db,
            event_type=event_type,
            entity_type="job_run",
            entity_id=maintenance_job_run.id,
            message=(
                "Post-market AI trade review writer succeeded"
                if not failed
                else "Post-market AI trade review writer failed"
            ),
            payload={
                "maintenance_job_run_id": str(maintenance_job_run.id),
                **ai_trade_reviews,
            },
        )
        db.commit()
    except Exception as exc:
        logger.error(
            "Failed to write AI trade-review audit log: %s: %s",
            exc.__class__.__name__,
            exc,
        )
