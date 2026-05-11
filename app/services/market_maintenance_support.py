from __future__ import annotations

from datetime import datetime, timezone

from typing import Any

from sqlalchemy import select

from sqlalchemy.orm import Session

from app.core.config import settings

from app.db.models import JobRun, OrderIntent, Signal, Strategy

from app.services.audit_logs import record_audit_log
from app.services.market_maintenance_dte import _json_safe_value

def _start_job_run(db: Session, job_name: str, *, started_at: datetime) -> JobRun:
    job_run = JobRun(
        job_name=job_name,
        status="running",
        started_at=started_at,
        details={},
    )
    db.add(job_run)
    db.flush()
    return job_run

def _finish_job_run(
    db: Session,
    job_run: JobRun,
    *,
    details: dict[str, Any],
    event_type: str,
) -> None:
    job_run.status = "succeeded"
    job_run.finished_at = datetime.now(timezone.utc)
    job_run.details = _json_safe_value(details)
    job_run.error = None
    db.add(job_run)
    record_audit_log(
        db,
        event_type=event_type,
        entity_type="job_run",
        entity_id=job_run.id,
        message="Market maintenance succeeded",
        payload=job_run.details,
    )
    db.commit()
    db.refresh(job_run)

def _fail_job_run(
    db: Session,
    job_run: JobRun,
    exc: Exception,
    *,
    event_type: str,
) -> None:
    db.rollback()
    job_run.status = "failed"
    job_run.finished_at = datetime.now(timezone.utc)
    job_run.details = {}
    job_run.error = f"{exc.__class__.__name__}: {exc}"
    db.add(job_run)
    record_audit_log(
        db,
        event_type=event_type,
        entity_type="job_run",
        entity_id=job_run.id,
        message="Market maintenance failed",
        payload={"error": job_run.error},
    )
    db.commit()
    db.refresh(job_run)

def _reconciliation_summary(result: object) -> dict[str, Any]:
    return {
        "job_run_id": str(result.job_run.id),
        "orders_seen": result.orders_seen,
        "orders_created": result.orders_created,
        "orders_updated": result.orders_updated,
        "fills_seen": result.fills_seen,
        "fills_created": result.fills_created,
        "fill_page_size_requested": result.fill_page_size_requested,
        "fill_page_size_used": result.fill_page_size_used,
        "fill_pages_fetched": result.fill_pages_fetched,
        "fill_pagination_complete": result.fill_pagination_complete,
        "fill_pagination_stop_reason": result.fill_pagination_stop_reason,
        "positions_seen": result.positions_seen,
        "position_snapshots_created": result.position_snapshots_created,
    }

def _news_summary(result: object) -> dict[str, Any]:
    return {
        "job_run_id": str(result.job_run.id),
        "market_items_seen": len(result.market_items),
        "ticker_symbols_seen": len(result.ticker_items),
        "owned_symbols": result.owned_symbols,
        "risk_assessment": result.risk_assessment,
        "sources_checked": result.sources_checked,
        "errors": result.errors,
    }

def _performance_summary(result: object) -> dict[str, Any]:
    return {
        "generated_at": result.generated_at.isoformat(),
        "fills_seen": result.fills_seen,
        "matched_round_trips": result.matched_round_trips,
        "totals": result.totals,
        "by_strategy": result.by_strategy[:20],
        "by_symbol": result.by_symbol[:20],
        "open_positions": result.open_positions,
        "signal_summary": result.signal_summary,
        "no_signal_summary": result.no_signal_summary,
        "option_selection_diagnostics": result.option_selection_diagnostics,
        "rejected_preview_outcomes": result.rejected_preview_outcomes[:20],
    }

def _readiness_summary(db: Session) -> dict[str, Any]:
    active_strategies = list(
        db.scalars(
            select(Strategy)
            .where(Strategy.is_active == True)  # noqa: E712
            .order_by(Strategy.name.asc())
        )
    )
    scanner_type_counts: dict[str, int] = {}
    preview_enabled = 0
    submit_enabled = 0
    symbols: set[str] = set()

    for strategy in active_strategies:
        scanner = (
            strategy.config.get("scanner")
            if isinstance(strategy.config, dict) and isinstance(strategy.config.get("scanner"), dict)
            else {}
        )
        scanner_type = str(scanner.get("type") or "unknown")
        scanner_type_counts[scanner_type] = scanner_type_counts.get(scanner_type, 0) + 1

        preview = scanner.get("preview") if isinstance(scanner.get("preview"), dict) else {}
        submit = scanner.get("submit") if isinstance(scanner.get("submit"), dict) else {}
        if preview.get("enabled") is True:
            preview_enabled += 1
        if submit.get("enabled") is True:
            submit_enabled += 1
        for symbol in scanner.get("symbols", []):
            if isinstance(symbol, str) and symbol.strip():
                symbols.add(symbol.strip().upper())

    return {
        "active_strategies": len(active_strategies),
        "preview_enabled_strategies": preview_enabled,
        "submit_enabled_strategies": submit_enabled,
        "scanner_type_counts": scanner_type_counts,
        "symbols": sorted(symbols),
    }

def _settings_snapshot() -> dict[str, Any]:
    return {
        "paper_mode": settings.alpaca_paper,
        "scan_enabled": settings.market_cycle_scan_enabled,
        "reconcile_enabled": settings.market_cycle_reconcile_enabled,
        "preview_enabled": settings.market_cycle_preview_enabled,
        "exit_enabled": settings.market_cycle_exit_enabled,
        "news_enabled": settings.market_cycle_news_enabled,
        "submit_enabled": settings.market_cycle_submit_enabled,
        "trading_automation_enabled": settings.trading_automation_enabled,
        "max_auto_orders_per_cycle": settings.max_auto_orders_per_cycle,
        "max_auto_orders_per_day": settings.max_auto_orders_per_day,
        "max_open_positions": settings.max_open_positions,
        "max_open_positions_per_symbol": settings.max_open_positions_per_symbol,
    }

def _disabled_step(step_name: str) -> dict[str, Any]:
    return {"status": "disabled", "step": step_name}
