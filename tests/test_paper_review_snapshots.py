from __future__ import annotations

from datetime import date, datetime, timezone
import unittest
import uuid

from app.db.models import PaperReviewSnapshot
from app.services.paper_review_snapshots import (
    _rejected_signal_shadow_outcomes,
    create_or_update_post_market_paper_review_snapshot,
)


class FakeSnapshotSession:
    def __init__(self, *, existing: PaperReviewSnapshot | None = None) -> None:
        self.execute_results = [[], [], [], [], [], [], [], []]
        self.scalar_results = [existing]
        self.scalars_results = [[], []]
        self.added: list[object] = []
        self.commit_count = 0

    def execute(self, _: object) -> list[object]:
        if not self.execute_results:
            return []
        return self.execute_results.pop(0)

    def scalars(self, _: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)

    def scalar(self, _: object) -> object | None:
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commit_count += 1

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class PaperReviewSnapshotTests(unittest.TestCase):
    def test_create_post_market_snapshot_persists_empty_daily_payload(self) -> None:
        db = FakeSnapshotSession()
        generated_at = datetime(2026, 5, 8, 21, 30, tzinfo=timezone.utc)

        result = create_or_update_post_market_paper_review_snapshot(
            db,
            generated_at=generated_at,
        )

        self.assertTrue(result.created)
        self.assertEqual(result.review_date, date(2026, 5, 8))
        self.assertEqual(result.signal_count, 0)
        self.assertEqual(result.rejected_shadow_outcome_count, 0)
        self.assertEqual(db.commit_count, 1)
        snapshot = db.added[-1]
        self.assertIsInstance(snapshot, PaperReviewSnapshot)
        self.assertEqual(snapshot.review_type, "post_market")
        self.assertEqual(snapshot.raw_payload["review_date"], "2026-05-08")

    def test_rejected_signal_shadow_outcomes_compare_later_market_snapshot(self) -> None:
        rejected_id = str(uuid.uuid4())
        later_id = str(uuid.uuid4())
        signals = [
            {
                "id": rejected_id,
                "created_at": "2026-05-08T15:00:00+00:00",
                "scanner_type": "moving_average",
                "symbol": "SPY",
                "underlying_symbol": "SPY",
                "direction": "bullish",
                "status": "preview_rejected",
                "snapshot_price": "500",
                "preview_rejection_reasons": {"wide_spread": 1},
            },
            {
                "id": later_id,
                "created_at": "2026-05-08T18:00:00+00:00",
                "scanner_type": "moving_average",
                "symbol": "SPY",
                "underlying_symbol": "SPY",
                "direction": "bullish",
                "status": "submitted",
                "snapshot_price": "510",
                "preview_rejection_reasons": {},
            },
        ]

        outcomes = _rejected_signal_shadow_outcomes(signals)

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["signal_id"], rejected_id)
        self.assertEqual(outcomes[0]["later_signal_id"], later_id)
        self.assertEqual(outcomes[0]["underlying_move_percent"], "2")
        self.assertEqual(outcomes[0]["directional_outcome"], "would_have_helped")


if __name__ == "__main__":
    unittest.main()
