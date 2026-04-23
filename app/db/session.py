from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base


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


class DatabaseSchemaNotReadyError(RuntimeError):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database_connection() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def check_database_schema() -> None:
    existing_tables = set(inspect(engine).get_table_names())
    required_tables = set(Base.metadata.tables)
    missing_tables = sorted(required_tables - existing_tables)

    if missing_tables:
        raise DatabaseSchemaNotReadyError(
            f"Missing required tables: {', '.join(missing_tables)}"
        )
