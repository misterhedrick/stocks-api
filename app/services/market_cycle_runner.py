from __future__ import annotations
import uuid
import logging
from datetime import datetime, timezone
from time import perf_counter
logger = logging.getLogger(__name__)
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db.models import JobRun
from app.services.audit_logs import record_audit_log
from app.services.news_scanner import scan_market_news
from app.services.position_exits import evaluate_position_exits
from app.services.signal_scanner import scan_signals
from app.services.market_cycle_helpers import (
    _diagnostics_for_steps,
    _disabled_step,
    _elapsed_seconds,
    _order_intent_ids_from_preview,
    _phase_budget_exceeded,
    _preview_created_signals,
    _reconcile_step,
    _remaining_budget_seconds,
    _signal_ids_for_preview,
    _submit_previewed_order_intents,
    _timeout_step,
    _switch,
)
from app.services.market_cycle_runner_lifecycle import (
    _complete_market_cycle,
    _fail_market_cycle,
    _skipped_lock_result,
    _submit_skipped_result,
)
from app.services.market_cycle_runner_news import _run_news_phase
from app.services.market_cycle_runner_types import (
    MarketCycleResult,
    _MARKET_CYCLE_LOCK_KEY,
    _normalize_symbol,
)
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
        return _skipped_lock_result(
            db,
            job_name=job_name,
            event_prefix=event_prefix,
            lock_key=lock_key,
            symbol_filter=symbol_filter,
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

        news, news_blocks_entries = _run_news_phase(
            db,
            news_enabled=news_enabled,
            cycle_started=cycle_started,
            phase_timeout=phase_timeout,
            timings=timings,
            scan_market_news_fn=scan_market_news,
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
            submit = _submit_skipped_result(
                submit_enabled=submit_enabled,
                submit_candidates_count=submit_candidates_count,
                phase_timeout=phase_timeout,
            )
            if submit_enabled:
                logger.warning(
                    "market_cycle phase=submit skipped: runtime budget reached at %.3fs (limit=%ds)",
                    _elapsed_seconds(cycle_started),
                    phase_timeout,
                )
            else:
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
        return _complete_market_cycle(
            db,
            job_run=job_run,
            event_prefix=event_prefix,
            final_status=final_status,
            details=details,
            exits=exits,
        )
    except Exception as exc:
        logger.error(
            "market_cycle failed after %.3fs: %s: %s",
            _elapsed_seconds(cycle_started),
            exc.__class__.__name__,
            exc,
        )
        _fail_market_cycle(
            db,
            job_run=job_run,
            event_prefix=event_prefix,
            timings=timings,
            cycle_started=cycle_started,
            phase_timeout=phase_timeout,
            exc=exc,
        )
        raise
