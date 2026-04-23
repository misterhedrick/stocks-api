import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.order_intents import router as order_intents_router
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
app.include_router(order_intents_router, prefix=settings.api_v1_prefix)
