"""Add strategy suggestion review metadata.

Revision ID: 0009_suggestion_review_metadata
Revises: 0008_paper_review_snapshots
Create Date: 2026-05-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0009_suggestion_review_metadata"
down_revision: str | Sequence[str] | None = "0008_paper_review_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "strategy_change_suggestions",
        sa.Column("review_notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "strategy_change_suggestions",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "strategy_change_suggestions",
        sa.Column("reviewed_by", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("strategy_change_suggestions", "reviewed_by")
    op.drop_column("strategy_change_suggestions", "reviewed_at")
    op.drop_column("strategy_change_suggestions", "review_notes")
