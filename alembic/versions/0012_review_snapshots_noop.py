"""No-op bridge after failed paper_review_snapshots rename attempt.

Revision ID: 0012_review_snapshots_noop
Revises: 0011_strategy_tuning_decisions
Create Date: 2026-05-20

This revision intentionally performs no schema work. It exists because production
was stamped past the failed 0012 attempt before the real rename was reintroduced
as revision 0013_review_snapshots_rename.
"""

from collections.abc import Sequence


revision: str = "0012_review_snapshots_noop"
down_revision: str | Sequence[str] | None = "0011_strategy_tuning_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
