from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import BrokerOrder, Fill, JobRun, OrderIntent, Signal, Strategy
from app.services.performance_review import get_paper_performance_review


@dataclass(slots=True)
class LearningReportResult:
    generated_at: datetime
    totals: dict[str, Any]
    performance: dict[str, Any]
    signals_by_strategy: list[dict[str, Any]]
    intents_by_strategy: list[dict[str, Any]]
    non_trade_reasons: list[dict[str, Any]]
    job_failures: list[dict[str, Any]]


def build_learning_report(db: Session, *, limit: int = 500) -> LearningReportResult:
    performance = get_paper_performance_review(db, limit=limit)
    return LearningReportResult(
        generated_at=datetime.now(timezone.utc),
        totals=_totals(db),
        performance={
            "fills_seen": performance.fills_seen,
            "matched_round_trips": performance.matched_round_trips,
            "totals": performance.totals,
            "by_strategy": performance.by_strategy,
            "by_symbol": performance.by_symbol,
            "open_positions": performance.open_positions,
            "signal_summary": performance.signal_summary,
            "no_signal_summary": performance.no_signal_summary,
            "option_selection_diagnostics": performance.option_selection_diagnostics,
            "rejected_preview_outcomes": performance.rejected_preview_outcomes,
        },
        signals_by_strategy=_signals_by_strategy(db, limit=limit),
        intents_by_strategy=_intents_by_strategy(db, limit=limit),
        non_trade_reasons=_non_trade_reasons(db, limit=limit),
        job_failures=_job_failures(db, limit=50),
    )


def _totals(db: Session) -> dict[str, Any]:
    return {
        "signals": _count(db, Signal),
        "order_intents": _count(db, OrderIntent),
        "broker_orders": _count(db, BrokerOrder),
        "fills": _count(db, Fill),
        "job_runs": _count(db, JobRun),
    }


def _count(db: Session, model: type) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _signals_by_strategy(db: Session, *, limit: int) -> list[dict[str, Any]]:
    statement = (
        select(
            Strategy.name,
            Signal.signal_type,
            Signal.direction,
            Signal.status,
            func.count(Signal.id),
        )
        .select_from(Signal)
        .join(Strategy, Signal.strategy_id == Strategy.id, isouter=True)
        .group_by(Strategy.name, Signal.signal_type, Signal.direction, Signal.status)
        .order_by(func.count(Signal.id).desc())
        .limit(limit)
    )
    return [
        {
            "strategy_name": row[0],
            "signal_type": row[1],
            "direction": row[2],
            "status": row[3],
            "count": int(row[4]),
        }
        for row in db.execute(statement)
    ]


def _intents_by_strategy(db: Session, *, limit: int) -> list[dict[str, Any]]:
    statement = (
        select(
            Strategy.name,
            OrderIntent.underlying_symbol,
            OrderIntent.side,
            OrderIntent.status,
            func.count(OrderIntent.id),
        )
        .select_from(OrderIntent)
        .join(Strategy, OrderIntent.strategy_id == Strategy.id, isouter=True)
        .group_by(
            Strategy.name,
            OrderIntent.underlying_symbol,
            OrderIntent.side,
            OrderIntent.status,
        )
        .order_by(func.count(OrderIntent.id).desc())
        .limit(limit)
    )
    return [
        {
            "strategy_name": row[0],
            "underlying_symbol": row[1],
            "side": row[2],
            "status": row[3],
            "count": int(row[4]),
        }
        for row in db.execute(statement)
    ]


def _non_trade_reasons(db: Session, *, limit: int) -> list[dict[str, Any]]:
    signal_statement = (
        select(Strategy.name, Signal.rejected_reason, func.count(Signal.id))
        .select_from(Signal)
        .join(Strategy, Signal.strategy_id == Strategy.id, isouter=True)
        .where(Signal.rejected_reason.is_not(None))
        .group_by(Strategy.name, Signal.rejected_reason)
        .order_by(func.count(Signal.id).desc())
        .limit(limit)
    )
    intent_statement = (
        select(Strategy.name, OrderIntent.rejection_reason, func.count(OrderIntent.id))
        .select_from(OrderIntent)
        .join(Strategy, OrderIntent.strategy_id == Strategy.id, isouter=True)
        .where(OrderIntent.rejection_reason.is_not(None))
        .group_by(Strategy.name, OrderIntent.rejection_reason)
        .order_by(func.count(OrderIntent.id).desc())
        .limit(limit)
    )
    reasons = [
        {
            "source": "signals",
            "strategy_name": row[0],
            "reason": row[1],
            "count": int(row[2]),
        }
        for row in db.execute(signal_statement)
    ]
    reasons.extend(
        {
            "source": "order_intents",
            "strategy_name": row[0],
            "reason": row[1],
            "count": int(row[2]),
        }
        for row in db.execute(intent_statement)
    )
    reasons.extend(_no_signal_reasons_from_job_runs(db, limit=limit))
    return sorted(reasons, key=lambda item: item["count"], reverse=True)[:limit]


def _no_signal_reasons_from_job_runs(db: Session, *, limit: int) -> list[dict[str, Any]]:
    """Aggregate no_signal_reasons from recent scan job_runs.

    These are the most common non-trade outcomes (threshold not crossed,
    no bars returned, dedupe suppression) and are stored in job_run details
    rather than on Signal or OrderIntent records.
    """
    statement = (
        select(JobRun)
        .where(JobRun.job_name.in_(["scan_signals", "market_cycle"]))
        .where(JobRun.status == "succeeded")
        .order_by(JobRun.started_at.desc())
        .limit(limit)
    )
    reason_counts: dict[str, int] = {}
    for job_run in db.scalars(statement):
        details = job_run.details if isinstance(job_run.details, dict) else {}
        for reason in _extract_no_signal_reasons(details):
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    return [
        {"source": "scan_no_signal", "strategy_name": None, "reason": reason, "count": count}
        for reason, count in reason_counts.items()
    ]


def _extract_no_signal_reasons(details: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    # scan_signals job stores reasons directly
    direct = details.get("no_signal_reasons")
    if isinstance(direct, list):
        reasons.extend(str(r) for r in direct if r)
    # market_cycle job nests them under the scan step
    scan = details.get("scan")
    if isinstance(scan, dict):
        nested = scan.get("no_signal_reasons")
        if isinstance(nested, list):
            reasons.extend(str(r) for r in nested if r)
    return reasons


def _job_failures(db: Session, *, limit: int) -> list[dict[str, Any]]:
    statement = (
        select(JobRun)
        .where(JobRun.status != "succeeded")
        .order_by(JobRun.started_at.desc())
        .limit(limit)
    )
    failures = []
    for job_run in db.scalars(statement):
        details = job_run.details if isinstance(job_run.details, dict) else {}
        diagnostics = details.get("diagnostics") if isinstance(details, dict) else None
        failures.append(
            {
                "job_run_id": str(job_run.id),
                "job_name": job_run.job_name,
                "status": job_run.status,
                "started_at": job_run.started_at.isoformat(),
                "error": job_run.error,
                "diagnostics": diagnostics if isinstance(diagnostics, dict) else {},
            }
        )
    return failures
