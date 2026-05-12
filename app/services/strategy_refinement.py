from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PaperReviewSnapshot, StrategyTuningDecision
from app.services.audit_logs import record_audit_log

READY_FOR_REVIEW = "ready_for_review"
WATCH = "watch"
NOT_ENOUGH_DATA = "not_enough_data"
NEEDS_OPTION_FILTER_REVIEW = "needs_option_filter_review"
NEEDS_SIGNAL_THRESHOLD_REVIEW = "needs_signal_threshold_review"
NEEDS_EXIT_RULE_REVIEW = "needs_exit_rule_review"

VALID_DECISION_STATUSES = {"approved", "rejected", "applied", "archived"}


@dataclass(slots=True)
class StrategyTuningDecisionResult:
    decision: StrategyTuningDecision


def build_strategy_refinement_summary(
    db: Session,
    *,
    days: int = 10,
    min_closed_trade_cases: int = 5,
    min_rejected_previews: int = 10,
    min_no_signal_reasons: int = 20,
    limit: int = 50,
) -> dict[str, Any]:
    snapshots = _recent_snapshots(db, limit=days)
    decisions = _recent_decisions(db, limit=500)
    groups = _aggregate_snapshot_candidates(snapshots)
    _attach_decisions(groups, decisions, snapshots)

    candidates = [
        _finalize_candidate(
            group,
            min_closed_trade_cases=min_closed_trade_cases,
            min_rejected_previews=min_rejected_previews,
            min_no_signal_reasons=min_no_signal_reasons,
        )
        for group in groups.values()
    ]
    candidates = sorted(
        candidates,
        key=lambda item: (
            _readiness_rank(str(item["readiness_status"])),
            -int(item["latest_priority_score"]),
            str(item["scanner_type"]),
            str(item["symbol"]),
        ),
    )[:limit]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_count": len(snapshots),
        "snapshot_window": _snapshot_window(snapshots),
        "minimum_evidence": {
            "closed_trade_cases": min_closed_trade_cases,
            "rejected_previews": min_rejected_previews,
            "no_signal_reasons": min_no_signal_reasons,
        },
        "summary": _summary_counts(candidates),
        "candidates": candidates,
        "recent_tuning_decisions": [
            _decision_read_item(decision)
            for decision in decisions[:25]
        ],
        "human_review_only": True,
        "auto_apply": False,
    }


def get_strategy_tuning_decisions(
    db: Session,
    *,
    status: str | None = None,
    scanner_type: str | None = None,
    symbol: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    statement = select(StrategyTuningDecision).order_by(
        StrategyTuningDecision.created_at.desc()
    )
    if status:
        statement = statement.where(StrategyTuningDecision.status == status.strip().lower())
    if scanner_type:
        statement = statement.where(StrategyTuningDecision.scanner_type == scanner_type)
    if symbol:
        statement = statement.where(StrategyTuningDecision.symbol == symbol.strip().upper())
    return [_decision_read_item(item) for item in db.scalars(statement.limit(limit))]


def create_strategy_tuning_decision(
    db: Session,
    *,
    scanner_type: str,
    symbol: str,
    decision_type: str,
    description: str | None = None,
    expected_effect: str | None = None,
    proposed_config_patch: dict[str, Any] | None = None,
    evidence_snapshot_ids: list[str] | None = None,
    evidence_summary: dict[str, Any] | None = None,
    strategy_id: uuid.UUID | None = None,
    created_by: str | None = None,
    status: str = "approved",
) -> StrategyTuningDecisionResult:
    normalized_status = _normalize_decision_status(status)
    decision = StrategyTuningDecision(
        strategy_id=strategy_id,
        scanner_type=scanner_type.strip(),
        symbol=symbol.strip().upper(),
        decision_type=decision_type.strip(),
        status=normalized_status,
        description=description,
        expected_effect=expected_effect,
        proposed_config_patch=proposed_config_patch or {},
        evidence_snapshot_ids=evidence_snapshot_ids or [],
        evidence_summary=evidence_summary or {},
        outcome_summary={},
        created_by=created_by,
    )
    db.add(decision)
    db.flush()
    record_audit_log(
        db,
        event_type="strategy_tuning_decisions.created",
        entity_type="strategy_tuning_decision",
        entity_id=decision.id,
        message="Strategy tuning decision recorded for human-reviewed refinement",
        payload={
            "scanner_type": decision.scanner_type,
            "symbol": decision.symbol,
            "decision_type": decision.decision_type,
            "status": decision.status,
            "auto_apply": False,
        },
    )
    db.commit()
    db.refresh(decision)
    return StrategyTuningDecisionResult(decision=decision)


def update_strategy_tuning_decision(
    db: Session,
    *,
    decision_id: uuid.UUID,
    status: str | None = None,
    outcome_summary: dict[str, Any] | None = None,
    expected_effect: str | None = None,
    description: str | None = None,
) -> StrategyTuningDecisionResult:
    decision = db.get(StrategyTuningDecision, decision_id)
    if decision is None:
        raise LookupError(f"strategy tuning decision {decision_id} was not found")
    if status is not None:
        decision.status = _normalize_decision_status(status)
    if outcome_summary is not None:
        decision.outcome_summary = outcome_summary
    if expected_effect is not None:
        decision.expected_effect = expected_effect
    if description is not None:
        decision.description = description
    db.add(decision)
    record_audit_log(
        db,
        event_type="strategy_tuning_decisions.updated",
        entity_type="strategy_tuning_decision",
        entity_id=decision.id,
        message="Strategy tuning decision metadata updated",
        payload={
            "status": decision.status,
            "outcome_summary": decision.outcome_summary,
            "auto_apply": False,
        },
    )
    db.commit()
    db.refresh(decision)
    return StrategyTuningDecisionResult(decision=decision)


def _recent_snapshots(db: Session, *, limit: int) -> list[PaperReviewSnapshot]:
    return list(
        db.scalars(
            select(PaperReviewSnapshot)
            .order_by(PaperReviewSnapshot.review_date.desc(), PaperReviewSnapshot.generated_at.desc())
            .limit(limit)
        )
    )


def _recent_decisions(db: Session, *, limit: int) -> list[StrategyTuningDecision]:
    return list(
        db.scalars(
            select(StrategyTuningDecision)
            .order_by(StrategyTuningDecision.created_at.desc())
            .limit(limit)
        )
    )


def _aggregate_snapshot_candidates(
    snapshots: list[PaperReviewSnapshot],
) -> dict[tuple[str, str], dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot in sorted(snapshots, key=lambda item: item.review_date):
        learning_report = _snapshot_learning_report(snapshot)
        candidates = learning_report.get("refinement_candidates")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            scanner_type = str(candidate.get("scanner_type") or "unknown")
            symbol = str(candidate.get("symbol") or "unknown").upper()
            group = groups.setdefault(
                (scanner_type, symbol),
                {
                    "scanner_type": scanner_type,
                    "symbol": symbol,
                    "snapshots": [],
                    "focus_counts": {},
                    "decisions": [],
                    "before_after_windows": [],
                },
            )
            group["snapshots"].append(
                {
                    "snapshot_id": str(snapshot.id),
                    "review_date": snapshot.review_date.isoformat(),
                    "priority_score": int(candidate.get("priority_score") or 0),
                    "recommended_focus": list(candidate.get("recommended_focus") or []),
                    "signals": candidate.get("signals") if isinstance(candidate.get("signals"), dict) else {},
                    "option_selection": candidate.get("option_selection")
                    if isinstance(candidate.get("option_selection"), dict)
                    else {},
                    "trade_cases": candidate.get("trade_cases")
                    if isinstance(candidate.get("trade_cases"), dict)
                    else {},
                    "no_signal": candidate.get("no_signal")
                    if isinstance(candidate.get("no_signal"), dict)
                    else {},
                    "suggestions": candidate.get("suggestions")
                    if isinstance(candidate.get("suggestions"), dict)
                    else {},
                }
            )
            for focus in candidate.get("recommended_focus") or []:
                key = str(focus)
                group["focus_counts"][key] = group["focus_counts"].get(key, 0) + 1
    return groups


def _attach_decisions(
    groups: dict[tuple[str, str], dict[str, Any]],
    decisions: list[StrategyTuningDecision],
    snapshots: list[PaperReviewSnapshot],
) -> None:
    for decision in decisions:
        key = (decision.scanner_type, decision.symbol.upper())
        group = groups.setdefault(
            key,
            {
                "scanner_type": decision.scanner_type,
                "symbol": decision.symbol.upper(),
                "snapshots": [],
                "focus_counts": {},
                "decisions": [],
                "before_after_windows": [],
            },
        )
        group["decisions"].append(_decision_read_item(decision))
        group["before_after_windows"].append(
            _before_after_window(decision, snapshots)
        )


def _finalize_candidate(
    group: dict[str, Any],
    *,
    min_closed_trade_cases: int,
    min_rejected_previews: int,
    min_no_signal_reasons: int,
) -> dict[str, Any]:
    snapshots = group["snapshots"]
    latest = snapshots[-1] if snapshots else {}
    previous = snapshots[-2] if len(snapshots) >= 2 else {}
    latest_score = int(latest.get("priority_score") or 0)
    previous_score = int(previous.get("priority_score") or 0)
    evidence = _evidence_totals(snapshots)
    readiness = _readiness_status(
        latest=latest,
        evidence=evidence,
        min_closed_trade_cases=min_closed_trade_cases,
        min_rejected_previews=min_rejected_previews,
        min_no_signal_reasons=min_no_signal_reasons,
    )
    return {
        "scanner_type": group["scanner_type"],
        "symbol": group["symbol"],
        "readiness_status": readiness,
        "minimum_evidence_met": readiness != NOT_ENOUGH_DATA,
        "latest_priority_score": latest_score,
        "priority_trend": {
            "previous": previous_score,
            "latest": latest_score,
            "delta": latest_score - previous_score,
            "direction": _trend_direction(latest_score - previous_score),
        },
        "recommended_focus": _top_focus(group["focus_counts"]),
        "evidence": evidence,
        "latest_snapshot": latest,
        "snapshot_history": snapshots,
        "tuning_events": group["decisions"],
        "before_after_windows": group["before_after_windows"],
        "human_review_only": True,
        "auto_apply": False,
    }


def _evidence_totals(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    closed = 0
    losses = 0
    rejected = 0
    diagnostics = 0
    no_signal = 0
    pending_suggestions = 0
    realized_pl = Decimal("0")
    for item in snapshots:
        trade_cases = item.get("trade_cases") if isinstance(item.get("trade_cases"), dict) else {}
        signals = item.get("signals") if isinstance(item.get("signals"), dict) else {}
        option_selection = (
            item.get("option_selection")
            if isinstance(item.get("option_selection"), dict)
            else {}
        )
        no_signal_payload = item.get("no_signal") if isinstance(item.get("no_signal"), dict) else {}
        suggestions = item.get("suggestions") if isinstance(item.get("suggestions"), dict) else {}
        closed += _int_value(trade_cases.get("closed"))
        losses += _int_value(trade_cases.get("losses"))
        rejected += _int_value(signals.get("preview_rejected"))
        diagnostics += _int_value(option_selection.get("diagnostics_seen"))
        no_signal += _int_value(no_signal_payload.get("reasons_seen"))
        pending_suggestions += _int_value(suggestions.get("pending"))
        realized_pl += _decimal_value(trade_cases.get("total_realized_pl"))
    return {
        "closed_trade_cases": closed,
        "losing_trade_cases": losses,
        "preview_rejected": rejected,
        "option_diagnostics": diagnostics,
        "no_signal_reasons": no_signal,
        "pending_suggestions": pending_suggestions,
        "total_realized_pl": _decimal_string(realized_pl),
    }


def _readiness_status(
    *,
    latest: dict[str, Any],
    evidence: dict[str, Any],
    min_closed_trade_cases: int,
    min_rejected_previews: int,
    min_no_signal_reasons: int,
) -> str:
    focus = set(latest.get("recommended_focus") or [])
    has_trade_evidence = int(evidence["closed_trade_cases"]) >= min_closed_trade_cases
    has_rejection_evidence = int(evidence["preview_rejected"]) >= min_rejected_previews
    has_no_signal_evidence = int(evidence["no_signal_reasons"]) >= min_no_signal_reasons
    if not (has_trade_evidence or has_rejection_evidence or has_no_signal_evidence):
        return NOT_ENOUGH_DATA
    if "review_option_selection_filters" in focus:
        return NEEDS_OPTION_FILTER_REVIEW
    if "review_signal_thresholds" in focus:
        return NEEDS_SIGNAL_THRESHOLD_REVIEW
    if "review_strategy_risk_controls" in focus and has_trade_evidence:
        return NEEDS_EXIT_RULE_REVIEW
    if focus - {"monitor_strategy"}:
        return READY_FOR_REVIEW
    return WATCH


def _before_after_window(
    decision: StrategyTuningDecision,
    snapshots: list[PaperReviewSnapshot],
) -> dict[str, Any]:
    before_scores: list[int] = []
    after_scores: list[int] = []
    for snapshot in snapshots:
        score = _candidate_score_for_snapshot(
            snapshot,
            scanner_type=decision.scanner_type,
            symbol=decision.symbol,
        )
        if score is None:
            continue
        if snapshot.generated_at < decision.created_at:
            before_scores.append(score)
        else:
            after_scores.append(score)
    return {
        "decision_id": str(decision.id),
        "decision_created_at": decision.created_at.isoformat(),
        "status": decision.status,
        "before_snapshot_count": len(before_scores),
        "after_snapshot_count": len(after_scores),
        "average_priority_before": _average_int(before_scores),
        "average_priority_after": _average_int(after_scores),
        "priority_delta_after": (
            _average_int(after_scores) - _average_int(before_scores)
            if before_scores and after_scores
            else None
        ),
    }


def _candidate_score_for_snapshot(
    snapshot: PaperReviewSnapshot,
    *,
    scanner_type: str,
    symbol: str,
) -> int | None:
    learning_report = _snapshot_learning_report(snapshot)
    candidates = learning_report.get("refinement_candidates")
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if (
            str(candidate.get("scanner_type") or "unknown") == scanner_type
            and str(candidate.get("symbol") or "unknown").upper() == symbol.upper()
        ):
            return int(candidate.get("priority_score") or 0)
    return None


def _snapshot_learning_report(snapshot: PaperReviewSnapshot) -> dict[str, Any]:
    raw_payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
    learning_report = raw_payload.get("learning_report")
    return learning_report if isinstance(learning_report, dict) else {}


def _snapshot_window(snapshots: list[PaperReviewSnapshot]) -> dict[str, Any]:
    if not snapshots:
        return {"start_date": None, "end_date": None}
    dates = sorted(snapshot.review_date for snapshot in snapshots)
    return {"start_date": dates[0].isoformat(), "end_date": dates[-1].isoformat()}


def _summary_counts(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        status = str(candidate.get("readiness_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _decision_read_item(decision: StrategyTuningDecision) -> dict[str, Any]:
    return {
        "id": str(decision.id),
        "strategy_id": str(decision.strategy_id) if decision.strategy_id else None,
        "scanner_type": decision.scanner_type,
        "symbol": decision.symbol,
        "decision_type": decision.decision_type,
        "status": decision.status,
        "description": decision.description,
        "expected_effect": decision.expected_effect,
        "proposed_config_patch": decision.proposed_config_patch,
        "evidence_snapshot_ids": decision.evidence_snapshot_ids,
        "evidence_summary": decision.evidence_summary,
        "outcome_summary": decision.outcome_summary,
        "created_by": decision.created_by,
        "created_at": decision.created_at.isoformat(),
        "updated_at": decision.updated_at.isoformat(),
        "auto_apply": False,
    }


def _normalize_decision_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized not in VALID_DECISION_STATUSES:
        raise ValueError(
            "status must be approved, rejected, applied, or archived"
        )
    return normalized


def _top_focus(counts: dict[str, int]) -> list[str]:
    if not counts:
        return ["monitor_strategy"]
    return [
        key
        for key, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _trend_direction(delta: int) -> str:
    if delta > 0:
        return "worsening"
    if delta < 0:
        return "improving"
    return "flat"


def _readiness_rank(status: str) -> int:
    ranks = {
        NEEDS_OPTION_FILTER_REVIEW: 0,
        NEEDS_SIGNAL_THRESHOLD_REVIEW: 1,
        NEEDS_EXIT_RULE_REVIEW: 2,
        READY_FOR_REVIEW: 3,
        WATCH: 4,
        NOT_ENOUGH_DATA: 5,
    }
    return ranks.get(status, 6)


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _decimal_value(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _average_int(values: list[int]) -> int:
    if not values:
        return 0
    return round(sum(values) / len(values))


def _decimal_string(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f")
