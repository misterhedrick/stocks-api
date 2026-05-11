from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services import market_cycle_runner as _runner
from app.services.market_cycle_helpers import (
    _contract_selection_for_signal,
    _diagnostics_for_steps,
    _disabled_step,
    _elapsed_seconds,
    _entry_preview_delay_reason,
    _error_category,
    _exit_alert_payload,
    _exit_config_for_strategy,
    _has_attention_reason,
    _order_intent_ids_from_preview,
    _order_intent_matches_symbol,
    _phase_budget_exceeded,
    _preview_created_signals,
    _preview_payload_for_signal,
    _reason_categories,
    _reconcile_step,
    _remaining_budget_seconds,
    _signal_ids_for_preview,
    _skip_reason_key,
    _submit_previewed_order_intents,
    _timeout_step,
    _switch,
)
from app.services.market_cycle_runner_types import (
    EXPOSURE_BROKER_ORDER_STATUSES,
    SUPPORTED_MARKET_ENTRY_SYMBOLS,
    MarketCycleResult,
    _MARKET_CYCLE_LOCK_KEY,
    _MARKET_ENTRY_LOCK_BASE_KEY,
    _market_entry_lock_key,
    _normalize_symbol,
    normalize_market_entry_symbol,
)
from app.services.news_scanner import scan_market_news
from app.services.position_exits import evaluate_position_exits
from app.services.signal_scanner import scan_signals


def _sync_runner_patch_points() -> None:
    _runner.settings = settings
    _runner.scan_signals = scan_signals
    _runner.scan_market_news = scan_market_news
    _runner.evaluate_position_exits = evaluate_position_exits
    _runner._phase_budget_exceeded = _phase_budget_exceeded
    _runner._signal_ids_for_preview = _signal_ids_for_preview


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
    _sync_runner_patch_points()
    return _runner.run_market_cycle(
        db,
        symbol=symbol,
        scan_limit=scan_limit,
        order_limit=order_limit,
        fill_page_size=fill_page_size,
        scan_enabled_override=scan_enabled_override,
        reconcile_enabled_override=reconcile_enabled_override,
        preview_enabled_override=preview_enabled_override,
        exit_enabled_override=exit_enabled_override,
        news_enabled_override=news_enabled_override,
        submit_enabled_override=submit_enabled_override,
        reconcile_before_exit=reconcile_before_exit,
        phase_timeout_seconds=phase_timeout_seconds,
        job_name=job_name,
        event_prefix=event_prefix,
        lock_key=lock_key,
    )


__all__: tuple[str, ...] = (
    "EXPOSURE_BROKER_ORDER_STATUSES",
    "SUPPORTED_MARKET_ENTRY_SYMBOLS",
    "MarketCycleResult",
    "normalize_market_entry_symbol",
    "run_market_cycle",
    "run_market_entry_cycle",
)
