from fastapi import APIRouter

from app.api.routes.jobs_core import router as core_router
from app.api.routes.jobs_market import router as market_router
from app.api.routes.jobs_maintenance import public_router as maintenance_public_router
from app.api.routes.jobs_maintenance import router as maintenance_router

router = APIRouter()
router.include_router(core_router)
router.include_router(market_router)
router.include_router(maintenance_router)

public_router = APIRouter()
public_router.include_router(maintenance_public_router)
