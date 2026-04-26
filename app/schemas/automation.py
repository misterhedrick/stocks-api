from datetime import datetime
from decimal import Decimal
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict

from app.schemas.jobs import JobRunRead


class AutomationSwitchesRead(BaseModel):
    scan_enabled: bool
    reconcile_enabled: bool
    preview_enabled: bool
    submit_enabled: bool


class AutomationStrategyRead(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool
    scanner_type: str | None
    scanner_symbols: list[str]
    preview_enabled: bool
    submit_enabled: bool
    submit_limits: dict[str, Any]
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AutomationStatusRead(BaseModel):
    switches: AutomationSwitchesRead
    trading_automation_enabled: bool
    auto_submit_requires_paper: bool
    paper_mode: bool
    max_auto_orders_per_cycle: int
    max_auto_orders_per_day: int
    max_open_positions: int
    max_open_positions_per_symbol: int
    max_contracts_per_order: int
    max_estimated_premium_per_order: Decimal
    active_strategies: list[AutomationStrategyRead]
    latest_job_runs: dict[str, JobRunRead | None]
