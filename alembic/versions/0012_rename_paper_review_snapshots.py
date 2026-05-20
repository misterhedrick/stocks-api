"""Expose review_snapshots view over paper_review_snapshots table.

Revision ID: 0012_rename_paper_review_snapshots
Revises: 0011_strategy_tuning_decisions
Create Date: 2026-05-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0012_rename_paper_review_snapshots"
down_revision: str | Sequence[str] | None = "0011_strategy_tuning_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CREATE VIEW requires no locks on the underlying table, so this is safe
    # during a Render zero-downtime deploy where the old instance still holds
    # connections. ALTER TABLE RENAME needs ACCESS EXCLUSIVE and deadlocks in
    # that scenario. The view lets the new code (referencing review_snapshots)
    # work immediately; migration 0013 will drop the view and do the real rename
    # during a maintenance window.
    op.execute(sa.text(
        "CREATE VIEW review_snapshots AS SELECT * FROM paper_review_snapshots"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP VIEW review_snapshots"))
