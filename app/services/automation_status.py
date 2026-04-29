from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import JobRun, Strategy
from app.schemas.automation import (
    AutomationStatusRead,
    AutomationStrategyRead,
    AutomationSwitchesRead,
)
from app.schemas.jobs import JobRunRead

JOB_NAMES = ("market_cycle", "scan_signals", "reconcile_broker")
SUBMIT_LIMIT_KEYS = (
    "max_orders_per_cycle",
    "max_contracts_per_order",
    "max_contracts_per_cycle",
    "max_notional_per_order",
    "max_open_contracts_per_symbol",
    "max_open_contracts_per_strategy",
    "max_orders_per_trading_day",
    "trading_day_timezone",
    "trade_windows",
    "allowed_sides",
)
EXIT_LIMIT_KEYS = (
    "profit_target_percent",
    "stop_loss_percent",
    "max_days_to_expiration",
    "max_contracts_per_exit",
    "order_type",
    "limit_price_source",
    "time_in_force",
    "data_feed",
    "max_spread",
)


def get_automation_status(db: Session) -> AutomationStatusRead:
    active_strategies = list(
        db.scalars(
            select(Strategy)
            .where(Strategy.is_active == True)  # noqa: E712
            .order_by(Strategy.name.asc())
        )
    )

    latest_job_runs = _latest_job_runs(db)

    return AutomationStatusRead(
        switches=AutomationSwitchesRead(
            scan_enabled=settings.market_cycle_scan_enabled,
            reconcile_enabled=settings.market_cycle_reconcile_enabled,
            preview_enabled=settings.market_cycle_preview_enabled,
            exit_enabled=settings.market_cycle_exit_enabled,
            news_enabled=settings.market_cycle_news_enabled,
            submit_enabled=settings.market_cycle_submit_enabled,
        ),
        operational_summary=_operational_summary(
            latest_job_runs,
            active_strategies=active_strategies,
        ),
        trading_automation_enabled=settings.trading_automation_enabled,
        auto_submit_requires_paper=settings.auto_submit_requires_paper,
        paper_mode=settings.alpaca_paper,
        max_auto_orders_per_cycle=settings.max_auto_orders_per_cycle,
        max_auto_orders_per_day=settings.max_auto_orders_per_day,
        max_open_positions=settings.max_open_positions,
        max_open_positions_per_symbol=settings.max_open_positions_per_symbol,
        max_contracts_per_order=settings.max_contracts_per_order,
        max_estimated_premium_per_order=settings.max_estimated_premium_per_order,
        active_strategies=[
            _strategy_status(strategy) for strategy in active_strategies
        ],
        latest_job_runs=latest_job_runs,
    )


def _strategy_status(strategy: Strategy) -> AutomationStrategyRead:
    scanner_config = strategy.config.get("scanner")
    if not isinstance(scanner_config, dict):
        scanner_config = {}

    preview_config = scanner_config.get("preview")
    exit_config = scanner_config.get("exit")
    submit_config = scanner_config.get("submit")

    return AutomationStrategyRead(
        id=strategy.id,
        name=strategy.name,
        is_active=strategy.is_active,
        scanner_type=_optional_string(scanner_config.get("type")),
        scanner_symbols=_scanner_symbols(scanner_config),
        preview_enabled=_enabled_config(preview_config),
        exit_enabled=_enabled_config(exit_config),
        submit_enabled=_enabled_config(submit_config),
        exit_limits=_exit_limits(exit_config),
        submit_limits=_submit_limits(submit_config),
        updated_at=strategy.updated_at,
    )


def _latest_job_runs(db: Session) -> dict[str, JobRunRead | None]:
    latest_job_runs: dict[str, JobRunRead | None] = {}
    for job_name in JOB_NAMES:
        job_run = db.scalar(
            select(JobRun)
            .where(JobRun.job_name == job_name)
            .order_by(JobRun.started_at.desc())
            .limit(1)
        )
        latest_job_runs[job_name] = (
            JobRunRead.model_validate(job_run) if job_run is not None else None
        )
    return latest_job_runs


def _operational_summary(
    latest_job_runs: dict[str, JobRunRead | None],
    *,
    active_strategies: list[Strategy],
) -> dict[str, Any]:
    market_cycle = latest_job_runs.get("market_cycle")
    details = market_cycle.details if market_cycle is not None else {}
    if not isinstance(details, dict):
        details = {}

    preview = details.get("preview") if isinstance(details.get("preview"), dict) else {}
    submit = details.get("submit") if isinstance(details.get("submit"), dict) else {}
    news = details.get("news") if isinstance(details.get("news"), dict) else {}
    risk = (
        news.get("risk_assessment")
        if isinstance(news.get("risk_assessment"), dict)
        else preview.get("news_risk")
    )
    if not isinstance(risk, dict):
        risk = {}

    blockers: list[str] = []
    if not settings.market_cycle_preview_enabled:
        blockers.append("MARKET_CYCLE_PREVIEW_ENABLED is false")
    if not settings.market_cycle_submit_enabled:
        blockers.append("MARKET_CYCLE_SUBMIT_ENABLED is false")
    if not settings.trading_automation_enabled:
        blockers.append("TRADING_AUTOMATION_ENABLED is false")
    if risk.get("should_block_new_entries") is True:
        blockers.append("news risk gate is blocking new entry previews")

    effective_mode = "watching"
    if settings.market_cycle_preview_enabled:
        effective_mode = "previewing"
    if settings.market_cycle_submit_enabled and settings.trading_automation_enabled:
        effective_mode = "paper_autosubmit" if settings.alpaca_paper else "live_autosubmit"
    if blockers and effective_mode in {"paper_autosubmit", "live_autosubmit"}:
        effective_mode = "blocked_autosubmit"

    return {
        "effective_mode": effective_mode,
        "blockers": blockers,
        "last_market_cycle_status": market_cycle.status if market_cycle else None,
        "last_market_cycle_started_at": market_cycle.started_at if market_cycle else None,
        "news_gate": {
            "enabled": settings.market_cycle_news_enabled,
            "market_risk_level": risk.get("market_risk_level"),
            "should_block_new_entries": risk.get("should_block_new_entries", False),
            "blocking_reasons": risk.get("blocking_reasons", []),
            "manual_review_symbols": risk.get("manual_review_symbols", []),
        },
        "last_preview": {
            "status": preview.get("status"),
            "signals_seen": preview.get("signals_seen"),
            "previews_created": preview.get("previews_created"),
            "previews_skipped": preview.get("previews_skipped"),
        },
        "last_submit": {
            "status": submit.get("status"),
            "order_intents_seen": submit.get("order_intents_seen"),
            "submitted": submit.get("submitted"),
            "skipped": submit.get("skipped"),
            "rejected": submit.get("rejected"),
        },
        "paper_trading_readiness": _paper_trading_readiness(
            active_strategies,
            risk=risk,
        ),
    }


def _paper_trading_readiness(
    active_strategies: list[Strategy],
    *,
    risk: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []

    if not settings.alpaca_paper:
        blockers.append("ALPACA_PAPER is false")
    if settings.auto_submit_requires_paper and not settings.alpaca_paper:
        blockers.append("AUTO_SUBMIT_REQUIRES_PAPER is true but paper mode is off")
    if not settings.market_cycle_scan_enabled:
        blockers.append("MARKET_CYCLE_SCAN_ENABLED is false")
    if not settings.market_cycle_preview_enabled:
        blockers.append("MARKET_CYCLE_PREVIEW_ENABLED is false")
    if not settings.market_cycle_reconcile_enabled:
        blockers.append("MARKET_CYCLE_RECONCILE_ENABLED is false")
    if not settings.market_cycle_submit_enabled:
        warnings.append("MARKET_CYCLE_SUBMIT_ENABLED must be true to auto-submit paper orders")
    if not settings.trading_automation_enabled:
        warnings.append("TRADING_AUTOMATION_ENABLED must be true to auto-submit paper orders")
    if risk.get("should_block_new_entries") is True:
        warnings.append("news risk gate is currently blocking new entry previews")

    submit_ready_strategies = []
    preview_only_strategies = []
    for strategy in active_strategies:
        scanner = strategy.config.get("scanner") if isinstance(strategy.config, dict) else None
        if not isinstance(scanner, dict):
            continue
        preview = scanner.get("preview")
        submit = scanner.get("submit")
        if isinstance(preview, dict) and preview.get("enabled") is True:
            if isinstance(submit, dict) and submit.get("enabled") is True:
                submit_ready_strategies.append(strategy.name)
            else:
                preview_only_strategies.append(strategy.name)

    if not submit_ready_strategies:
        warnings.append("no active strategy has scanner.submit.enabled=true")
    if not active_strategies:
        blockers.append("no active strategies found")

    return {
        "ready_to_auto_submit_now": not blockers
        and settings.market_cycle_submit_enabled
        and settings.trading_automation_enabled
        and bool(submit_ready_strategies)
        and risk.get("should_block_new_entries") is not True,
        "ready_after_switches": not blockers and bool(active_strategies),
        "blockers": blockers,
        "warnings": warnings,
        "submit_ready_strategies": submit_ready_strategies,
        "preview_only_strategies": preview_only_strategies,
    }


def _scanner_symbols(scanner_config: dict[str, Any]) -> list[str]:
    symbols = scanner_config.get("symbols")
    if not isinstance(symbols, list):
        return []
    return [
        symbol.strip().upper()
        for symbol in symbols
        if isinstance(symbol, str) and symbol.strip()
    ]


def _enabled_config(config: object) -> bool:
    return isinstance(config, dict) and config.get("enabled") is True


def _submit_limits(config: object) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    return {
        key: config[key]
        for key in SUBMIT_LIMIT_KEYS
        if key in config
    }


def _exit_limits(config: object) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    limits = {
        key: config[key]
        for key in EXIT_LIMIT_KEYS
        if key in config
    }
    submit_config = config.get("submit")
    if isinstance(submit_config, dict):
        limits["submit"] = _submit_limits(submit_config)
    return limits


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
