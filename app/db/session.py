from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def get_connect_args() -> dict[str, int]:
    if settings.sqlalchemy_database_url.startswith("postgresql"):
        return {"connect_timeout": settings.database_connect_timeout_seconds}
    return {}


engine = create_engine(
    settings.sqlalchemy_database_url,
    connect_args=get_connect_args(),
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database_connection() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
