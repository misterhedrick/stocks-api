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
    order_intent_ids: list[uuid.UUID]


class NewsScanRead(BaseModel):
    job_run: JobRunRead
    market_items: list[dict[str, Any]]
    ticker_items: dict[str, list[dict[str, Any]]]
    owned_symbols: list[str]
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
