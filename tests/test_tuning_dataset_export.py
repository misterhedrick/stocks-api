from __future__ import annotations

from datetime import date, datetime, timezone
import unittest
import uuid

from app.db.models import ReviewSnapshot
from scripts.export_tuning_dataset import (
    EvidenceThresholds,
    build_tuning_dataset,
    render_dataset,
)


class PaperTuningDatasetExportTests(unittest.TestCase):
    def test_build_tuning_dataset_aggregates_snapshot_candidates(self) -> None:
        snapshots = [
            _snapshot(
                review_date=date(2026, 5, 18),
                priority_score=4,
                preview_rejected=6,
                diagnostics_seen=3,
                no_signal_reasons_seen=5,
                closed_trade_cases=1,
                losses=1,
            ),
            _snapshot(
                review_date=date(2026, 5, 19),
                priority_score=7,
                preview_rejected=5,
                diagnostics_seen=4,
                no_signal_reasons_seen=8,
                closed_trade_cases=0,
                losses=0,
            ),
        ]

        dataset = build_tuning_dataset(
            snapshots,
            thresholds=EvidenceThresholds(
                min_snapshots=2,
                min_closed_trades=5,
                min_preview_rejections=10,
                min_no_signal_reasons=20,
            ),
        )

        self.assertEqual(dataset["snapshot_count"], 2)
        self.assertEqual(dataset["summary"]["ready_rows"], 1)
        row = dataset["aggregate_rows"][0]
        self.assertEqual(row["scanner_type"], "moving_average")
        self.assertEqual(row["snapshots"], 2)
        self.assertEqual(row["priority_score"], 7)
        self.assertEqual(row["preview_rejected"], 11)
        self.assertEqual(row["diagnostics_seen"], 7)
        self.assertEqual(row["closed_trade_cases"], 1)
        self.assertEqual(row["readiness_status"], "ready_for_option_filter_review")
        self.assertEqual(row["top_preview_rejection_reasons"]["wide_spread"], 11)

    def test_render_dataset_can_emit_csv(self) -> None:
        dataset = build_tuning_dataset(
            [_snapshot(review_date=date(2026, 5, 19), preview_rejected=1)],
            thresholds=EvidenceThresholds(min_snapshots=1),
        )

        csv_text = render_dataset(dataset, output_format="csv")

        self.assertIn("row_type,review_date,scanner_type", csv_text)
        self.assertIn("aggregate", csv_text)
        self.assertIn("daily", csv_text)


def _snapshot(
    *,
    review_date: date,
    priority_score: int = 1,
    preview_rejected: int = 0,
    diagnostics_seen: int = 0,
    no_signal_reasons_seen: int = 0,
    closed_trade_cases: int = 0,
    losses: int = 0,
) -> ReviewSnapshot:
    return ReviewSnapshot(
        id=uuid.uuid4(),
        review_date=review_date,
        review_type="post_market",
        status="completed",
        generated_at=datetime.combine(review_date, datetime.min.time(), timezone.utc),
        raw_payload={
            "learning_report": {
                "refinement_candidates": [
                    {
                        "scanner_type": "moving_average",
                        "symbol": "ALL_SYMBOLS",
                        "priority_score": priority_score,
                        "recommended_focus": ["review_option_selection_filters"],
                        "signals": {
                            "seen": 12,
                            "status_counts": {"submitted": 2, "created": 3},
                            "preview_rejected": preview_rejected,
                            "preview_rejection_reasons": {
                                "wide_spread": preview_rejected
                            },
                        },
                        "option_selection": {
                            "diagnostics_seen": diagnostics_seen,
                            "candidate_count": diagnostics_seen * 20,
                            "reason_counts": {"wide_spread": diagnostics_seen},
                        },
                        "trade_cases": {
                            "closed": closed_trade_cases,
                            "open": 1,
                            "wins": 0,
                            "losses": losses,
                            "flats": 0,
                            "total_realized_pl": "-12.5" if losses else "0",
                            "average_return_percent": "-4.2" if losses else "0",
                        },
                        "no_signal": {
                            "reasons_seen": no_signal_reasons_seen,
                            "reasons": {
                                "moving average evaluator produced no signal": no_signal_reasons_seen
                            },
                        },
                    }
                ]
            }
        },
    )


if __name__ == "__main__":
    unittest.main()
