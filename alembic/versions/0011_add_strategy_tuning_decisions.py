"""Add strategy tuning decisions.

Revision ID: 0011_strategy_tuning_decisions
Revises: 0010_deactivate_legacy_scanners
Create Date: 2026-05-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0011_strategy_tuning_decisions"
down_revision: str | Sequence[str] | None = "0010_deactivate_legacy_scanners"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_tuning_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("scanner_type", sa.String(length=80), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("decision_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="approved", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("expected_effect", sa.Text(), nullable=True),
        sa.Column(
            "proposed_config_patch",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evidence_snapshot_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evidence_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "outcome_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_tuning_decisions_strategy_id", "strategy_tuning_decisions", ["strategy_id"], unique=False)
    op.create_index("ix_strategy_tuning_decisions_scanner_type", "strategy_tuning_decisions", ["scanner_type"], unique=False)
    op.create_index("ix_strategy_tuning_decisions_symbol", "strategy_tuning_decisions", ["symbol"], unique=False)
    op.create_index("ix_strategy_tuning_decisions_decision_type", "strategy_tuning_decisions", ["decision_type"], unique=False)
    op.create_index("ix_strategy_tuning_decisions_status", "strategy_tuning_decisions", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_strategy_tuning_decisions_status", table_name="strategy_tuning_decisions")
    op.drop_index("ix_strategy_tuning_decisions_decision_type", table_name="strategy_tuning_decisions")
    op.drop_index("ix_strategy_tuning_decisions_symbol", table_name="strategy_tuning_decisions")
    op.drop_index("ix_strategy_tuning_decisions_scanner_type", table_name="strategy_tuning_decisions")
    op.drop_index("ix_strategy_tuning_decisions_strategy_id", table_name="strategy_tuning_decisions")
    op.drop_table("strategy_tuning_decisions")
