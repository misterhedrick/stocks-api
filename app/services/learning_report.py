from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AiTradeReview,
    BrokerOrder,
    Fill,
    JobRun,
    OptionSelectionDiagnostic,
    OrderIntent,
    Signal,
    Strategy,
    StrategyChangeSuggestion,
    TradeCase,
)
from app.services.performance_review import get_performance_review


@dataclass(slots=True)
class LearningReportResult:
    generated_at: datetime
    totals: dict[str, Any]
    performance: dict[str, Any]
    signals_by_strategy: list[dict[str, Any]]
    intents_by_strategy: list[dict[str, Any]]
    non_trade_reasons: list[dict[str, Any]]
    refinement_candidates: list[dict[str, Any]]
    job_failures: list[dict[str, Any]]


def build_learning_report(db: Session, *, limit: int = 500) -> LearningReportResult:
    performance = get_performance_review(db, limit=limit)
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
        refinement_candidates=_refinement_candidates(
            db,
            performance=performance,
            limit=limit,
        ),
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
            if _is_expected_symbol_routing_miss(reason):
                continue
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


def _is_expected_symbol_routing_miss(reason: str) -> bool:
    if ":" in reason:
        _, detail = reason.split(":", 1)
        reason = detail.strip()
    return reason.startswith("scanner does not include symbol ")


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


def _refinement_candidates(
    db: Session,
    *,
    performance: object,
    limit: int,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    _merge_signal_refinement(groups, db, limit=limit)
    _merge_diagnostic_refinement(groups, db, limit=limit)
    _merge_trade_case_refinement(groups, db, limit=limit)
    _merge_suggestion_refinement(groups, db, limit=limit)
    _merge_no_signal_refinement(
        groups,
        getattr(performance, "no_signal_summary", {}),
    )
    _merge_rejected_preview_refinement(
        groups,
        getattr(performance, "rejected_preview_outcomes", []),
    )

    candidates = [
        _finalize_refinement_group(group)
        for group in groups.values()
        if group["scanner_type"] != "unknown"
    ]
    return sorted(
        candidates,
        key=lambda item: (
            -int(item["priority_score"]),
            str(item["scanner_type"]),
            str(item["symbol"]),
        ),
    )[:limit]


def _refinement_group(
    groups: dict[tuple[str, str], dict[str, Any]],
    *,
    scanner_type: str | None,
    symbol: str | None,
) -> dict[str, Any]:
    scanner_key = str(scanner_type or "unknown")
    symbol_key = "ALL_SYMBOLS"
    key = (scanner_key, symbol_key)
    if key not in groups:
        groups[key] = {
            "scanner_type": scanner_key,
            "symbol": symbol_key,
            "signals_seen": 0,
            "signal_status_counts": Counter(),
            "preview_rejected": 0,
            "preview_rejection_reasons": Counter(),
            "diagnostics_seen": 0,
            "diagnostic_candidate_count": 0,
            "diagnostic_reasons": Counter(),
            "closed_trade_cases": 0,
            "open_trade_cases": 0,
            "wins": 0,
            "losses": 0,
            "flats": 0,
            "total_realized_pl": Decimal("0"),
            "total_return_percent": Decimal("0"),
            "pending_suggestions": 0,
            "suggestion_status_counts": Counter(),
            "suggestion_type_counts": Counter(),
            "no_signal_reasons_seen": 0,
            "no_signal_reasons": Counter(),
            "rejected_preview_evidence": [],
        }
    return groups[key]


def _merge_signal_refinement(
    groups: dict[tuple[str, str], dict[str, Any]],
    db: Session,
    *,
    limit: int,
) -> None:
    statement = (
        select(Signal, Strategy.config)
        .join(Strategy, Signal.strategy_id == Strategy.id, isouter=True)
        .order_by(Signal.created_at.desc())
        .limit(limit)
    )
    for signal, strategy_config in db.execute(statement):
        market_context = signal.market_context if isinstance(signal.market_context, dict) else {}
        scanner_type = _scanner_type(strategy_config, market_context)
        group = _refinement_group(
            groups,
            scanner_type=scanner_type,
            symbol=signal.underlying_symbol or signal.symbol,
        )
        group["signals_seen"] += 1
        group["signal_status_counts"][str(signal.status or "unknown")] += 1
        rejection_reasons = (
            signal.preview_rejection_reasons
            if isinstance(signal.preview_rejection_reasons, dict)
            else {}
        )
        if signal.status == "preview_rejected" or rejection_reasons:
            group["preview_rejected"] += 1
            _merge_counter(group["preview_rejection_reasons"], rejection_reasons)


def _merge_diagnostic_refinement(
    groups: dict[tuple[str, str], dict[str, Any]],
    db: Session,
    *,
    limit: int,
) -> None:
    statement = (
        select(OptionSelectionDiagnostic)
        .order_by(OptionSelectionDiagnostic.created_at.desc())
        .limit(limit)
    )
    for diagnostic in db.scalars(statement):
        group = _refinement_group(
            groups,
            scanner_type=diagnostic.scanner_type or diagnostic.preview_profile,
            symbol=diagnostic.underlying_symbol,
        )
        group["diagnostics_seen"] += 1
        group["diagnostic_candidate_count"] += int(diagnostic.candidate_count or 0)
        _merge_counter(
            group["diagnostic_reasons"],
            diagnostic.reason_counts if isinstance(diagnostic.reason_counts, dict) else {},
        )


def _merge_trade_case_refinement(
    groups: dict[tuple[str, str], dict[str, Any]],
    db: Session,
    *,
    limit: int,
) -> None:
    statement = (
        select(TradeCase)
        .order_by(TradeCase.entry_time.desc())
        .limit(limit)
    )
    for trade_case in db.scalars(statement):
        group = _refinement_group(
            groups,
            scanner_type=_scanner_type_for_trade_case(trade_case),
            symbol=trade_case.underlying_symbol or trade_case.symbol,
        )
        if trade_case.is_open:
            group["open_trade_cases"] += 1
            continue
        group["closed_trade_cases"] += 1
        realized_pl = Decimal(str(trade_case.realized_pl or "0"))
        realized_return = Decimal(str(trade_case.realized_pl_percent or "0"))
        group["total_realized_pl"] += realized_pl
        group["total_return_percent"] += realized_return
        if realized_pl > 0:
            group["wins"] += 1
        elif realized_pl < 0:
            group["losses"] += 1
        else:
            group["flats"] += 1


def _merge_suggestion_refinement(
    groups: dict[tuple[str, str], dict[str, Any]],
    db: Session,
    *,
    limit: int,
) -> None:
    statement = (
        select(StrategyChangeSuggestion, AiTradeReview.assessment)
        .join(
            AiTradeReview,
            StrategyChangeSuggestion.ai_trade_review_id == AiTradeReview.id,
            isouter=True,
        )
        .order_by(StrategyChangeSuggestion.created_at.desc())
        .limit(limit)
    )
    for suggestion, assessment in db.execute(statement):
        context = assessment if isinstance(assessment, dict) else {}
        group = _refinement_group(
            groups,
            scanner_type=context.get("scanner_type"),
            symbol=context.get("underlying_symbol") or context.get("symbol"),
        )
        status = str(suggestion.status or "unknown")
        suggestion_type = str(suggestion.suggestion_type or "unknown")
        group["suggestion_status_counts"][status] += 1
        group["suggestion_type_counts"][suggestion_type] += 1
        if status == "pending":
            group["pending_suggestions"] += 1


def _merge_no_signal_refinement(
    groups: dict[tuple[str, str], dict[str, Any]],
    no_signal_summary: Any,
) -> None:
    if not isinstance(no_signal_summary, dict):
        return
    by_scanner = no_signal_summary.get("by_scanner_type")
    if not isinstance(by_scanner, list):
        return
    for item in by_scanner:
        if not isinstance(item, dict):
            continue
        group = _refinement_group(
            groups,
            scanner_type=item.get("scanner_type"),
            symbol="ALL_SYMBOLS",
        )
        try:
            group["no_signal_reasons_seen"] += int(item.get("reasons_seen") or 0)
        except (TypeError, ValueError):
            pass
        _merge_counter(
            group["no_signal_reasons"],
            item.get("reasons") if isinstance(item.get("reasons"), dict) else {},
        )


def _merge_rejected_preview_refinement(
    groups: dict[tuple[str, str], dict[str, Any]],
    rejected_preview_outcomes: Any,
) -> None:
    if not isinstance(rejected_preview_outcomes, list):
        return
    for item in rejected_preview_outcomes:
        if not isinstance(item, dict):
            continue
        group = _refinement_group(
            groups,
            scanner_type=item.get("scanner_type"),
            symbol=item.get("symbol"),
        )
        group["rejected_preview_evidence"].append(
            {
                "rejected_signals": item.get("rejected_signals", 0),
                "later_matched_round_trips": item.get("later_matched_round_trips", 0),
                "later_realized_pnl": item.get("later_realized_pnl", "0"),
                "later_win_rate_percent": item.get("later_win_rate_percent", "0"),
            }
        )


def _finalize_refinement_group(group: dict[str, Any]) -> dict[str, Any]:
    closed = int(group["closed_trade_cases"])
    avg_return = (
        group["total_return_percent"] / Decimal(closed)
        if closed
        else Decimal("0")
    )
    focus = _refinement_focus(group)
    priority_score = (
        int(group["losses"]) * 5
        + int(group["preview_rejected"]) * 3
        + int(group["diagnostics_seen"]) * 2
        + int(group["pending_suggestions"]) * 4
        + int(group["no_signal_reasons_seen"])
        + _rejected_preview_priority(group["rejected_preview_evidence"])
    )
    return {
        "scanner_type": group["scanner_type"],
        "symbol": group["symbol"],
        "priority_score": priority_score,
        "human_review_only": True,
        "recommended_focus": focus,
        "signals": {
            "seen": group["signals_seen"],
            "status_counts": _counter_dict_from_counter(group["signal_status_counts"]),
            "preview_rejected": group["preview_rejected"],
            "preview_rejection_reasons": _counter_dict_from_counter(
                group["preview_rejection_reasons"],
            ),
        },
        "option_selection": {
            "diagnostics_seen": group["diagnostics_seen"],
            "candidate_count": group["diagnostic_candidate_count"],
            "reason_counts": _counter_dict_from_counter(group["diagnostic_reasons"]),
        },
        "trade_cases": {
            "closed": closed,
            "open": group["open_trade_cases"],
            "wins": group["wins"],
            "losses": group["losses"],
            "flats": group["flats"],
            "total_realized_pl": _decimal_string(group["total_realized_pl"]),
            "average_return_percent": _decimal_string(avg_return),
        },
        "no_signal": {
            "reasons_seen": group["no_signal_reasons_seen"],
            "reasons": _counter_dict_from_counter(group["no_signal_reasons"]),
        },
        "rejected_preview_evidence": group["rejected_preview_evidence"][:10],
        "suggestions": {
            "pending": group["pending_suggestions"],
            "by_status": _counter_dict_from_counter(group["suggestion_status_counts"]),
            "by_type": _counter_dict_from_counter(group["suggestion_type_counts"]),
        },
    }


def _refinement_focus(group: dict[str, Any]) -> list[str]:
    focus: list[str] = []
    if group["losses"]:
        focus.append("review_strategy_risk_controls")
    if group["preview_rejected"] or group["diagnostics_seen"]:
        focus.append("review_option_selection_filters")
    if group["no_signal_reasons_seen"]:
        focus.append("review_signal_thresholds")
    if _rejected_preview_priority(group["rejected_preview_evidence"]):
        focus.append("review_rejected_signal_outcomes")
    if group["pending_suggestions"]:
        focus.append("review_pending_suggestions")
    if not focus:
        focus.append("monitor_strategy")
    return focus


def _rejected_preview_priority(evidence: list[dict[str, Any]]) -> int:
    priority = 0
    for item in evidence:
        try:
            rejected = int(item.get("rejected_signals") or 0)
            later_pnl = Decimal(str(item.get("later_realized_pnl") or "0"))
        except (TypeError, ValueError):
            continue
        if rejected > 0 and later_pnl > 0:
            priority += 4
    return priority


def _scanner_type_for_trade_case(trade_case: TradeCase) -> str:
    context = trade_case.context if isinstance(trade_case.context, dict) else {}
    entry_context = context.get("entry") if isinstance(context.get("entry"), dict) else {}
    signal_context = (
        entry_context.get("signal")
        if isinstance(entry_context.get("signal"), dict)
        else {}
    )
    market_context = (
        signal_context.get("market_context")
        if isinstance(signal_context.get("market_context"), dict)
        else {}
    )
    scanner_type = market_context.get("strategy_type")
    return str(scanner_type or "unknown")


def _scanner_type(strategy_config: Any, market_context: Any) -> str | None:
    if isinstance(market_context, dict):
        strategy_type = market_context.get("strategy_type")
        if isinstance(strategy_type, str) and strategy_type.strip():
            return strategy_type.strip()
    if isinstance(strategy_config, dict):
        scanner = strategy_config.get("scanner")
        if isinstance(scanner, dict):
            scanner_type = scanner.get("type")
            if isinstance(scanner_type, str) and scanner_type.strip():
                return scanner_type.strip()
    return None


def _merge_counter(target: Counter[str], values: dict[str, Any]) -> None:
    for key, value in values.items():
        try:
            increment = int(value)
        except (TypeError, ValueError):
            increment = 1
        target[str(key)] += increment


def _counter_dict_from_counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _decimal_string(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f")
