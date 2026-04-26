from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from app.db.models import AuditLog, BrokerOrder, JobRun, OrderIntent, Signal, Strategy
from app.services.broker_reconciliation import BrokerReconciliationResult
from app.services.market_cycle import run_market_cycle
from app.services.signal_scanner import SignalScanResult


class FakeMarketCycleSession:
    def __init__(
        self,
        *,
        signal: Signal | None = None,
        strategy: Strategy | None = None,
        order_intent: OrderIntent | None = None,
        scalar_results: list[object | None] | None = None,
    ) -> None:
        self.signal = signal
        self.strategy = strategy
        self.order_intent = order_intent
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

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def scalar(self, _: object) -> object | None:
        if self.scalar_results:
            return self.scalar_results.pop(0)
        return 0

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    def get(self, model: type, record_id: uuid.UUID) -> object | None:
        if model is Signal:
            if self.signal is None or self.signal.id != record_id:
                return None
            return self.signal
        if model is Strategy:
            if self.strategy is None or self.strategy.id != record_id:
                return None
            return self.strategy
        if model is OrderIntent:
            if self.order_intent is None or self.order_intent.id != record_id:
                return None
            return self.order_intent
        return None


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


def build_signal_scan_result(signal_id: uuid.UUID | None = None) -> SignalScanResult:
    return SignalScanResult(
        job_run=build_job_run("scan_signals"),
        strategies_seen=2,
        strategies_scanned=1,
        signals_created=1,
        signals_skipped=0,
        errors=[],
        created_signal_ids=[signal_id or uuid.uuid4()],
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


def build_strategy() -> Strategy:
    now = datetime.now(timezone.utc)
    return Strategy(
        id=uuid.uuid4(),
        name="Auto Preview Strategy",
        description="Test strategy",
        is_active=True,
        config={
            "scanner": {
                "type": "price_threshold",
                "preview": {
                    "enabled": True,
                    "option_type": "call",
                    "target_strike": "500",
                    "side": "buy",
                    "quantity": 1,
                    "order_type": "limit",
                    "time_in_force": "day",
                    "data_feed": "indicative",
                },
                "submit": {
                    "enabled": True,
                    "max_orders_per_cycle": 1,
                    "max_contracts_per_order": 1,
                    "max_contracts_per_cycle": 1,
                    "max_notional_per_order": "250.00",
                    "max_open_contracts_per_symbol": 1,
                    "max_open_contracts_per_strategy": 2,
                    "allowed_sides": ["buy"],
                },
            }
        },
        created_at=now,
        updated_at=now,
    )


def build_signal(strategy: Strategy) -> Signal:
    now = datetime.now(timezone.utc)
    return Signal(
        id=uuid.uuid4(),
        strategy_id=strategy.id,
        symbol="SPY",
        underlying_symbol="SPY",
        signal_type="price_breakout",
        direction="bullish",
        confidence=Decimal("0.6500"),
        rationale="Scanner signal",
        market_context={"price": "500.50"},
        status="new",
        created_at=now,
        updated_at=now,
    )


def build_order_intent(signal: Signal) -> OrderIntent:
    return OrderIntent(
        id=uuid.uuid4(),
        strategy_id=signal.strategy_id,
        signal_id=signal.id,
        underlying_symbol="SPY",
        option_symbol="SPY260417C00500000",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=Decimal("1.25"),
        time_in_force="day",
        status="previewed",
        preview={"source": "test"},
    )


def build_broker_order(order_intent: OrderIntent) -> BrokerOrder:
    return BrokerOrder(
        id=uuid.uuid4(),
        order_intent_id=order_intent.id,
        alpaca_order_id="alpaca-order-123",
        symbol=order_intent.option_symbol,
        side=order_intent.side,
        quantity=Decimal(order_intent.quantity),
        order_type=order_intent.order_type,
        limit_price=order_intent.limit_price,
        status="new",
        raw_response={"id": "alpaca-order-123"},
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

    def test_run_market_cycle_auto_previews_scanner_created_signals_when_enabled(self) -> None:
        strategy = build_strategy()
        signal = build_signal(strategy)
        db = FakeMarketCycleSession(signal=signal, strategy=strategy)
        order_intent = build_order_intent(signal)

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(signal.id),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
            return_value=order_intent,
        ) as preview:
            result = run_market_cycle(db)

        self.assertTrue(result.preview_enabled)
        self.assertEqual(result.preview["signals_seen"], 1)
        self.assertEqual(result.preview["previews_created"], 1)
        self.assertEqual(result.preview["previews_skipped"], 0)
        self.assertEqual(result.preview["order_intent_ids"], [str(order_intent.id)])
        preview.assert_called_once()

    def test_run_market_cycle_skips_auto_preview_without_strategy_preview_config(self) -> None:
        strategy = build_strategy()
        strategy.config = {"scanner": {"type": "price_threshold"}}
        signal = build_signal(strategy)
        db = FakeMarketCycleSession(signal=signal, strategy=strategy)

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(signal.id),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
        ) as preview:
            result = run_market_cycle(db)

        self.assertEqual(result.preview["signals_seen"], 1)
        self.assertEqual(result.preview["previews_created"], 0)
        self.assertEqual(result.preview["previews_skipped"], 1)
        self.assertIn("scanner.preview config is required", result.preview["errors"][0])
        preview.assert_not_called()

    def test_run_market_cycle_auto_submits_current_cycle_previews_when_enabled(self) -> None:
        strategy = build_strategy()
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        broker_order = build_broker_order(order_intent)
        db = FakeMarketCycleSession(
            signal=signal,
            strategy=strategy,
            order_intent=order_intent,
        )

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled",
            True,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_submit_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(signal.id),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
            return_value=order_intent,
        ), patch(
            "app.services.market_cycle.submit_order_intent",
            return_value=(order_intent, broker_order),
        ) as submit:
            result = run_market_cycle(db)

        self.assertTrue(result.submit_enabled)
        self.assertEqual(result.submit["order_intents_seen"], 1)
        self.assertEqual(result.submit["submitted"], 1)
        self.assertEqual(result.submit["skipped"], 0)
        self.assertEqual(result.submit["broker_order_ids"], [str(broker_order.id)])
        submit.assert_called_once_with(db, order_intent.id)

    def test_run_market_cycle_skips_auto_submit_without_strategy_submit_config(self) -> None:
        strategy = build_strategy()
        strategy.config["scanner"].pop("submit")
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        db = FakeMarketCycleSession(
            signal=signal,
            strategy=strategy,
            order_intent=order_intent,
        )

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled",
            True,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_submit_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(signal.id),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
            return_value=order_intent,
        ), patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertIn("scanner.submit config is required", result.submit["errors"][0])
        submit.assert_not_called()

    def test_run_market_cycle_enforces_submit_max_contracts_per_order(self) -> None:
        strategy = build_strategy()
        strategy.config["scanner"]["submit"]["max_contracts_per_order"] = 1
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        order_intent.quantity = 2
        db = FakeMarketCycleSession(
            signal=signal,
            strategy=strategy,
            order_intent=order_intent,
        )

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled",
            True,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_submit_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(signal.id),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
            return_value=order_intent,
        ), patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertIn("max_contracts_per_order", result.submit["errors"][0])
        submit.assert_not_called()

    def test_run_market_cycle_enforces_submit_max_contracts_per_cycle(self) -> None:
        strategy = build_strategy()
        strategy.config["scanner"]["submit"]["max_contracts_per_order"] = 5
        strategy.config["scanner"]["submit"]["max_contracts_per_cycle"] = 1
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        order_intent.quantity = 2
        db = FakeMarketCycleSession(
            signal=signal,
            strategy=strategy,
            order_intent=order_intent,
        )

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled",
            True,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_submit_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(signal.id),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
            return_value=order_intent,
        ), patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertIn("max_contracts_per_cycle", result.submit["errors"][0])
        submit.assert_not_called()

    def test_run_market_cycle_enforces_submit_max_notional_per_order(self) -> None:
        strategy = build_strategy()
        strategy.config["scanner"]["submit"]["max_notional_per_order"] = "100.00"
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        order_intent.limit_price = Decimal("1.25")
        db = FakeMarketCycleSession(
            signal=signal,
            strategy=strategy,
            order_intent=order_intent,
        )

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled",
            True,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_submit_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(signal.id),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
            return_value=order_intent,
        ), patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertIn("max_notional_per_order", result.submit["errors"][0])
        submit.assert_not_called()

    def test_run_market_cycle_enforces_submit_max_orders_per_trading_day(self) -> None:
        strategy = build_strategy()
        strategy.config["scanner"]["submit"]["max_orders_per_trading_day"] = 1
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        db = FakeMarketCycleSession(
            signal=signal,
            strategy=strategy,
            order_intent=order_intent,
            scalar_results=[1],
        )

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled",
            True,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_submit_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(signal.id),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
            return_value=order_intent,
        ), patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertIn("max_orders_per_trading_day", result.submit["errors"][0])
        submit.assert_not_called()

    def test_run_market_cycle_enforces_submit_trade_windows(self) -> None:
        strategy = build_strategy()
        strategy.config["scanner"]["submit"]["trade_windows"] = [
            {
                "timezone": "UTC",
                "start": "00:00",
                "end": "00:00",
            }
        ]
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        db = FakeMarketCycleSession(
            signal=signal,
            strategy=strategy,
            order_intent=order_intent,
        )

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled",
            True,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_submit_enabled",
            True,
        ), patch(
            "app.services.market_cycle.datetime",
            wraps=datetime,
        ) as market_cycle_datetime, patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(signal.id),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
            return_value=order_intent,
        ), patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit:
            market_cycle_datetime.now.return_value = datetime(
                2026,
                4,
                23,
                12,
                0,
                tzinfo=timezone.utc,
            )
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertIn("outside scanner.submit.trade_windows", result.submit["errors"][0])
        submit.assert_not_called()

    def test_run_market_cycle_enforces_submit_max_open_contracts_per_symbol(self) -> None:
        strategy = build_strategy()
        strategy.config["scanner"]["submit"].pop("max_open_contracts_per_strategy")
        strategy.config["scanner"]["submit"]["max_open_contracts_per_symbol"] = 1
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        db = FakeMarketCycleSession(
            signal=signal,
            strategy=strategy,
            order_intent=order_intent,
            scalar_results=[Decimal("1")],
        )

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled",
            True,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_submit_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(signal.id),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
            return_value=order_intent,
        ), patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertIn("max_open_contracts_per_symbol", result.submit["errors"][0])
        submit.assert_not_called()

    def test_run_market_cycle_enforces_submit_max_open_contracts_per_strategy(self) -> None:
        strategy = build_strategy()
        strategy.config["scanner"]["submit"].pop("max_open_contracts_per_symbol")
        strategy.config["scanner"]["submit"]["max_open_contracts_per_strategy"] = 2
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        db = FakeMarketCycleSession(
            signal=signal,
            strategy=strategy,
            order_intent=order_intent,
            scalar_results=[Decimal("2")],
        )

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled",
            True,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_submit_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(signal.id),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
            return_value=order_intent,
        ), patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertIn("max_open_contracts_per_strategy", result.submit["errors"][0])
        submit.assert_not_called()

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
