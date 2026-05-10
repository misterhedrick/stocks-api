"""Add paper review snapshots.

Revision ID: 0008_paper_review_snapshots
Revises: 0007_legacy_scanner_noop
Create Date: 2026-05-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0008_paper_review_snapshots"
down_revision: str | Sequence[str] | None = "0007_legacy_scanner_noop"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_review_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("review_date", sa.Date(), nullable=False),
        sa.Column(
            "review_type",
            sa.String(length=40),
            server_default="post_market",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="completed",
            nullable=False,
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "signals",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "previews",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "orders",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "fills",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "diagnostics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "rejected_outcomes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "review_date",
            "review_type",
            name="uq_paper_review_snapshots_date_type",
        ),
    )
    op.create_index(
        "ix_paper_review_snapshots_review_date",
        "paper_review_snapshots",
        ["review_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_paper_review_snapshots_review_date",
        table_name="paper_review_snapshots",
    )
    op.drop_table("paper_review_snapshots")
