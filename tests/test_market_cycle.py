from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from app.db.models import AuditLog, JobRun
from app.services.broker_reconciliation import BrokerReconciliationResult
from app.services.market_cycle import run_market_cycle
from app.services.signal_scanner import SignalScanResult


class FakeMarketCycleSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.flush_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flush_count += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


def build_job_run(job_name: str) -> JobRun:
    now = datetime.now(timezone.utc)
    return JobRun(
        id=uuid.uuid4(),
        job_name=job_name,
        status="succeeded",
        started_at=now,
        finished_at=now,
        details={},
        error=None,
        created_at=now,
    )


def build_signal_scan_result() -> SignalScanResult:
    return SignalScanResult(
        job_run=build_job_run("scan_signals"),
        strategies_seen=2,
        strategies_scanned=1,
        signals_created=1,
        signals_skipped=0,
        errors=[],
    )


def build_reconciliation_result() -> BrokerReconciliationResult:
    return BrokerReconciliationResult(
        job_run=build_job_run("reconcile_broker"),
        orders_seen=2,
        orders_created=1,
        orders_updated=1,
        fills_seen=1,
        fills_created=1,
        positions_seen=1,
        position_snapshots_created=1,
    )


class MarketCycleTests(unittest.TestCase):
    def test_run_market_cycle_runs_enabled_scan_and_reconciliation_steps(self) -> None:
        db = FakeMarketCycleSession()

        with patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(),
        ) as scanner, patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ) as reconcile:
            result = run_market_cycle(
                db,
                scan_limit=25,
                order_limit=50,
                fill_page_size=75,
            )

        self.assertEqual(result.job_run.status, "succeeded")
        self.assertTrue(result.scan_enabled)
        self.assertTrue(result.reconcile_enabled)
        self.assertFalse(result.preview_enabled)
        self.assertFalse(result.submit_enabled)
        self.assertEqual(result.scan["signals_created"], 1)
        self.assertEqual(result.reconcile["orders_seen"], 2)
        self.assertEqual(result.preview["status"], "disabled")
        self.assertEqual(result.submit["status"], "disabled")
        scanner.assert_called_once_with(db, limit=25)
        reconcile.assert_called_once_with(db, order_limit=50, fill_page_size=75)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "market_cycle.succeeded")

    def test_run_market_cycle_honors_disabled_scan_and_reconcile_switches(self) -> None:
        db = FakeMarketCycleSession()

        with patch(
            "app.services.market_cycle.settings.market_cycle_scan_enabled",
            False,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_reconcile_enabled",
            False,
        ), patch(
            "app.services.market_cycle.scan_signals",
        ) as scanner, patch(
            "app.services.market_cycle.reconcile_broker_state",
        ) as reconcile:
            result = run_market_cycle(db)

        self.assertEqual(result.scan["status"], "disabled")
        self.assertEqual(result.reconcile["status"], "disabled")
        scanner.assert_not_called()
        reconcile.assert_not_called()

    def test_run_market_cycle_records_failed_job_run(self) -> None:
        db = FakeMarketCycleSession()

        with patch(
            "app.services.market_cycle.scan_signals",
            side_effect=RuntimeError("scanner failed"),
        ):
            with self.assertRaises(RuntimeError):
                run_market_cycle(db)

        self.assertEqual(db.rollback_count, 1)
        self.assertEqual(db.commit_count, 1)
        job_runs = [item for item in db.added if isinstance(item, JobRun)]
        self.assertEqual(job_runs[-1].status, "failed")
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "market_cycle.failed")


if __name__ == "__main__":
    unittest.main()
