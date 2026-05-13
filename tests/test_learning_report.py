from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest
import uuid

from app.services.learning_report import _no_signal_reasons_from_job_runs, _refinement_candidates


class FakeLearningReportSession:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.execute_results = [
            [
                (
                    SimpleNamespace(
                        id=uuid.uuid4(),
                        created_at=now,
                        market_context={"strategy_type": "moving_average"},
                        underlying_symbol="SPY",
                        symbol="SPY",
                        status="preview_rejected",
                        preview_rejection_reasons={"wide_spread": 2},
                    ),
                    {"scanner": {"type": "moving_average"}},
                )
            ],
            [
                (
                    SimpleNamespace(
                        status="pending",
                        suggestion_type="review_option_selection_filters",
                    ),
                    {
                        "scanner_type": "moving_average",
                        "underlying_symbol": "SPY",
                    },
                )
            ],
        ]
        self.scalars_results = [
            [
                SimpleNamespace(
                    scanner_type="moving_average",
                    preview_profile="moving_average",
                    underlying_symbol="SPY",
                    candidate_count=12,
                    reason_counts={"wide_spread": 3, "low_open_interest": 1},
                )
            ],
            [
                SimpleNamespace(
                    is_open=False,
                    underlying_symbol="SPY",
                    symbol="SPY260501C00500000",
                    realized_pl=Decimal("-25"),
                    realized_pl_percent=Decimal("-12.5"),
                    entry_time=now,
                    context={
                        "entry": {
                            "signal": {
                                "market_context": {
                                    "strategy_type": "moving_average",
                                },
                            },
                        },
                    },
                )
            ],
        ]

    def execute(self, _: object) -> list[object]:
        return self.execute_results.pop(0)

    def scalars(self, _: object) -> list[object]:
        return self.scalars_results.pop(0)


class FakeNoSignalSession:
    def scalars(self, _: object) -> list[object]:
        return [
            SimpleNamespace(
                details={
                    "no_signal_reasons": [
                        "Moving Average.SPY: moving average evaluator produced no signal",
                        "Moving Average.AAPL: scanner does not include symbol AAPL",
                    ]
                }
            )
        ]


class LearningReportTests(unittest.TestCase):
    def test_refinement_candidates_consolidate_strategy_evidence(self) -> None:
        performance = SimpleNamespace(
            no_signal_summary={
                "by_scanner_type": [
                    {
                        "scanner_type": "moving_average",
                        "reasons_seen": 2,
                        "reasons": {"moving average evaluator produced no signal": 2},
                    }
                ]
            },
            rejected_preview_outcomes=[
                {
                    "scanner_type": "moving_average",
                    "symbol": "SPY",
                    "rejected_signals": 1,
                    "later_matched_round_trips": 1,
                    "later_realized_pnl": "30",
                    "later_win_rate_percent": "100",
                }
            ],
        )

        candidates = _refinement_candidates(
            FakeLearningReportSession(),
            performance=performance,
            limit=10,
        )

        spy_candidate = next(
            item
            for item in candidates
            if item["scanner_type"] == "moving_average" and item["symbol"] == "SPY"
        )
        all_symbols_candidate = next(
            item
            for item in candidates
            if item["scanner_type"] == "moving_average"
            and item["symbol"] == "ALL_SYMBOLS"
        )

        self.assertTrue(spy_candidate["human_review_only"])
        self.assertIn("review_strategy_risk_controls", spy_candidate["recommended_focus"])
        self.assertIn("review_option_selection_filters", spy_candidate["recommended_focus"])
        self.assertIn("review_rejected_signal_outcomes", spy_candidate["recommended_focus"])
        self.assertEqual(spy_candidate["signals"]["preview_rejected"], 1)
        self.assertEqual(spy_candidate["option_selection"]["reason_counts"]["wide_spread"], 3)
        self.assertEqual(spy_candidate["trade_cases"]["losses"], 1)
        self.assertEqual(spy_candidate["trade_cases"]["total_realized_pl"], "-25")
        self.assertEqual(spy_candidate["suggestions"]["pending"], 1)
        self.assertEqual(all_symbols_candidate["no_signal"]["reasons_seen"], 2)
        self.assertIn(
            "review_signal_thresholds",
            all_symbols_candidate["recommended_focus"],
        )

    def test_non_trade_reasons_ignore_symbol_routing_misses(self) -> None:
        reasons = _no_signal_reasons_from_job_runs(FakeNoSignalSession(), limit=10)

        self.assertEqual(len(reasons), 1)
        self.assertEqual(
            reasons[0]["reason"],
            "Moving Average.SPY: moving average evaluator produced no signal",
        )


if __name__ == "__main__":
    unittest.main()
