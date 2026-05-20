import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class JobRunRead(BaseModel):
    id: uuid.UUID
    job_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    details: dict[str, Any]
    error: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BrokerReconciliationRead(BaseModel):
    job_run: JobRunRead
    orders_seen: int
    orders_created: int
    orders_updated: int
    fills_seen: int
    fills_created: int
    fill_page_size_requested: int = 100
    fill_page_size_used: int = 100
    fill_pages_fetched: int = 0
    fill_pagination_complete: bool = True
    fill_pagination_stop_reason: str = "not_run"
    positions_seen: int
    position_snapshots_created: int


class SignalScanRead(BaseModel):
    job_run: JobRunRead
    strategies_seen: int
    strategies_scanned: int
    signals_created: int
    signals_skipped: int
    errors: list[str]
    no_signal_reasons: list[str]
    created_signal_ids: list[uuid.UUID]


class PositionExitEvaluationRead(BaseModel):
    positions_seen: int
    positions_evaluated: int
    exits_created: int
    exits_skipped: int
    errors: list[str]
    no_exit_reasons: list[str]
    position_ownership: list[dict[str, Any]]
    order_intent_ids: list[uuid.UUID]
    exit_evaluations: list[dict[str, Any]] = []


class NewsScanRead(BaseModel):
    job_run: JobRunRead
    market_items: list[dict[str, Any]]
    ticker_items: dict[str, list[dict[str, Any]]]
    owned_symbols: list[str]
    risk_assessment: dict[str, Any]
    sources_checked: int
    errors: list[str]


class MarketCycleRead(BaseModel):
    job_run: JobRunRead
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


class MarketMaintenanceRead(BaseModel):
    job_run: JobRunRead
    phase: str
    cleanup: dict[str, Any]
    reconcile: dict[str, Any] | None
    news: dict[str, Any] | None
    performance: dict[str, Any] | None
    readiness: dict[str, Any]
    settings_snapshot: dict[str, Any]
    trade_cases: dict[str, Any] | None = None
    review_snapshot: dict[str, Any] | None = None
    ai_trade_reviews: dict[str, Any] | None = None
    review_snapshot_retention: dict[str, Any] | None = None


class TradingDataResetRead(BaseModel):
    job_run: JobRunRead
    dry_run: bool
    include_history: bool
    counts_before: dict[str, int]
    deleted: dict[str, int]
    kept_tables: list[str]
    confirmation_phrase: str


class TradeCasePopulationRead(BaseModel):
    job_run: JobRunRead
    round_trips_seen: int
    inserted: int
    updated: int
    skipped: int
    errors: list[str]


class AiTradeReviewWriterRead(BaseModel):
    job_run: JobRunRead
    trade_cases_seen: int
    reviews_created: int
    reviews_skipped: int
    suggestions_created: int
    errors: list[str]


class PatchStrategyDteRead(BaseModel):
    job_run: JobRunRead
    strategies_seen: int
    strategies_updated: int
    strategies_skipped: int
