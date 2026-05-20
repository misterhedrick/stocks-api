"""Rename paper_review_snapshots to review_snapshots.

Revision ID: 0012_rename_paper_review_snapshots
Revises: 0011_strategy_tuning_decisions
Create Date: 2026-05-20
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0012_rename_paper_review_snapshots"
down_revision: str | Sequence[str] | None = "0011_strategy_tuning_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("paper_review_snapshots", "review_snapshots")
    op.execute(
        "ALTER TABLE review_snapshots RENAME CONSTRAINT "
        "uq_paper_review_snapshots_date_type TO uq_review_snapshots_date_type"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE review_snapshots RENAME CONSTRAINT "
        "uq_review_snapshots_date_type TO uq_paper_review_snapshots_date_type"
    )
    op.rename_table("review_snapshots", "paper_review_snapshots")
