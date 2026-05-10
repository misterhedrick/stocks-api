"""Deactivate legacy scanner strategy rows.

Revision ID: 0010_deactivate_legacy_scanners
Revises: 0009_suggestion_review_metadata
Create Date: 2026-05-10
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0010_deactivate_legacy_scanners"
down_revision: str | Sequence[str] | None = "0009_suggestion_review_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_SCANNER_TYPES = (
    "price_threshold",
    "percent_change",
    "trend_confirmation",
)


def upgrade() -> None:
    op.execute(
        """
        UPDATE strategies
        SET is_active = false,
            updated_at = now()
        WHERE is_active = true
          AND config #>> '{scanner,type}' = ANY (
              ARRAY['price_threshold', 'percent_change', 'trend_confirmation']
          )
        """
    )


def downgrade() -> None:
    pass
