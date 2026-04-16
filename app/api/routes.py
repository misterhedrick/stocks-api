from fastapi import APIRouter, Depends

from app.api.deps import require_admin_bearer_token

api_router = APIRouter()


@api_router.get('/secure/ping', dependencies=[Depends(require_admin_bearer_token)])
def secure_ping() -> dict[str, str]:
    return {'message': 'Authenticated'}
