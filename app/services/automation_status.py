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


def get_automation_status(db: Session) -> AutomationStatusRead:
    active_strategies = list(
        db.scalars(
            select(Strategy)
            .where(Strategy.is_active == True)  # noqa: E712
            .order_by(Strategy.name.asc())
        )
    )

    return AutomationStatusRead(
        switches=AutomationSwitchesRead(
            scan_enabled=settings.market_cycle_scan_enabled,
            reconcile_enabled=settings.market_cycle_reconcile_enabled,
            preview_enabled=settings.market_cycle_preview_enabled,
            submit_enabled=settings.market_cycle_submit_enabled,
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
        latest_job_runs=_latest_job_runs(db),
    )


def _strategy_status(strategy: Strategy) -> AutomationStrategyRead:
    scanner_config = strategy.config.get("scanner")
    if not isinstance(scanner_config, dict):
        scanner_config = {}

    preview_config = scanner_config.get("preview")
    submit_config = scanner_config.get("submit")

    return AutomationStrategyRead(
        id=strategy.id,
        name=strategy.name,
        is_active=strategy.is_active,
        scanner_type=_optional_string(scanner_config.get("type")),
        scanner_symbols=_scanner_symbols(scanner_config),
        preview_enabled=_enabled_config(preview_config),
        submit_enabled=_enabled_config(submit_config),
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


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
