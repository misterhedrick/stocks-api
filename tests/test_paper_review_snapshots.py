from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from app.db.models import PaperReviewSnapshot
from app.services.paper_review_snapshots import (
    _rejected_signal_shadow_outcomes,
    create_or_update_post_market_paper_review_snapshot,
    prune_old_paper_review_snapshots,
)


class FakeSnapshotSession:
    def __init__(self, *, existing: PaperReviewSnapshot | None = None) -> None:
        self.execute_results = [[], [], [], [], [], [], [], []]
        self.scalar_results = [existing]
        self.scalars_results = [[], []]
        self.added: list[object] = []
        self.deleted: list[object] = []
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

    def delete(self, obj: object) -> None:
        self.deleted.append(obj)

    def commit(self) -> None:
        self.commit_count += 1

    def expunge(self, obj: object) -> None:
        pass

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class PaperReviewSnapshotTests(unittest.TestCase):
    def test_create_post_market_snapshot_persists_empty_daily_payload(self) -> None:
        db = FakeSnapshotSession()
        generated_at = datetime(2026, 5, 8, 21, 30, tzinfo=timezone.utc)

        with patch(
            "app.services.paper_review_snapshots.build_learning_report",
            return_value=_learning_report(),
        ):
            result = create_or_update_post_market_paper_review_snapshot(
                db,
                generated_at=generated_at,
            )

        self.assertTrue(result.created)
        self.assertEqual(result.review_date, date(2026, 5, 8))
        self.assertEqual(result.signal_count, 0)
        self.assertEqual(result.rejected_shadow_outcome_count, 0)
        self.assertEqual(result.refinement_candidate_count, 1)
        self.assertEqual(db.commit_count, 1)
        snapshot = db.added[-1]
        self.assertIsInstance(snapshot, PaperReviewSnapshot)
        self.assertEqual(snapshot.review_type, "post_market")
        self.assertEqual(snapshot.raw_payload["review_date"], "2026-05-08")
        self.assertEqual(
            snapshot.raw_payload["learning_report"]["refinement_candidates"][0]["symbol"],
            "SPY",
        )

    def test_prune_old_paper_review_snapshots_deletes_before_cutoff(self) -> None:
        old_snapshot = PaperReviewSnapshot(
            id=uuid.uuid4(),
            review_date=date(2026, 4, 1),
            review_type="post_market",
            status="completed",
            generated_at=datetime(2026, 4, 1, 21, 0, tzinfo=timezone.utc),
        )
        db = FakeSnapshotSession()
        db.scalars_results = [[old_snapshot]]

        result = prune_old_paper_review_snapshots(
            db,
            before_date=date(2026, 4, 15),
        )

        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["before_date"], "2026-04-15")
        self.assertEqual(db.deleted, [old_snapshot])
        self.assertEqual(db.commit_count, 1)

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


def _learning_report() -> SimpleNamespace:
    return SimpleNamespace(
        generated_at=datetime(2026, 5, 8, 21, 30, tzinfo=timezone.utc),
        totals={"signals": 1},
        performance={"matched_round_trips": 0},
        signals_by_strategy=[],
        intents_by_strategy=[],
        non_trade_reasons=[],
        refinement_candidates=[
            {
                "scanner_type": "moving_average",
                "symbol": "SPY",
                "priority_score": 4,
                "human_review_only": True,
            }
        ],
        job_failures=[],
    )
