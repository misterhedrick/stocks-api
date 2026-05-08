from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from time import perf_counter
from typing import Any
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.utils import current_trading_day_start_utc
from app.db.models import BrokerOrder, JobRun, OrderIntent, Signal, Strategy
from app.schemas.options import OptionContractSelectionCreate
from app.schemas.order_intents import OrderIntentPreviewCreate
from app.services.automation_guard import can_auto_submit_order_intent
from app.services.audit_logs import record_audit_log
from app.services.broker_reconciliation import reconcile_broker_state
from app.services.news_scanner import scan_market_news
from app.services.order_intents import preview_order_intent_from_signal, submit_order_intent
from app.services.position_exits import evaluate_position_exits
from app.services.signal_scanner import scan_signals


@dataclass(slots=True)
class MarketCycleResult:
    job_run: JobRun
    scan_enabled: bool
    reconcile_enabled: bool
    preview_enabled: bool
    exit_enabled: bool
    news_enabled: bool
    submit_enabled: bool
    scan: dict[str, Any] | None
    reconcile: dict[str, Any] | None
    preview: dict[str, Any] | None
    exits: dict[str, Any] | None
    news: dict[str, Any] | None
    submit: dict[str, Any] | None
    timings: dict[str, float] | None = None
    phase_timeout_seconds: int | None = None
    diagnostics: dict[str, Any] | None = None
    symbol: str | None = None


# Stable integer key for the PostgreSQL advisory lock that prevents concurrent
# market_cycle runs. Must be unique across all jobs; chosen arbitrarily.
_MARKET_CYCLE_LOCK_KEY = 4_096_001
_MARKET_ENTRY_LOCK_BASE_KEY = 4_096_100
SUPPORTED_MARKET_ENTRY_SYMBOLS = ("SPY", "QQQ", "AAPL", "MSFT", "NVDA")


EXPOSURE_BROKER_ORDER_STATUSES = (
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "filled",
    "submitted",
)


def run_market_entry_cycle(
    db: Session,
    *,
    symbol: str,
    scan_limit: int = 100,
    order_limit: int = 100,
    fill_page_size: int = 100,
    phase_timeout_seconds: int | None = None,
) -> MarketCycleResult:
    normalized_symbol = normalize_market_entry_symbol(symbol)
    return run_market_cycle(
        db,
        symbol=normalized_symbol,
        scan_limit=scan_limit,
        order_limit=order_limit,
        fill_page_size=fill_page_size,
        reconcile_enabled_override=False,
        news_enabled_override=False,
        exit_enabled_override=False,
        phase_timeout_seconds=phase_timeout_seconds,
        job_name="market_entry_cycle",
        event_prefix="market_entry_cycle",
        lock_key=_market_entry_lock_key(normalized_symbol),
    )


def normalize_market_entry_symbol(symbol: str) -> str:
    normalized = _normalize_symbol(symbol)
    if normalized is None:
        raise ValueError("symbol is required")
    if normalized not in SUPPORTED_MARKET_ENTRY_SYMBOLS:
        supported = ", ".join(SUPPORTED_MARKET_ENTRY_SYMBOLS)
        raise ValueError(f"unsupported symbol {normalized!r}; supported symbols: {supported}")
    return normalized


def _normalize_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    normalized = symbol.strip().upper()
    return normalized or None


def _market_entry_lock_key(symbol: str) -> int:
    return _MARKET_ENTRY_LOCK_BASE_KEY + SUPPORTED_MARKET_ENTRY_SYMBOLS.index(symbol)


def run_market_cycle(
    db: Session,
    *,
    symbol: str | None = None,
    scan_limit: int = 100,
    order_limit: int = 100,
    fill_page_size: int = 100,
    scan_enabled_override: bool | None = None,
    reconcile_enabled_override: bool | None = None,
    preview_enabled_override: bool | None = None,
    exit_enabled_override: bool | None = None,
    news_enabled_override: bool | None = None,
    submit_enabled_override: bool | None = None,
    reconcile_before_exit: bool = False,
    phase_timeout_seconds: int | None = None,
    job_name: str = "market_cycle",
    event_prefix: str = "market_cycle",
    lock_key: int = _MARKET_CYCLE_LOCK_KEY,
) -> MarketCycleResult:
    symbol_filter = _normalize_symbol(symbol)
    # Non-blocking advisory lock: only one market_cycle may run at a time.
    # pg_try_advisory_xact_lock is transaction-scoped and releases on commit/rollback.
    lock_acquired = db.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)").bindparams(key=lock_key)
    )
    if not lock_acquired:
        logger.info(
            "%s skipped: pg_try_advisory_xact_lock(%d) held by another running instance",
            event_prefix,
            lock_key,
        )
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

    started_at = datetime.now(timezone.utc)
    cycle_started = perf_counter()
    timings: dict[str, float] = {}
    job_run = JobRun(
        job_name=job_name,
        status="running",
        started_at=started_at,
        details={"symbol": symbol_filter} if symbol_filter else {},
    )
    db.add(job_run)
    db.flush()

    scan_enabled = _switch(settings.market_cycle_scan_enabled, scan_enabled_override)
    reconcile_enabled = _switch(
        settings.market_cycle_reconcile_enabled,
        reconcile_enabled_override,
    )
    preview_enabled = _switch(
        settings.market_cycle_preview_enabled,
        preview_enabled_override,
    )
    exit_enabled = _switch(settings.market_cycle_exit_enabled, exit_enabled_override)
    news_enabled = _switch(settings.market_cycle_news_enabled, news_enabled_override)
    submit_enabled = _switch(
        settings.market_cycle_submit_enabled,
        submit_enabled_override,
    )

    phase_timeout = (
        settings.market_cycle_phase_timeout_seconds
        if phase_timeout_seconds is None
        else phase_timeout_seconds
    )

    logger.info(
        "%s starting: symbol=%s scan_limit=%d order_limit=%d fill_page_size=%d phase_timeout=%ds",
        event_prefix,
        symbol_filter,
        scan_limit,
        order_limit,
        fill_page_size,
        phase_timeout,
    )
    logger.info(
        "%s config: preview_enabled=%s submit_enabled=%s global_market_cycle_submit_enabled=%s "
        "trading_automation_enabled=%s auto_submit_requires_paper=%s alpaca_paper=%s",
        event_prefix,
        preview_enabled,
        submit_enabled,
        settings.market_cycle_submit_enabled,
        settings.trading_automation_enabled,
        settings.auto_submit_requires_paper,
        settings.alpaca_paper,
    )

    scan = None
    reconcile = None
    preview = _disabled_step("preview")
    exits = _disabled_step("exits")
    news = _disabled_step("news")
    submit = _disabled_step("submit")

    try:
        created_signal_ids: list[uuid.UUID] = []
        submittable_order_intent_ids: list[uuid.UUID] = []
        news_blocks_entries = False
        if scan_enabled and not _phase_budget_exceeded(cycle_started, phase_timeout):
            step_started = perf_counter()
            logger.info("market_cycle phase=scan starting (scan_limit=%d)", scan_limit)
            scan_kwargs = {"symbol": symbol_filter} if symbol_filter is not None else {}
            scan_result = scan_signals(db, limit=scan_limit, **scan_kwargs)
            elapsed = _elapsed_seconds(step_started)
            timings["scan_seconds"] = elapsed
            created_signal_ids = scan_result.created_signal_ids
            scan = {
                "symbol": symbol_filter,
                "job_run_id": str(scan_result.job_run.id),
                "strategies_seen": scan_result.strategies_seen,
                "strategies_scanned": scan_result.strategies_scanned,
                "signals_created": scan_result.signals_created,
                "signals_skipped": scan_result.signals_skipped,
                "errors": scan_result.errors,
                "no_signal_reasons": scan_result.no_signal_reasons,
                "created_signal_ids": [
                    str(signal_id) for signal_id in scan_result.created_signal_ids
                ],
            }
            logger.info(
                "market_cycle phase=scan done: elapsed=%.3fs signals_created=%d signals_skipped=%d errors=%d",
                elapsed,
                scan_result.signals_created,
                scan_result.signals_skipped,
                len(scan_result.errors),
            )
        else:
            scan = (
                _timeout_step("scan", phase_timeout)
                if scan_enabled
                else _disabled_step("scan")
            )
            timings["scan_seconds"] = 0.0
            if scan_enabled:
                logger.warning(
                    "market_cycle phase=scan skipped: runtime budget reached at %.3fs (limit=%ds)",
                    _elapsed_seconds(cycle_started),
                    phase_timeout,
                )

        if news_enabled and not _phase_budget_exceeded(cycle_started, phase_timeout):
            step_started = perf_counter()
            logger.info("market_cycle phase=news starting")
            news_result = scan_market_news(db)
            elapsed = _elapsed_seconds(step_started)
            timings["news_seconds"] = elapsed
            news = {
                "job_run_id": str(news_result.job_run.id),
                "market_items": news_result.market_items,
                "ticker_items": news_result.ticker_items,
                "owned_symbols": news_result.owned_symbols,
                "risk_assessment": news_result.risk_assessment,
                "sources_checked": news_result.sources_checked,
                "errors": news_result.errors,
            }
            risk_assessment = news_result.risk_assessment
            news_blocks_entries = (
                isinstance(risk_assessment, dict)
                and risk_assessment.get("should_block_new_entries") is True
            )
            logger.info(
                "market_cycle phase=news done: elapsed=%.3fs sources=%d blocks_entries=%s errors=%d",
                elapsed,
                news_result.sources_checked,
                news_blocks_entries,
                len(news_result.errors),
            )
        else:
            timings["news_seconds"] = 0.0
            if news_enabled:
                news = _timeout_step("news", phase_timeout)
                logger.warning(
                    "market_cycle phase=news skipped: runtime budget reached at %.3fs (limit=%ds)",
                    _elapsed_seconds(cycle_started),
                    phase_timeout,
                )

        if preview_enabled and not _phase_budget_exceeded(cycle_started, phase_timeout):
            step_started = perf_counter()
            signal_ids_for_preview = _signal_ids_for_preview(
                db,
                created_signal_ids,
                limit=scan_limit,
                symbol=symbol_filter,
            )
            logger.info(
                "market_cycle phase=preview starting: signals_for_preview=%d",
                len(signal_ids_for_preview),
            )
            if news_blocks_entries:
                news_risk = news.get("risk_assessment") if isinstance(news, dict) else None
                logger.warning(
                    "News risk gate blocked %d entry preview(s) this cycle",
                    len(signal_ids_for_preview),
                )
                record_audit_log(
                    db,
                    event_type="market_cycle.preview_blocked_by_news_risk",
                    entity_type="job_run",
                    entity_id=job_run.id,
                    message="News risk gate blocked new entry previews this cycle",
                    payload={
                        "signals_blocked": len(signal_ids_for_preview),
                        "news_risk": news_risk,
                    },
                )
                preview = {
                    "status": "blocked",
                    "signals_seen": len(signal_ids_for_preview),
                    "previews_created": 0,
                    "previews_skipped": len(signal_ids_for_preview),
                    "errors": ["News risk gate blocked new entry previews"],
                    "order_intent_ids": [],
                    "news_risk": news_risk,
                }
            else:
                preview = _preview_created_signals(
                    db,
                    signal_ids_for_preview,
                    cycle_started=cycle_started,
                    phase_timeout=phase_timeout,
                    symbol=symbol_filter,
                )
            submittable_order_intent_ids.extend(_order_intent_ids_from_preview(preview))
            logger.info(
                "market_cycle submit_candidates_from_preview count=%d ids=%s",
                len(submittable_order_intent_ids),
                [str(order_intent_id) for order_intent_id in submittable_order_intent_ids],
            )
            elapsed = _elapsed_seconds(step_started)
            timings["preview_seconds"] = elapsed
            logger.info(
                "market_cycle phase=preview done: elapsed=%.3fs previews_created=%s previews_skipped=%s errors=%d",
                elapsed,
                preview.get("previews_created") if isinstance(preview, dict) else "n/a",
                preview.get("previews_skipped") if isinstance(preview, dict) else "n/a",
                len(preview.get("errors", [])) if isinstance(preview, dict) else 0,
            )
        else:
            timings["preview_seconds"] = 0.0
            if preview_enabled:
                preview = _timeout_step("preview", phase_timeout)
                logger.warning(
                    "market_cycle phase=preview skipped: runtime budget reached at %.3fs (limit=%ds)",
                    _elapsed_seconds(cycle_started),
                    phase_timeout,
                )

        if (
            reconcile_enabled
            and reconcile_before_exit
            and not _phase_budget_exceeded(cycle_started, phase_timeout)
        ):
            step_started = perf_counter()
            logger.info(
                "market_cycle phase=reconcile starting (pre-exit): order_limit=%d fill_page_size=%d",
                order_limit,
                fill_page_size,
            )
            reconcile = _reconcile_step(
                db,
                order_limit=order_limit,
                fill_page_size=fill_page_size,
                cycle_started=cycle_started,
                phase_timeout=phase_timeout,
            )
            elapsed = _elapsed_seconds(step_started)
            timings["reconcile_seconds"] = elapsed
            logger.info(
                "market_cycle phase=reconcile done: elapsed=%.3fs orders_seen=%s fills_seen=%s positions_seen=%s",
                elapsed,
                reconcile.get("orders_seen") if isinstance(reconcile, dict) else "n/a",
                reconcile.get("fills_seen") if isinstance(reconcile, dict) else "n/a",
                reconcile.get("positions_seen") if isinstance(reconcile, dict) else "n/a",
            )

        if exit_enabled and not _phase_budget_exceeded(cycle_started, phase_timeout):
            step_started = perf_counter()
            logger.info("market_cycle phase=exits starting (scan_limit=%d)", scan_limit)
            exit_result = evaluate_position_exits(db, limit=scan_limit)
            elapsed = _elapsed_seconds(step_started)
            timings["exit_seconds"] = elapsed
            exits = {
                "status": "completed",
                "positions_seen": exit_result.positions_seen,
                "positions_evaluated": exit_result.positions_evaluated,
                "exits_created": exit_result.exits_created,
                "exits_skipped": exit_result.exits_skipped,
                "errors": exit_result.errors,
                "no_exit_reasons": exit_result.no_exit_reasons,
                "position_ownership": exit_result.position_ownership,
                "order_intent_ids": [
                    str(order_intent_id)
                    for order_intent_id in exit_result.order_intent_ids
                ],
            }
            submittable_order_intent_ids.extend(exit_result.order_intent_ids)
            logger.info(
                "market_cycle phase=exits done: elapsed=%.3fs positions_seen=%d exits_created=%d errors=%d",
                elapsed,
                exit_result.positions_seen,
                exit_result.exits_created,
                len(exit_result.errors),
            )
        else:
            timings["exit_seconds"] = 0.0
            if exit_enabled:
                exits = _timeout_step("exits", phase_timeout)
                logger.warning(
                    "market_cycle phase=exits skipped: runtime budget reached at %.3fs (limit=%ds)",
                    _elapsed_seconds(cycle_started),
                    phase_timeout,
                )

        submit_candidates_count = len(submittable_order_intent_ids)
        submit_budget_exceeded = _phase_budget_exceeded(cycle_started, phase_timeout)
        submit_skip_reason = None
        if not submit_enabled:
            submit_skip_reason = "submit disabled by global config"
        elif submit_budget_exceeded:
            submit_skip_reason = "runtime budget exceeded"
        logger.info(
            "market_cycle submit_phase_check submit_enabled=%s global_market_cycle_submit_enabled=%s "
            "candidate_count=%d elapsed_seconds=%.3f remaining_budget_seconds=%s will_run=%s skip_reason=%s",
            submit_enabled,
            settings.market_cycle_submit_enabled,
            submit_candidates_count,
            _elapsed_seconds(cycle_started),
            _remaining_budget_seconds(cycle_started, phase_timeout),
            submit_enabled and not submit_budget_exceeded,
            submit_skip_reason,
        )

        if submit_enabled and not submit_budget_exceeded:
            step_started = perf_counter()
            logger.info(
                "market_cycle phase=submit starting: order_intents=%d",
                len(submittable_order_intent_ids),
            )
            submit = _submit_previewed_order_intents(
                db,
                submittable_order_intent_ids,
                cycle_id=str(job_run.id),
                cycle_started=cycle_started,
                phase_timeout=phase_timeout,
                symbol=symbol_filter,
            )
            elapsed = _elapsed_seconds(step_started)
            timings["submit_seconds"] = elapsed
            logger.info(
                "market_cycle phase=submit done: elapsed=%.3fs submitted=%s rejected=%s skipped=%s errors=%d",
                elapsed,
                submit.get("submitted") if isinstance(submit, dict) else "n/a",
                submit.get("rejected") if isinstance(submit, dict) else "n/a",
                submit.get("skipped") if isinstance(submit, dict) else "n/a",
                len(submit.get("errors", [])) if isinstance(submit, dict) else 0,
            )
        else:
            timings["submit_seconds"] = 0.0
            if submit_enabled:
                submit = {
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
                logger.warning(
                    "market_cycle phase=submit skipped: runtime budget reached at %.3fs (limit=%ds)",
                    _elapsed_seconds(cycle_started),
                    phase_timeout,
                )
            else:
                submit = {
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
                if submit_candidates_count:
                    logger.warning(
                        "market_cycle phase=submit skipped: submit disabled by global config candidates=%d ids=%s",
                        submit_candidates_count,
                        [
                            str(order_intent_id)
                            for order_intent_id in submittable_order_intent_ids
                        ],
                    )

        if (
            reconcile_enabled
            and reconcile is None
            and not _phase_budget_exceeded(cycle_started, phase_timeout)
        ):
            step_started = perf_counter()
            logger.info(
                "market_cycle phase=reconcile starting (post-cycle): order_limit=%d fill_page_size=%d",
                order_limit,
                fill_page_size,
            )
            reconcile = _reconcile_step(
                db,
                order_limit=order_limit,
                fill_page_size=fill_page_size,
                cycle_started=cycle_started,
                phase_timeout=phase_timeout,
            )
            elapsed = _elapsed_seconds(step_started)
            timings["reconcile_seconds"] = elapsed
            logger.info(
                "market_cycle phase=reconcile done: elapsed=%.3fs orders_seen=%s fills_seen=%s positions_seen=%s",
                elapsed,
                reconcile.get("orders_seen") if isinstance(reconcile, dict) else "n/a",
                reconcile.get("fills_seen") if isinstance(reconcile, dict) else "n/a",
                reconcile.get("positions_seen") if isinstance(reconcile, dict) else "n/a",
            )
        else:
            if reconcile is None:
                reconcile = (
                    _timeout_step("reconcile", phase_timeout)
                    if reconcile_enabled
                    else _disabled_step("reconcile")
                )
                timings["reconcile_seconds"] = 0.0
                if reconcile_enabled:
                    logger.warning(
                        "market_cycle phase=reconcile skipped: runtime budget reached at %.3fs (limit=%ds)",
                        _elapsed_seconds(cycle_started),
                        phase_timeout,
                    )

        timings["total_seconds"] = _elapsed_seconds(cycle_started)

        diagnostics = _diagnostics_for_steps(
            scan=scan,
            preview=preview,
            exits=exits,
            submit=submit,
            news=news,
            reconcile=reconcile,
        )
        if symbol_filter is not None:
            diagnostics["symbol"] = symbol_filter

        # Mark as partial when the runtime budget cut short one or more phases.
        budget_exceeded = bool(diagnostics.get("skipped_steps"))
        final_status = "partial" if budget_exceeded else "succeeded"
        if budget_exceeded:
            logger.warning(
                "market_cycle completed with partial results: skipped_steps=%s total_elapsed=%.3fs budget=%ds",
                diagnostics["skipped_steps"],
                timings["total_seconds"],
                phase_timeout,
            )
        else:
            logger.info(
                "market_cycle completed successfully: total_elapsed=%.3fs",
                timings["total_seconds"],
            )

        details = {
            "symbol": symbol_filter,
            "scan_enabled": scan_enabled,
            "reconcile_enabled": reconcile_enabled,
            "preview_enabled": preview_enabled,
            "exit_enabled": exit_enabled,
            "news_enabled": news_enabled,
            "submit_enabled": submit_enabled,
            "scan": scan,
            "reconcile": reconcile,
            "preview": preview,
            "exits": exits,
            "news": news,
            "submit": submit,
            "timings": timings,
            "phase_timeout_seconds": phase_timeout,
            "diagnostics": diagnostics,
        }
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
    except Exception as exc:
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
        logger.error(
            "market_cycle failed after %.3fs: %s: %s",
            timings["total_seconds"],
            exc.__class__.__name__,
            exc,
        )
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
        raise


def _disabled_step(step_name: str) -> dict[str, Any]:
    return {"status": "disabled", "step": step_name}


def _timeout_step(step_name: str, phase_timeout_seconds: int) -> dict[str, Any]:
    return {
        "status": "skipped",
        "step": step_name,
        "reason": f"market cycle phase budget reached after {phase_timeout_seconds}s",
    }


def _switch(default: bool, override: bool | None) -> bool:
    return default if override is None else override


def _elapsed_seconds(started: float) -> float:
    return round(perf_counter() - started, 3)


def _phase_budget_exceeded(started: float, phase_timeout_seconds: int) -> bool:
    if phase_timeout_seconds <= 0:
        return False
    return perf_counter() - started >= phase_timeout_seconds


def _reconcile_step(
    db: Session,
    *,
    order_limit: int,
    fill_page_size: int,
    cycle_started: float,
    phase_timeout: int,
) -> dict[str, Any]:
    deadline = cycle_started + phase_timeout if phase_timeout > 0 else None
    reconciliation_result = reconcile_broker_state(
        db,
        order_limit=order_limit,
        fill_page_size=fill_page_size,
        deadline=deadline,
    )
    return {
        "job_run_id": str(reconciliation_result.job_run.id),
        "orders_seen": reconciliation_result.orders_seen,
        "orders_created": reconciliation_result.orders_created,
        "orders_updated": reconciliation_result.orders_updated,
        "fills_seen": reconciliation_result.fills_seen,
        "fills_created": reconciliation_result.fills_created,
        "fill_page_size_requested": reconciliation_result.fill_page_size_requested,
        "fill_page_size_used": reconciliation_result.fill_page_size_used,
        "fill_pages_fetched": reconciliation_result.fill_pages_fetched,
        "fill_pagination_complete": reconciliation_result.fill_pagination_complete,
        "fill_pagination_stop_reason": reconciliation_result.fill_pagination_stop_reason,
        "positions_seen": reconciliation_result.positions_seen,
        "position_snapshots_created": reconciliation_result.position_snapshots_created,
    }


def _diagnostics_for_steps(**steps: dict[str, Any] | None) -> dict[str, Any]:
    errors_by_category: dict[str, int] = {}
    skipped_steps = []
    for step_name, step in steps.items():
        if not isinstance(step, dict):
            continue
        if step.get("status") == "skipped":
            skipped_steps.append(step_name)
        for error in step.get("errors", []) or []:
            category = _error_category(str(error))
            errors_by_category[category] = errors_by_category.get(category, 0) + 1
    return {
        "status": "completed",
        "skipped_steps": skipped_steps,
        "errors_by_category": errors_by_category,
    }


def _error_category(message: str) -> str:
    clean_message = message.lower()
    if "too many requests" in clean_message or "429" in clean_message:
        return "rate_limit"
    if "timeout" in clean_message or "timed out" in clean_message:
        return "timeout"
    if "trade_windows" in clean_message or "outside scanner.submit" in clean_message:
        return "trade_window"
    if "spread" in clean_message:
        return "spread_filter"
    if "no stock bars" in clean_message or "no latest stock quote" in clean_message:
        return "market_data_missing"
    if "duplicate signal" in clean_message:
        return "duplicate_signal"
    if "automation" in clean_message or "auto-submit" in clean_message:
        return "automation_guard"
    return "other"


def _exit_alert_payload(exits: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(exits, dict):
        return None
    if exits.get("status") == "disabled":
        return None

    errors = [str(error) for error in exits.get("errors", []) or []]
    no_exit_reasons = [str(reason) for reason in exits.get("no_exit_reasons", []) or []]
    positions_seen = _int_from_step(exits.get("positions_seen"))
    positions_evaluated = _int_from_step(exits.get("positions_evaluated"))
    exits_created = _int_from_step(exits.get("exits_created"))
    status = str(exits.get("status", "completed"))

    needs_attention = (
        status in {"skipped", "failed"}
        or bool(errors)
        or (positions_seen > 0 and positions_evaluated == 0)
        or (positions_seen > 0 and exits_created == 0 and _has_attention_reason(no_exit_reasons))
    )
    if not needs_attention:
        return None

    return {
        "status": status,
        "positions_seen": positions_seen,
        "positions_evaluated": positions_evaluated,
        "exits_created": exits_created,
        "errors": errors[:25],
        "no_exit_reasons": no_exit_reasons[:25],
        "reason_categories": _reason_categories(errors + no_exit_reasons),
    }


def _int_from_step(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _has_attention_reason(reasons: list[str]) -> bool:
    attention_terms = (
        "missing exit config",
        "does not have scanner.exit",
        "inactive strategy",
        "unmanaged",
        "no linked entry",
        "max spread",
        "unable to derive",
        "not found",
    )
    return any(
        any(term in reason.lower() for term in attention_terms)
        for reason in reasons
    )


def _reason_categories(reasons: list[str]) -> dict[str, int]:
    categories: dict[str, int] = {}
    for reason in reasons:
        category = _error_category(reason)
        categories[category] = categories.get(category, 0) + 1
    return categories


def _preview_created_signals(
    db: Session,
    signal_ids: list[uuid.UUID],
    *,
    cycle_started: float,
    phase_timeout: int,
    symbol: str | None = None,
) -> dict[str, Any]:
    symbol_filter = _normalize_symbol(symbol)
    deadline = cycle_started + phase_timeout if phase_timeout > 0 else None
    previews_created = 0
    previews_skipped = 0
    errors: list[str] = []
    order_intent_ids: list[str] = []
    skipped_reasons: Counter[str] = Counter()

    for i, signal_id in enumerate(signal_ids):
        if deadline is not None and perf_counter() >= deadline:
            remaining = len(signal_ids) - i
            previews_skipped += remaining
            errors.append(
                f"Skipped {remaining} signal(s): runtime budget exceeded"
            )
            logger.warning(
                "market_cycle preview loop stopped: budget exceeded after %d/%d signals",
                i,
                len(signal_ids),
            )
            break

        signal = db.get(Signal, signal_id)
        if signal is None:
            previews_skipped += 1
            skipped_reasons["not_found"] += 1
            errors.append(f"Signal '{signal_id}' was not found")
            continue
        if symbol_filter is not None and not _signal_matches_symbol(signal, symbol_filter):
            previews_skipped += 1
            skipped_reasons["symbol_mismatch"] += 1
            errors.append(
                f"Signal '{signal_id}' skipped: symbol does not match {symbol_filter}"
            )
            continue

        if _signal_preview_attempts_exhausted(signal):
            previews_skipped += 1
            skipped_reasons["max_preview_attempts"] += 1
            errors.append(
                f"Signal '{signal_id}': max preview attempts reached "
                f"({signal.preview_attempts}/{_options_preview_max_attempts()})"
            )
            _mark_signal_preview_rejected(db, signal)
            continue

        strategy = db.get(Strategy, signal.strategy_id) if signal.strategy_id else None
        if strategy is None:
            previews_skipped += 1
            skipped_reasons["missing_strategy"] += 1
            errors.append(f"Signal '{signal_id}' has no strategy")
            continue

        delay_reason = _entry_preview_delay_reason(strategy)
        if delay_reason is not None:
            previews_skipped += 1
            skipped_reasons["delayed"] += 1
            errors.append(f"Signal '{signal_id}': {delay_reason}")
            continue

        try:
            payload = _preview_payload_for_signal(signal, strategy)
        except ValueError as exc:
            previews_skipped += 1
            skipped_reasons["invalid_preview_config"] += 1
            errors.append(f"Signal '{signal_id}': {exc}")
            continue

        try:
            order_intent = preview_order_intent_from_signal(db, payload, deadline=deadline)
        except Exception as exc:
            previews_skipped += 1
            skipped_reasons[_preview_failure_reason_key(exc)] += 1
            _record_signal_preview_failure(db, signal, exc)
            errors.append(f"Signal '{signal_id}': {exc.__class__.__name__}: {exc}")
            continue

        previews_created += 1
        order_intent_ids.append(str(order_intent.id))
        logger.info(
            "market_cycle preview_created intent_id=%s signal_id=%s ticker=%s option_symbol=%s",
            order_intent.id,
            signal.id,
            order_intent.underlying_symbol,
            order_intent.option_symbol,
        )

    return {
        "status": "completed",
        "symbol": symbol_filter,
        "signals_seen": len(signal_ids),
        "previews_created": previews_created,
        "previews_skipped": previews_skipped,
        "errors": errors,
        "skipped_reasons": dict(skipped_reasons),
        "order_intent_ids": order_intent_ids,
    }


def _record_signal_preview_failure(db: Session, signal: Signal, exc: Exception) -> None:
    now = datetime.now(timezone.utc)
    diagnostics = getattr(exc, "diagnostics", None)
    reason_counts = {}
    if isinstance(diagnostics, dict) and isinstance(diagnostics.get("reason_counts"), dict):
        reason_counts = dict(diagnostics["reason_counts"])

    signal.preview_attempts = int(signal.preview_attempts or 0) + 1
    signal.last_previewed_at = now
    signal.last_preview_error = _concise_error_message(exc)
    signal.last_preview_error_code = exc.__class__.__name__
    signal.preview_rejection_reasons = reason_counts or None

    if _signal_preview_attempts_exhausted(signal):
        signal.status = "preview_rejected"
        signal.rejected_reason = signal.last_preview_error
        logger.info(
            "market_cycle signal preview rejected after max attempts: signal_id=%s attempts=%d error_code=%s reasons=%s",
            signal.id,
            signal.preview_attempts,
            signal.last_preview_error_code,
            reason_counts,
        )

    db.add(signal)
    db.commit()


def _mark_signal_preview_rejected(db: Session, signal: Signal) -> None:
    if signal.status != "preview_rejected":
        signal.status = "preview_rejected"
        if not signal.rejected_reason:
            signal.rejected_reason = (
                f"Max preview attempts reached ({signal.preview_attempts}/{_options_preview_max_attempts()})"
            )
        db.add(signal)
        db.commit()


def _signal_preview_attempts_exhausted(signal: Signal) -> bool:
    return int(signal.preview_attempts or 0) >= _options_preview_max_attempts()


def _options_preview_max_attempts() -> int:
    try:
        return max(int(settings.options_preview_max_attempts), 1)
    except (TypeError, ValueError):
        return 3


def _preview_failure_reason_key(exc: Exception) -> str:
    if exc.__class__.__name__ == "OptionContractNotFoundError":
        return "option_contract_not_found"
    return _error_category(str(exc))


def _concise_error_message(exc: Exception, *, max_length: int = 500) -> str:
    text = f"{exc.__class__.__name__}: {exc}"
    text = " ".join(text.split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def _entry_preview_delay_reason(strategy: Strategy) -> str | None:
    if not settings.market_cycle_submit_enabled:
        return None

    try:
        submit_config = _submit_config_for_strategy(strategy)
    except ValueError:
        return None

    try:
        _validate_trade_windows(submit_config, now=datetime.now(timezone.utc))
    except ValueError as exc:
        return f"auto-preview delayed until scanner.submit.trade_windows opens: {exc}"
    return None


def _signal_ids_for_preview(
    db: Session,
    created_signal_ids: list[uuid.UUID],
    *,
    limit: int,
    symbol: str | None = None,
) -> list[uuid.UUID]:
    symbol_filter = _normalize_symbol(symbol)
    signal_ids = list(created_signal_ids)
    seen = set(signal_ids)
    pending_limit = max(limit - len(signal_ids), 0)
    if pending_limit == 0:
        return signal_ids

    has_order_intent = (
        select(OrderIntent.id)
        .where(OrderIntent.signal_id == Signal.id)
        .exists()
    )
    pending_statement = (
        select(Signal.id)
        .where(Signal.status == "new")
        .where(Signal.preview_attempts < _options_preview_max_attempts())
        .where(~has_order_intent)
        .where(Signal.created_at >= current_trading_day_start_utc())
        .order_by(Signal.created_at.asc())
        .limit(pending_limit)
    )
    if symbol_filter is not None:
        pending_statement = pending_statement.where(_signal_symbol_clause(symbol_filter))
    pending_signal_ids = db.scalars(pending_statement)
    for signal_id in pending_signal_ids:
        if signal_id not in seen:
            signal_ids.append(signal_id)
            seen.add(signal_id)
    return signal_ids


def _signal_symbol_clause(symbol: str):
    return or_(
        func.upper(Signal.symbol) == symbol,
        func.upper(Signal.underlying_symbol) == symbol,
    )


def _signal_matches_symbol(signal: Signal, symbol: str) -> bool:
    return any(
        isinstance(value, str) and value.strip().upper() == symbol
        for value in (signal.symbol, signal.underlying_symbol)
    )


def _order_intent_matches_symbol(order_intent: OrderIntent, symbol: str) -> bool:
    return isinstance(order_intent.underlying_symbol, str) and (
        order_intent.underlying_symbol.strip().upper() == symbol
    )


def _submit_previewed_order_intents(
    db: Session,
    order_intent_ids: list[uuid.UUID],
    *,
    cycle_id: str | None = None,
    cycle_started: float,
    phase_timeout: int,
    symbol: str | None = None,
) -> dict[str, Any]:
    symbol_filter = _normalize_symbol(symbol)
    deadline = cycle_started + phase_timeout if phase_timeout > 0 else None
    submitted = 0
    rejected = 0
    skipped = 0
    errors: list[str] = []
    broker_order_ids: list[str] = []
    submitted_order_intent_ids: list[str] = []
    skipped_reasons: Counter[str] = Counter()

    orders_submitted_by_strategy: dict[uuid.UUID, int] = {}
    contracts_submitted_by_strategy: dict[uuid.UUID, int] = {}
    contracts_submitted_by_strategy_symbol: dict[tuple[uuid.UUID, str], int] = {}
    for i, order_intent_id in enumerate(order_intent_ids):
        if deadline is not None and perf_counter() >= deadline:
            remaining = len(order_intent_ids) - i
            skipped += remaining
            skipped_reasons["runtime_budget_exceeded"] += remaining
            errors.append(
                f"Skipped {remaining} order intent(s): runtime budget exceeded"
            )
            logger.warning(
                "market_cycle submit loop stopped: budget exceeded after %d/%d order intents",
                i,
                len(order_intent_ids),
            )
            break
        now = datetime.now(timezone.utc)
        order_intent = db.get(OrderIntent, order_intent_id)
        if order_intent is None:
            skipped += 1
            skipped_reasons["not_found"] += 1
            errors.append(f"Order intent '{order_intent_id}' was not found")
            logger.warning(
                "market_cycle submit_candidate_skipped reason=not_found id=%s",
                order_intent_id,
            )
            continue
        if symbol_filter is not None and not _order_intent_matches_symbol(
            order_intent,
            symbol_filter,
        ):
            skipped += 1
            skipped_reasons["symbol_mismatch"] += 1
            errors.append(
                f"Order intent '{order_intent_id}' skipped: symbol does not match {symbol_filter}"
            )
            logger.warning(
                "market_cycle submit_candidate_skipped reason=symbol_mismatch id=%s symbol=%s underlying_symbol=%s",
                order_intent_id,
                symbol_filter,
                order_intent.underlying_symbol,
            )
            continue

        strategy = db.get(Strategy, order_intent.strategy_id) if order_intent.strategy_id else None
        if strategy is None:
            skipped += 1
            skipped_reasons["missing_strategy"] += 1
            errors.append(f"Order intent '{order_intent_id}' has no strategy")
            logger.warning(
                "market_cycle submit_candidate_skipped reason=missing_strategy id=%s strategy_id=%s",
                order_intent_id,
                order_intent.strategy_id,
            )
            continue

        try:
            submit_config = _submit_config_for_order_intent(strategy, order_intent)
            logger.info(
                "market_cycle submit_candidate intent_id=%s strategy_id=%s strategy_name=%s underlying_symbol=%s "
                "option_symbol=%s side=%s status=%s global_submit_enabled=%s strategy_submit_enabled=%s "
                "allowed_sides=%s trade_windows=%s current_time_utc=%s current_time_et=%s",
                order_intent.id,
                strategy.id,
                strategy.name,
                order_intent.underlying_symbol,
                order_intent.option_symbol,
                order_intent.side,
                order_intent.status,
                settings.market_cycle_submit_enabled,
                submit_config.get("enabled"),
                submit_config.get("allowed_sides"),
                submit_config.get("trade_windows"),
                now.isoformat(),
                _current_time_et(now),
            )
            if order_intent.status != "previewed":
                skipped += 1
                skipped_reasons["ineligible_status"] += 1
                message = f"ineligible_status status={order_intent.status}"
                errors.append(f"Order intent '{order_intent_id}': {message}")
                logger.warning(
                    "market_cycle submit_candidate_skipped reason=ineligible_status status=%s id=%s",
                    order_intent.status,
                    order_intent.id,
                )
                continue
            guard_decision = can_auto_submit_order_intent(
                db,
                order_intent,
                cycle_id=cycle_id,
            )
            logger.info(
                "market_cycle submit_guard_decision intent_id=%s strategy_id=%s strategy_name=%s underlying_symbol=%s "
                "option_symbol=%s side=%s status=%s allowed=%s reasons=%s limits_snapshot=%s "
                "trade_windows=%s current_time_utc=%s current_time_et=%s submit_enabled_config=%s allowed_sides=%s",
                order_intent.id,
                strategy.id,
                strategy.name,
                order_intent.underlying_symbol,
                order_intent.option_symbol,
                order_intent.side,
                order_intent.status,
                guard_decision.allowed,
                guard_decision.reasons,
                guard_decision.limits_snapshot,
                submit_config.get("trade_windows"),
                now.isoformat(),
                _current_time_et(now),
                submit_config.get("enabled"),
                submit_config.get("allowed_sides"),
            )
            if not guard_decision.allowed:
                skipped += 1
                reason_key = _skip_reason_key(guard_decision.reasons)
                skipped_reasons[reason_key] += 1
                message = "; ".join(guard_decision.reasons)
                errors.append(f"Order intent '{order_intent_id}': {message}")
                logger.warning(
                    "market_cycle submit_candidate_skipped reason=%s id=%s status=%s",
                    reason_key,
                    order_intent.id,
                    order_intent.status,
                )
                record_audit_log(
                    db,
                    event_type="order_intent.auto_submit_skipped",
                    entity_type="order_intent",
                    entity_id=order_intent.id,
                    message="Auto-submit skipped by automation guard",
                    payload={
                        "order_intent_id": str(order_intent.id),
                        "strategy_id": str(strategy.id),
                        "cycle_id": cycle_id,
                        "reasons": guard_decision.reasons,
                        "limits_snapshot": guard_decision.limits_snapshot,
                    },
                )
                continue
            _validate_submit_limits(
                db,
                order_intent,
                strategy.id,
                submit_config,
                orders_submitted_by_strategy.get(strategy.id, 0),
                contracts_submitted_by_strategy.get(strategy.id, 0),
                contracts_submitted_by_strategy_symbol.get(
                    (strategy.id, order_intent.option_symbol),
                    0,
                ),
                now=now,
            )
        except ValueError as exc:
            skipped += 1
            reason_key = _skip_reason_key([str(exc)])
            skipped_reasons[reason_key] += 1
            errors.append(f"Order intent '{order_intent_id}': {exc}")
            logger.warning(
                "market_cycle submit_candidate_skipped reason=%s id=%s status=%s error=%s",
                reason_key,
                order_intent_id,
                getattr(order_intent, "status", None),
                exc,
            )
            continue

        try:
            _, broker_order = submit_order_intent(db, order_intent.id)
        except Exception as exc:
            rejected += 1
            errors.append(f"Order intent '{order_intent_id}': {exc.__class__.__name__}: {exc}")
            logger.error(
                "Order intent submission failed: %s %s: %s",
                order_intent_id,
                exc.__class__.__name__,
                exc,
            )
            record_audit_log(
                db,
                event_type="order_intent.submit_failed",
                entity_type="order_intent",
                entity_id=order_intent.id,
                message="Order intent submission failed during market cycle",
                payload={
                    "order_intent_id": str(order_intent.id),
                    "strategy_id": str(strategy.id),
                    "cycle_id": cycle_id,
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                },
            )
            continue

        submitted += 1
        submitted_order_intent_ids.append(str(order_intent.id))
        orders_submitted_by_strategy[strategy.id] = (
            orders_submitted_by_strategy.get(strategy.id, 0) + 1
        )
        contracts_submitted_by_strategy[strategy.id] = (
            contracts_submitted_by_strategy.get(strategy.id, 0) + order_intent.quantity
        )
        strategy_symbol_key = (strategy.id, order_intent.option_symbol)
        contracts_submitted_by_strategy_symbol[strategy_symbol_key] = (
            contracts_submitted_by_strategy_symbol.get(strategy_symbol_key, 0)
            + order_intent.quantity
        )
        broker_order_ids.append(str(broker_order.id))

    return {
        "status": "completed",
        "symbol": symbol_filter,
        "candidates_seen": len(order_intent_ids),
        "order_intents_seen": len(order_intent_ids),
        "submitted": submitted,
        "rejected": rejected,
        "skipped": skipped,
        "errors": errors,
        "skipped_reasons": dict(skipped_reasons),
        "submitted_order_intent_ids": submitted_order_intent_ids,
        "broker_order_ids": broker_order_ids,
    }


def _order_intent_ids_from_preview(preview: dict[str, Any] | None) -> list[uuid.UUID]:
    if not isinstance(preview, dict):
        return []

    order_intent_ids = []
    raw_values = preview.get("order_intent_ids", [])
    if not raw_values and int(preview.get("previews_created") or 0) > 0:
        logger.warning(
            "market_cycle preview produced previews_created=%s but no order_intent_ids key/value",
            preview.get("previews_created"),
        )

    for value in raw_values:
        try:
            order_intent_ids.append(uuid.UUID(str(value)))
        except ValueError:
            logger.warning(
                "market_cycle ignored invalid preview order_intent_id value=%s",
                value,
            )
            continue
    return order_intent_ids


def _current_time_et(now: datetime) -> str:
    return now.astimezone(ZoneInfo("America/New_York")).isoformat()


def _remaining_budget_seconds(cycle_started: float, phase_timeout: int) -> float | None:
    if phase_timeout <= 0:
        return None
    return max(phase_timeout - _elapsed_seconds(cycle_started), 0.0)


def _skip_reason_key(reasons: list[str]) -> str:
    text = "; ".join(reasons).lower()
    if "outside scanner.submit.trade_windows" in text or "trade_windows" in text:
        return "outside_trade_window"
    if "status" in text and "previewed" in text:
        return "ineligible_status"
    if "trading_automation_enabled" in text:
        return "trading_automation_disabled"
    if "market_cycle_submit_enabled" in text:
        return "submit disabled by global config"
    if "auto_submit_requires_paper" in text:
        return "paper_mode_required"
    if "broker_order" in text:
        return "already_has_broker_order"
    if "max_auto_orders_per_day" in text:
        return "max_auto_orders_per_day"
    if "max_auto_orders_per_symbol_per_day" in text:
        return "max_auto_orders_per_symbol_per_day"
    if "max_auto_orders_per_cycle" in text:
        return "max_auto_orders_per_cycle"
    if "max_open_positions_per_symbol" in text:
        return "max_open_positions_per_symbol"
    if "max_open_positions" in text:
        return "max_open_positions"
    if "max_contracts_per_order" in text:
        return "max_contracts_per_order"
    if "max_estimated_premium_per_order" in text:
        return "max_estimated_premium_per_order"
    if "allowed_sides" in text or "order side is not allowed" in text:
        return "side_not_allowed"
    if "scanner.submit.enabled" in text:
        return "strategy_submit_disabled"
    if "scanner.submit config" in text:
        return "missing_strategy_submit_config"
    return "guard_blocked" if reasons else "unknown"


def _preview_payload_for_signal(
    signal: Signal,
    strategy: Strategy,
) -> OrderIntentPreviewCreate:
    preview_config = _preview_config_for_strategy(strategy)

    option_symbol = preview_config.get("option_symbol")
    contract_selection = None
    if option_symbol is None:
        contract_selection = _contract_selection_for_signal(signal, preview_config)

    return OrderIntentPreviewCreate(
        signal_id=signal.id,
        option_symbol=option_symbol if isinstance(option_symbol, str) else None,
        contract_selection=contract_selection,
        side=_string_config(preview_config, "side", default="buy"),
        quantity=_int_config(preview_config, "quantity", default=1),
        order_type=_string_config(preview_config, "order_type", default="limit"),
        limit_price=preview_config.get("limit_price"),
        time_in_force=_string_config(preview_config, "time_in_force", default="day"),
        rationale=preview_config.get("rationale")
        if isinstance(preview_config.get("rationale"), str)
        else signal.rationale,
        data_feed=_string_config(preview_config, "data_feed", default="indicative"),
        max_estimated_notional=preview_config.get("max_estimated_notional"),
        max_spread=preview_config.get("max_spread"),
    )


def _preview_config_for_strategy(strategy: Strategy) -> dict[str, Any]:
    scanner_config = strategy.config.get("scanner")
    if not isinstance(scanner_config, dict):
        raise ValueError("strategy scanner config is required for auto-preview")

    preview_config = scanner_config.get("preview")
    if not isinstance(preview_config, dict):
        raise ValueError("scanner.preview config is required")
    if preview_config.get("enabled") is not True:
        raise ValueError("scanner.preview.enabled must be true")
    return preview_config


def _submit_config_for_order_intent(
    strategy: Strategy,
    order_intent: OrderIntent,
) -> dict[str, Any]:
    preview = order_intent.preview if isinstance(order_intent.preview, dict) else {}
    if (
        order_intent.side.lower() == "sell"
        and preview.get("source") == "position_exit_evaluator"
    ):
        exit_config = _exit_config_for_strategy(strategy)
        submit_config = exit_config.get("submit")
        if not isinstance(submit_config, dict):
            raise ValueError("scanner.exit.submit config is required")
        if submit_config.get("enabled") is not True:
            raise ValueError("scanner.exit.submit.enabled must be true")
        return submit_config

    return _submit_config_for_strategy(strategy)


def _submit_config_for_strategy(strategy: Strategy) -> dict[str, Any]:
    scanner_config = strategy.config.get("scanner")
    if not isinstance(scanner_config, dict):
        raise ValueError("strategy scanner config is required for auto-submit")

    submit_config = scanner_config.get("submit")
    if not isinstance(submit_config, dict):
        raise ValueError("scanner.submit config is required")
    if submit_config.get("enabled") is not True:
        raise ValueError("scanner.submit.enabled must be true")
    return submit_config


def _exit_config_for_strategy(strategy: Strategy) -> dict[str, Any]:
    scanner_config = strategy.config.get("scanner")
    if not isinstance(scanner_config, dict):
        raise ValueError("strategy scanner config is required for exit submit")

    exit_config = scanner_config.get("exit")
    if not isinstance(exit_config, dict):
        raise ValueError("scanner.exit config is required")
    if exit_config.get("enabled") is not True:
        raise ValueError("scanner.exit.enabled must be true")
    return exit_config


def _validate_submit_limits(
    db: Session,
    order_intent: OrderIntent,
    strategy_id: uuid.UUID,
    submit_config: dict[str, Any],
    submitted_for_strategy: int,
    contracts_submitted_for_strategy: int,
    contracts_submitted_for_strategy_symbol: int,
    now: datetime,
) -> None:
    _validate_trade_windows(submit_config, now=now)

    allowed_sides = submit_config.get("allowed_sides", ["buy"])
    if not isinstance(allowed_sides, list) or not allowed_sides:
        raise ValueError("scanner.submit.allowed_sides must be a non-empty list")
    clean_allowed_sides = {
        value.strip().lower()
        for value in allowed_sides
        if isinstance(value, str) and value.strip()
    }
    if order_intent.side.lower() not in clean_allowed_sides:
        raise ValueError("order side is not allowed by scanner.submit.allowed_sides")


def _contract_selection_for_signal(
    signal: Signal,
    preview_config: dict[str, Any],
) -> OptionContractSelectionCreate:
    underlying_symbol = preview_config.get("underlying_symbol")
    if not isinstance(underlying_symbol, str) or not underlying_symbol.strip():
        underlying_symbol = signal.underlying_symbol or signal.symbol

    return OptionContractSelectionCreate(
        underlying_symbol=underlying_symbol,
        option_type=_string_config(preview_config, "option_type"),
        side=_string_config(preview_config, "side", default="buy"),
        expiration_date=preview_config.get("expiration_date"),
        expiration_date_gte=preview_config.get("expiration_date_gte"),
        expiration_date_lte=preview_config.get("expiration_date_lte"),
        min_days_to_expiration=preview_config.get("min_days_to_expiration"),
        max_days_to_expiration=preview_config.get("max_days_to_expiration"),
        target_strike=preview_config.get("target_strike"),
        underlying_price=preview_config.get("underlying_price"),
        max_estimated_notional=preview_config.get("max_estimated_notional"),
        max_spread=preview_config.get("max_spread"),
        max_spread_percent=preview_config.get("max_spread_percent"),
        min_open_interest=preview_config.get("min_open_interest"),
        min_quote_size=preview_config.get("min_quote_size"),
        data_feed=_string_config(preview_config, "data_feed", default="indicative"),
        limit=_options_candidate_limit(),
    )


def _options_candidate_limit() -> int:
    try:
        return max(int(settings.options_max_candidates), 1)
    except (TypeError, ValueError):
        return 100


def _string_config(
    config: dict[str, Any],
    key: str,
    *,
    default: str | None = None,
    label_prefix: str = "scanner.preview",
) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label_prefix}.{key} must be a non-empty string")
    return value.strip()


def _int_config(
    config: dict[str, Any],
    key: str,
    *,
    default: int,
    label_prefix: str = "scanner.preview",
) -> int:
    value = config.get(key, default)
    try:
        int_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label_prefix}.{key} must be an integer") from exc
    if int_value <= 0:
        raise ValueError(f"{label_prefix}.{key} must be greater than 0")
    return int_value


def _validate_trade_windows(
    submit_config: dict[str, Any],
    *,
    now: datetime,
) -> None:
    windows = submit_config.get("trade_windows")
    if windows is None:
        return
    if not isinstance(windows, list) or not windows:
        raise ValueError("scanner.submit.trade_windows must be a non-empty list")

    window_errors: list[str] = []
    for window in windows:
        try:
            if _is_inside_trade_window(window, now=now):
                return
        except ValueError as exc:
            window_errors.append(str(exc))

    if window_errors:
        raise ValueError(window_errors[0])
    raise ValueError("current time is outside scanner.submit.trade_windows")


def _is_inside_trade_window(window: object, *, now: datetime) -> bool:
    if not isinstance(window, dict):
        raise ValueError("scanner.submit.trade_windows entries must be objects")

    timezone_name = _string_config(
        window,
        "timezone",
        default="America/New_York",
        label_prefix="scanner.submit.trade_windows",
    )
    try:
        window_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            "scanner.submit.trade_windows.timezone must be a valid timezone"
        ) from exc

    start = _time_config(window, "start")
    end = _time_config(window, "end")
    current_time = (
        now.astimezone(window_timezone).time().replace(second=0, microsecond=0)
    )

    if start <= end:
        return start <= current_time <= end
    return current_time >= start or current_time <= end


def _time_config(config: dict[str, Any], key: str) -> time:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"scanner.submit.trade_windows.{key} must be HH:MM")
    try:
        parsed_time = time.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"scanner.submit.trade_windows.{key} must be HH:MM") from exc
    return parsed_time.replace(second=0, microsecond=0)


def _optional_int_config(
    config: dict[str, Any],
    key: str,
    *,
    label_prefix: str,
) -> int | None:
    if config.get(key) is None:
        return None
    return _int_config(config, key, default=1, label_prefix=label_prefix)


def _optional_decimal_config(
    config: dict[str, Any],
    key: str,
    *,
    label_prefix: str,
) -> Decimal | None:
    value = config.get(key)
    if value is None:
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label_prefix}.{key} must be a decimal") from exc
    if decimal_value <= Decimal("0"):
        raise ValueError(f"{label_prefix}.{key} must be greater than 0")
    return decimal_value


def _order_intent_notional(order_intent: OrderIntent) -> Decimal | None:
    if order_intent.limit_price is not None:
        return order_intent.limit_price * Decimal(order_intent.quantity) * Decimal("100")

    preview = order_intent.preview if isinstance(order_intent.preview, dict) else {}
    quote_preview = preview.get("quote")
    if isinstance(quote_preview, dict):
        notional = _decimal_from_preview(quote_preview.get("estimated_notional"))
        if notional is not None:
            return notional

    selection_preview = preview.get("selection")
    if isinstance(selection_preview, dict):
        selection_quote = selection_preview.get("quote")
        if isinstance(selection_quote, dict):
            return _decimal_from_preview(selection_quote.get("estimated_notional"))

    return None


def _decimal_from_preview(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _existing_contract_exposure(
    db: Session,
    *,
    strategy_id: uuid.UUID,
    option_symbol: str | None = None,
) -> Decimal:
    signed_quantity = case(
        (func.lower(BrokerOrder.side) == "sell", -BrokerOrder.quantity),
        else_=BrokerOrder.quantity,
    )
    statement = (
        select(func.coalesce(func.sum(signed_quantity), 0))
        .select_from(BrokerOrder)
        .join(OrderIntent, BrokerOrder.order_intent_id == OrderIntent.id)
        .where(OrderIntent.strategy_id == strategy_id)
        .where(BrokerOrder.status.in_(EXPOSURE_BROKER_ORDER_STATUSES))
    )
    if option_symbol is not None:
        statement = statement.where(BrokerOrder.symbol == option_symbol)

    value = db.scalar(statement)
    if value is None:
        return Decimal("0")
    try:
        exposure = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    return max(exposure, Decimal("0"))


def _submitted_orders_for_trading_day(
    db: Session,
    *,
    strategy_id: uuid.UUID,
    submit_config: dict[str, Any],
    now: datetime,
) -> int:
    day_timezone = _trading_day_timezone(submit_config)
    current_local = now.astimezone(day_timezone)
    day_start_local = datetime.combine(
        current_local.date(),
        time.min,
        tzinfo=day_timezone,
    )
    day_end_local = datetime.combine(
        current_local.date(),
        time.max,
        tzinfo=day_timezone,
    )
    statement = (
        select(func.count(BrokerOrder.id))
        .select_from(BrokerOrder)
        .join(OrderIntent, BrokerOrder.order_intent_id == OrderIntent.id)
        .where(OrderIntent.strategy_id == strategy_id)
        .where(BrokerOrder.submitted_at.is_not(None))
        .where(BrokerOrder.submitted_at >= day_start_local.astimezone(timezone.utc))
        .where(BrokerOrder.submitted_at <= day_end_local.astimezone(timezone.utc))
    )
    value = db.scalar(statement)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _trading_day_timezone(submit_config: dict[str, Any]) -> ZoneInfo:
    timezone_name = submit_config.get("trading_day_timezone", "America/New_York")
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ValueError("scanner.submit.trading_day_timezone must be a non-empty string")
    try:
        return ZoneInfo(timezone_name.strip())
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            "scanner.submit.trading_day_timezone must be a valid timezone"
        ) from exc
