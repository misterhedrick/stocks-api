"""Enable auto-submit on all strategies that have scanner.submit.enabled = false.

Revision ID: 0004_enable_strategy_auto_submit
Revises: 0003_add_ai_review_tables
Create Date: 2026-05-04
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_enable_strategy_auto_submit"
down_revision: str | Sequence[str] | None = "0003_add_ai_review_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE strategies
            SET config = jsonb_set(config, '{scanner,submit,enabled}', 'true'::jsonb)
            WHERE config -> 'scanner' -> 'submit' ->> 'enabled' = 'false'
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE strategies
            SET config = jsonb_set(config, '{scanner,submit,enabled}', 'false'::jsonb)
            WHERE config -> 'scanner' -> 'submit' ->> 'enabled' = 'true'
            """
        )
    )
