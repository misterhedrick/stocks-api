from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.session import get_db
from app.schemas.automation import (
    AutomationStatusRead,
    LearningReportRead,
    PaperPerformanceRead,
    PositionManagementStatusRead,
    TradeCasesRead,
    TradeLifecycleRead,
)
from app.services.automation_status import get_automation_status
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
