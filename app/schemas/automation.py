from datetime import datetime
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
    active_strategies: list[AutomationStrategyRead]
    latest_job_runs: dict[str, JobRunRead | None]
