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


class MarketCycleRead(BaseModel):
    job_run: JobRunRead
    scan_enabled: bool
    reconcile_enabled: bool
    preview_enabled: bool
    submit_enabled: bool
    scan: dict[str, Any] | None
    reconcile: dict[str, Any] | None
    preview: dict[str, Any] | None
    submit: dict[str, Any] | None
