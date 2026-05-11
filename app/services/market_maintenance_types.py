from __future__ import annotations

from dataclasses import dataclass

from typing import Any

from app.db.models import JobRun

AUTO_POST_MARKET_START_HOUR_UTC = 17

@dataclass(slots=True)
class MarketMaintenanceResult:
    job_run: JobRun
    phase: str
    cleanup: dict[str, Any]
    reconcile: dict[str, Any] | None
    news: dict[str, Any] | None
    performance: dict[str, Any] | None
    readiness: dict[str, Any]
    settings_snapshot: dict[str, Any]
    trade_cases: dict[str, Any] | None = None
    paper_review_snapshot: dict[str, Any] | None = None
    ai_trade_reviews: dict[str, Any] | None = None

@dataclass(slots=True)
class PatchStrategyDteResult:
    job_run: JobRun
    strategies_seen: int
    strategies_updated: int
    strategies_skipped: int
