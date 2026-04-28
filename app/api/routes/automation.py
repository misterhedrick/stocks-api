from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.session import get_db
from app.schemas.automation import AutomationStatusRead, PositionManagementStatusRead
from app.services.automation_status import get_automation_status
from app.services.position_exits import get_position_management_statuses

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
