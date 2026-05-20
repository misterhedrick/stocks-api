"""Rename paper_review_snapshots to review_snapshots.

Revision ID: 0012_rename_paper_review_snapshots
Revises: 0011_strategy_tuning_decisions
Create Date: 2026-05-20

NOTE: This migration is intentionally a no-op. The table rename was attempted
but could not be applied safely due to lock contention in the managed Postgres
environment. The table remains as paper_review_snapshots; the SQLAlchemy model
class was renamed to ReviewSnapshot in Python while keeping the original table
name. A future maintenance-window migration can perform the rename when locks
can be cleared safely.
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0012_rename_paper_review_snapshots"
down_revision: str | Sequence[str] | None = "0011_strategy_tuning_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
