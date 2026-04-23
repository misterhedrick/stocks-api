from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError

from app.core.security import require_admin
from app.db.session import check_database_connection

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", dependencies=[Depends(require_admin)])
def readiness_check() -> dict[str, str]:
    try:
        check_database_connection()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not ready",
        ) from exc

    return {"status": "ready", "database": "ok"}
