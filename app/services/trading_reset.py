from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    BrokerOrder,
    Fill,
    JobRun,
    OrderIntent,
    PositionSnapshot,
    Signal,
)
from app.services.audit_logs import record_audit_log


RESET_TRADING_DATA_CONFIRMATION = "RESET_TRADING_DATA"
RUNTIME_TABLES = (
    (Fill, "fills"),
    (BrokerOrder, "broker_orders"),
    (OrderIntent, "order_intents"),
    (Signal, "signals"),
    (PositionSnapshot, "position_snapshots"),
)
KEPT_TABLES = ("strategies", "job_runs", "audit_logs")


class TradingDataResetConfirmationError(RuntimeError):
    pass


@dataclass(slots=True)
class TradingDataResetResult:
    job_run: JobRun
    dry_run: bool
    counts_before: dict[str, int]
    deleted: dict[str, int]
    kept_tables: list[str]
    confirmation_phrase: str


def run_trading_data_reset(
    db: Session,
    *,
    dry_run: bool = True,
    confirm: str | None = None,
) -> TradingDataResetResult:
    if not dry_run and confirm != RESET_TRADING_DATA_CONFIRMATION:
        raise TradingDataResetConfirmationError(
            f"Set confirm={RESET_TRADING_DATA_CONFIRMATION} to clear local trading data."
        )

    started_at = datetime.now(timezone.utc)
    job_run = JobRun(
        job_name="trading_data_reset",
        status="running",
        started_at=started_at,
        details={},
    )
    db.add(job_run)
    db.flush()

    try:
        counts_before = _runtime_table_counts(db)
        deleted = {table_name: 0 for _, table_name in RUNTIME_TABLES}

        if not dry_run:
            for model, table_name in RUNTIME_TABLES:
                result = db.execute(delete(model))
                deleted[table_name] = _delete_rowcount(
                    result,
                    fallback=counts_before[table_name],
                )

        details = {
            "dry_run": dry_run,
            "counts_before": counts_before,
            "deleted": deleted,
            "kept_tables": list(KEPT_TABLES),
            "confirmation_phrase": RESET_TRADING_DATA_CONFIRMATION,
        }
        job_run.status = "succeeded"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = details
        job_run.error = None
        db.add(job_run)
        record_audit_log(
            db,
            event_type="trading_data_reset.succeeded",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Local trading runtime data reset completed",
            payload=details,
        )
        db.commit()
        db.refresh(job_run)

        return TradingDataResetResult(
            job_run=job_run,
            dry_run=dry_run,
            counts_before=counts_before,
            deleted=deleted,
            kept_tables=list(KEPT_TABLES),
            confirmation_phrase=RESET_TRADING_DATA_CONFIRMATION,
        )
    except Exception as exc:
        db.rollback()
        job_run.status = "failed"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = {}
        job_run.error = f"{exc.__class__.__name__}: {exc}"
        db.add(job_run)
        record_audit_log(
            db,
            event_type="trading_data_reset.failed",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Local trading runtime data reset failed",
            payload={"error": job_run.error},
        )
        db.commit()
        db.refresh(job_run)
        raise


def _runtime_table_counts(db: Session) -> dict[str, int]:
    return {
        table_name: int(db.scalar(select(func.count(model.id))) or 0)
        for model, table_name in RUNTIME_TABLES
    }


def _delete_rowcount(result: Any, *, fallback: int) -> int:
    rowcount = getattr(result, "rowcount", None)
    if isinstance(rowcount, int) and rowcount >= 0:
        return rowcount
    return fallback
