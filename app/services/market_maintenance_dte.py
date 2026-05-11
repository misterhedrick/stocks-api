from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from typing import Any

from sqlalchemy import select

from sqlalchemy.orm import Session

from app.db.models import Strategy

from app.services.market_maintenance_types import PatchStrategyDteResult

def patch_strategy_dte(
    db: Session,
    *,
    min_dte: int = 2,
    max_dte: int = 30,
) -> PatchStrategyDteResult:
    """Update scanner.preview DTE window on all active strategies that need it."""
    import copy

    started_at = datetime.now(timezone.utc)
    job_run = JobRun(
        job_name="patch_strategy_dte",
        status="running",
        started_at=started_at,
        details={},
    )
    db.add(job_run)
    db.flush()

    strategies = list(db.scalars(select(Strategy).where(Strategy.is_active == True)))  # noqa: E712
    updated = 0
    skipped = 0

    for strategy in strategies:
        config = strategy.config or {}
        scanner = config.get("scanner")
        if not isinstance(scanner, dict):
            skipped += 1
            continue
        preview = scanner.get("preview")
        if not isinstance(preview, dict):
            skipped += 1
            continue
        if (
            preview.get("min_days_to_expiration") == min_dte
            and preview.get("max_days_to_expiration") == max_dte
        ):
            skipped += 1
            continue

        new_config = copy.deepcopy(config)
        new_config["scanner"]["preview"]["min_days_to_expiration"] = min_dte
        new_config["scanner"]["preview"]["max_days_to_expiration"] = max_dte
        strategy.config = new_config
        db.add(strategy)
        updated += 1

    details = {
        "strategies_seen": len(strategies),
        "strategies_updated": updated,
        "strategies_skipped": skipped,
        "min_dte": min_dte,
        "max_dte": max_dte,
    }
    job_run.status = "succeeded"
    job_run.finished_at = datetime.now(timezone.utc)
    job_run.details = details
    db.add(job_run)
    db.commit()
    db.refresh(job_run)

    logger.info(
        "patch_strategy_dte succeeded: updated=%d skipped=%d min_dte=%d max_dte=%d",
        updated, skipped, min_dte, max_dte,
    )
    return PatchStrategyDteResult(
        job_run=job_run,
        strategies_seen=len(strategies),
        strategies_updated=updated,
        strategies_skipped=skipped,
    )

def _json_safe_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    return value
