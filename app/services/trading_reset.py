from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AiTradeReview,
    AuditLog,
    BrokerOrder,
    Fill,
    JobRun,
    OptionSelectionDiagnostic,
    OrderIntent,
    PositionSnapshot,
    Signal,
    StrategyChangeSuggestion,
    TradeCase,
)
from app.services.audit_logs import record_audit_log


RESET_TRADING_DATA_CONFIRMATION = "RESET_TRADING_DATA"
RUNTIME_TABLES = (
    (StrategyChangeSuggestion, "strategy_change_suggestions"),
    (AiTradeReview, "ai_trade_reviews"),
    (TradeCase, "trade_cases"),
    (OptionSelectionDiagnostic, "option_selection_diagnostics"),
    (Fill, "fills"),
    (BrokerOrder, "broker_orders"),
    (OrderIntent, "order_intents"),
    (Signal, "signals"),
    (PositionSnapshot, "position_snapshots"),
)
HISTORY_TABLES = (
    (AuditLog, "audit_logs"),
    (JobRun, "job_runs"),
)
KEPT_TABLES = ("strategies",)
KEPT_TABLES_WITH_HISTORY = ("strategies", "job_runs", "audit_logs")


class TradingDataResetConfirmationError(RuntimeError):
    pass


@dataclass(slots=True)
class TradingDataResetResult:
    job_run: JobRun
    dry_run: bool
    include_history: bool
    counts_before: dict[str, int]
    deleted: dict[str, int]
    kept_tables: list[str]
    confirmation_phrase: str


def run_trading_data_reset(
    db: Session,
    *,
    dry_run: bool = True,
    include_history: bool = True,
    confirm: str | None = None,
) -> TradingDataResetResult:
    if not dry_run and confirm != RESET_TRADING_DATA_CONFIRMATION:
        raise TradingDataResetConfirmationError(
            f"Set confirm={RESET_TRADING_DATA_CONFIRMATION} to clear local trading data."
        )

    try:
        started_at = datetime.now(timezone.utc)
        reset_tables = _reset_tables(include_history=include_history)
        kept_tables = _kept_tables(include_history=include_history)
        counts_before = _table_counts(db, reset_tables)
        deleted = {table_name: 0 for _, table_name in reset_tables}

        if not dry_run:
            for model, table_name in reset_tables:
                result = db.execute(delete(model))
                deleted[table_name] = _delete_rowcount(
                    result,
                    fallback=counts_before[table_name],
                )

        details = {
            "dry_run": dry_run,
            "include_history": include_history,
            "counts_before": counts_before,
            "deleted": deleted,
            "kept_tables": list(kept_tables),
            "confirmation_phrase": RESET_TRADING_DATA_CONFIRMATION,
        }
        job_run = _record_reset_job_run(
            db,
            started_at=started_at,
            details=details,
        )

        return TradingDataResetResult(
            job_run=job_run,
            dry_run=dry_run,
            include_history=include_history,
            counts_before=counts_before,
            deleted=deleted,
            kept_tables=list(kept_tables),
            confirmation_phrase=RESET_TRADING_DATA_CONFIRMATION,
        )
    except Exception as exc:
        db.rollback()
        _record_failed_reset_job_run(db, started_at=started_at, exc=exc)
        raise


def _reset_tables(*, include_history: bool) -> tuple[tuple[type, str], ...]:
    if include_history:
        return RUNTIME_TABLES + HISTORY_TABLES
    return RUNTIME_TABLES


def _kept_tables(*, include_history: bool) -> tuple[str, ...]:
    if include_history:
        return KEPT_TABLES
    return KEPT_TABLES_WITH_HISTORY


def _table_counts(
    db: Session,
    tables: tuple[tuple[type, str], ...],
) -> dict[str, int]:
    return {
        table_name: int(db.scalar(select(func.count(model.id))) or 0)
        for model, table_name in tables
    }


def _delete_rowcount(result: Any, *, fallback: int) -> int:
    rowcount = getattr(result, "rowcount", None)
    if isinstance(rowcount, int) and rowcount >= 0:
        return rowcount
    return fallback


def _record_reset_job_run(
    db: Session,
    *,
    started_at: datetime,
    details: dict[str, Any],
) -> JobRun:
    job_run = JobRun(
        job_name="trading_data_reset",
        status="succeeded",
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        details=details,
        error=None,
    )
    db.add(job_run)
    db.flush()
    record_audit_log(
        db,
        event_type="trading_data_reset.succeeded",
        entity_type="job_run",
        entity_id=job_run.id,
        message="Local trading data clean slate completed",
        payload=details,
    )
    db.commit()
    db.refresh(job_run)
    return job_run


def _record_failed_reset_job_run(
    db: Session,
    *,
    started_at: datetime,
    exc: Exception,
) -> JobRun:
    error = f"{exc.__class__.__name__}: {exc}"
    job_run = JobRun(
        job_name="trading_data_reset",
        status="failed",
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        details={},
        error=error,
    )
    db.add(job_run)
    db.flush()
    record_audit_log(
        db,
        event_type="trading_data_reset.failed",
        entity_type="job_run",
        entity_id=job_run.id,
        message="Local trading data clean slate failed",
        payload={"error": error},
    )
    db.commit()
    db.refresh(job_run)
    return job_run
