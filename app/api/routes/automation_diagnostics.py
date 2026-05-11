from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.session import get_db
from app.services.option_selection_diagnostics import (
    build_option_selection_diagnostics_summary,
)
from app.services.phase1_readiness import build_phase1_readiness
from app.services.retention_report import build_retention_report

router = APIRouter(
    prefix="/automation",
    tags=["automation"],
    dependencies=[Depends(require_admin)],
)


@router.get(
    "/phase-1-readiness",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def phase1_readiness_route(
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    return build_phase1_readiness(db)


@router.get(
    "/retention-report",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def retention_report_route(
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    return build_retention_report(db)


@router.get(
    "/option-selection-diagnostics/summary",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def option_selection_diagnostics_summary_route(
    db: Annotated[Session, Depends(get_db)],
    review_date: Annotated[date | None, Query(alias="date")] = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 5000,
) -> dict[str, Any]:
    return build_option_selection_diagnostics_summary(
        db,
        review_date=review_date,
        limit=limit,
    )
