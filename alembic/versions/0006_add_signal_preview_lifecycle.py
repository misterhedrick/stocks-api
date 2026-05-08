"""Add signal preview lifecycle fields.

Revision ID: 0006_signal_preview_lifecycle
Revises: 0005_option_diagnostics
Create Date: 2026-05-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006_signal_preview_lifecycle"
down_revision: str | Sequence[str] | None = "0005_option_diagnostics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signals",
        sa.Column("preview_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "signals",
        sa.Column("last_previewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("signals", sa.Column("last_preview_error", sa.Text(), nullable=True))
    op.add_column(
        "signals",
        sa.Column("last_preview_error_code", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "signals",
        sa.Column(
            "preview_rejection_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("signals", "preview_rejection_reasons")
    op.drop_column("signals", "last_preview_error_code")
    op.drop_column("signals", "last_preview_error")
    op.drop_column("signals", "last_previewed_at")
    op.drop_column("signals", "preview_attempts")
