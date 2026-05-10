from __future__ import annotations

from datetime import date, datetime, timezone
import unittest
import uuid

from app.db.models import PaperReviewSnapshot
from scripts.print_paper_review_snapshot import format_snapshot_report


class PaperReviewSnapshotReportTests(unittest.TestCase):
    def test_format_snapshot_report_prints_review_sections(self) -> None:
        snapshot = PaperReviewSnapshot(
            id=uuid.uuid4(),
            review_date=date(2026, 5, 8),
            review_type="post_market",
            status="completed",
            generated_at=datetime(2026, 5, 8, 21, 30, tzinfo=timezone.utc),
            summary={
                "counts": {"signals": 4, "fills": 2},
                "performance": {
                    "totals": {"realized_pnl": "35", "win_rate_percent": "100"},
                    "by_strategy": [
                        {
                            "strategy_name": "Moving Average",
                            "matched_round_trips": 1,
                            "realized_pnl": "35",
                            "win_rate_percent": "100",
                        }
                    ],
                    "by_symbol": [
                        {
                            "symbol": "SPY",
                            "matched_round_trips": 1,
                            "realized_pnl": "35",
                            "win_rate_percent": "100",
                        }
                    ],
                },
            },
            signals={
                "summary": {
                    "by_scanner_type": [
                        {
                            "scanner_type": "moving_average",
                            "signals_seen": 4,
                            "by_status": {"submitted": 2},
                            "preview_rejected": 1,
                        }
                    ]
                },
                "no_signal_summary": {"top_reasons": {"insufficient candles": 3}},
            },
            diagnostics={"summary": {"reason_counts": {"wide_spread": 2}}},
            rejected_outcomes={
                "trade_comparison": [
                    {
                        "scanner_type": "moving_average",
                        "symbol": "SPY",
                        "rejected_signals": 1,
                        "matched_round_trips": 1,
                    }
                ],
                "shadow_market_movement": [
                    {
                        "scanner_type": "moving_average",
                        "symbol": "SPY",
                        "directional_outcome": "would_have_helped",
                        "underlying_move_percent": "2.0",
                    }
                ],
            },
        )

        report = format_snapshot_report(snapshot, limit=5)

        self.assertIn("Paper Review Snapshot", report)
        self.assertIn("Performance Totals", report)
        self.assertIn("Moving Average", report)
        self.assertIn("insufficient candles: 3", report)
        self.assertIn("wide_spread: 2", report)
        self.assertIn("would_have_helped", report)


if __name__ == "__main__":
    unittest.main()
