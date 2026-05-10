from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AiTradeReview,
    JobRun,
    PaperReviewSnapshot,
    StrategyChangeSuggestion,
    TradeCase,
)
from app.services.audit_logs import record_audit_log


LOCAL_REVIEW_MODEL = "local-paper-review-v1"


@dataclass(slots=True)
class AiTradeReviewWriterResult:
    job_run: JobRun
    trade_cases_seen: int
    reviews_created: int
    reviews_skipped: int
    suggestions_created: int
    errors: list[str] = field(default_factory=list)


def write_ai_trade_reviews_from_paper_evidence(
    db: Session,
    *,
    limit: int = 100,
    review_model: str = LOCAL_REVIEW_MODEL,
) -> AiTradeReviewWriterResult:
    started_at = datetime.now(timezone.utc)
    job_run = JobRun(
        job_name="write_ai_trade_reviews",
        status="running",
        started_at=started_at,
        details={},
    )
    db.add(job_run)
    db.flush()

    try:
        latest_snapshot = _latest_snapshot(db)
        trade_cases = list(
            db.scalars(
                select(TradeCase)
                .where(TradeCase.is_open == False)  # noqa: E712
                .order_by(TradeCase.exit_time.desc().nullslast(), TradeCase.created_at.desc())
                .limit(limit)
            )
        )
        created = 0
        skipped = 0
        suggestions_created = 0
        errors: list[str] = []

        for trade_case in trade_cases:
            try:
                existing = db.scalar(
                    select(AiTradeReview)
                    .where(AiTradeReview.trade_case_id == trade_case.id)
                    .where(AiTradeReview.review_model == review_model)
                    .limit(1)
                )
                if existing is not None:
                    skipped += 1
                    continue

                assessment = _assessment_for_trade_case(
                    trade_case,
                    latest_snapshot=latest_snapshot,
                    review_model=review_model,
                )
                review = AiTradeReview(
                    trade_case_id=trade_case.id,
                    review_model=review_model,
                    review_status="generated",
                    assessment=assessment,
                    raw_response={
                        "source": "local_rule_based_review",
                        "review_model": review_model,
                        "generated_at": started_at.isoformat(),
                        "paper_review_snapshot_id": str(latest_snapshot.id)
                        if latest_snapshot is not None
                        else None,
                    },
                )
                db.add(review)
                db.flush()
                created += 1

                for suggestion_payload in _suggestions_for_assessment(
                    trade_case,
                    assessment,
                ):
                    db.add(
                        StrategyChangeSuggestion(
                            ai_trade_review_id=review.id,
                            strategy_id=trade_case.strategy_id,
                            suggestion_type=suggestion_payload["suggestion_type"],
                            description=suggestion_payload["description"],
                            proposed_config_patch=suggestion_payload.get(
                                "proposed_config_patch",
                                {},
                            ),
                            status="pending",
                        )
                    )
                    suggestions_created += 1
            except Exception as exc:
                errors.append(
                    f"trade_case {trade_case.id}: {exc.__class__.__name__}: {exc}"
                )

        details = {
            "trade_cases_seen": len(trade_cases),
            "reviews_created": created,
            "reviews_skipped": skipped,
            "suggestions_created": suggestions_created,
            "errors": errors,
            "review_model": review_model,
            "paper_review_snapshot_id": str(latest_snapshot.id)
            if latest_snapshot is not None
            else None,
        }
        job_run.status = "succeeded"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = details
        db.add(job_run)
        record_audit_log(
            db,
            event_type="ai_trade_reviews.write_succeeded",
            entity_type="job_run",
            entity_id=job_run.id,
            message="AI trade review writer succeeded",
            payload=details,
        )
        db.commit()
        db.refresh(job_run)
        return AiTradeReviewWriterResult(
            job_run=job_run,
            trade_cases_seen=len(trade_cases),
            reviews_created=created,
            reviews_skipped=skipped,
            suggestions_created=suggestions_created,
            errors=errors,
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
            event_type="ai_trade_reviews.write_failed",
            entity_type="job_run",
            entity_id=job_run.id,
            message="AI trade review writer failed",
            payload={"error": job_run.error},
        )
        db.commit()
        db.refresh(job_run)
        raise


def _latest_snapshot(db: Session) -> PaperReviewSnapshot | None:
    return db.scalar(
        select(PaperReviewSnapshot)
        .order_by(PaperReviewSnapshot.generated_at.desc())
        .limit(1)
    )


def _assessment_for_trade_case(
    trade_case: TradeCase,
    *,
    latest_snapshot: PaperReviewSnapshot | None,
    review_model: str,
) -> dict[str, Any]:
    realized_pl = Decimal(str(trade_case.realized_pl or "0"))
    realized_pl_percent = Decimal(str(trade_case.realized_pl_percent or "0"))
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
    scanner_type = market_context.get("strategy_type") or "unknown"
    symbol = trade_case.underlying_symbol or trade_case.symbol
    snapshot_context = _snapshot_context_for_trade(
        latest_snapshot,
        scanner_type=str(scanner_type),
        symbol=str(symbol),
    )

    outcome = "win" if realized_pl > 0 else "loss" if realized_pl < 0 else "flat"
    confidence = "medium"
    observations = [
        f"Closed trade outcome is {outcome}.",
        f"Realized P/L: {realized_pl}; return: {realized_pl_percent}%.",
    ]
    if snapshot_context["diagnostic_reasons"]:
        observations.append(
            "Recent option-selection diagnostics exist for this scanner/symbol."
        )
    if snapshot_context["rejected_shadow_outcomes"]:
        observations.append(
            "Rejected-signal shadow outcomes are available for comparison."
        )

    risk_notes: list[str] = []
    if realized_pl < 0:
        risk_notes.append("Loss should be compared with entry signal quality and option selection filters.")
    if abs(realized_pl_percent) >= Decimal("25"):
        risk_notes.append("Large percentage move; review sizing, spread, and exit timing.")
    if snapshot_context["diagnostic_reasons"]:
        risk_notes.append("Rejected candidates may indicate liquidity or spread pressure.")

    return {
        "review_model": review_model,
        "review_status": "generated",
        "trade_case_id": str(trade_case.id),
        "strategy_id": str(trade_case.strategy_id) if trade_case.strategy_id else None,
        "symbol": trade_case.symbol,
        "underlying_symbol": trade_case.underlying_symbol,
        "scanner_type": scanner_type,
        "outcome": outcome,
        "confidence": confidence,
        "realized_pl": str(realized_pl),
        "realized_pl_percent": str(realized_pl_percent),
        "observations": observations,
        "risk_notes": risk_notes,
        "snapshot_context": snapshot_context,
        "recommendation_boundary": "Suggestions are pending human review and must not be applied automatically.",
    }


def _snapshot_context_for_trade(
    snapshot: PaperReviewSnapshot | None,
    *,
    scanner_type: str,
    symbol: str,
) -> dict[str, Any]:
    if snapshot is None:
        return {
            "paper_review_snapshot_id": None,
            "diagnostic_reasons": {},
            "rejected_trade_comparisons": [],
            "rejected_shadow_outcomes": [],
        }

    diagnostics = snapshot.diagnostics if isinstance(snapshot.diagnostics, dict) else {}
    diagnostic_summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
    rejected = snapshot.rejected_outcomes if isinstance(snapshot.rejected_outcomes, dict) else {}

    return {
        "paper_review_snapshot_id": str(snapshot.id),
        "diagnostic_reasons": diagnostic_summary.get("reason_counts", {}),
        "rejected_trade_comparisons": [
            item
            for item in rejected.get("trade_comparison", [])
            if _matches_scanner_symbol(item, scanner_type=scanner_type, symbol=symbol)
        ][:10],
        "rejected_shadow_outcomes": [
            item
            for item in rejected.get("shadow_market_movement", [])
            if _matches_scanner_symbol(item, scanner_type=scanner_type, symbol=symbol)
        ][:10],
    }


def _matches_scanner_symbol(
    item: Any,
    *,
    scanner_type: str,
    symbol: str,
) -> bool:
    if not isinstance(item, dict):
        return False
    return (
        str(item.get("scanner_type") or "unknown") == scanner_type
        and str(item.get("symbol") or "").upper() == symbol.upper()
    )


def _suggestions_for_assessment(
    trade_case: TradeCase,
    assessment: dict[str, Any],
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    outcome = assessment.get("outcome")
    scanner_type = assessment.get("scanner_type")
    symbol = assessment.get("underlying_symbol") or assessment.get("symbol")

    if outcome == "loss":
        suggestions.append(
            {
                "suggestion_type": "review_strategy_risk_controls",
                "description": (
                    f"Review {scanner_type} risk controls for {symbol}: this closed "
                    "trade lost money. Compare signal features, exit timing, spread, "
                    "and notional sizing before changing config."
                ),
                "proposed_config_patch": {},
            }
        )

    snapshot_context = assessment.get("snapshot_context", {})
    if isinstance(snapshot_context, dict) and snapshot_context.get("diagnostic_reasons"):
        suggestions.append(
            {
                "suggestion_type": "review_option_selection_filters",
                "description": (
                    f"Review option-selection filters for {scanner_type} {symbol}; "
                    "recent diagnostics show rejected candidates. Consider liquidity, "
                    "spread, moneyness, and notional limits with human approval."
                ),
                "proposed_config_patch": {},
            }
        )

    if (
        isinstance(snapshot_context, dict)
        and snapshot_context.get("rejected_shadow_outcomes")
    ):
        suggestions.append(
            {
                "suggestion_type": "review_rejected_signal_outcomes",
                "description": (
                    f"Review rejected-signal shadow outcomes for {scanner_type} {symbol}; "
                    "some rejected signals have later market movement evidence."
                ),
                "proposed_config_patch": {},
            }
        )

    if not suggestions:
        suggestions.append(
            {
                "suggestion_type": "monitor_strategy",
                "description": (
                    f"Monitor {scanner_type} {symbol}; no immediate config patch is "
                    "recommended from this single trade case."
                ),
                "proposed_config_patch": {},
            }
        )

    # Deduplicate suggestion types while preserving order.
    seen: set[str] = set()
    unique = []
    for suggestion in suggestions:
        suggestion_type = suggestion["suggestion_type"]
        if suggestion_type in seen:
            continue
        seen.add(suggestion_type)
        unique.append(suggestion)
    return unique
