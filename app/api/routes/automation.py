from datetime import date
from typing import Annotated, Any

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.session import get_db
from app.schemas.automation import (
    AutomationStatusRead,
    LearningReportRead,
    PerformanceRead,
    PositionManagementStatusRead,
    StrategySuggestionReviewUpdate,
    StrategyTuningDecisionCreate,
    StrategyTuningDecisionUpdate,
    TradeCasesRead,
    TradeLifecycleRead,
)
from app.services.ai_trade_review import (
    get_ai_trade_reviews,
    get_strategy_change_suggestions,
    update_strategy_change_suggestion_review,
)
from app.services.automation_status import get_automation_status
from app.services.daily_review import build_daily_review
from app.services.learning_report import build_learning_report
from app.services.performance_review import get_performance_review
from app.services.review_snapshots import get_review_snapshots
from app.services.position_exits import get_position_management_statuses
from app.services.strategy_refinement import (
    build_strategy_refinement_summary,
    create_strategy_tuning_decision,
    get_strategy_tuning_decisions,
    update_strategy_tuning_decision,
)
from app.services.trade_lifecycle import get_trade_cases, get_trade_lifecycle

router = APIRouter(
    prefix="/automation",
    tags=["automation"],
    dependencies=[Depends(require_admin)],
)


@router.get(
    "/status",
    response_model=AutomationStatusRead,
    status_code=status.HTTP_200_OK,
)
def automation_status_route(
    db: Annotated[Session, Depends(get_db)],
) -> AutomationStatusRead:
    return get_automation_status(db)


@router.get(
    "/positions",
    response_model=list[PositionManagementStatusRead],
    status_code=status.HTTP_200_OK,
)
def position_management_status_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict]:
    return get_position_management_statuses(db, limit=limit)


@router.get(
    "/performance",
    response_model=PerformanceRead,
    status_code=status.HTTP_200_OK,
)
def performance_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> PerformanceRead:
    return get_performance_review(db, limit=limit)


@router.get(
    "/daily-review",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def daily_review_route(
    db: Annotated[Session, Depends(get_db)],
    review_date: Annotated[date | None, Query(alias="date")] = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 5000,
) -> dict[str, Any]:
    return build_daily_review(db, review_date=review_date, limit=limit)


@router.get(
    "/trade-lifecycle",
    response_model=TradeLifecycleRead,
    status_code=status.HTTP_200_OK,
)
def trade_lifecycle_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TradeLifecycleRead:
    return get_trade_lifecycle(db, limit=limit)


@router.get(
    "/trade-cases",
    response_model=TradeCasesRead,
    status_code=status.HTTP_200_OK,
)
def trade_cases_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> TradeCasesRead:
    return get_trade_cases(db, limit=limit)


@router.get(
    "/learning-report",
    response_model=LearningReportRead,
    status_code=status.HTTP_200_OK,
)
def learning_report_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> LearningReportRead:
    return build_learning_report(db, limit=limit)


@router.get(
    "/review-snapshots",
    response_model=list[dict[str, Any]],
    status_code=status.HTTP_200_OK,
)
def review_snapshots_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[dict[str, Any]]:
    return get_review_snapshots(db, limit=limit)


@router.get(
    "/ai-trade-reviews",
    response_model=list[dict[str, Any]],
    status_code=status.HTTP_200_OK,
)
def ai_trade_reviews_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    return get_ai_trade_reviews(db, limit=limit)


@router.get(
    "/strategy-change-suggestions",
    response_model=list[dict[str, Any]],
    status_code=status.HTTP_200_OK,
)
def strategy_change_suggestions_route(
    db: Annotated[Session, Depends(get_db)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    return get_strategy_change_suggestions(
        db,
        status=status_filter,
        limit=limit,
    )


@router.patch(
    "/strategy-change-suggestions/{suggestion_id}",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def update_strategy_change_suggestion_route(
    suggestion_id: uuid.UUID,
    payload: StrategySuggestionReviewUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        result = update_strategy_change_suggestion_review(
            db,
            suggestion_id=suggestion_id,
            status=payload.status,
            review_notes=payload.review_notes,
            reviewed_by=payload.reviewed_by,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    suggestion = result.suggestion
    return {
        "id": str(suggestion.id),
        "status": suggestion.status,
        "review_notes": suggestion.review_notes,
        "reviewed_at": suggestion.reviewed_at.isoformat()
        if suggestion.reviewed_at
        else None,
        "reviewed_by": suggestion.reviewed_by,
        "auto_apply": False,
    }


@router.get(
    "/strategy-refinement",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def strategy_refinement_route(
    db: Annotated[Session, Depends(get_db)],
    days: Annotated[int, Query(ge=1, le=90)] = 10,
    min_closed_trade_cases: Annotated[int, Query(ge=1, le=100)] = 5,
    min_rejected_previews: Annotated[int, Query(ge=1, le=500)] = 10,
    min_no_signal_reasons: Annotated[int, Query(ge=1, le=1000)] = 20,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict[str, Any]:
    return build_strategy_refinement_summary(
        db,
        days=days,
        min_closed_trade_cases=min_closed_trade_cases,
        min_rejected_previews=min_rejected_previews,
        min_no_signal_reasons=min_no_signal_reasons,
        limit=limit,
    )


@router.get(
    "/strategy-tuning-decisions",
    response_model=list[dict[str, Any]],
    status_code=status.HTTP_200_OK,
)
def strategy_tuning_decisions_route(
    db: Annotated[Session, Depends(get_db)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    scanner_type: Annotated[str | None, Query()] = None,
    symbol: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    return get_strategy_tuning_decisions(
        db,
        status=status_filter,
        scanner_type=scanner_type,
        symbol=symbol,
        limit=limit,
    )


@router.post(
    "/strategy-tuning-decisions",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
)
def create_strategy_tuning_decision_route(
    payload: StrategyTuningDecisionCreate,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        result = create_strategy_tuning_decision(
            db,
            scanner_type=payload.scanner_type,
            symbol=payload.symbol,
            decision_type=payload.decision_type,
            description=payload.description,
            expected_effect=payload.expected_effect,
            proposed_config_patch=payload.proposed_config_patch,
            evidence_snapshot_ids=payload.evidence_snapshot_ids,
            evidence_summary=payload.evidence_summary,
            strategy_id=payload.strategy_id,
            created_by=payload.created_by,
            status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _strategy_tuning_decision_response(result.decision)


@router.patch(
    "/strategy-tuning-decisions/{decision_id}",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def update_strategy_tuning_decision_route(
    decision_id: uuid.UUID,
    payload: StrategyTuningDecisionUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        result = update_strategy_tuning_decision(
            db,
            decision_id=decision_id,
            status=payload.status,
            outcome_summary=payload.outcome_summary,
            expected_effect=payload.expected_effect,
            description=payload.description,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _strategy_tuning_decision_response(result.decision)


def _strategy_tuning_decision_response(decision: object) -> dict[str, Any]:
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
