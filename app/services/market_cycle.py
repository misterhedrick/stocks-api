from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from time import perf_counter
from typing import Any
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
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


EXPOSURE_BROKER_ORDER_STATUSES = (
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "filled",
    "submitted",
)


def run_market_cycle(
    db: Session,
    *,
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
) -> MarketCycleResult:
    started_at = datetime.now(timezone.utc)
    cycle_started = perf_counter()
    timings: dict[str, float] = {}
    job_run = JobRun(
        job_name="market_cycle",
        status="running",
        started_at=started_at,
        details={},
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
            scan_result = scan_signals(db, limit=scan_limit)
            timings["scan_seconds"] = _elapsed_seconds(step_started)
            created_signal_ids = scan_result.created_signal_ids
            scan = {
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
        else:
            scan = (
                _timeout_step("scan", phase_timeout)
                if scan_enabled
                else _disabled_step("scan")
            )
            timings["scan_seconds"] = 0.0

        if news_enabled and not _phase_budget_exceeded(cycle_started, phase_timeout):
            step_started = perf_counter()
            news_result = scan_market_news(db)
            timings["news_seconds"] = _elapsed_seconds(step_started)
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
        else:
            timings["news_seconds"] = 0.0
            if news_enabled:
                news = _timeout_step("news", phase_timeout)

        if preview_enabled and not _phase_budget_exceeded(cycle_started, phase_timeout):
            step_started = perf_counter()
            signal_ids_for_preview = _signal_ids_for_preview(
                db,
                created_signal_ids,
                limit=scan_limit,
            )
            if news_blocks_entries:
                preview = {
                    "status": "blocked",
                    "signals_seen": len(signal_ids_for_preview),
                    "previews_created": 0,
                    "previews_skipped": len(signal_ids_for_preview),
                    "errors": ["News risk gate blocked new entry previews"],
                    "order_intent_ids": [],
                    "news_risk": news.get("risk_assessment")
                    if isinstance(news, dict)
                    else None,
                }
            else:
                preview = _preview_created_signals(db, signal_ids_for_preview)
            submittable_order_intent_ids.extend(_order_intent_ids_from_preview(preview))
            timings["preview_seconds"] = _elapsed_seconds(step_started)
        else:
            timings["preview_seconds"] = 0.0
            if preview_enabled:
                preview = _timeout_step("preview", phase_timeout)

        if (
            reconcile_enabled
            and reconcile_before_exit
            and not _phase_budget_exceeded(cycle_started, phase_timeout)
        ):
            step_started = perf_counter()
            reconcile = _reconcile_step(
                db,
                order_limit=order_limit,
                fill_page_size=fill_page_size,
            )
            timings["reconcile_seconds"] = _elapsed_seconds(step_started)

        if exit_enabled and not _phase_budget_exceeded(cycle_started, phase_timeout):
            step_started = perf_counter()
            exit_result = evaluate_position_exits(db, limit=scan_limit)
            timings["exit_seconds"] = _elapsed_seconds(step_started)
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
        else:
            timings["exit_seconds"] = 0.0
            if exit_enabled:
                exits = _timeout_step("exits", phase_timeout)

        if submit_enabled and not _phase_budget_exceeded(cycle_started, phase_timeout):
            step_started = perf_counter()
            submit = _submit_previewed_order_intents(
                db,
                submittable_order_intent_ids,
                cycle_id=str(job_run.id),
            )
            timings["submit_seconds"] = _elapsed_seconds(step_started)
        else:
            timings["submit_seconds"] = 0.0
            if submit_enabled:
                submit = _timeout_step("submit", phase_timeout)

        if (
            reconcile_enabled
            and reconcile is None
            and not _phase_budget_exceeded(cycle_started, phase_timeout)
        ):
            step_started = perf_counter()
            reconcile = _reconcile_step(
                db,
                order_limit=order_limit,
                fill_page_size=fill_page_size,
            )
            timings["reconcile_seconds"] = _elapsed_seconds(step_started)
        else:
            if reconcile is None:
                reconcile = (
                    _timeout_step("reconcile", phase_timeout)
                    if reconcile_enabled
                    else _disabled_step("reconcile")
                )
                timings["reconcile_seconds"] = 0.0

        timings["total_seconds"] = _elapsed_seconds(cycle_started)

        details = {
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
            "diagnostics": _diagnostics_for_steps(
                scan=scan,
                preview=preview,
                exits=exits,
                submit=submit,
                news=news,
                reconcile=reconcile,
            ),
        }
        job_run.status = "succeeded"
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
) -> dict[str, Any]:
    reconciliation_result = reconcile_broker_state(
        db,
        order_limit=order_limit,
        fill_page_size=fill_page_size,
    )
    return {
        "job_run_id": str(reconciliation_result.job_run.id),
        "orders_seen": reconciliation_result.orders_seen,
        "orders_created": reconciliation_result.orders_created,
        "orders_updated": reconciliation_result.orders_updated,
        "fills_seen": reconciliation_result.fills_seen,
        "fills_created": reconciliation_result.fills_created,
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
) -> dict[str, Any]:
    previews_created = 0
    previews_skipped = 0
    errors: list[str] = []
    order_intent_ids: list[str] = []

    for signal_id in signal_ids:
        signal = db.get(Signal, signal_id)
        if signal is None:
            previews_skipped += 1
            errors.append(f"Signal '{signal_id}' was not found")
            continue

        strategy = db.get(Strategy, signal.strategy_id) if signal.strategy_id else None
        if strategy is None:
            previews_skipped += 1
            errors.append(f"Signal '{signal_id}' has no strategy")
            continue

        delay_reason = _entry_preview_delay_reason(strategy)
        if delay_reason is not None:
            previews_skipped += 1
            errors.append(f"Signal '{signal_id}': {delay_reason}")
            continue

        try:
            payload = _preview_payload_for_signal(signal, strategy)
        except ValueError as exc:
            previews_skipped += 1
            errors.append(f"Signal '{signal_id}': {exc}")
            continue

        try:
            order_intent = preview_order_intent_from_signal(db, payload)
        except Exception as exc:
            previews_skipped += 1
            errors.append(f"Signal '{signal_id}': {exc.__class__.__name__}: {exc}")
            continue

        previews_created += 1
        order_intent_ids.append(str(order_intent.id))

    return {
        "status": "completed",
        "signals_seen": len(signal_ids),
        "previews_created": previews_created,
        "previews_skipped": previews_skipped,
        "errors": errors,
        "order_intent_ids": order_intent_ids,
    }


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
) -> list[uuid.UUID]:
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
    pending_signal_ids = db.scalars(
        select(Signal.id)
        .where(Signal.status == "new")
        .where(~has_order_intent)
        .where(Signal.created_at >= _current_trading_day_start_utc())
        .order_by(Signal.created_at.asc())
        .limit(pending_limit)
    )
    for signal_id in pending_signal_ids:
        if signal_id not in seen:
            signal_ids.append(signal_id)
            seen.add(signal_id)
    return signal_ids


def _current_trading_day_start_utc() -> datetime:
    trading_tz = ZoneInfo("America/New_York")
    local_now = datetime.now(timezone.utc).astimezone(trading_tz)
    local_start = datetime.combine(local_now.date(), time.min, tzinfo=trading_tz)
    return local_start.astimezone(timezone.utc)


def _submit_previewed_order_intents(
    db: Session,
    order_intent_ids: list[uuid.UUID],
    *,
    cycle_id: str | None = None,
) -> dict[str, Any]:
    submitted = 0
    rejected = 0
    skipped = 0
    errors: list[str] = []
    broker_order_ids: list[str] = []

    orders_submitted_by_strategy: dict[uuid.UUID, int] = {}
    contracts_submitted_by_strategy: dict[uuid.UUID, int] = {}
    contracts_submitted_by_strategy_symbol: dict[tuple[uuid.UUID, str], int] = {}
    for order_intent_id in order_intent_ids:
        now = datetime.now(timezone.utc)
        order_intent = db.get(OrderIntent, order_intent_id)
        if order_intent is None:
            skipped += 1
            errors.append(f"Order intent '{order_intent_id}' was not found")
            continue

        strategy = db.get(Strategy, order_intent.strategy_id) if order_intent.strategy_id else None
        if strategy is None:
            skipped += 1
            errors.append(f"Order intent '{order_intent_id}' has no strategy")
            continue

        try:
            submit_config = _submit_config_for_order_intent(strategy, order_intent)
            guard_decision = can_auto_submit_order_intent(
                db,
                order_intent,
                cycle_id=cycle_id,
            )
            if not guard_decision.allowed:
                skipped += 1
                message = "; ".join(guard_decision.reasons)
                errors.append(f"Order intent '{order_intent_id}': {message}")
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
            errors.append(f"Order intent '{order_intent_id}': {exc}")
            continue

        try:
            _, broker_order = submit_order_intent(db, order_intent.id)
        except Exception as exc:
            rejected += 1
            errors.append(f"Order intent '{order_intent_id}': {exc.__class__.__name__}: {exc}")
            continue

        submitted += 1
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
        "order_intents_seen": len(order_intent_ids),
        "submitted": submitted,
        "rejected": rejected,
        "skipped": skipped,
        "errors": errors,
        "broker_order_ids": broker_order_ids,
    }


def _order_intent_ids_from_preview(preview: dict[str, Any] | None) -> list[uuid.UUID]:
    if not isinstance(preview, dict):
        return []

    order_intent_ids = []
    for value in preview.get("order_intent_ids", []):
        try:
            order_intent_ids.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return order_intent_ids


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
        limit=_int_config(preview_config, "limit", default=20),
    )


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
