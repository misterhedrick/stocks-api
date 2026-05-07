"""Add option selection diagnostics.

Revision ID: 0005_add_option_selection_diagnostics
Revises: 0004_enable_strategy_auto_submit
Create Date: 2026-05-06
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_add_option_selection_diagnostics"
down_revision: str | Sequence[str] | None = "0004_enable_strategy_auto_submit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "option_selection_diagnostics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("signal_id", sa.Uuid(), nullable=True),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("strategy_name", sa.String(length=120), nullable=True),
        sa.Column("underlying_symbol", sa.String(length=16), nullable=False),
        sa.Column("scanner_type", sa.String(length=80), nullable=True),
        sa.Column("preview_profile", sa.String(length=80), nullable=True),
        sa.Column("candidate_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reason_counts", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("market_context", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_option_selection_diagnostics_signal_id", "option_selection_diagnostics", ["signal_id"], unique=False)
    op.create_index("ix_option_selection_diagnostics_strategy_id", "option_selection_diagnostics", ["strategy_id"], unique=False)
    op.create_index("ix_option_selection_diagnostics_strategy_name", "option_selection_diagnostics", ["strategy_name"], unique=False)
    op.create_index("ix_option_selection_diagnostics_underlying_symbol", "option_selection_diagnostics", ["underlying_symbol"], unique=False)
    op.create_index("ix_option_selection_diagnostics_scanner_type", "option_selection_diagnostics", ["scanner_type"], unique=False)
    op.create_index("ix_option_selection_diagnostics_preview_profile", "option_selection_diagnostics", ["preview_profile"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_option_selection_diagnostics_preview_profile", table_name="option_selection_diagnostics")
    op.drop_index("ix_option_selection_diagnostics_scanner_type", table_name="option_selection_diagnostics")
    op.drop_index("ix_option_selection_diagnostics_underlying_symbol", table_name="option_selection_diagnostics")
    op.drop_index("ix_option_selection_diagnostics_strategy_name", table_name="option_selection_diagnostics")
    op.drop_index("ix_option_selection_diagnostics_strategy_id", table_name="option_selection_diagnostics")
    op.drop_index("ix_option_selection_diagnostics_signal_id", table_name="option_selection_diagnostics")
    op.drop_table("option_selection_diagnostics")
