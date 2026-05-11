from __future__ import annotations

from datetime import datetime, timezone

from typing import Any

import uuid

from sqlalchemy import select

from sqlalchemy.orm import Session

from app.db.models import AiTradeReview, StrategyChangeSuggestion

from app.services.audit_logs import record_audit_log

from app.services.ai_trade_review_types import SuggestionReviewResult

def get_ai_trade_reviews(
    db: Session,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    statement = (
        select(AiTradeReview)
        .order_by(AiTradeReview.created_at.desc())
        .limit(limit)
    )
    return [_ai_trade_review_read_item(review) for review in db.scalars(statement)]

def get_strategy_change_suggestions(
    db: Session,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    statement = select(StrategyChangeSuggestion).order_by(
        StrategyChangeSuggestion.created_at.desc()
    )
    if status:
        statement = statement.where(StrategyChangeSuggestion.status == status)
    statement = statement.limit(limit)
    return [
        _strategy_change_suggestion_read_item(suggestion)
        for suggestion in db.scalars(statement)
    ]

def update_strategy_change_suggestion_review(
    db: Session,
    *,
    suggestion_id: uuid.UUID,
    status: str | None = None,
    review_notes: str | None = None,
    reviewed_by: str | None = None,
) -> SuggestionReviewResult:
    suggestion = db.get(StrategyChangeSuggestion, suggestion_id)
    if suggestion is None:
        raise LookupError(f"strategy change suggestion {suggestion_id} was not found")

    if status is not None:
        normalized = status.strip().lower()
        if normalized not in {"pending", "approved", "rejected"}:
            raise ValueError("status must be pending, approved, or rejected")
        suggestion.status = normalized

    if review_notes is not None:
        suggestion.review_notes = review_notes
    if reviewed_by is not None:
        suggestion.reviewed_by = reviewed_by
    if status is not None or review_notes is not None or reviewed_by is not None:
        suggestion.reviewed_at = datetime.now(timezone.utc)

    db.add(suggestion)
    record_audit_log(
        db,
        event_type="strategy_change_suggestions.review_updated",
        entity_type="strategy_change_suggestion",
        entity_id=suggestion.id,
        message="Strategy change suggestion review metadata updated",
        payload={
            "status": suggestion.status,
            "review_notes": suggestion.review_notes,
            "reviewed_by": suggestion.reviewed_by,
            "reviewed_at": suggestion.reviewed_at.isoformat()
            if suggestion.reviewed_at
            else None,
            "auto_apply": False,
        },
    )
    db.commit()
    db.refresh(suggestion)
    return SuggestionReviewResult(suggestion=suggestion)

def _ai_trade_review_read_item(review: AiTradeReview) -> dict[str, Any]:
    return {
        "id": str(review.id),
        "trade_case_id": str(review.trade_case_id),
        "review_model": review.review_model,
        "review_status": review.review_status,
        "assessment": review.assessment,
        "raw_response": review.raw_response,
        "created_at": review.created_at.isoformat(),
    }

def _strategy_change_suggestion_read_item(
    suggestion: StrategyChangeSuggestion,
) -> dict[str, Any]:
    return {
        "id": str(suggestion.id),
        "ai_trade_review_id": str(suggestion.ai_trade_review_id)
        if suggestion.ai_trade_review_id
        else None,
        "strategy_id": str(suggestion.strategy_id) if suggestion.strategy_id else None,
        "suggestion_type": suggestion.suggestion_type,
        "description": suggestion.description,
        "proposed_config_patch": suggestion.proposed_config_patch,
        "status": suggestion.status,
        "review_notes": suggestion.review_notes,
        "reviewed_at": suggestion.reviewed_at.isoformat()
        if suggestion.reviewed_at
        else None,
        "reviewed_by": suggestion.reviewed_by,
        "created_at": suggestion.created_at.isoformat(),
        "auto_apply": False,
    }
