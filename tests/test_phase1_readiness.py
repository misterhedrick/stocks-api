from __future__ import annotations

import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app.db.models import JobRun, ReviewSnapshot
from app.services.phase1_readiness import build_phase1_readiness


class FakeScalarResult:
    def __init__(self, values: list) -> None:
        self._values = values

    def __iter__(self):
        return iter(self._values)


class FakePhase1Session:
    """Sequences calls in the order build_phase1_readiness makes them:
    scalars() #1 -> strategy IDs
    scalar()  #1-4 -> job runs (market_entry_cycle, market_cycle, market_cycle_exits, market_maintenance)
    scalar()  #5   -> latest ReviewSnapshot
    scalars() #2   -> trade case IDs
    """

    def __init__(
        self,
        *,
        strategy_ids: list | None = None,
        job_runs: list | None = None,
        snapshot: ReviewSnapshot | None = None,
        trade_case_ids: list | None = None,
    ) -> None:
        self._scalars_queue = [
            strategy_ids or [],
            trade_case_ids or [],
        ]
        scalar_sequence = list(job_runs or [None, None, None, None])
        scalar_sequence.append(snapshot)
        self._scalar_queue = scalar_sequence

    def scalars(self, _) -> FakeScalarResult:
        return FakeScalarResult(self._scalars_queue.pop(0))

    def scalar(self, _):
        if self._scalar_queue:
            return self._scalar_queue.pop(0)
        return None


def _build_job_run(job_name: str, *, status: str = "succeeded", started_at: datetime | None = None) -> JobRun:
    now = started_at or datetime.now(timezone.utc)
    return JobRun(
        id=uuid.uuid4(),
        job_name=job_name,
        status=status,
        started_at=now,
        finished_at=now,
        details={},
        error=None,
        created_at=now,
    )


def _build_snapshot(review_date: date = date(2026, 5, 10)) -> ReviewSnapshot:
    now = datetime.now(timezone.utc)
    return ReviewSnapshot(
        id=uuid.uuid4(),
        review_date=review_date,
        review_type="post_market",
        status="completed",
        generated_at=now,
        summary={},
        signals={},
        previews={},
        orders={},
        fills={},
        diagnostics={},
        rejected_outcomes={},
        raw_payload={},
        created_at=now,
        updated_at=now,
    )


def _base_settings() -> dict:
    return dict(
        alpaca_paper=True,
        auto_submit_requires_paper=True,
        trading_automation_enabled=True,
        market_cycle_submit_enabled=True,
        market_cycle_scan_enabled=True,
        market_cycle_preview_enabled=True,
        market_cycle_reconcile_enabled=True,
        market_cycle_exit_enabled=True,
        max_auto_orders_per_cycle=5,
        max_auto_orders_per_day=20,
        max_auto_orders_per_symbol_per_day=2,
        max_open_positions=50,
        max_open_positions_per_symbol=3,
        max_contracts_per_order=1,
        max_estimated_premium_per_order="2500",
    )


def _recent_jobs() -> list:
    return [
        _build_job_run("market_entry_cycle"),
        _build_job_run("market_cycle"),
        _build_job_run("market_cycle_exits"),
        _build_job_run("market_maintenance"),
    ]


class Phase1ReadinessTests(unittest.TestCase):
    def test_build_phase1_readiness_ready_when_all_conditions_met(self) -> None:
        db = FakePhase1Session(
            strategy_ids=[uuid.uuid4(), uuid.uuid4()],
            job_runs=_recent_jobs(),
            snapshot=_build_snapshot(),
            trade_case_ids=[uuid.uuid4(), uuid.uuid4(), uuid.uuid4()],
        )

        with patch.multiple("app.services.phase1_readiness.settings", **_base_settings()):
            result = build_phase1_readiness(db)

        self.assertTrue(result["ready"])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["active_strategy_count"], 2)
        self.assertEqual(result["recent_trade_case_count"], 3)
        self.assertIsNotNone(result["latest_review_snapshot"])

    def test_build_phase1_readiness_blocked_when_no_strategies(self) -> None:
        db = FakePhase1Session(strategy_ids=[])

        with patch.multiple("app.services.phase1_readiness.settings", **_base_settings()):
            result = build_phase1_readiness(db)

        self.assertFalse(result["ready"])
        self.assertIn("no active strategies found", result["blockers"])
        self.assertEqual(result["mode"], "blocked")

    def test_build_phase1_readiness_blocked_when_paper_mode_off(self) -> None:
        db = FakePhase1Session(strategy_ids=[uuid.uuid4()])
        settings = {**_base_settings(), "alpaca_paper": False}

        with patch.multiple("app.services.phase1_readiness.settings", **settings):
            result = build_phase1_readiness(db)

        self.assertFalse(result["ready"])
        self.assertIn("ALPACA_PAPER is false", result["blockers"])
        self.assertEqual(result["mode"], "blocked")

    def test_build_phase1_readiness_blocked_when_scan_disabled(self) -> None:
        db = FakePhase1Session(strategy_ids=[uuid.uuid4()])
        settings = {**_base_settings(), "market_cycle_scan_enabled": False}

        with patch.multiple("app.services.phase1_readiness.settings", **settings):
            result = build_phase1_readiness(db)

        self.assertFalse(result["ready"])
        self.assertIn("MARKET_CYCLE_SCAN_ENABLED is false", result["blockers"])

    def test_build_phase1_readiness_blocked_when_preview_disabled(self) -> None:
        db = FakePhase1Session(strategy_ids=[uuid.uuid4()])
        settings = {**_base_settings(), "market_cycle_preview_enabled": False}

        with patch.multiple("app.services.phase1_readiness.settings", **settings):
            result = build_phase1_readiness(db)

        self.assertFalse(result["ready"])
        self.assertIn("MARKET_CYCLE_PREVIEW_ENABLED is false", result["blockers"])

    def test_build_phase1_readiness_warns_when_no_snapshot(self) -> None:
        db = FakePhase1Session(strategy_ids=[uuid.uuid4()], snapshot=None)

        with patch.multiple("app.services.phase1_readiness.settings", **_base_settings()):
            result = build_phase1_readiness(db)

        self.assertIn("no review snapshot found yet", result["warnings"])
        self.assertIsNone(result["latest_review_snapshot"])

    def test_build_phase1_readiness_warns_for_missing_required_jobs(self) -> None:
        db = FakePhase1Session(
            strategy_ids=[uuid.uuid4()],
            job_runs=[None, None, None, None],
        )

        with patch.multiple("app.services.phase1_readiness.settings", **_base_settings()):
            result = build_phase1_readiness(db)

        warning_text = " ".join(result["warnings"])
        self.assertIn("no recent market_entry_cycle job found", result["warnings"])
        self.assertIn("no recent market_cycle job found", result["warnings"])
        self.assertIn("no recent market_cycle_exits job found", result["warnings"])
        self.assertIn("no recent market_maintenance job found", result["warnings"])
        self.assertIn("no recent", warning_text)

    def test_build_phase1_readiness_warns_for_stale_job(self) -> None:
        stale_time = datetime.now(timezone.utc) - timedelta(days=5)
        jobs = [
            _build_job_run("market_entry_cycle", started_at=stale_time),
            _build_job_run("market_cycle"),
            _build_job_run("market_cycle_exits"),
            _build_job_run("market_maintenance"),
        ]
        db = FakePhase1Session(strategy_ids=[uuid.uuid4()], job_runs=jobs)

        with patch.multiple("app.services.phase1_readiness.settings", **_base_settings()):
            result = build_phase1_readiness(db)

        self.assertTrue(
            any("market_entry_cycle" in w and "older than 3 days" in w for w in result["warnings"])
        )

    def test_build_phase1_readiness_warns_for_failed_job(self) -> None:
        jobs = [
            _build_job_run("market_entry_cycle", status="failed"),
            _build_job_run("market_cycle"),
            _build_job_run("market_cycle_exits"),
            _build_job_run("market_maintenance"),
        ]
        db = FakePhase1Session(strategy_ids=[uuid.uuid4()], job_runs=jobs)

        with patch.multiple("app.services.phase1_readiness.settings", **_base_settings()):
            result = build_phase1_readiness(db)

        self.assertTrue(
            any("market_entry_cycle" in w and "failed" in w for w in result["warnings"])
        )

    def test_build_phase1_readiness_mode_paper_auto_submit_when_all_enabled(self) -> None:
        db = FakePhase1Session(strategy_ids=[uuid.uuid4()])

        with patch.multiple("app.services.phase1_readiness.settings", **_base_settings()):
            result = build_phase1_readiness(db)

        self.assertEqual(result["mode"], "paper_auto_submit")

    def test_build_phase1_readiness_mode_preview_only_when_submit_disabled(self) -> None:
        db = FakePhase1Session(strategy_ids=[uuid.uuid4()])
        settings = {**_base_settings(), "market_cycle_submit_enabled": False}

        with patch.multiple("app.services.phase1_readiness.settings", **settings):
            result = build_phase1_readiness(db)

        self.assertEqual(result["mode"], "paper_preview_only")

    def test_build_phase1_readiness_includes_safety_block(self) -> None:
        db = FakePhase1Session(strategy_ids=[uuid.uuid4()])

        with patch.multiple("app.services.phase1_readiness.settings", **_base_settings()):
            result = build_phase1_readiness(db)

        safety = result["safety"]
        self.assertIn("paper_mode", safety)
        self.assertIn("trading_automation_enabled", safety)
        self.assertIn("market_cycle_submit_enabled", safety)
        self.assertTrue(safety["paper_mode"])

    def test_build_phase1_readiness_includes_risk_caps(self) -> None:
        db = FakePhase1Session(strategy_ids=[uuid.uuid4()])

        with patch.multiple("app.services.phase1_readiness.settings", **_base_settings()):
            result = build_phase1_readiness(db)

        caps = result["risk_caps"]
        self.assertEqual(caps["max_auto_orders_per_cycle"], 5)
        self.assertEqual(caps["max_auto_orders_per_day"], 20)
        self.assertEqual(caps["max_open_positions"], 50)
        self.assertEqual(str(caps["max_estimated_premium_per_order"]), "2500")

    def test_build_phase1_readiness_snapshot_fields_serialized(self) -> None:
        snapshot = _build_snapshot(review_date=date(2026, 5, 10))
        db = FakePhase1Session(strategy_ids=[uuid.uuid4()], snapshot=snapshot)

        with patch.multiple("app.services.phase1_readiness.settings", **_base_settings()):
            result = build_phase1_readiness(db)

        snap = result["latest_review_snapshot"]
        self.assertIsNotNone(snap)
        self.assertEqual(snap["review_date"], "2026-05-10")
        self.assertEqual(snap["review_type"], "post_market")
        self.assertEqual(snap["status"], "completed")
        self.assertIn("id", snap)
        self.assertIn("generated_at", snap)

    def test_build_phase1_readiness_latest_jobs_keyed_by_job_name(self) -> None:
        db = FakePhase1Session(strategy_ids=[uuid.uuid4()], job_runs=_recent_jobs())

        with patch.multiple("app.services.phase1_readiness.settings", **_base_settings()):
            result = build_phase1_readiness(db)

        jobs = result["latest_jobs"]
        self.assertIn("market_entry_cycle", jobs)
        self.assertIn("market_cycle", jobs)
        self.assertIn("market_cycle_exits", jobs)
        self.assertIn("market_maintenance", jobs)
        self.assertEqual(jobs["market_entry_cycle"]["job_name"], "market_entry_cycle")
        self.assertEqual(jobs["market_entry_cycle"]["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
