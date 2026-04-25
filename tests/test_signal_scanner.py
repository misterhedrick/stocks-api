from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from app.db.models import AuditLog, JobRun, Signal, Strategy
from app.services.signal_scanner import scan_signals


class FakeScalarResult:
    def __init__(self, values: list[Strategy]) -> None:
        self.values = values

    def __iter__(self):
        return iter(self.values)


class FakeScannerSession:
    def __init__(self, strategies: list[Strategy] | None = None) -> None:
        self.strategies = strategies or []
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

    def scalars(self, _: object) -> FakeScalarResult:
        return FakeScalarResult(
            [strategy for strategy in self.strategies if strategy.is_active]
        )

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


def build_strategy(
    *,
    is_active: bool = True,
    config: dict | None = None,
) -> Strategy:
    now = datetime.now(timezone.utc)
    return Strategy(
        id=uuid.uuid4(),
        name=f"Strategy {uuid.uuid4()}",
        description="Test strategy",
        is_active=is_active,
        config=config or {},
        created_at=now,
        updated_at=now,
    )


class SignalScannerTests(unittest.TestCase):
    def test_scan_signals_creates_signals_from_active_strategy_config(self) -> None:
        strategy = build_strategy(
            config={
                "scan_signals": [
                    {
                        "symbol": "SPY",
                        "underlying_symbol": "SPY",
                        "signal_type": "manual_scan",
                        "direction": "bullish",
                        "confidence": "0.75",
                        "rationale": "Scanner test",
                        "market_context": {"price": "500"},
                    }
                ]
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(db)

        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.strategies_seen, 1)
        self.assertEqual(result.strategies_scanned, 1)
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(result.signals_skipped, 0)
        self.assertEqual(signals[-1].strategy_id, strategy.id)
        self.assertEqual(signals[-1].symbol, "SPY")
        self.assertEqual(signals[-1].status, "new")
        self.assertEqual(db.commit_count, 1)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "signal_scan.succeeded")

    def test_scan_signals_ignores_inactive_strategies(self) -> None:
        db = FakeScannerSession(
            [
                build_strategy(
                    is_active=False,
                    config={
                        "scan_signals": [
                            {
                                "symbol": "SPY",
                                "signal_type": "manual_scan",
                                "direction": "bullish",
                            }
                        ]
                    },
                )
            ]
        )

        result = scan_signals(db)

        self.assertEqual(result.strategies_seen, 0)
        self.assertEqual(result.signals_created, 0)
        self.assertFalse([item for item in db.added if isinstance(item, Signal)])

    def test_scan_signals_skips_malformed_signal_specs(self) -> None:
        strategy = build_strategy(
            config={
                "scan_signals": [
                    {
                        "symbol": "SPY",
                        "signal_type": "manual_scan",
                    },
                    {
                        "symbol": "QQQ",
                        "signal_type": "manual_scan",
                        "direction": "bearish",
                    },
                ]
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(db)

        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(result.signals_skipped, 1)
        self.assertEqual(signals[-1].symbol, "QQQ")
        self.assertIn("direction is required", result.errors[0])

    def test_scan_signals_records_failed_job_run(self) -> None:
        class FailingScannerSession(FakeScannerSession):
            def scalars(self, _: object) -> FakeScalarResult:
                raise RuntimeError("database unavailable")

        db = FailingScannerSession()

        with self.assertRaises(RuntimeError):
            scan_signals(db)

        self.assertEqual(db.rollback_count, 1)
        self.assertEqual(db.commit_count, 1)
        job_runs = [item for item in db.added if isinstance(item, JobRun)]
        self.assertEqual(job_runs[-1].status, "failed")
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "signal_scan.failed")


if __name__ == "__main__":
    unittest.main()
