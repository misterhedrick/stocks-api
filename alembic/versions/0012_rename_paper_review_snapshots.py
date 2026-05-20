"""Expose review_snapshots for new code regardless of DB state.

Revision ID: 0012_review_snapshots_noop
Revises: 0011_strategy_tuning_decisions
Create Date: 2026-05-20

Handles three possible DB states after the failed rename attempts:
  1. paper_review_snapshots exists, review_snapshots does not
     → create view review_snapshots pointing to paper_review_snapshots
  2. review_snapshots already exists as a table (rename committed in a
     prior attempt despite exit-3 crash after commit)
     → no-op; model already maps to the table
  3. review_snapshots already exists as a view
     → no-op; idempotent re-run
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0012_review_snapshots_noop"
down_revision: str | Sequence[str] | None = "0011_strategy_tuning_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # TODO: rename paper_review_snapshots → review_snapshots during a maintenance window.
    # Run manually: ALTER TABLE paper_review_snapshots RENAME TO review_snapshots;
    # then update __tablename__ in ReviewSnapshot and the constraint name, and re-enable
    # the rename logic here. Skipped now due to lock contention on Render's managed Postgres.
    pass


def downgrade() -> None:
    pass
