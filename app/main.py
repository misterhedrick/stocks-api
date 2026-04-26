import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.automation import router as automation_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.order_intents import router as order_intents_router
from app.api.routes.options import router as options_router
from app.api.routes.signals import router as signals_router
from app.api.routes.strategies import router as strategies_router
from app.core.config import settings
from app.db.migrations import upgrade_database_to_head

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.should_auto_migrate_on_startup:
        logger.info("Auto-migrating database during startup")
        upgrade_database_to_head()

    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": f"{settings.app_name} is running"}


@app.get("/health")
def root_health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(health_router, prefix=settings.api_v1_prefix)
app.include_router(automation_router, prefix=settings.api_v1_prefix)
app.include_router(jobs_router, prefix=settings.api_v1_prefix)
app.include_router(order_intents_router, prefix=settings.api_v1_prefix)
app.include_router(options_router, prefix=settings.api_v1_prefix)
app.include_router(signals_router, prefix=settings.api_v1_prefix)
app.include_router(strategies_router, prefix=settings.api_v1_prefix)
