from datetime import datetime
from decimal import Decimal
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.jobs import JobRunRead


class AutomationSwitchesRead(BaseModel):
    scan_enabled: bool
    reconcile_enabled: bool
    preview_enabled: bool
    exit_enabled: bool
    news_enabled: bool
    submit_enabled: bool


class AutomationStrategyRead(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool
    scanner_type: str | None
    scanner_symbols: list[str]
    preview_enabled: bool
    exit_enabled: bool
    submit_enabled: bool
    exit_limits: dict[str, Any]
    submit_limits: dict[str, Any]
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AutomationStatusRead(BaseModel):
    switches: AutomationSwitchesRead
    operational_summary: dict[str, Any]
    trading_automation_enabled: bool
    auto_submit_requires_paper: bool
    paper_mode: bool
    max_auto_orders_per_cycle: int
    max_auto_orders_per_day: int
    max_auto_orders_per_symbol_per_day: int
    max_open_positions: int
    max_open_positions_per_symbol: int
    max_contracts_per_order: int
    max_estimated_premium_per_order: Decimal
    active_strategies: list[AutomationStrategyRead]
    latest_job_runs: dict[str, JobRunRead | None]


class PositionManagementStatusRead(BaseModel):
    symbol: str
    quantity: str
    market_value: str | None
    cost_basis: str | None
    unrealized_pl: str | None
    captured_at: datetime
    ownership: dict[str, Any]
    exit_config_enabled: bool
    active_exit_order: dict[str, Any] | None
    recommended_action: str
    reason: str


class PaperPerformanceRead(BaseModel):
    generated_at: datetime
    fills_seen: int
    matched_round_trips: int
    open_positions: list[dict[str, Any]]
    totals: dict[str, Any]
    by_strategy: list[dict[str, Any]]
    by_symbol: list[dict[str, Any]]
    recent_round_trips: list[dict[str, Any]]
    unmatched_closing_fills: list[dict[str, Any]] = Field(default_factory=list)
    ignored_fills: list[dict[str, Any]] = Field(default_factory=list)
    signal_summary: dict[str, Any] = Field(default_factory=dict)
    no_signal_summary: dict[str, Any] = Field(default_factory=dict)
    option_selection_diagnostics: dict[str, Any] = Field(default_factory=dict)
    rejected_preview_outcomes: list[dict[str, Any]] = Field(default_factory=list)


class TradeLifecycleRead(BaseModel):
    generated_at: datetime
    positions_seen: int
    managed_positions: int
    unmanaged_positions: int
    positions: list[dict[str, Any]]


class LearningReportRead(BaseModel):
    generated_at: datetime
    totals: dict[str, Any]
    performance: dict[str, Any]
    signals_by_strategy: list[dict[str, Any]]
    intents_by_strategy: list[dict[str, Any]]
    non_trade_reasons: list[dict[str, Any]]
    job_failures: list[dict[str, Any]]


class TradeCasesRead(BaseModel):
    generated_at: datetime
    fills_seen: int
    matched_round_trips: int
    open_positions: list[dict[str, Any]]
    recent_round_trips: list[dict[str, Any]]
    totals: dict[str, Any]
    by_strategy: list[dict[str, Any]]
    by_symbol: list[dict[str, Any]]
    unmatched_closing_fills: list[dict[str, Any]] = Field(default_factory=list)
    ignored_fills: list[dict[str, Any]] = Field(default_factory=list)


class StrategySuggestionReviewUpdate(BaseModel):
    status: str | None = None
    review_notes: str | None = None
    reviewed_by: str | None = None
