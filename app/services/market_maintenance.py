from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import JobRun, OrderIntent, Signal, Strategy
from app.services.audit_logs import record_audit_log
from app.services.broker_reconciliation import reconcile_broker_state
from app.services.news_scanner import scan_market_news
from app.services.performance_review import get_paper_performance_review
from app.services.trade_cases import populate_trade_cases_from_closed_round_trips


AUTO_POST_MARKET_START_HOUR_UTC = 17


@dataclass(slots=True)
class MarketMaintenanceResult:
    job_run: JobRun
    phase: str
    cleanup: dict[str, Any]
    reconcile: dict[str, Any] | None
    news: dict[str, Any] | None
    performance: dict[str, Any] | None
    readiness: dict[str, Any]
    settings_snapshot: dict[str, Any]
    trade_cases: dict[str, Any] | None = None


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
        performance = _performance_summary(get_paper_performance_review(db, limit=5000))
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
    trade_cases = _populate_trade_cases_safely(db, limit=5000)
    _write_trade_cases_audit_log(db, maintenance_job_run=job_run, trade_cases=trade_cases)

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
    )


def cleanup_stale_trading_state(
    db: Session,
    *,
    stale_before: datetime,
    source: str,
    limit: int = 1000,
) -> dict[str, Any]:
    stale_signals = list(
        db.scalars(
            select(Signal)
            .where(Signal.status == "new")
            .where(Signal.created_at < stale_before)
            .order_by(Signal.created_at.asc())
            .limit(limit)
        )
    )
    stale_order_intents = list(
        db.scalars(
            select(OrderIntent)
            .where(OrderIntent.status == "previewed")
            .where(OrderIntent.submitted_at.is_(None))
            .where(OrderIntent.created_at < stale_before)
            .order_by(OrderIntent.created_at.asc())
            .limit(limit)
        )
    )

    reason = f"Marked stale by {source} before {stale_before.isoformat()}"
    for signal in stale_signals:
        signal.status = "stale"
        signal.rejected_reason = reason
        db.add(signal)

    for order_intent in stale_order_intents:
        order_intent.status = "stale"
        order_intent.rejection_reason = reason
        db.add(order_intent)

    return {
        "stale_before": stale_before.isoformat(),
        "signals_marked_stale": len(stale_signals),
        "order_intents_marked_stale": len(stale_order_intents),
        "oldest_stale_signal_created_at": _oldest_created_at(stale_signals),
        "oldest_stale_order_intent_created_at": _oldest_created_at(stale_order_intents),
        "signals_by_strategy_id": _counts_by_strategy_id(stale_signals),
        "order_intents_by_strategy_id": _counts_by_strategy_id(stale_order_intents),
        "signal_ids": [str(signal.id) for signal in stale_signals],
        "order_intent_ids": [str(order_intent.id) for order_intent in stale_order_intents],
    }


def _oldest_created_at(rows: list[object]) -> str | None:
    timestamps = [
        getattr(row, "created_at", None)
        for row in rows
        if getattr(row, "created_at", None) is not None
    ]
    if not timestamps:
        return None
    return min(timestamps).isoformat()


def _counts_by_strategy_id(rows: list[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        strategy_id = getattr(row, "strategy_id", None)
        key = str(strategy_id) if strategy_id is not None else "none"
        counts[key] = counts.get(key, 0) + 1
    return counts


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


@dataclass(slots=True)
class PatchStrategyDteResult:
    job_run: JobRun
    strategies_seen: int
    strategies_updated: int
    strategies_skipped: int


def patch_strategy_dte(
    db: Session,
    *,
    min_dte: int = 2,
    max_dte: int = 30,
) -> PatchStrategyDteResult:
    """Update scanner.preview DTE window on all active strategies that need it."""
    import copy

    started_at = datetime.now(timezone.utc)
    job_run = JobRun(
        job_name="patch_strategy_dte",
        status="running",
        started_at=started_at,
        details={},
    )
    db.add(job_run)
    db.flush()

    strategies = list(db.scalars(select(Strategy).where(Strategy.is_active == True)))  # noqa: E712
    updated = 0
    skipped = 0

    for strategy in strategies:
        config = strategy.config or {}
        scanner = config.get("scanner")
        if not isinstance(scanner, dict):
            skipped += 1
            continue
        preview = scanner.get("preview")
        if not isinstance(preview, dict):
            skipped += 1
            continue
        if (
            preview.get("min_days_to_expiration") == min_dte
            and preview.get("max_days_to_expiration") == max_dte
        ):
            skipped += 1
            continue

        new_config = copy.deepcopy(config)
        new_config["scanner"]["preview"]["min_days_to_expiration"] = min_dte
        new_config["scanner"]["preview"]["max_days_to_expiration"] = max_dte
        strategy.config = new_config
        db.add(strategy)
        updated += 1

    details = {
        "strategies_seen": len(strategies),
        "strategies_updated": updated,
        "strategies_skipped": skipped,
        "min_dte": min_dte,
        "max_dte": max_dte,
    }
    job_run.status = "succeeded"
    job_run.finished_at = datetime.now(timezone.utc)
    job_run.details = details
    db.add(job_run)
    db.commit()
    db.refresh(job_run)

    logger.info(
        "patch_strategy_dte succeeded: updated=%d skipped=%d min_dte=%d max_dte=%d",
        updated, skipped, min_dte, max_dte,
    )
    return PatchStrategyDteResult(
        job_run=job_run,
        strategies_seen=len(strategies),
        strategies_updated=updated,
        strategies_skipped=skipped,
    )


def _json_safe_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    return value
