"""Add AI review tables: trade_cases, ai_trade_reviews, strategy_change_suggestions.

Revision ID: 0003_add_ai_review_tables
Revises: 0002_add_performance_indexes
Create Date: 2026-05-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_add_ai_review_tables"
down_revision: str | Sequence[str] | None = "0002_add_performance_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trade_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("entry_order_intent_id", sa.Uuid(), nullable=True),
        sa.Column("entry_fill_id", sa.Uuid(), nullable=True),
        sa.Column("exit_fill_id", sa.Uuid(), nullable=True),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("underlying_symbol", sa.String(length=16), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("entry_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("exit_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_pl", sa.Numeric(18, 4), nullable=True),
        sa.Column("realized_pl_percent", sa.Numeric(10, 4), nullable=True),
        sa.Column("is_open", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["entry_fill_id"], ["fills.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["entry_order_intent_id"], ["order_intents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["exit_fill_id"], ["fills.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trade_cases_strategy_id", "trade_cases", ["strategy_id"], unique=False)
    op.create_index("ix_trade_cases_entry_order_intent_id", "trade_cases", ["entry_order_intent_id"], unique=False)
    op.create_index("ix_trade_cases_entry_fill_id", "trade_cases", ["entry_fill_id"], unique=False)
    op.create_index("ix_trade_cases_exit_fill_id", "trade_cases", ["exit_fill_id"], unique=False)
    op.create_index("ix_trade_cases_symbol", "trade_cases", ["symbol"], unique=False)
    op.create_index("ix_trade_cases_underlying_symbol", "trade_cases", ["underlying_symbol"], unique=False)

    op.create_table(
        "ai_trade_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trade_case_id", sa.Uuid(), nullable=False),
        sa.Column("review_model", sa.String(length=120), nullable=False),
        sa.Column("review_status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("assessment", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("raw_response", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["trade_case_id"], ["trade_cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_trade_reviews_trade_case_id", "ai_trade_reviews", ["trade_case_id"], unique=False)

    op.create_table(
        "strategy_change_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ai_trade_review_id", sa.Uuid(), nullable=True),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("suggestion_type", sa.String(length=60), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("proposed_config_patch", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["ai_trade_review_id"], ["ai_trade_reviews.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_change_suggestions_ai_trade_review_id", "strategy_change_suggestions", ["ai_trade_review_id"], unique=False)
    op.create_index("ix_strategy_change_suggestions_strategy_id", "strategy_change_suggestions", ["strategy_id"], unique=False)
    op.create_index("ix_strategy_change_suggestions_suggestion_type", "strategy_change_suggestions", ["suggestion_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_strategy_change_suggestions_suggestion_type", table_name="strategy_change_suggestions")
    op.drop_index("ix_strategy_change_suggestions_strategy_id", table_name="strategy_change_suggestions")
    op.drop_index("ix_strategy_change_suggestions_ai_trade_review_id", table_name="strategy_change_suggestions")
    op.drop_table("strategy_change_suggestions")

    op.drop_index("ix_ai_trade_reviews_trade_case_id", table_name="ai_trade_reviews")
    op.drop_table("ai_trade_reviews")

    op.drop_index("ix_trade_cases_underlying_symbol", table_name="trade_cases")
    op.drop_index("ix_trade_cases_symbol", table_name="trade_cases")
    op.drop_index("ix_trade_cases_exit_fill_id", table_name="trade_cases")
    op.drop_index("ix_trade_cases_entry_fill_id", table_name="trade_cases")
    op.drop_index("ix_trade_cases_entry_order_intent_id", table_name="trade_cases")
    op.drop_index("ix_trade_cases_strategy_id", table_name="trade_cases")
    op.drop_table("trade_cases")
