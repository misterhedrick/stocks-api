"""Deactivate legacy direct scanner strategies.

Revision ID: 0007_deactivate_legacy_scanner_strategies
Revises: 0006_signal_preview_lifecycle
Create Date: 2026-05-08
"""

from collections.abc import Sequence


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
    print("MIGRATION_0007_MARKER: no-op checkpoint reached", flush=True)
    pass


def downgrade() -> None:
    print("MIGRATION_0007_MARKER: downgrade no-op reached", flush=True)
    pass
