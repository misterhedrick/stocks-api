"""Add performance indexes for hot query columns.

Revision ID: 0002_add_performance_indexes
Revises: 0001_initial_trading_tables
Create Date: 2026-05-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_add_performance_indexes"
down_revision: str | Sequence[str] | None = "0001_initial_trading_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # signals — dedupe and pending-preview queries filter/sort on these
    op.create_index("ix_signals_status", "signals", ["status"], unique=False)
    op.create_index("ix_signals_created_at", "signals", ["created_at"], unique=False)

    # order_intents — exit detection and active-order filter hit these
    op.create_index("ix_order_intents_status", "order_intents", ["status"], unique=False)
    op.create_index("ix_order_intents_side", "order_intents", ["side"], unique=False)
    op.create_index("ix_order_intents_created_at", "order_intents", ["created_at"], unique=False)

    # broker_orders — exposure queries and daily order count hit these
    op.create_index("ix_broker_orders_status", "broker_orders", ["status"], unique=False)
    op.create_index("ix_broker_orders_submitted_at", "broker_orders", ["submitted_at"], unique=False)

    # fills — performance review sorts and filters on these
    op.create_index("ix_fills_side", "fills", ["side"], unique=False)
    op.create_index("ix_fills_filled_at", "fills", ["filled_at"], unique=False)

    # position_snapshots — latest-snapshot query orders by this
    op.create_index("ix_position_snapshots_captured_at", "position_snapshots", ["captured_at"], unique=False)

    # job_runs — latest reconciliation lookup filters on both
    op.create_index("ix_job_runs_status", "job_runs", ["status"], unique=False)
    op.create_index("ix_job_runs_finished_at", "job_runs", ["finished_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_job_runs_finished_at", table_name="job_runs")
    op.drop_index("ix_job_runs_status", table_name="job_runs")
    op.drop_index("ix_position_snapshots_captured_at", table_name="position_snapshots")
    op.drop_index("ix_fills_filled_at", table_name="fills")
    op.drop_index("ix_fills_side", table_name="fills")
    op.drop_index("ix_broker_orders_submitted_at", table_name="broker_orders")
    op.drop_index("ix_broker_orders_status", table_name="broker_orders")
    op.drop_index("ix_order_intents_created_at", table_name="order_intents")
    op.drop_index("ix_order_intents_side", table_name="order_intents")
    op.drop_index("ix_order_intents_status", table_name="order_intents")
    op.drop_index("ix_signals_created_at", table_name="signals")
    op.drop_index("ix_signals_status", table_name="signals")
