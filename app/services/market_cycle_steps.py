from __future__ import annotations

from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from app.services.broker_reconciliation import reconcile_broker_state


def _normalize_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    normalized = symbol.strip().upper()
    return normalized or None

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


