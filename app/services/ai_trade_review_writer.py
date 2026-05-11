from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AiTradeReview, JobRun, StrategyChangeSuggestion, TradeCase
from app.services.ai_trade_review_assessment import (
    _assessment_for_trade_case,
    _latest_snapshot,
    _suggestions_for_assessment,
)
from app.services.ai_trade_review_stats import _trade_case_group_stats
from app.services.ai_trade_review_types import AiTradeReviewWriterResult, LOCAL_REVIEW_MODEL
from app.services.audit_logs import record_audit_log


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
        group_stats = _trade_case_group_stats(trade_cases)
        suggested_groups: set[tuple[str, str, str]] = set()

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
                    group_stats=group_stats,
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
                    group_key = (
                        str(suggestion_payload["suggestion_type"]),
                        str(assessment.get("scanner_type") or "unknown"),
                        str(
                            assessment.get("underlying_symbol")
                            or assessment.get("symbol")
                            or "unknown"
                        ).upper(),
                    )
                    if group_key in suggested_groups:
                        continue
                    suggested_groups.add(group_key)
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
            "suggestion_grouping": "suggestions are deduplicated by type, scanner, and symbol per run",
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
