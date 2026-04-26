from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.session import get_db
from app.schemas.automation import AutomationStatusRead
from app.services.automation_status import get_automation_status

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
