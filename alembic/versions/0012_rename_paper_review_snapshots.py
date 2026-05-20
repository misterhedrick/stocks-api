"""Expose review_snapshots for new code regardless of DB state.

Revision ID: 0012_rename_paper_review_snapshots
Revises: 0011_strategy_tuning_decisions
Create Date: 2026-05-20

Handles three possible DB states after the failed rename attempts:
  1. paper_review_snapshots exists, review_snapshots does not
     → create view review_snapshots pointing to paper_review_snapshots
  2. review_snapshots already exists as a table (rename committed in a
     prior attempt despite exit-3 crash after commit)
     → no-op; model already maps to the table
  3. review_snapshots already exists as a view
     → no-op; idempotent re-run
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0012_rename_paper_review_snapshots"
down_revision: str | Sequence[str] | None = "0011_strategy_tuning_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            -- Already done (table renamed or view created by a prior attempt)
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name   = 'review_snapshots'
            ) THEN
                RETURN;
            END IF;

            -- Normal path: create view over the original table
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name   = 'paper_review_snapshots'
            ) THEN
                CREATE VIEW review_snapshots
                    AS SELECT * FROM paper_review_snapshots;
            END IF;
        END $$
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.views
                WHERE table_schema = 'public'
                  AND table_name   = 'review_snapshots'
            ) THEN
                DROP VIEW review_snapshots;
            END IF;
        END $$
    """))
