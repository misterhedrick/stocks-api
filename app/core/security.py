from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

bearer_scheme = HTTPBearer(auto_error=False)


def require_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise_unauthorized()

    if not settings.admin_api_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ADMIN_API_TOKEN is not configured",
        )

    if not compare_digest(credentials.credentials, settings.admin_api_token):
        raise_unauthorized()


def raise_unauthorized() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )
