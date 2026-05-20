"""Rename paper_review_snapshots to review_snapshots.

Revision ID: 0012_rename_paper_review_snapshots
Revises: 0011_strategy_tuning_decisions
Create Date: 2026-05-20
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0012_rename_paper_review_snapshots"
down_revision: str | Sequence[str] | None = "0011_strategy_tuning_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_EXISTS_SQL = sa.text(
    "SELECT 1 FROM information_schema.tables "
    "WHERE table_schema = 'public' AND table_name = :tname"
)
_CONSTRAINT_EXISTS_SQL = sa.text(
    "SELECT 1 FROM pg_constraint c "
    "JOIN pg_class t ON c.conrelid = t.oid "
    "WHERE t.relname = :tname AND c.conname = :cname"
)


def upgrade() -> None:
    conn = op.get_bind()

    if conn.execute(_TABLE_EXISTS_SQL, {"tname": "paper_review_snapshots"}).scalar():
        op.rename_table("paper_review_snapshots", "review_snapshots")

    if conn.execute(
        _CONSTRAINT_EXISTS_SQL,
        {"tname": "review_snapshots", "cname": "uq_paper_review_snapshots_date_type"},
    ).scalar():
        op.execute(
            "ALTER TABLE review_snapshots RENAME CONSTRAINT "
            "uq_paper_review_snapshots_date_type TO uq_review_snapshots_date_type"
        )


def downgrade() -> None:
    conn = op.get_bind()

    if conn.execute(
        _CONSTRAINT_EXISTS_SQL,
        {"tname": "review_snapshots", "cname": "uq_review_snapshots_date_type"},
    ).scalar():
        op.execute(
            "ALTER TABLE review_snapshots RENAME CONSTRAINT "
            "uq_review_snapshots_date_type TO uq_paper_review_snapshots_date_type"
        )

    if conn.execute(_TABLE_EXISTS_SQL, {"tname": "review_snapshots"}).scalar():
        op.rename_table("review_snapshots", "paper_review_snapshots")
