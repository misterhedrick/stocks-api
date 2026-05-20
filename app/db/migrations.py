from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.core.config import settings

logger = logging.getLogger(__name__)

NOOP_BRIDGE_CURRENT_REVISION = "0011_strategy_tuning_decisions"
NOOP_BRIDGE_HEAD_REVISION = "0012_rename_paper_review_snapshots"


def get_alembic_config() -> Config:
    project_root = Path(__file__).resolve().parents[2]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.sqlalchemy_database_url)
    return config


def _migration_connect_args() -> dict[str, int]:
    if settings.sqlalchemy_database_url.startswith("postgresql"):
        return {"connect_timeout": settings.database_connect_timeout_seconds}
    return {}


def _get_current_database_revision() -> str | None:
    engine = create_engine(
        settings.sqlalchemy_database_url,
        connect_args=_migration_connect_args(),
        poolclass=NullPool,
    )
    try:
        with engine.connect() as connection:
            return connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
    finally:
        engine.dispose()


def _can_skip_noop_bridge_migration(config: Config) -> bool:
    try:
        current_revision = _get_current_database_revision()
    except SQLAlchemyError:
        logger.exception("Could not read current Alembic revision before startup migration")
        return False

    script_heads = set(ScriptDirectory.from_config(config).get_heads())
    return (
        current_revision == NOOP_BRIDGE_CURRENT_REVISION
        and script_heads == {NOOP_BRIDGE_HEAD_REVISION}
    )


def upgrade_database_to_head() -> None:
    logger.info("Running database migrations to head")
    config = get_alembic_config()
    if _can_skip_noop_bridge_migration(config):
        logger.warning(
            "Skipping Alembic startup migration because database is at %s and only pending "
            "head is no-op bridge revision %s; stamp the database manually after PgBouncer "
            "locks are cleared.",
            NOOP_BRIDGE_CURRENT_REVISION,
            NOOP_BRIDGE_HEAD_REVISION,
        )
        return

    command.upgrade(config, "head")
