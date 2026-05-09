"""Deactivate legacy direct scanner strategies.

Revision ID: 0007_deactivate_legacy_scanner_strategies
Revises: 0006_signal_preview_lifecycle
Create Date: 2026-05-08
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0007_deactivate_legacy_scanner_strategies"
down_revision: str | Sequence[str] | None = "0006_signal_preview_lifecycle"
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
        DO $$
        BEGIN
            IF to_regclass('public.strategies') IS NOT NULL
               AND EXISTS (
                   SELECT 1
                   FROM information_schema.columns
                   WHERE table_schema = 'public'
                     AND table_name = 'strategies'
                     AND column_name = 'is_active'
               )
               AND EXISTS (
                   SELECT 1
                   FROM information_schema.columns
                   WHERE table_schema = 'public'
                     AND table_name = 'strategies'
                     AND column_name = 'config'
                     AND data_type IN ('json', 'jsonb')
               )
            THEN
                UPDATE strategies
                SET is_active = false
                WHERE config::jsonb->'scanner'->>'type' IN (
                    'price_threshold',
                    'percent_change',
                    'trend_confirmation'
                );
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Intentionally do not reactivate strategies automatically. Deactivation is
    # safer than deletion and preserves historical signals, orders, fills, and
    # trade cases while preventing removed scanner paths from running.
    pass
