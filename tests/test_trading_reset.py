from __future__ import annotations

import unittest
import uuid

from app.db.models import AuditLog, JobRun
from app.services.trading_reset import (
    RESET_TRADING_DATA_CONFIRMATION,
    TradingDataResetConfirmationError,
    run_trading_data_reset,
)


class FakeDeleteResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class FakeResetSession:
    def __init__(
        self,
        *,
        counts: list[int],
        rowcounts: list[int] | None = None,
    ) -> None:
        self.counts = counts
        self.rowcounts = rowcounts or []
        self.added: list[object] = []
        self.executed: list[object] = []
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

    def scalar(self, _: object) -> int:
        return self.counts.pop(0)

    def execute(self, statement: object) -> FakeDeleteResult:
        self.executed.append(statement)
        return FakeDeleteResult(self.rowcounts.pop(0))

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class TradingDataResetTests(unittest.TestCase):
    def test_reset_requires_confirmation_when_not_dry_run(self) -> None:
        db = FakeResetSession(counts=[])

        with self.assertRaises(TradingDataResetConfirmationError):
            run_trading_data_reset(db, dry_run=False)

        self.assertEqual(db.commit_count, 0)
        self.assertEqual(db.executed, [])

    def test_dry_run_counts_runtime_tables_without_deleting(self) -> None:
        db = FakeResetSession(counts=[2, 3, 4, 5, 6, 7, 8])

        result = run_trading_data_reset(db)

        self.assertTrue(result.dry_run)
        self.assertTrue(result.include_history)
        self.assertEqual(result.counts_before["fills"], 2)
        self.assertEqual(result.counts_before["position_snapshots"], 6)
        self.assertEqual(result.counts_before["audit_logs"], 7)
        self.assertEqual(result.counts_before["job_runs"], 8)
        self.assertEqual(result.deleted["fills"], 0)
        self.assertEqual(db.executed, [])
        self.assertEqual(db.commit_count, 1)

    def test_confirmed_reset_deletes_runtime_tables_and_history(self) -> None:
        db = FakeResetSession(
            counts=[2, 3, 4, 5, 6, 7, 8],
            rowcounts=[2, 3, 4, 5, 6, 7, 8],
        )

        result = run_trading_data_reset(
            db,
            dry_run=False,
            confirm=RESET_TRADING_DATA_CONFIRMATION,
        )

        self.assertFalse(result.dry_run)
        self.assertEqual(result.deleted["fills"], 2)
        self.assertEqual(result.deleted["broker_orders"], 3)
        self.assertEqual(result.deleted["order_intents"], 4)
        self.assertEqual(result.deleted["signals"], 5)
        self.assertEqual(result.deleted["position_snapshots"], 6)
        self.assertEqual(result.deleted["audit_logs"], 7)
        self.assertEqual(result.deleted["job_runs"], 8)
        self.assertEqual(result.kept_tables, ["strategies"])
        self.assertEqual(len(db.executed), 7)
        self.assertEqual(db.commit_count, 1)
        job_runs = [item for item in db.added if isinstance(item, JobRun)]
        self.assertEqual(job_runs[-1].status, "succeeded")
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "trading_data_reset.succeeded")

    def test_reset_can_preserve_job_and_audit_history(self) -> None:
        db = FakeResetSession(
            counts=[2, 3, 4, 5, 6],
            rowcounts=[2, 3, 4, 5, 6],
        )

        result = run_trading_data_reset(
            db,
            dry_run=False,
            include_history=False,
            confirm=RESET_TRADING_DATA_CONFIRMATION,
        )

        self.assertFalse(result.include_history)
        self.assertNotIn("audit_logs", result.deleted)
        self.assertNotIn("job_runs", result.deleted)
        self.assertEqual(result.kept_tables, ["strategies", "job_runs", "audit_logs"])
        self.assertEqual(len(db.executed), 5)


if __name__ == "__main__":
    unittest.main()
