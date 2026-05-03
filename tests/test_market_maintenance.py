from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from app.db.models import AuditLog, JobRun, OrderIntent, Signal, Strategy
from app.services.broker_reconciliation import BrokerReconciliationResult
from app.services.market_maintenance import (
    MarketMaintenanceResult,
    cleanup_stale_trading_state,
    resolve_market_maintenance_phase,
    run_market_maintenance,
    run_post_market_maintenance,
    run_pre_market_maintenance,
)
from app.services.news_scanner import NewsScanResult
from app.services.performance_review import PerformanceReviewResult
from app.services.trade_cases import TradeCasePopulationResult


class FakeScalarResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def __iter__(self):
        return iter(self.values)


class FakeMaintenanceSession:
    def __init__(self, scalar_results: list[list[object]] | None = None) -> None:
        self.scalar_results = scalar_results or []
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
        values = self.scalar_results.pop(0) if self.scalar_results else []
        return FakeScalarResult(values)

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


def build_signal() -> Signal:
    now = datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc)
    return Signal(
        id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        symbol="SPY",
        underlying_symbol="SPY",
        signal_type="test",
        direction="bullish",
        status="new",
        created_at=now,
        updated_at=now,
    )


def build_order_intent() -> OrderIntent:
    now = datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc)
    return OrderIntent(
        id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        signal_id=uuid.uuid4(),
        underlying_symbol="SPY",
        option_symbol="SPY260501C00690000",
        side="buy",
        quantity=1,
        order_type="limit",
        time_in_force="day",
        status="previewed",
        preview={},
        created_at=now,
        updated_at=now,
    )


def build_strategy() -> Strategy:
    now = datetime.now(timezone.utc)
    return Strategy(
        id=uuid.uuid4(),
        name="Paper SPY confirmed trend call preview",
        is_active=True,
        config={
            "scanner": {
                "type": "trend_confirmation",
                "symbols": ["SPY"],
                "preview": {"enabled": True},
                "submit": {"enabled": True},
            }
        },
        created_at=now,
        updated_at=now,
    )


def build_reconciliation_result() -> BrokerReconciliationResult:
    return BrokerReconciliationResult(
        job_run=build_job_run("reconcile_broker"),
        orders_seen=2,
        orders_created=0,
        orders_updated=2,
        fills_seen=1,
        fills_created=0,
        positions_seen=1,
        position_snapshots_created=1,
    )


def build_news_scan_result() -> NewsScanResult:
    return NewsScanResult(
        job_run=build_job_run("news_scan"),
        market_items=[{"title": "Market risk"}],
        ticker_items={"SPY": []},
        owned_symbols=["SPY"],
        risk_assessment={"market_risk_level": "low", "should_block_new_entries": False},
        sources_checked=2,
        errors=[],
    )


def build_performance_result() -> PerformanceReviewResult:
    now = datetime.now(timezone.utc)
    return PerformanceReviewResult(
        generated_at=now,
        fills_seen=2,
        matched_round_trips=1,
        open_positions=[],
        totals={"realized_pnl": "25", "win_rate_percent": "100"},
        by_strategy=[],
        by_symbol=[],
        recent_round_trips=[],
    )


def build_trade_case_population_result() -> TradeCasePopulationResult:
    return TradeCasePopulationResult(
        job_run=build_job_run("populate_trade_cases"),
        round_trips_seen=2,
        inserted=1,
        updated=0,
        skipped=1,
        errors=[],
    )


def build_market_maintenance_result(phase: str) -> MarketMaintenanceResult:
    return MarketMaintenanceResult(
        job_run=build_job_run(f"{phase}_maintenance"),
        phase=phase,
        cleanup={"signals_marked_stale": 0, "order_intents_marked_stale": 0},
        reconcile={"orders_seen": 0, "fills_seen": 0, "positions_seen": 0},
        news={"status": "disabled"} if phase == "pre_market" else None,
        performance={"matched_round_trips": 0} if phase == "post_market" else None,
        readiness={"active_strategies": 1},
        settings_snapshot={"paper_mode": True},
        trade_cases={"round_trips_seen": 2, "inserted": 1, "updated": 0, "skipped": 1, "errors": []}
        if phase == "post_market"
        else None,
    )


class MarketMaintenanceTests(unittest.TestCase):
    def test_auto_phase_uses_utc_time_for_combined_cron(self) -> None:
        self.assertEqual(
            resolve_market_maintenance_phase(
                "auto",
                now=datetime(2026, 4, 29, 13, 15, tzinfo=timezone.utc),
            ),
            "pre_market",
        )
        self.assertEqual(
            resolve_market_maintenance_phase(
                "auto",
                now=datetime(2026, 4, 29, 21, 15, tzinfo=timezone.utc),
            ),
            "post_market",
        )

    def test_auto_market_maintenance_uses_phase_defaults(self) -> None:
        db = FakeMaintenanceSession()

        with patch(
            "app.services.market_maintenance.run_pre_market_maintenance",
            return_value=build_market_maintenance_result("pre_market"),
        ) as pre_market:
            result = run_market_maintenance(
                db,
                phase="auto",
                now=datetime(2026, 4, 29, 13, 15, tzinfo=timezone.utc),
                news_enabled=False,
            )

        self.assertEqual(result.phase, "pre_market")
        pre_market.assert_called_once_with(
            db,
            order_limit=100,
            fill_page_size=100,
            stale_after_hours=12,
            news_enabled=False,
        )

        with patch(
            "app.services.market_maintenance.run_post_market_maintenance",
            return_value=build_market_maintenance_result("post_market"),
        ) as post_market:
            result = run_market_maintenance(
                db,
                phase="auto",
                now=datetime(2026, 4, 29, 21, 15, tzinfo=timezone.utc),
            )

        self.assertEqual(result.phase, "post_market")
        post_market.assert_called_once_with(
            db,
            order_limit=500,
            fill_page_size=500,
            stale_after_hours=0,
        )

    def test_cleanup_stales_local_unsubmitted_state(self) -> None:
        signal = build_signal()
        order_intent = build_order_intent()
        db = FakeMaintenanceSession([[signal], [order_intent]])

        result = cleanup_stale_trading_state(
            db,
            stale_before=datetime(2026, 4, 30, tzinfo=timezone.utc),
            source="test",
        )

        self.assertEqual(result["signals_marked_stale"], 1)
        self.assertEqual(result["order_intents_marked_stale"], 1)
        self.assertEqual(signal.status, "stale")
        self.assertEqual(order_intent.status, "stale")
        self.assertIn("Marked stale by test", signal.rejected_reason)
        self.assertIn("Marked stale by test", order_intent.rejection_reason)

    def test_pre_market_maintenance_reconciles_cleans_news_and_readiness(self) -> None:
        signal = build_signal()
        order_intent = build_order_intent()
        strategy = build_strategy()
        db = FakeMaintenanceSession([[signal], [order_intent], [strategy]])

        with patch(
            "app.services.market_maintenance.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ) as reconcile, patch(
            "app.services.market_maintenance.scan_market_news",
            return_value=build_news_scan_result(),
        ) as news:
            result = run_pre_market_maintenance(
                db,
                order_limit=25,
                fill_page_size=50,
                stale_after_hours=0,
            )

        self.assertEqual(result.phase, "pre_market")
        self.assertEqual(result.cleanup["signals_marked_stale"], 1)
        self.assertEqual(result.reconcile["orders_seen"], 2)
        self.assertEqual(result.news["market_items_seen"], 1)
        self.assertEqual(result.readiness["active_strategies"], 1)
        self.assertEqual(result.job_run.status, "succeeded")
        reconcile.assert_called_once_with(db, order_limit=25, fill_page_size=50)
        news.assert_called_once_with(db)
        self.assertEqual(db.commit_count, 1)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "market_maintenance.pre_market.succeeded")

    def test_post_market_maintenance_records_performance_summary(self) -> None:
        signal = build_signal()
        order_intent = build_order_intent()
        strategy = build_strategy()
        db = FakeMaintenanceSession([[signal], [order_intent], [strategy]])

        with patch(
            "app.services.market_maintenance.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_maintenance.get_paper_performance_review",
            return_value=build_performance_result(),
        ) as performance, patch(
            "app.services.market_maintenance.populate_trade_cases_from_closed_round_trips",
            return_value=build_trade_case_population_result(),
        ):
            result = run_post_market_maintenance(db, stale_after_hours=0)

        self.assertEqual(result.phase, "post_market")
        self.assertEqual(result.performance["matched_round_trips"], 1)
        self.assertEqual(result.performance["totals"]["realized_pnl"], "25")
        self.assertIsNone(result.news)
        performance.assert_called_once_with(db, limit=5000)

    def test_post_market_maintenance_populates_trade_cases(self) -> None:
        signal = build_signal()
        order_intent = build_order_intent()
        strategy = build_strategy()
        db = FakeMaintenanceSession([[signal], [order_intent], [strategy]])

        with patch(
            "app.services.market_maintenance.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_maintenance.get_paper_performance_review",
            return_value=build_performance_result(),
        ), patch(
            "app.services.market_maintenance.populate_trade_cases_from_closed_round_trips",
            return_value=build_trade_case_population_result(),
        ) as populate:
            result = run_post_market_maintenance(db, stale_after_hours=0)

        self.assertIsNotNone(result.trade_cases)
        self.assertEqual(result.trade_cases["round_trips_seen"], 2)
        self.assertEqual(result.trade_cases["inserted"], 1)
        self.assertEqual(result.trade_cases["skipped"], 1)
        self.assertEqual(result.trade_cases["errors"], [])
        populate.assert_called_once_with(db, limit=5000)
        # Two commits: _finish_job_run + _write_trade_cases_audit_log
        self.assertEqual(db.commit_count, 2)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        tc_audit = next(
            (a for a in audit_logs if "trade_cases" in a.event_type), None
        )
        self.assertIsNotNone(tc_audit)
        self.assertEqual(
            tc_audit.event_type,
            "market_maintenance.post_market.trade_cases.succeeded",
        )
        self.assertIn("maintenance_job_run_id", tc_audit.payload)
        self.assertIn("trade_case_population_job_run_id", tc_audit.payload)
        self.assertEqual(tc_audit.payload["round_trips_seen"], 2)
        self.assertEqual(tc_audit.payload["inserted"], 1)

    def test_post_market_maintenance_trade_case_failure_does_not_fail_maintenance(self) -> None:
        signal = build_signal()
        order_intent = build_order_intent()
        strategy = build_strategy()
        db = FakeMaintenanceSession([[signal], [order_intent], [strategy]])

        with patch(
            "app.services.market_maintenance.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_maintenance.get_paper_performance_review",
            return_value=build_performance_result(),
        ), patch(
            "app.services.market_maintenance.populate_trade_cases_from_closed_round_trips",
            side_effect=RuntimeError("fill table exploded"),
        ):
            result = run_post_market_maintenance(db, stale_after_hours=0)

        # Maintenance itself succeeded
        self.assertEqual(result.job_run.status, "succeeded")
        self.assertEqual(result.phase, "post_market")
        # Trade cases reports the error but does not propagate
        self.assertIsNotNone(result.trade_cases)
        self.assertIn("error", result.trade_cases)
        self.assertIn("fill table exploded", result.trade_cases["error"])
        self.assertEqual(result.trade_cases["status"], "failed")
        # Failure audit event is written and committed
        self.assertEqual(db.commit_count, 2)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        tc_audit = next(
            (a for a in audit_logs if "trade_cases" in a.event_type), None
        )
        self.assertIsNotNone(tc_audit)
        self.assertEqual(
            tc_audit.event_type,
            "market_maintenance.post_market.trade_cases.failed",
        )
        self.assertIn("maintenance_job_run_id", tc_audit.payload)
        self.assertIn("fill table exploded", tc_audit.payload.get("error", ""))

    def test_pre_market_maintenance_has_no_trade_cases(self) -> None:
        signal = build_signal()
        order_intent = build_order_intent()
        strategy = build_strategy()
        db = FakeMaintenanceSession([[signal], [order_intent], [strategy]])

        with patch(
            "app.services.market_maintenance.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_maintenance.scan_market_news",
            return_value=build_news_scan_result(),
        ):
            result = run_pre_market_maintenance(db, stale_after_hours=0)

        self.assertIsNone(result.trade_cases)


if __name__ == "__main__":
    unittest.main()
