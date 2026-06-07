from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest
import uuid

from app.db.models import (
    AiTradeReview,
    JobRun,
    ReviewSnapshot,
    StrategyChangeSuggestion,
    TradeCase,
)
from app.services.ai_trade_review import (
    _assessment_for_trade_case,
    _suggestions_for_assessment,
    write_ai_trade_reviews,
)


class FakeAiReviewSession:
    def __init__(
        self,
        *,
        snapshot: ReviewSnapshot,
        trade_case: TradeCase,
        pending_suggestions: list[StrategyChangeSuggestion] | None = None,
    ) -> None:
        self.scalar_results = [snapshot, None]
        self.scalars_results = [[trade_case], pending_suggestions or []]
        self.added: list[object] = []
        self.commit_count = 0
        self.flush_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flush_count += 1
        for obj in self.added:
            if hasattr(obj, "id") and getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    def scalar(self, _: object) -> object | None:
        if not self.scalar_results:
            return None
        return self.scalar_results.pop(0)

    def scalars(self, _: object) -> list[object]:
        if not self.scalars_results:
            return []
        return self.scalars_results.pop(0)

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        pass

    def refresh(self, obj: object) -> None:
        if hasattr(obj, "id") and getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class AiTradeReviewTests(unittest.TestCase):
    def test_writer_creates_review_and_pending_suggestions(self) -> None:
        trade_case = _trade_case(realized_pl=Decimal("-42"), realized_pl_percent=Decimal("-21"))
        snapshot = _snapshot()
        db = FakeAiReviewSession(snapshot=snapshot, trade_case=trade_case)

        result = write_ai_trade_reviews(db, limit=10)

        self.assertEqual(result.trade_cases_seen, 1)
        self.assertEqual(result.reviews_created, 1)
        self.assertEqual(result.reviews_skipped, 0)
        self.assertEqual(result.suggestions_created, 3)
        self.assertEqual(result.errors, [])
        self.assertEqual(db.commit_count, 1)
        reviews = [item for item in db.added if isinstance(item, AiTradeReview)]
        suggestions = [
            item for item in db.added if isinstance(item, StrategyChangeSuggestion)
        ]
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].review_status, "generated")
        self.assertEqual(reviews[0].assessment["outcome"], "loss")
        self.assertEqual(len(suggestions), 3)
        self.assertTrue(all(item.status == "pending" for item in suggestions))
        self.assertTrue(
            all(item.proposed_config_patch == {} for item in suggestions)
        )
        job_runs = [item for item in db.added if isinstance(item, JobRun)]
        self.assertEqual(job_runs[0].status, "succeeded")

    def test_writer_skips_existing_pending_suggestion_groups(self) -> None:
        trade_case = _trade_case(realized_pl=Decimal("-42"), realized_pl_percent=Decimal("-21"))
        snapshot = _snapshot()
        review = AiTradeReview(
            id=uuid.uuid4(),
            trade_case_id=uuid.uuid4(),
            review_model="local-test",
            review_status="generated",
            assessment={
                "scanner_type": "moving_average",
                "underlying_symbol": "SPY",
            },
            raw_response={},
        )
        pending = StrategyChangeSuggestion(
            id=uuid.uuid4(),
            ai_trade_review_id=review.id,
            strategy_id=trade_case.strategy_id,
            suggestion_type="review_strategy_risk_controls",
            description="Existing pending duplicate",
            proposed_config_patch={},
            status="pending",
        )
        pending.ai_trade_review = review
        db = FakeAiReviewSession(
            snapshot=snapshot,
            trade_case=trade_case,
            pending_suggestions=[pending],
        )

        result = write_ai_trade_reviews(db, limit=10)

        self.assertEqual(result.reviews_created, 1)
        self.assertEqual(result.suggestions_created, 2)
        suggestions = [
            item for item in db.added if isinstance(item, StrategyChangeSuggestion)
        ]
        self.assertEqual(
            [item.suggestion_type for item in suggestions],
            [
                "review_option_selection_filters",
                "review_rejected_signal_outcomes",
            ],
        )

    def test_assessment_marks_recommendations_as_human_review_only(self) -> None:
        assessment = _assessment_for_trade_case(
            _trade_case(realized_pl=Decimal("12"), realized_pl_percent=Decimal("6")),
            latest_snapshot=_snapshot(),
            review_model="local-test",
        )

        self.assertEqual(assessment["outcome"], "win")
        self.assertIn("must not be applied automatically", assessment["recommendation_boundary"])

    def test_assessment_parses_occ_contract_type_from_option_symbol(self) -> None:
        assessment = _assessment_for_trade_case(
            _trade_case(),
            latest_snapshot=_snapshot(),
            review_model="local-test",
        )

        self.assertEqual(assessment["entry_option"]["contract_type"], "call")
        self.assertEqual(assessment["entry_option"]["strike"], "500")

    def test_suggestions_default_to_monitoring_for_single_clean_win(self) -> None:
        assessment = {
            "outcome": "win",
            "scanner_type": "moving_average",
            "symbol": "SPY",
            "underlying_symbol": "SPY",
            "snapshot_context": {
                "diagnostic_reasons": {},
                "rejected_shadow_outcomes": [],
            },
        }

        suggestions = _suggestions_for_assessment(_trade_case(), assessment)

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["suggestion_type"], "monitor_strategy")
        self.assertEqual(suggestions[0]["proposed_config_patch"], {})


def _trade_case(
    *,
    realized_pl: Decimal = Decimal("10"),
    realized_pl_percent: Decimal = Decimal("5"),
) -> TradeCase:
    now = datetime.now(timezone.utc)
    return TradeCase(
        id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        entry_order_intent_id=uuid.uuid4(),
        entry_fill_id=uuid.uuid4(),
        exit_fill_id=uuid.uuid4(),
        symbol="SPY260501C00500000",
        underlying_symbol="SPY",
        quantity=Decimal("1"),
        entry_price=Decimal("1.00"),
        entry_time=now,
        exit_price=Decimal("1.10"),
        exit_time=now,
        realized_pl=realized_pl,
        realized_pl_percent=realized_pl_percent,
        is_open=False,
        context={
            "entry": {
                "signal": {
                    "market_context": {"strategy_type": "moving_average"},
                },
            },
        },
    )


def _snapshot() -> ReviewSnapshot:
    now = datetime.now(timezone.utc)
    return ReviewSnapshot(
        id=uuid.uuid4(),
        review_date=date(2026, 5, 8),
        review_type="post_market",
        status="completed",
        generated_at=now,
        diagnostics={"summary": {"reason_counts": {"wide_spread": 2}}},
        rejected_outcomes={
            "trade_comparison": [
                {
                    "scanner_type": "moving_average",
                    "symbol": "SPY",
                    "rejected_signals": 1,
                }
            ],
            "shadow_market_movement": [
                {
                    "scanner_type": "moving_average",
                    "symbol": "SPY",
                    "directional_outcome": "would_have_helped",
                }
            ],
        },
    )


if __name__ == "__main__":
    unittest.main()
