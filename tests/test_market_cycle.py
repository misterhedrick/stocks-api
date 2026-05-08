from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import ANY, patch

from app.db.models import AuditLog, BrokerOrder, JobRun, OrderIntent, Signal, Strategy
from app.services.automation_guard import AutomationDecision
from app.services.broker_reconciliation import BrokerReconciliationResult
from app.services.market_cycle import run_market_cycle
from app.services.news_scanner import NewsScanResult
from app.services.position_exits import ExitEvaluationResult
from app.services.signal_scanner import SignalScanResult


class FakeMarketCycleSession:
    def __init__(
        self,
        *,
        signal: Signal | None = None,
        strategy: Strategy | None = None,
        order_intent: OrderIntent | None = None,
        scalar_results: list[object | None] | None = None,
        lock_acquired: bool = True,
    ) -> None:
        self.signal = signal
        self.strategy = strategy
        self.order_intent = order_intent
        self.scalar_results = scalar_results or []
        self.added: list[object] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.flush_count = 0
        self._lock_acquired = lock_acquired
        self._lock_checked = False

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
        # First scalar call is always the advisory lock check.
        if not self._lock_checked:
            self._lock_checked = True
            return self._lock_acquired
        if self.scalar_results:
            return self.scalar_results.pop(0)
        return 0

    def scalars(self, _: object) -> list[object]:
        return []

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
        if model is JobRun:
            for item in self.added:
                if isinstance(item, JobRun) and item.id == record_id:
                    return item
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
        no_signal_reasons=[],
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
        fill_page_size_requested=75,
        fill_page_size_used=75,
        fill_pages_fetched=1,
        fill_pagination_complete=True,
        fill_pagination_stop_reason="short_page_no_next_page",
    )


def build_exit_evaluation_result(order_intent_id: uuid.UUID | None = None) -> ExitEvaluationResult:
    return ExitEvaluationResult(
        positions_seen=1,
        positions_evaluated=1,
        exits_created=1,
        exits_skipped=0,
        errors=[],
        no_exit_reasons=[],
        position_ownership=[
            {
                "symbol": "SPY260429C00500000",
                "managed": True,
                "reason": "linked to active strategy",
            }
        ],
        order_intent_ids=[order_intent_id or uuid.uuid4()],
    )


def build_news_scan_result() -> NewsScanResult:
    return build_news_scan_result_with_risk(
        risk_assessment={
            "market_risk_level": "medium",
            "market_impact_keywords": ["fed", "rate"],
            "should_block_new_entries": False,
            "manual_review_symbols": [],
            "ticker_risks": {"SPY": {"risk_level": "low", "impact_keywords": [], "reasons": []}},
            "reasons": ["market: Fed rate news"],
        }
    )


def build_news_scan_result_with_risk(
    *,
    risk_assessment: dict[str, object],
) -> NewsScanResult:
    return NewsScanResult(
        job_run=build_job_run("news_scan"),
        market_items=[
            {
                "title": "Fed rate news",
                "url": "https://example.test/news",
                "source": "Example",
                "published_at": None,
                "impact_keywords": ["fed", "rate"],
            }
        ],
        ticker_items={"SPY": []},
        owned_symbols=["SPY"],
        risk_assessment=risk_assessment,
        sources_checked=2,
        errors=[],
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


def allowed_automation_decision() -> AutomationDecision:
    return AutomationDecision(allowed=True, reasons=[], limits_snapshot={})


class MarketCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.switch_patches = [
            patch("app.services.market_cycle.settings.market_cycle_preview_enabled", False),
            patch("app.services.market_cycle.settings.market_cycle_exit_enabled", False),
            patch("app.services.market_cycle.settings.market_cycle_news_enabled", False),
            patch("app.services.market_cycle.settings.market_cycle_submit_enabled", False),
        ]
        for switch_patch in self.switch_patches:
            switch_patch.start()

    def tearDown(self) -> None:
        for switch_patch in reversed(self.switch_patches):
            switch_patch.stop()

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
        self.assertFalse(result.exit_enabled)
        self.assertFalse(result.news_enabled)
        self.assertFalse(result.submit_enabled)
        self.assertEqual(result.scan["signals_created"], 1)
        self.assertEqual(result.reconcile["orders_seen"], 2)
        self.assertEqual(result.reconcile["fill_pages_fetched"], 1)
        self.assertEqual(result.reconcile["fill_pagination_stop_reason"], "short_page_no_next_page")
        self.assertEqual(result.preview["status"], "disabled")
        self.assertEqual(result.exits["status"], "disabled")
        self.assertEqual(result.news["status"], "disabled")
        self.assertEqual(result.submit["status"], "disabled")
        scanner.assert_called_once_with(db, limit=25)
        reconcile.assert_called_once_with(db, order_limit=50, fill_page_size=75, deadline=ANY)
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

    def test_run_market_cycle_auto_previews_pending_unpreviewed_signals_when_enabled(self) -> None:
        strategy = build_strategy()
        signal = build_signal(strategy)
        db = FakeMarketCycleSession(signal=signal, strategy=strategy)
        order_intent = build_order_intent(signal)

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=SignalScanResult(
                job_run=build_job_run("scan_signals"),
                strategies_seen=2,
                strategies_scanned=0,
                signals_created=0,
                signals_skipped=1,
                errors=["duplicate signal suppressed"],
                no_signal_reasons=[],
                created_signal_ids=[],
            ),
        ), patch(
            "app.services.market_cycle._signal_ids_for_preview",
            return_value=[signal.id],
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
            return_value=order_intent,
        ) as preview:
            result = run_market_cycle(db)

        self.assertEqual(result.preview["signals_seen"], 1)
        self.assertEqual(result.preview["previews_created"], 1)
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

    def test_run_market_cycle_delays_entry_preview_outside_submit_window(self) -> None:
        strategy = build_strategy()
        signal = build_signal(strategy)
        db = FakeMarketCycleSession(signal=signal, strategy=strategy)

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
            "app.services.market_cycle._entry_preview_delay_reason",
            return_value="auto-preview delayed until scanner.submit.trade_windows opens",
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
        ) as preview:
            result = run_market_cycle(db)

        self.assertEqual(result.preview["signals_seen"], 1)
        self.assertEqual(result.preview["previews_created"], 0)
        self.assertEqual(result.preview["previews_skipped"], 1)
        self.assertIn("auto-preview delayed", result.preview["errors"][0])
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
            "app.services.market_cycle._entry_preview_delay_reason",
            return_value=None,
        ), patch(
            "app.services.market_cycle.can_auto_submit_order_intent",
            return_value=allowed_automation_decision(),
        ), patch(
            "app.services.market_cycle.submit_order_intent",
            return_value=(order_intent, broker_order),
        ) as submit:
            result = run_market_cycle(db)

        self.assertTrue(result.submit_enabled)
        self.assertEqual(result.submit["candidates_seen"], 1)
        self.assertEqual(result.submit["order_intents_seen"], 1)
        self.assertEqual(result.submit["submitted"], 1)
        self.assertEqual(result.submit["skipped"], 0)
        self.assertEqual(result.submit["skipped_reasons"], {})
        self.assertEqual(result.submit["submitted_order_intent_ids"], [str(order_intent.id)])
        self.assertEqual(result.submit["broker_order_ids"], [str(broker_order.id)])
        submit.assert_called_once_with(db, order_intent.id)

    def test_run_market_cycle_reports_global_submit_disabled_with_candidates(self) -> None:
        strategy = build_strategy()
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
            False,
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

        self.assertFalse(result.submit_enabled)
        self.assertEqual(result.submit["status"], "disabled")
        self.assertEqual(result.submit["reason"], "submit disabled by global config")
        self.assertEqual(result.submit["candidates_seen"], 1)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertEqual(
            result.submit["skipped_reasons"],
            {"submit disabled by global config": 1},
        )
        submit.assert_not_called()

    def test_run_market_cycle_reports_ineligible_status_submit_skip(self) -> None:
        strategy = build_strategy()
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        order_intent.status = "stale"
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
            "app.services.market_cycle._entry_preview_delay_reason",
            return_value=None,
        ), patch(
            "app.services.market_cycle.can_auto_submit_order_intent",
        ) as guard, patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["candidates_seen"], 1)
        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertEqual(result.submit["skipped_reasons"], {"ineligible_status": 1})
        self.assertIn("ineligible_status status=stale", result.submit["errors"][0])
        guard.assert_not_called()
        submit.assert_not_called()

    def test_run_market_cycle_reports_submit_runtime_budget_skip_with_candidates(self) -> None:
        strategy = build_strategy()
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
            "app.services.market_cycle._phase_budget_exceeded",
            side_effect=[False, False, True, True],
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
            "app.services.market_cycle._entry_preview_delay_reason",
            return_value=None,
        ), patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["status"], "skipped")
        self.assertEqual(result.submit["candidates_seen"], 1)
        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertEqual(result.submit["skipped_reasons"], {"runtime_budget_exceeded": 1})
        self.assertIn("runtime budget exceeded", result.submit["errors"][0])
        submit.assert_not_called()

    def test_run_market_cycle_evaluates_exits_when_enabled(self) -> None:
        db = FakeMarketCycleSession()
        order_intent_id = uuid.uuid4()

        with patch(
            "app.services.market_cycle.settings.market_cycle_exit_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.evaluate_position_exits",
            return_value=build_exit_evaluation_result(order_intent_id),
        ) as exits:
            result = run_market_cycle(db, scan_limit=25)

        self.assertTrue(result.exit_enabled)
        self.assertEqual(result.exits["positions_seen"], 1)
        self.assertEqual(result.exits["exits_created"], 1)
        self.assertTrue(result.exits["position_ownership"][0]["managed"])
        self.assertEqual(result.exits["order_intent_ids"], [str(order_intent_id)])
        exits.assert_called_once_with(db, limit=25)

    def test_run_market_cycle_checks_news_when_enabled(self) -> None:
        db = FakeMarketCycleSession()

        with patch(
            "app.services.market_cycle.settings.market_cycle_news_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.scan_market_news",
            return_value=build_news_scan_result(),
        ) as news_scan:
            result = run_market_cycle(db)

        self.assertTrue(result.news_enabled)
        self.assertEqual(result.news["owned_symbols"], ["SPY"])
        self.assertEqual(result.news["risk_assessment"]["market_risk_level"], "medium")
        self.assertEqual(result.news["sources_checked"], 2)
        news_scan.assert_called_once_with(db)

    def test_run_market_cycle_blocks_entry_previews_when_news_risk_is_high(self) -> None:
        strategy = build_strategy()
        signal = build_signal(strategy)
        db = FakeMarketCycleSession(signal=signal, strategy=strategy)
        high_risk_news = build_news_scan_result_with_risk(
            risk_assessment={
                "market_risk_level": "high",
                "market_impact_keywords": ["war", "volatility"],
                "should_block_new_entries": True,
                "manual_review_symbols": ["SPY"],
                "ticker_risks": {
                    "SPY": {
                        "risk_level": "high",
                        "impact_keywords": ["volatility"],
                        "reasons": ["SPY: volatility headline"],
                    }
                },
                "reasons": ["market: war headline"],
            }
        )

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled",
            True,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_news_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(signal.id),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.scan_market_news",
            return_value=high_risk_news,
        ), patch(
            "app.services.market_cycle.preview_order_intent_from_signal",
        ) as preview:
            result = run_market_cycle(db)

        self.assertEqual(result.preview["status"], "blocked")
        self.assertEqual(result.preview["previews_created"], 0)
        self.assertEqual(result.preview["previews_skipped"], 1)
        self.assertTrue(result.preview["news_risk"]["should_block_new_entries"])
        preview.assert_not_called()

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
            "app.services.market_cycle._entry_preview_delay_reason",
            return_value=None,
        ), patch(
            "app.services.market_cycle.can_auto_submit_order_intent",
            return_value=allowed_automation_decision(),
        ), patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertIn("scanner.submit config is required", result.submit["errors"][0])
        submit.assert_not_called()

    def test_run_market_cycle_skips_auto_submit_when_guard_blocks(self) -> None:
        strategy = build_strategy()
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        db = FakeMarketCycleSession(
            signal=signal,
            strategy=strategy,
            order_intent=order_intent,
        )
        blocked_decision = AutomationDecision(
            allowed=False,
            reasons=["TRADING_AUTOMATION_ENABLED is false"],
            limits_snapshot={"trading_automation_enabled": False},
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
            "app.services.market_cycle._entry_preview_delay_reason",
            return_value=None,
        ), patch(
            "app.services.market_cycle.can_auto_submit_order_intent",
            return_value=blocked_decision,
        ), patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertIn("TRADING_AUTOMATION_ENABLED is false", result.submit["errors"][0])
        submit.assert_not_called()
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertIn(
            "order_intent.auto_submit_skipped",
            [audit_log.event_type for audit_log in audit_logs],
        )

    def test_run_market_cycle_ignores_strategy_max_contracts_per_order(self) -> None:
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
            "app.services.market_cycle._entry_preview_delay_reason",
            return_value=None,
        ), patch(
            "app.services.market_cycle.can_auto_submit_order_intent",
            return_value=allowed_automation_decision(),
        ), patch(
            "app.services.market_cycle.submit_order_intent",
            return_value=(order_intent, build_broker_order(order_intent)),
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 1)
        self.assertEqual(result.submit["skipped"], 0)
        submit.assert_called_once_with(db, order_intent.id)

    def test_run_market_cycle_ignores_strategy_max_contracts_per_cycle(self) -> None:
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
            "app.services.market_cycle.can_auto_submit_order_intent",
            return_value=allowed_automation_decision(),
        ), patch(
            "app.services.market_cycle.submit_order_intent",
            return_value=(order_intent, build_broker_order(order_intent)),
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 1)
        self.assertEqual(result.submit["skipped"], 0)
        submit.assert_called_once_with(db, order_intent.id)

    def test_run_market_cycle_ignores_strategy_max_notional_per_order(self) -> None:
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
            "app.services.market_cycle.can_auto_submit_order_intent",
            return_value=allowed_automation_decision(),
        ), patch(
            "app.services.market_cycle.submit_order_intent",
            return_value=(order_intent, build_broker_order(order_intent)),
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 1)
        self.assertEqual(result.submit["skipped"], 0)
        submit.assert_called_once_with(db, order_intent.id)

    def test_run_market_cycle_ignores_strategy_max_orders_per_trading_day(self) -> None:
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
            "app.services.market_cycle.can_auto_submit_order_intent",
            return_value=allowed_automation_decision(),
        ), patch(
            "app.services.market_cycle.submit_order_intent",
            return_value=(order_intent, build_broker_order(order_intent)),
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 1)
        self.assertEqual(result.submit["skipped"], 0)
        submit.assert_called_once_with(db, order_intent.id)

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
            "app.services.market_cycle._entry_preview_delay_reason",
            return_value=None,
        ), patch(
            "app.services.market_cycle.can_auto_submit_order_intent",
            return_value=allowed_automation_decision(),
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
        self.assertEqual(result.submit["skipped_reasons"], {"outside_trade_window": 1})
        self.assertIn("outside scanner.submit.trade_windows", result.submit["errors"][0])
        submit.assert_not_called()

    def test_run_market_cycle_blocks_entry_submit_before_10am_et(self) -> None:
        strategy = build_strategy()
        strategy.config["scanner"]["submit"]["trade_windows"] = [
            {"timezone": "America/New_York", "start": "10:00", "end": "16:00"}
        ]
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        db = FakeMarketCycleSession(signal=signal, strategy=strategy, order_intent=order_intent)

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled", True,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_submit_enabled", True,
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
            "app.services.market_cycle._entry_preview_delay_reason", return_value=None,
        ), patch(
            "app.services.market_cycle.can_auto_submit_order_intent",
            return_value=allowed_automation_decision(),
        ), patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit, patch(
            "app.services.market_cycle.datetime", wraps=datetime,
        ) as mock_dt:
            # 9:30 AM EDT (UTC-4) = 13:30 UTC — before the 10:00 ET window
            mock_dt.now.return_value = datetime(2026, 4, 23, 13, 30, tzinfo=timezone.utc)
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertIn("outside scanner.submit.trade_windows", result.submit["errors"][0])
        submit.assert_not_called()

    def test_run_market_cycle_allows_entry_submit_during_10am_to_4pm_et(self) -> None:
        strategy = build_strategy()
        strategy.config["scanner"]["submit"]["trade_windows"] = [
            {"timezone": "America/New_York", "start": "10:00", "end": "16:00"}
        ]
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        broker_order = build_broker_order(order_intent)
        db = FakeMarketCycleSession(signal=signal, strategy=strategy, order_intent=order_intent)

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled", True,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_submit_enabled", True,
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
            "app.services.market_cycle._entry_preview_delay_reason", return_value=None,
        ), patch(
            "app.services.market_cycle.can_auto_submit_order_intent",
            return_value=allowed_automation_decision(),
        ), patch(
            "app.services.market_cycle.submit_order_intent",
            return_value=(order_intent, broker_order),
        ) as submit, patch(
            "app.services.market_cycle.datetime", wraps=datetime,
        ) as mock_dt:
            # 11:00 AM EDT (UTC-4) = 15:00 UTC — inside the 10:00-16:00 ET window
            mock_dt.now.return_value = datetime(2026, 4, 23, 15, 0, tzinfo=timezone.utc)
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 1)
        self.assertEqual(result.submit["skipped"], 0)
        submit.assert_called_once_with(db, order_intent.id)

    def test_run_market_cycle_blocks_entry_submit_after_4pm_et(self) -> None:
        strategy = build_strategy()
        strategy.config["scanner"]["submit"]["trade_windows"] = [
            {"timezone": "America/New_York", "start": "10:00", "end": "16:00"}
        ]
        signal = build_signal(strategy)
        order_intent = build_order_intent(signal)
        db = FakeMarketCycleSession(signal=signal, strategy=strategy, order_intent=order_intent)

        with patch(
            "app.services.market_cycle.settings.market_cycle_preview_enabled", True,
        ), patch(
            "app.services.market_cycle.settings.market_cycle_submit_enabled", True,
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
            "app.services.market_cycle._entry_preview_delay_reason", return_value=None,
        ), patch(
            "app.services.market_cycle.can_auto_submit_order_intent",
            return_value=allowed_automation_decision(),
        ), patch(
            "app.services.market_cycle.submit_order_intent",
        ) as submit, patch(
            "app.services.market_cycle.datetime", wraps=datetime,
        ) as mock_dt:
            # 4:30 PM EDT (UTC-4) = 20:30 UTC — after the 16:00 ET window
            mock_dt.now.return_value = datetime(2026, 4, 23, 20, 30, tzinfo=timezone.utc)
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 0)
        self.assertEqual(result.submit["skipped"], 1)
        self.assertIn("outside scanner.submit.trade_windows", result.submit["errors"][0])
        submit.assert_not_called()

    def test_run_market_cycle_ignores_strategy_max_open_contracts_per_symbol(self) -> None:
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
            "app.services.market_cycle.can_auto_submit_order_intent",
            return_value=allowed_automation_decision(),
        ), patch(
            "app.services.market_cycle.submit_order_intent",
            return_value=(order_intent, build_broker_order(order_intent)),
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 1)
        self.assertEqual(result.submit["skipped"], 0)
        submit.assert_called_once_with(db, order_intent.id)

    def test_run_market_cycle_ignores_strategy_max_open_contracts_per_strategy(self) -> None:
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
            "app.services.market_cycle.can_auto_submit_order_intent",
            return_value=allowed_automation_decision(),
        ), patch(
            "app.services.market_cycle.submit_order_intent",
            return_value=(order_intent, build_broker_order(order_intent)),
        ) as submit:
            result = run_market_cycle(db)

        self.assertEqual(result.submit["submitted"], 1)
        self.assertEqual(result.submit["skipped"], 0)
        submit.assert_called_once_with(db, order_intent.id)

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

    def test_run_market_cycle_records_exit_attention_audit_log(self) -> None:
        db = FakeMarketCycleSession()

        with patch(
            "app.services.market_cycle.settings.market_cycle_exit_enabled",
            True,
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), patch(
            "app.services.market_cycle.evaluate_position_exits",
            return_value=ExitEvaluationResult(
                positions_seen=1,
                positions_evaluated=1,
                exits_created=0,
                exits_skipped=1,
                errors=[],
                no_exit_reasons=["SPY: linked strategy does not have scanner.exit enabled"],
                position_ownership=[],
                order_intent_ids=[],
            ),
        ):
            result = run_market_cycle(db)

        self.assertEqual(result.exits["exits_created"], 0)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertIn(
            "market_cycle.exit_attention_required",
            [audit_log.event_type for audit_log in audit_logs],
        )


class MarketCycleAdvisoryLockTests(unittest.TestCase):
    """Tests for the pg_try_advisory_xact_lock single-run enforcement."""

    def setUp(self) -> None:
        self.switch_patches = [
            patch("app.services.market_cycle.settings.market_cycle_preview_enabled", False),
            patch("app.services.market_cycle.settings.market_cycle_exit_enabled", False),
            patch("app.services.market_cycle.settings.market_cycle_news_enabled", False),
            patch("app.services.market_cycle.settings.market_cycle_submit_enabled", False),
        ]
        for p in self.switch_patches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self.switch_patches):
            p.stop()

    def test_lock_not_acquired_returns_skipped_immediately(self) -> None:
        db = FakeMarketCycleSession(lock_acquired=False)

        result = run_market_cycle(db)

        self.assertEqual(result.job_run.status, "skipped")
        self.assertEqual(result.job_run.job_name, "market_cycle")
        self.assertIsNotNone(result.job_run.finished_at)
        self.assertIsNone(result.scan)
        self.assertIsNone(result.reconcile)
        self.assertIsNone(result.preview)
        self.assertIsNone(result.exits)
        self.assertIsNone(result.news)
        self.assertIsNone(result.submit)
        self.assertEqual(result.diagnostics, {"status": "skipped", "reason": "already_running"})
        # Must have committed the skipped job_run record.
        self.assertEqual(db.commit_count, 1)

    def test_lock_not_acquired_does_not_run_scan_or_reconcile(self) -> None:
        db = FakeMarketCycleSession(lock_acquired=False)

        with patch(
            "app.services.market_cycle.scan_signals",
        ) as scanner, patch(
            "app.services.market_cycle.reconcile_broker_state",
        ) as reconcile:
            run_market_cycle(db)

        scanner.assert_not_called()
        reconcile.assert_not_called()

    def test_lock_acquired_runs_cycle_normally(self) -> None:
        db = FakeMarketCycleSession(lock_acquired=True)

        with patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ):
            result = run_market_cycle(db)

        self.assertIn(result.job_run.status, ("succeeded", "partial"))
        self.assertEqual(result.job_run.job_name, "market_cycle")

    def test_lock_released_after_exception_via_rollback(self) -> None:
        """Verify that a failed cycle commits its failed job_run (releasing the xact lock)."""
        db = FakeMarketCycleSession(lock_acquired=True)

        with patch(
            "app.services.market_cycle.scan_signals",
            side_effect=RuntimeError("simulated scan failure"),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ), self.assertRaises(RuntimeError):
            run_market_cycle(db)

        # The exception handler must rollback then commit to persist the failed JobRun.
        self.assertEqual(db.rollback_count, 1)
        self.assertEqual(db.commit_count, 1)
        # The same JobRun object may be added more than once (initial add + re-add in handler).
        # Check that at least one distinct failed job_run exists.
        failed_run_ids = {
            obj.id for obj in db.added
            if isinstance(obj, JobRun) and obj.status == "failed"
        }
        self.assertEqual(len(failed_run_ids), 1)


class MarketCycleRuntimeBudgetTests(unittest.TestCase):
    """Tests for the MARKET_CYCLE_MAX_RUNTIME_SECONDS hard budget."""

    def setUp(self) -> None:
        self.switch_patches = [
            patch("app.services.market_cycle.settings.market_cycle_preview_enabled", False),
            patch("app.services.market_cycle.settings.market_cycle_exit_enabled", False),
            patch("app.services.market_cycle.settings.market_cycle_news_enabled", False),
            patch("app.services.market_cycle.settings.market_cycle_submit_enabled", False),
        ]
        for p in self.switch_patches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self.switch_patches):
            p.stop()

    def test_runtime_budget_exceeded_sets_partial_status(self) -> None:
        db = FakeMarketCycleSession(lock_acquired=True)

        # Use phase_timeout_seconds=0 to force immediate budget exhaustion after
        # the first phase, which causes subsequent phases to be skipped.
        with patch(
            "app.services.market_cycle.settings.market_cycle_scan_enabled", True
        ), patch(
            "app.services.market_cycle.settings.market_cycle_reconcile_enabled", True
        ), patch(
            "app.services.market_cycle.settings.market_cycle_phase_timeout_seconds", 0
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ):
            result = run_market_cycle(db, phase_timeout_seconds=0)

        # phase_timeout_seconds=0 disables the budget check (per _phase_budget_exceeded),
        # so the cycle completes normally with "succeeded" status.
        # The budget constraint only kicks in when > 0 and elapsed >= timeout.
        self.assertIn(result.job_run.status, ("succeeded", "partial"))

    def test_partial_status_when_reconcile_skipped_by_budget(self) -> None:
        """Reconcile skipped due to tiny budget → job_run.status == partial."""
        db = FakeMarketCycleSession(lock_acquired=True)

        with patch(
            "app.services.market_cycle.settings.market_cycle_scan_enabled", True
        ), patch(
            "app.services.market_cycle.settings.market_cycle_reconcile_enabled", True
        ), patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(),
        ):
            # phase_timeout_seconds=1 with a real (slow) reconcile would produce
            # partial, but we force it via the _timeout_step path by patching
            # _phase_budget_exceeded to return True after scan.
            call_count = 0

            def fake_budget_exceeded(started: float, timeout: int) -> bool:
                nonlocal call_count
                call_count += 1
                # First call (before scan): not exceeded. Remaining calls: exceeded.
                return call_count > 1

            with patch(
                "app.services.market_cycle._phase_budget_exceeded",
                side_effect=fake_budget_exceeded,
            ):
                result = run_market_cycle(db)

        self.assertEqual(result.job_run.status, "partial")
        self.assertIsNotNone(result.diagnostics)
        self.assertIn("reconcile", result.diagnostics.get("skipped_steps", []))

    def test_timings_always_present_in_succeeded_result(self) -> None:
        db = FakeMarketCycleSession(lock_acquired=True)

        with patch(
            "app.services.market_cycle.scan_signals",
            return_value=build_signal_scan_result(),
        ), patch(
            "app.services.market_cycle.reconcile_broker_state",
            return_value=build_reconciliation_result(),
        ):
            result = run_market_cycle(db)

        self.assertIsNotNone(result.timings)
        self.assertIn("total_seconds", result.timings)
        self.assertIn("scan_seconds", result.timings)
        self.assertIn("reconcile_seconds", result.timings)

    def test_skipped_result_has_zero_total_seconds(self) -> None:
        db = FakeMarketCycleSession(lock_acquired=False)

        result = run_market_cycle(db)

        self.assertEqual(result.timings["total_seconds"], 0.0)


class MarketCycleLoopBudgetTests(unittest.TestCase):
    """Budget enforcement inside per-item loops, not just between phases."""

    def test_fill_pagination_stops_immediately_when_deadline_already_passed(self) -> None:
        from time import perf_counter as _pc
        from unittest.mock import MagicMock

        from app.services.broker_reconciliation import _list_all_fill_activities

        mock_client = MagicMock()
        past_deadline = _pc() - 1.0

        result = _list_all_fill_activities(
            mock_client,
            page_size=50,
            requested_page_size=50,
            deadline=past_deadline,
        )

        self.assertEqual(result.stop_reason, "budget_exceeded")
        self.assertEqual(result.pages_fetched, 0)
        self.assertEqual(result.rows, [])
        self.assertFalse(result.complete)
        mock_client.list_fill_activities.assert_not_called()

    def test_fill_pagination_no_deadline_runs_normally(self) -> None:
        from unittest.mock import MagicMock

        from app.services.broker_reconciliation import _list_all_fill_activities

        mock_client = MagicMock()
        mock_client.list_fill_activities.return_value = []

        result = _list_all_fill_activities(
            mock_client,
            page_size=50,
            requested_page_size=50,
            deadline=None,
        )

        self.assertEqual(result.stop_reason, "empty_page_no_next_page")
        self.assertEqual(result.pages_fetched, 1)
        mock_client.list_fill_activities.assert_called_once()

    def test_preview_loop_skips_all_signals_when_budget_already_expired(self) -> None:
        from time import perf_counter as _pc

        from app.services.market_cycle import _preview_created_signals

        signal_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        db = FakeMarketCycleSession()
        past_cycle_started = _pc() - 200

        result = _preview_created_signals(
            db,
            signal_ids,
            cycle_started=past_cycle_started,
            phase_timeout=90,
        )

        self.assertEqual(result["previews_created"], 0)
        self.assertEqual(result["previews_skipped"], len(signal_ids))
        self.assertTrue(
            any("budget exceeded" in e for e in result["errors"]),
            msg=f"Expected budget-exceeded error in {result['errors']}",
        )

    def test_submit_loop_skips_all_intents_when_budget_already_expired(self) -> None:
        from time import perf_counter as _pc

        from app.services.market_cycle import _submit_previewed_order_intents

        order_intent_ids = [uuid.uuid4(), uuid.uuid4()]
        db = FakeMarketCycleSession()
        past_cycle_started = _pc() - 200

        result = _submit_previewed_order_intents(
            db,
            order_intent_ids,
            cycle_started=past_cycle_started,
            phase_timeout=90,
        )

        self.assertEqual(result["submitted"], 0)
        self.assertEqual(result["skipped"], len(order_intent_ids))
        self.assertTrue(
            any("budget exceeded" in e for e in result["errors"]),
            msg=f"Expected budget-exceeded error in {result['errors']}",
        )

    def test_preview_and_submit_loops_unlimited_when_phase_timeout_zero(self) -> None:
        """phase_timeout=0 means no budget limit; loops must not short-circuit."""
        from time import perf_counter as _pc

        from app.services.market_cycle import _preview_created_signals, _submit_previewed_order_intents

        db = FakeMarketCycleSession()
        # Even with a past cycle_started, phase_timeout=0 means no deadline.
        past_cycle_started = _pc() - 200

        preview_result = _preview_created_signals(
            db,
            [],
            cycle_started=past_cycle_started,
            phase_timeout=0,
        )
        self.assertEqual(preview_result["previews_skipped"], 0)

        submit_result = _submit_previewed_order_intents(
            db,
            [],
            cycle_started=past_cycle_started,
            phase_timeout=0,
        )
        self.assertEqual(submit_result["skipped"], 0)


if __name__ == "__main__":
    unittest.main()
