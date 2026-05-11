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
    PaperPerformanceRead,
    PositionManagementStatusRead,
    StrategySuggestionReviewUpdate,
    TradeCasesRead,
    TradeLifecycleRead,
)
from app.services.ai_trade_review import (
    get_ai_trade_reviews,
    get_strategy_change_suggestions,
    update_strategy_change_suggestion_review,
)
from app.services.automation_status import get_automation_status
from app.services.daily_paper_review import build_daily_paper_review
from app.services.learning_report import build_learning_report
from app.services.performance_review import get_paper_performance_review
from app.services.paper_review_snapshots import get_paper_review_snapshots
from app.services.position_exits import get_position_management_statuses
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
    response_model=PaperPerformanceRead,
    status_code=status.HTTP_200_OK,
)
def paper_performance_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> PaperPerformanceRead:
    return get_paper_performance_review(db, limit=limit)


@router.get(
    "/daily-paper-review",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def daily_paper_review_route(
    db: Annotated[Session, Depends(get_db)],
    review_date: Annotated[date | None, Query(alias="date")] = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 5000,
) -> dict[str, Any]:
    return build_daily_paper_review(db, review_date=review_date, limit=limit)


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
    "/paper-review-snapshots",
    response_model=list[dict[str, Any]],
    status_code=status.HTTP_200_OK,
)
def paper_review_snapshots_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[dict[str, Any]]:
    return get_paper_review_snapshots(db, limit=limit)


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
