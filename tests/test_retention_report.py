from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from app.services.retention_report import build_retention_report


class FakeScalarResult:
    def __init__(self, values: list) -> None:
        self._values = values

    def __iter__(self):
        return iter(self._values)


class FakeRetentionReportSession:
    def __init__(self, counts: list[list]) -> None:
        self._queue = list(counts)

    def scalars(self, _) -> FakeScalarResult:
        return FakeScalarResult(self._queue.pop(0))


def _make_db(
    *,
    job_run_ids: list | None = None,
    audit_log_ids: list | None = None,
    option_diag_ids: list | None = None,
    signal_ids: list | None = None,
) -> FakeRetentionReportSession:
    return FakeRetentionReportSession([
        job_run_ids or [],
        audit_log_ids or [],
        option_diag_ids or [],
        signal_ids or [],
    ])


class RetentionReportTests(unittest.TestCase):
    def test_build_retention_report_returns_required_keys(self) -> None:
        db = _make_db()
        result = build_retention_report(db)

        self.assertIn("generated_at", result)
        self.assertIn("mode", result)
        self.assertIn("cutoffs", result)
        self.assertIn("eligible_counts", result)
        self.assertIn("always_preserved", result)

    def test_build_retention_report_mode_is_report_only(self) -> None:
        db = _make_db()
        result = build_retention_report(db)
        self.assertEqual(result["mode"], "report_only")

    def test_build_retention_report_counts_match_db_results(self) -> None:
        job_ids = [uuid.uuid4(), uuid.uuid4()]
        audit_ids = [uuid.uuid4()]
        diag_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        sig_ids = [uuid.uuid4()]

        db = _make_db(
            job_run_ids=job_ids,
            audit_log_ids=audit_ids,
            option_diag_ids=diag_ids,
            signal_ids=sig_ids,
        )
        result = build_retention_report(db)

        counts = result["eligible_counts"]
        self.assertEqual(counts["successful_job_runs"], 2)
        self.assertEqual(counts["audit_logs"], 1)
        self.assertEqual(counts["option_selection_diagnostics"], 3)
        self.assertEqual(counts["rejected_signals_without_order_intents"], 1)

    def test_build_retention_report_empty_db_returns_zero_counts(self) -> None:
        db = _make_db()
        result = build_retention_report(db)

        counts = result["eligible_counts"]
        self.assertEqual(counts["successful_job_runs"], 0)
        self.assertEqual(counts["audit_logs"], 0)
        self.assertEqual(counts["option_selection_diagnostics"], 0)
        self.assertEqual(counts["rejected_signals_without_order_intents"], 0)

    def test_build_retention_report_now_param_sets_generated_at(self) -> None:
        now = datetime(2026, 5, 11, 8, 30, 0, tzinfo=timezone.utc)
        db = _make_db()
        result = build_retention_report(db, now=now)
        self.assertEqual(result["generated_at"], now.isoformat())

    def test_build_retention_report_cutoffs_use_defaults_when_settings_absent(self) -> None:
        now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        db = _make_db()

        # Settings don't define retention_*_days — service falls back to built-in defaults
        # (30d for job runs / rejected signals, 60d for audit logs / option diagnostics).
        result = build_retention_report(db, now=now)

        cutoffs = result["cutoffs"]
        self.assertEqual(cutoffs["job_runs_before"], (now - timedelta(days=30)).isoformat())
        self.assertEqual(cutoffs["audit_logs_before"], (now - timedelta(days=60)).isoformat())
        self.assertEqual(cutoffs["rejected_signals_before"], (now - timedelta(days=30)).isoformat())
        self.assertEqual(cutoffs["option_diagnostics_before"], (now - timedelta(days=60)).isoformat())

    def test_build_retention_report_always_preserved_includes_critical_tables(self) -> None:
        db = _make_db()
        result = build_retention_report(db)

        preserved = result["always_preserved"]
        for expected in ("broker_orders", "fills", "trade_cases", "ai_trade_reviews"):
            self.assertIn(expected, preserved)

    def test_build_retention_report_naive_now_treated_as_utc(self) -> None:
        naive_now = datetime(2026, 5, 11, 0, 0, 0)
        db = _make_db()
        result = build_retention_report(db, now=naive_now)
        self.assertIn("2026-05-11", result["generated_at"])


if __name__ == "__main__":
    unittest.main()
