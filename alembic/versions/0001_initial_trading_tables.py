"""Create initial trading tables.

Revision ID: 0001_initial_trading_tables
Revises:
Create Date: 2026-04-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_trading_tables"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "job_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_job_runs_job_name"), "job_runs", ["job_name"], unique=False)

    op.create_table(
        "position_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("market_value", sa.Numeric(18, 4), nullable=True),
        sa.Column("cost_basis", sa.Numeric(18, 4), nullable=True),
        sa.Column("unrealized_pl", sa.Numeric(18, 4), nullable=True),
        sa.Column("raw_position", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_position_snapshots_symbol"), "position_snapshots", ["symbol"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("entity_type", sa.String(length=120), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_entity_id"), "audit_logs", ["entity_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_entity_type"), "audit_logs", ["entity_type"], unique=False)
    op.create_index(op.f("ix_audit_logs_event_type"), "audit_logs", ["event_type"], unique=False)

    op.create_table(
        "signals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("underlying_symbol", sa.String(length=16), nullable=True),
        sa.Column("signal_type", sa.String(length=50), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("market_context", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="new", nullable=False),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_signals_strategy_id"), "signals", ["strategy_id"], unique=False)
    op.create_index(op.f("ix_signals_symbol"), "signals", ["symbol"], unique=False)
    op.create_index(op.f("ix_signals_underlying_symbol"), "signals", ["underlying_symbol"], unique=False)

    op.create_table(
        "order_intents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("signal_id", sa.Uuid(), nullable=True),
        sa.Column("underlying_symbol", sa.String(length=16), nullable=False),
        sa.Column("option_symbol", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("order_type", sa.String(length=20), server_default="limit", nullable=False),
        sa.Column("limit_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("time_in_force", sa.String(length=20), server_default="day", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="previewed", nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("preview", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_order_intents_option_symbol"), "order_intents", ["option_symbol"], unique=False)
    op.create_index(op.f("ix_order_intents_signal_id"), "order_intents", ["signal_id"], unique=False)
    op.create_index(op.f("ix_order_intents_strategy_id"), "order_intents", ["strategy_id"], unique=False)
    op.create_index(op.f("ix_order_intents_underlying_symbol"), "order_intents", ["underlying_symbol"], unique=False)

    op.create_table(
        "broker_orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_intent_id", sa.Uuid(), nullable=True),
        sa.Column("alpaca_order_id", sa.String(length=120), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("order_type", sa.String(length=20), nullable=False),
        sa.Column("limit_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_response", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["order_intent_id"], ["order_intents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alpaca_order_id"),
    )
    op.create_index(op.f("ix_broker_orders_order_intent_id"), "broker_orders", ["order_intent_id"], unique=False)
    op.create_index(op.f("ix_broker_orders_symbol"), "broker_orders", ["symbol"], unique=False)

    op.create_table(
        "fills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("broker_order_id", sa.Uuid(), nullable=True),
        sa.Column("alpaca_fill_id", sa.String(length=120), nullable=True),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("price", sa.Numeric(12, 4), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_response", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["broker_order_id"], ["broker_orders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alpaca_fill_id"),
    )
    op.create_index(op.f("ix_fills_broker_order_id"), "fills", ["broker_order_id"], unique=False)
    op.create_index(op.f("ix_fills_symbol"), "fills", ["symbol"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_fills_symbol"), table_name="fills")
    op.drop_index(op.f("ix_fills_broker_order_id"), table_name="fills")
    op.drop_table("fills")
    op.drop_index(op.f("ix_broker_orders_symbol"), table_name="broker_orders")
    op.drop_index(op.f("ix_broker_orders_order_intent_id"), table_name="broker_orders")
    op.drop_table("broker_orders")
    op.drop_index(op.f("ix_order_intents_underlying_symbol"), table_name="order_intents")
    op.drop_index(op.f("ix_order_intents_strategy_id"), table_name="order_intents")
    op.drop_index(op.f("ix_order_intents_signal_id"), table_name="order_intents")
    op.drop_index(op.f("ix_order_intents_option_symbol"), table_name="order_intents")
    op.drop_table("order_intents")
    op.drop_index(op.f("ix_signals_underlying_symbol"), table_name="signals")
    op.drop_index(op.f("ix_signals_symbol"), table_name="signals")
    op.drop_index(op.f("ix_signals_strategy_id"), table_name="signals")
    op.drop_table("signals")
    op.drop_index(op.f("ix_audit_logs_event_type"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_entity_type"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_entity_id"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_position_snapshots_symbol"), table_name="position_snapshots")
    op.drop_table("position_snapshots")
    op.drop_index(op.f("ix_job_runs_job_name"), table_name="job_runs")
    op.drop_table("job_runs")
    op.drop_table("strategies")
