"""Rename paper_review_snapshots to review_snapshots.

Revision ID: 0013_review_snapshots_rename
Revises: 0012_review_snapshots_noop
Create Date: 2026-05-20
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0013_review_snapshots_rename"
down_revision: str | Sequence[str] | None = "0012_review_snapshots_noop"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.review_snapshots') IS NULL
               AND to_regclass('public.paper_review_snapshots') IS NOT NULL THEN
                ALTER TABLE paper_review_snapshots RENAME TO review_snapshots;
            END IF;

            IF to_regclass('public.review_snapshots') IS NOT NULL
               AND EXISTS (
                   SELECT 1
                   FROM pg_constraint
                   WHERE conname = 'uq_paper_review_snapshots_date_type'
                     AND conrelid = 'public.review_snapshots'::regclass
               ) THEN
                ALTER TABLE review_snapshots
                RENAME CONSTRAINT uq_paper_review_snapshots_date_type
                TO uq_review_snapshots_date_type;
            END IF;

            IF to_regclass('public.ix_paper_review_snapshots_review_date') IS NOT NULL
               AND to_regclass('public.ix_review_snapshots_review_date') IS NULL THEN
                ALTER INDEX ix_paper_review_snapshots_review_date
                RENAME TO ix_review_snapshots_review_date;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.review_snapshots') IS NOT NULL
               AND EXISTS (
                   SELECT 1
                   FROM pg_constraint
                   WHERE conname = 'uq_review_snapshots_date_type'
                     AND conrelid = 'public.review_snapshots'::regclass
               ) THEN
                ALTER TABLE review_snapshots
                RENAME CONSTRAINT uq_review_snapshots_date_type
                TO uq_paper_review_snapshots_date_type;
            END IF;

            IF to_regclass('public.ix_review_snapshots_review_date') IS NOT NULL
               AND to_regclass('public.ix_paper_review_snapshots_review_date') IS NULL THEN
                ALTER INDEX ix_review_snapshots_review_date
                RENAME TO ix_paper_review_snapshots_review_date;
            END IF;

            IF to_regclass('public.paper_review_snapshots') IS NULL
               AND to_regclass('public.review_snapshots') IS NOT NULL THEN
                ALTER TABLE review_snapshots RENAME TO paper_review_snapshots;
            END IF;
        END $$;
        """
    )
