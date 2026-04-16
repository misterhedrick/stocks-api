from fastapi import FastAPI

from app.api.routes import api_router
from app.core.config import settings

app = FastAPI(title=settings.app_name)
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get('/')
def root() -> dict[str, str]:
    return {'message': f'{settings.app_name} is running'}


@app.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}
