from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
import uuid

from app.db.models import ReviewSnapshot, StrategyTuningDecision
from app.services.strategy_refinement import (
    build_strategy_refinement_summary,
    create_strategy_tuning_decision,
    update_strategy_tuning_decision,
)


class FakeStrategyRefinementSession:
    def __init__(
        self,
        *,
        snapshots: list[ReviewSnapshot] | None = None,
        decisions: list[StrategyTuningDecision] | None = None,
        decision: StrategyTuningDecision | None = None,
    ) -> None:
        self.scalars_results = [snapshots or [], decisions or []]
        self.decision = decision
        self.added: list[object] = []
        self.commit_count = 0
        self.flush_count = 0

    def scalars(self, _: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flush_count += 1
        _hydrate(obj_list=self.added)

    def commit(self) -> None:
        self.commit_count += 1

    def refresh(self, obj: object) -> None:
        _hydrate(obj_list=[obj])

    def get(self, model: type, record_id: uuid.UUID) -> object | None:
        if model is StrategyTuningDecision and self.decision and self.decision.id == record_id:
            return self.decision
        return None


class StrategyRefinementTests(unittest.TestCase):
    def test_summary_trends_candidates_and_marks_option_review_ready(self) -> None:
        now = datetime(2026, 5, 12, 21, 30, tzinfo=timezone.utc)
        decision = _decision(created_at=now - timedelta(hours=12))
        db = FakeStrategyRefinementSession(
            snapshots=[
                _snapshot(
                    review_date=date(2026, 5, 12),
                    generated_at=now,
                    priority_score=18,
                    preview_rejected=8,
                    diagnostics_seen=4,
                    closed_trade_cases=3,
                ),
                _snapshot(
                    review_date=date(2026, 5, 11),
                    generated_at=now - timedelta(days=1),
                    priority_score=10,
                    preview_rejected=5,
                    diagnostics_seen=2,
                    closed_trade_cases=2,
                ),
            ],
            decisions=[decision],
        )

        result = build_strategy_refinement_summary(
            db,
            days=10,
            min_closed_trade_cases=5,
            min_rejected_previews=10,
            min_no_signal_reasons=20,
        )

        candidate = result["candidates"][0]
        self.assertEqual(candidate["scanner_type"], "moving_average")
        self.assertEqual(candidate["symbol"], "ALL_SYMBOLS")
        self.assertEqual(candidate["readiness_status"], "needs_option_filter_review")
        self.assertTrue(candidate["minimum_evidence_met"])
        self.assertEqual(candidate["priority_trend"]["direction"], "worsening")
        self.assertEqual(candidate["evidence"]["closed_trade_cases"], 5)
        self.assertEqual(candidate["evidence"]["preview_rejected"], 13)
        self.assertEqual(candidate["tuning_events"][0]["id"], str(decision.id))
        self.assertEqual(candidate["before_after_windows"][0]["after_snapshot_count"], 1)
        self.assertFalse(result["auto_apply"])

    def test_summary_marks_low_sample_candidates_not_enough_data(self) -> None:
        db = FakeStrategyRefinementSession(
            snapshots=[
                _snapshot(
                    review_date=date(2026, 5, 12),
                    priority_score=4,
                    preview_rejected=1,
                    diagnostics_seen=1,
                    closed_trade_cases=1,
                )
            ],
            decisions=[],
        )

        result = build_strategy_refinement_summary(db)

        self.assertEqual(result["candidates"][0]["readiness_status"], "not_enough_data")
        self.assertFalse(result["candidates"][0]["minimum_evidence_met"])

    def test_summary_watches_signal_only_scanners_with_only_no_signal_evidence(self) -> None:
        db = FakeStrategyRefinementSession(
            snapshots=[
                _snapshot(
                    review_date=date(2026, 5, 12),
                    scanner_type="options_spread_candidate",
                    priority_score=24,
                    recommended_focus=["review_signal_thresholds"],
                    preview_rejected=0,
                    diagnostics_seen=0,
                    closed_trade_cases=0,
                    no_signal_reasons=25,
                )
            ],
            decisions=[],
        )

        result = build_strategy_refinement_summary(
            db,
            min_no_signal_reasons=20,
        )

        self.assertEqual(result["candidates"][0]["readiness_status"], "watch")
        self.assertTrue(result["candidates"][0]["minimum_evidence_met"])

    def test_create_strategy_tuning_decision_records_human_review_only_decision(self) -> None:
        db = FakeStrategyRefinementSession()

        result = create_strategy_tuning_decision(
            db,
            scanner_type="moving_average",
            symbol="spy",
            decision_type="tighten_spread_filter",
            description="Keep SPY moving-average entries away from wide spreads.",
            expected_effect="Fewer rejected previews and lower slippage.",
            proposed_config_patch={"preview": {"max_spread_percent": "25"}},
            evidence_snapshot_ids=["snapshot-1"],
            created_by="admin",
        )

        self.assertEqual(result.decision.symbol, "SPY")
        self.assertEqual(result.decision.status, "approved")
        self.assertEqual(db.commit_count, 1)
        self.assertTrue(any(item.__class__.__name__ == "AuditLog" for item in db.added))

    def test_update_strategy_tuning_decision_rejects_invalid_status(self) -> None:
        decision = _decision()
        db = FakeStrategyRefinementSession(decision=decision)

        with self.assertRaises(ValueError):
            update_strategy_tuning_decision(
                db,
                decision_id=decision.id,
                status="auto_apply",
            )


def _snapshot(
    *,
    review_date: date,
    scanner_type: str = "moving_average",
    generated_at: datetime | None = None,
    priority_score: int,
    recommended_focus: list[str] | None = None,
    preview_rejected: int,
    diagnostics_seen: int,
    closed_trade_cases: int,
    no_signal_reasons: int = 0,
) -> ReviewSnapshot:
    generated = generated_at or datetime(2026, 5, 12, 21, 30, tzinfo=timezone.utc)
    return ReviewSnapshot(
        id=uuid.uuid4(),
        review_date=review_date,
        review_type="post_market",
        status="completed",
        generated_at=generated,
        raw_payload={
            "learning_report": {
                "refinement_candidates": [
                    {
                        "scanner_type": scanner_type,
                        "symbol": "SPY",
                        "priority_score": priority_score,
                        "recommended_focus": recommended_focus
                        or ["review_option_selection_filters"],
                        "signals": {"preview_rejected": preview_rejected},
                        "option_selection": {"diagnostics_seen": diagnostics_seen},
                        "trade_cases": {
                            "closed": closed_trade_cases,
                            "losses": 1,
                            "total_realized_pl": "-10",
                        },
                        "no_signal": {"reasons_seen": no_signal_reasons},
                        "suggestions": {"pending": 1},
                    }
                ]
            }
        },
    )


def _decision(
    *,
    created_at: datetime | None = None,
) -> StrategyTuningDecision:
    now = created_at or datetime(2026, 5, 12, 21, 30, tzinfo=timezone.utc)
    return StrategyTuningDecision(
        id=uuid.uuid4(),
        scanner_type="moving_average",
        symbol="SPY",
        decision_type="tighten_spread_filter",
        status="approved",
        description="Test decision",
        expected_effect="Cleaner fills",
        proposed_config_patch={},
        evidence_snapshot_ids=[],
        evidence_summary={},
        outcome_summary={},
        created_by="admin",
        created_at=now,
        updated_at=now,
    )


def _hydrate(*, obj_list: list[object]) -> None:
    now = datetime.now(timezone.utc)
    for obj in obj_list:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        if getattr(obj, "updated_at", None) is None:
            obj.updated_at = now


if __name__ == "__main__":
    unittest.main()
