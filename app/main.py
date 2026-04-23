from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.order_intents import router as order_intents_router
from app.core.config import settings

app = FastAPI(title=settings.app_name)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": f"{settings.app_name} is running"}


@app.get("/health")
def root_health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(health_router, prefix=settings.api_v1_prefix)
app.include_router(order_intents_router, prefix=settings.api_v1_prefix)
