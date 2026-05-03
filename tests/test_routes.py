from __future__ import annotations

import unittest
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db.models import JobRun, OrderIntent, Signal, Strategy
from app.db.session import DatabaseSchemaNotReadyError, get_db
from app.integrations.alpaca import AlpacaTradingConfigurationError, AlpacaTradingError
from app.main import app
from app.schemas.options import OptionContractRead, OptionContractSelectionRead
from app.schemas.automation import (
    AutomationStatusRead,
    AutomationSwitchesRead,
)
from app.services.broker_reconciliation import BrokerReconciliationResult
from app.services.market_cycle import MarketCycleResult
from app.services.market_maintenance import MarketMaintenanceResult
from app.services.news_scanner import NewsScanResult
from app.services.performance_review import PerformanceReviewResult
from app.services.position_exits import ExitEvaluationResult
from app.services.signal_scanner import SignalScanResult
from app.services.trade_lifecycle import TradeCasesResult, TradeLifecycleResult
from app.services.trading_reset import TradingDataResetResult


class FakeRouteSession:
    def __init__(
        self,
        *,
        strategy: Strategy | None = None,
        signal: Signal | None = None,
    ) -> None:
        self.strategy = strategy
        self.signal = signal
        self.order_intent: OrderIntent | None = None
        self.added: list[object] = []
        self.commit_count = 0
        self.flush_count = 0

    def get(self, model: type, record_id: uuid.UUID) -> object | None:
        if model is Strategy:
            if self.strategy is None or self.strategy.id != record_id:
                return None
            return self.strategy
        if model is Signal:
            if self.signal is None or self.signal.id != record_id:
                return None
            return self.signal
        if model is OrderIntent:
            if self.order_intent is None or self.order_intent.id != record_id:
                return None
            return self.order_intent
        return None

    def add(self, obj: object) -> None:
        self.added.append(obj)
        if isinstance(obj, Strategy):
            self.strategy = obj
        if isinstance(obj, OrderIntent):
            self.order_intent = obj
        if isinstance(obj, Signal):
            self.signal = obj

    def flush(self) -> None:
        self.flush_count += 1
        for obj in self.added:
            _hydrate_model_defaults(obj)

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        pass

    def refresh(self, obj: object) -> None:
        _hydrate_model_defaults(obj)


def _hydrate_model_defaults(obj: object) -> None:
    if hasattr(obj, "id") and getattr(obj, "id", None) is None:
        obj.id = uuid.uuid4()

    if isinstance(obj, OrderIntent):
        if obj.status is None:
            obj.status = "previewed"
        if obj.preview is None:
            obj.preview = {}

    if isinstance(obj, Signal) and obj.status is None:
        obj.status = "new"

    now = datetime.now(timezone.utc)
    if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
        obj.created_at = now
    if hasattr(obj, "updated_at") and getattr(obj, "updated_at", None) is None:
        obj.updated_at = now


def build_strategy(strategy_id: uuid.UUID | None = None) -> Strategy:
    return Strategy(
        id=strategy_id or uuid.uuid4(),
        name="Opening Range Breakout",
        description="Test strategy",
        is_active=True,
        config={"underlying": "SPY"},
    )


def build_signal(strategy_id: uuid.UUID | None = None) -> Signal:
    now = datetime.now(timezone.utc)
    return Signal(
        id=uuid.uuid4(),
        strategy_id=strategy_id or uuid.uuid4(),
        symbol="SPY260417C00500000",
        underlying_symbol="SPY",
        signal_type="breakout",
        direction="bullish",
        confidence=Decimal("0.7500"),
        rationale="Opening range breakout",
        market_context={"price": "512.34"},
        status="new",
        created_at=now,
        updated_at=now,
    )


class RouteBehaviorTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_health_routes_are_public(self) -> None:
        client = TestClient(app)

        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/api/v1/health").status_code, 200)

    def test_protected_routes_require_valid_bearer_token(self) -> None:
        client = TestClient(app)

        missing_auth = client.get("/api/v1/order-intents")
        missing_automation_auth = client.get("/api/v1/automation/status")
        bad_auth = client.get(
            "/api/v1/order-intents",
            headers={"Authorization": "Bearer wrong-token"},
        )

        self.assertEqual(missing_auth.status_code, 401)
        self.assertEqual(missing_automation_auth.status_code, 401)
        self.assertEqual(bad_auth.status_code, 401)

    def test_readiness_requires_auth(self) -> None:
        client = TestClient(app)

        response = client.get("/api/v1/ready")

        self.assertEqual(response.status_code, 401)

    def test_readiness_returns_ready_when_database_checks_pass(self) -> None:
        client = TestClient(app)

        with (
            patch("app.api.routes.health.check_database_connection") as check_connection,
            patch("app.api.routes.health.check_database_schema") as check_schema,
        ):
            response = client.get(
                "/api/v1/ready",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready", "database": "ok"})
        check_connection.assert_called_once_with()
        check_schema.assert_called_once_with()

    def test_readiness_returns_503_when_database_schema_is_not_ready(self) -> None:
        client = TestClient(app)

        with patch("app.api.routes.health.check_database_connection"), patch(
            "app.api.routes.health.check_database_schema",
            side_effect=DatabaseSchemaNotReadyError("missing tables"),
        ):
            response = client.get(
                "/api/v1/ready",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 503)

    def test_create_strategy_route_happy_path(self) -> None:
        def override_db() -> Iterator[FakeRouteSession]:
            yield FakeRouteSession()

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)

        response = client.post(
            "/api/v1/strategies",
            headers={"Authorization": "Bearer change-me"},
            json={
                "name": "Opening Range Breakout",
                "description": "Test strategy",
                "is_active": True,
                "config": {"underlying": "SPY"},
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "Opening Range Breakout")

    def test_create_signal_route_happy_path(self) -> None:
        strategy = build_strategy()

        def override_db() -> Iterator[FakeRouteSession]:
            yield FakeRouteSession(strategy=strategy)

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)

        response = client.post(
            "/api/v1/signals",
            headers={"Authorization": "Bearer change-me"},
            json={
                "strategy_id": str(strategy.id),
                "symbol": "SPY260417C00500000",
                "underlying_symbol": "SPY",
                "signal_type": "breakout",
                "direction": "bullish",
                "confidence": "0.7500",
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["strategy_id"], str(strategy.id))

    def test_create_signal_returns_404_for_missing_strategy(self) -> None:
        def override_db() -> Iterator[FakeRouteSession]:
            yield FakeRouteSession()

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)

        response = client.post(
            "/api/v1/signals",
            headers={"Authorization": "Bearer change-me"},
            json={
                "strategy_id": str(uuid.uuid4()),
                "symbol": "SPY260417C00500000",
                "underlying_symbol": "SPY",
                "signal_type": "breakout",
                "direction": "bullish",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("Strategy", response.json()["detail"])

    def test_create_order_intent_route_happy_path(self) -> None:
        signal = build_signal()
        strategy = build_strategy(signal.strategy_id)

        def override_db() -> Iterator[FakeRouteSession]:
            yield FakeRouteSession(strategy=strategy, signal=signal)

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)

        response = client.post(
            "/api/v1/order-intents",
            headers={"Authorization": "Bearer change-me"},
            json={
                "strategy_id": str(strategy.id),
                "signal_id": str(signal.id),
                "underlying_symbol": "SPY",
                "option_symbol": "SPY260417C00500000",
                "side": "buy",
                "quantity": 1,
                "order_type": "limit",
                "limit_price": "1.25",
                "time_in_force": "day",
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["strategy_id"], str(strategy.id))
        self.assertEqual(response.json()["signal_id"], str(signal.id))

    def test_create_order_intent_returns_409_for_strategy_signal_mismatch(self) -> None:
        signal = build_signal()
        strategy = build_strategy()

        def override_db() -> Iterator[FakeRouteSession]:
            yield FakeRouteSession(strategy=strategy, signal=signal)

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)

        response = client.post(
            "/api/v1/order-intents",
            headers={"Authorization": "Bearer change-me"},
            json={
                "strategy_id": str(strategy.id),
                "signal_id": str(signal.id),
                "underlying_symbol": "SPY",
                "option_symbol": "SPY260417C00500000",
                "side": "buy",
                "quantity": 1,
                "order_type": "limit",
                "limit_price": "1.25",
                "time_in_force": "day",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("strategy_id", response.json()["detail"])

    def test_select_option_contract_route_returns_service_result(self) -> None:
        client = TestClient(app)
        result = build_option_contract_selection_result()

        with patch(
            "app.api.routes.options.select_option_contract",
            return_value=result,
        ):
            response = client.post(
                "/api/v1/options/select-contract",
                headers={"Authorization": "Bearer change-me"},
                json={
                    "underlying_symbol": "SPY",
                    "option_type": "call",
                    "target_strike": "500",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["selected_contract"]["symbol"],
            "SPY260417C00500000",
        )
        self.assertEqual(response.json()["quote"]["midpoint"], "1.25")

    def test_reconcile_broker_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = build_reconciliation_result()

        with patch(
            "app.api.routes.jobs.reconcile_broker_state",
            return_value=result,
        ) as reconcile:
            response = client.post(
                "/api/v1/jobs/reconcile-broker?order_limit=25&fill_page_size=50",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["orders_seen"], 2)
        self.assertEqual(response.json()["fills_created"], 1)
        reconcile.assert_called_once_with(db, order_limit=25, fill_page_size=50)

    def test_reconcile_broker_route_maps_configuration_error(self) -> None:
        def override_db() -> Iterator[FakeRouteSession]:
            yield FakeRouteSession()

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)

        with patch(
            "app.api.routes.jobs.reconcile_broker_state",
            side_effect=AlpacaTradingConfigurationError(
                "Alpaca API credentials are not configured"
            ),
        ):
            response = client.post(
                "/api/v1/jobs/reconcile-broker",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertIn("Alpaca API credentials", response.json()["detail"])

    def test_reconcile_broker_route_maps_alpaca_error(self) -> None:
        def override_db() -> Iterator[FakeRouteSession]:
            yield FakeRouteSession()

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)

        with patch(
            "app.api.routes.jobs.reconcile_broker_state",
            side_effect=AlpacaTradingError("Alpaca is unavailable"),
        ):
            response = client.post(
                "/api/v1/jobs/reconcile-broker",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "Alpaca is unavailable")

    def test_scan_signals_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = build_signal_scan_result()

        with patch(
            "app.api.routes.jobs.scan_signals",
            return_value=result,
        ) as scanner:
            response = client.post(
                "/api/v1/jobs/scan-signals?limit=25",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["strategies_seen"], 2)
        self.assertEqual(response.json()["signals_created"], 1)
        self.assertEqual(response.json()["no_signal_reasons"], ["No trigger"])
        scanner.assert_called_once_with(db, limit=25)

    def test_market_cycle_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = build_market_cycle_result()

        with patch(
            "app.api.routes.jobs.run_market_cycle",
            return_value=result,
        ) as market_cycle:
            response = client.post(
                "/api/v1/jobs/market-cycle?scan_limit=25&order_limit=50&fill_page_size=75",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["scan_enabled"])
        self.assertEqual(response.json()["scan"]["signals_created"], 1)
        market_cycle.assert_called_once_with(
            db,
            scan_limit=25,
            order_limit=50,
            fill_page_size=75,
            exit_enabled_override=False,
        )

    def test_market_cycle_stress_route_forces_no_submit_overrides(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = build_market_cycle_result()

        with patch(
            "app.api.routes.jobs.run_market_cycle",
            return_value=result,
        ) as market_cycle:
            response = client.post(
                "/api/v1/jobs/market-cycle-stress?scan_limit=130&order_limit=25&fill_page_size=25",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["submit_enabled"])
        self.assertEqual(response.json()["timings"]["total_seconds"], 1.23)
        market_cycle.assert_called_once_with(
            db,
            scan_limit=130,
            order_limit=25,
            fill_page_size=25,
            preview_enabled_override=True,
            reconcile_enabled_override=True,
            exit_enabled_override=False,
            news_enabled_override=False,
            submit_enabled_override=False,
        )

    def test_market_maintenance_route_returns_auto_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = build_market_maintenance_result("pre_market")

        with patch(
            "app.api.routes.jobs.run_market_maintenance",
            return_value=result,
        ) as maintenance:
            response = client.post(
                "/api/v1/jobs/market-maintenance?phase=auto&news_enabled=false",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["phase"], "pre_market")
        maintenance.assert_called_once_with(
            db,
            phase="auto",
            order_limit=None,
            fill_page_size=None,
            stale_after_hours=None,
            news_enabled=False,
        )

    def test_pre_market_maintenance_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = build_market_maintenance_result("pre_market")

        with patch(
            "app.api.routes.jobs.run_pre_market_maintenance",
            return_value=result,
        ) as maintenance:
            response = client.post(
                "/api/v1/jobs/pre-market-maintenance?order_limit=25&fill_page_size=50&stale_after_hours=8&news_enabled=false",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["phase"], "pre_market")
        self.assertEqual(response.json()["cleanup"]["signals_marked_stale"], 1)
        self.assertEqual(response.json()["reconcile"]["orders_seen"], 2)
        maintenance.assert_called_once_with(
            db,
            order_limit=25,
            fill_page_size=50,
            stale_after_hours=8,
            news_enabled=False,
        )

    def test_post_market_maintenance_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = build_market_maintenance_result("post_market")

        with patch(
            "app.api.routes.jobs.run_post_market_maintenance",
            return_value=result,
        ) as maintenance:
            response = client.post(
                "/api/v1/jobs/post-market-maintenance?order_limit=250&fill_page_size=250&stale_after_hours=0",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["phase"], "post_market")
        self.assertEqual(response.json()["performance"]["matched_round_trips"], 1)
        maintenance.assert_called_once_with(
            db,
            order_limit=250,
            fill_page_size=250,
            stale_after_hours=0,
        )

    def test_reset_trading_data_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = build_trading_data_reset_result()

        with patch(
            "app.api.routes.jobs.run_trading_data_reset",
            return_value=result,
        ) as reset:
            response = client.post(
                "/api/v1/jobs/reset-trading-data?dry_run=false&confirm=RESET_TRADING_DATA",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["dry_run"])
        self.assertEqual(response.json()["deleted"]["signals"], 5)
        reset.assert_called_once_with(
            db,
            dry_run=False,
            include_history=True,
            confirm="RESET_TRADING_DATA",
        )

    def test_evaluate_exits_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = ExitEvaluationResult(
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
            order_intent_ids=[uuid.uuid4()],
        )

        with patch(
            "app.api.routes.jobs.evaluate_position_exits",
            return_value=result,
        ) as exits:
            response = client.post(
                "/api/v1/jobs/evaluate-exits?limit=25",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["positions_seen"], 1)
        self.assertEqual(response.json()["exits_created"], 1)
        self.assertTrue(response.json()["position_ownership"][0]["managed"])
        exits.assert_called_once_with(db, limit=25)

    def test_preview_unmanaged_exits_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = ExitEvaluationResult(
            positions_seen=1,
            positions_evaluated=1,
            exits_created=1,
            exits_skipped=0,
            errors=[],
            no_exit_reasons=[],
            position_ownership=[
                {
                    "symbol": "SPY",
                    "managed": False,
                    "reason": "no linked entry order intent found",
                }
            ],
            order_intent_ids=[uuid.uuid4()],
        )

        with patch(
            "app.api.routes.jobs.preview_unmanaged_position_exits",
            return_value=result,
        ) as unmanaged_exits:
            response = client.post(
                "/api/v1/jobs/preview-unmanaged-exits?symbol=SPY&limit=25",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["exits_created"], 1)
        self.assertFalse(response.json()["position_ownership"][0]["managed"])
        unmanaged_exits.assert_called_once_with(db, symbol="SPY", limit=25)

    def test_check_news_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = build_news_scan_result()

        with patch(
            "app.api.routes.jobs.scan_market_news",
            return_value=result,
        ) as news_scan:
            response = client.post(
                "/api/v1/jobs/check-news?market_limit=3&ticker_limit=2",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["owned_symbols"], ["SPY"])
        self.assertEqual(response.json()["risk_assessment"]["market_risk_level"], "medium")
        self.assertEqual(response.json()["sources_checked"], 2)
        news_scan.assert_called_once_with(db, market_limit=3, ticker_limit=2)

    def test_automation_status_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = AutomationStatusRead(
            switches=AutomationSwitchesRead(
                scan_enabled=True,
                reconcile_enabled=True,
                preview_enabled=False,
                exit_enabled=False,
                news_enabled=False,
                submit_enabled=False,
            ),
            operational_summary={
                "effective_mode": "watching",
                "blockers": ["MARKET_CYCLE_PREVIEW_ENABLED is false"],
                "news_gate": {
                    "enabled": False,
                    "should_block_new_entries": False,
                    "blocking_reasons": [],
                    "manual_review_symbols": [],
                },
                "last_preview": {},
                "last_submit": {},
            },
            trading_automation_enabled=False,
            auto_submit_requires_paper=True,
            paper_mode=True,
            max_auto_orders_per_cycle=1,
            max_auto_orders_per_day=3,
            max_open_positions=3,
            max_open_positions_per_symbol=1,
            max_contracts_per_order=1,
            max_estimated_premium_per_order=Decimal("250"),
            active_strategies=[],
            latest_job_runs={
                "market_cycle": None,
                "scan_signals": None,
                "reconcile_broker": None,
            },
        )

        with patch(
            "app.api.routes.automation.get_automation_status",
            return_value=result,
        ) as automation_status:
            response = client.get(
                "/api/v1/automation/status",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["switches"]["scan_enabled"])
        self.assertFalse(response.json()["trading_automation_enabled"])
        self.assertEqual(response.json()["max_auto_orders_per_day"], 3)
        self.assertEqual(response.json()["active_strategies"], [])
        automation_status.assert_called_once_with(db)

    def test_position_management_status_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = [
            {
                "symbol": "SPY",
                "quantity": "100",
                "market_value": "71188.00",
                "cost_basis": "71343.00",
                "unrealized_pl": "-155.00",
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "ownership": {
                    "symbol": "SPY",
                    "managed": False,
                    "reason": "no linked entry order intent found",
                },
                "exit_config_enabled": False,
                "active_exit_order": None,
                "recommended_action": "preview_unmanaged_exit",
                "reason": "no linked entry order intent found",
            }
        ]

        with patch(
            "app.api.routes.automation.get_position_management_statuses",
            return_value=result,
        ) as positions:
            response = client.get(
                "/api/v1/automation/positions?limit=25",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["symbol"], "SPY")
        self.assertEqual(response.json()[0]["recommended_action"], "preview_unmanaged_exit")
        positions.assert_called_once_with(db, limit=25)

    def test_paper_performance_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        now = datetime.now(timezone.utc)
        result = PerformanceReviewResult(
            generated_at=now,
            fills_seen=2,
            matched_round_trips=1,
            open_positions=[],
            totals={"realized_pnl": "35", "win_rate_percent": "100"},
            by_strategy=[
                {
                    "strategy_name": "Confirmed Trend",
                    "matched_round_trips": 1,
                    "realized_pnl": "35",
                }
            ],
            by_symbol=[
                {
                    "symbol": "SPY260501C00500000",
                    "matched_round_trips": 1,
                    "realized_pnl": "35",
                }
            ],
            recent_round_trips=[
                {
                    "symbol": "SPY260501C00500000",
                    "realized_pnl": "35",
                    "entry_at": now.isoformat(),
                    "exit_at": now.isoformat(),
                }
            ],
        )

        with patch(
            "app.api.routes.automation.get_paper_performance_review",
            return_value=result,
        ) as performance:
            response = client.get(
                "/api/v1/automation/performance?limit=25",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["fills_seen"], 2)
        self.assertEqual(response.json()["totals"]["realized_pnl"], "35")
        performance.assert_called_once_with(db, limit=25)

    def test_trade_lifecycle_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        now = datetime.now(timezone.utc)
        result = TradeLifecycleResult(
            generated_at=now,
            positions_seen=1,
            managed_positions=1,
            unmanaged_positions=0,
            positions=[
                {
                    "symbol": "SPY260501C00500000",
                    "ownership": {"managed": True},
                    "entry_order_intent": {"id": str(uuid.uuid4())},
                    "entry_fill_summary": {"filled_notional": "100"},
                    "recommended_action": "hold",
                }
            ],
        )

        with patch(
            "app.api.routes.automation.get_trade_lifecycle",
            return_value=result,
        ) as lifecycle:
            response = client.get(
                "/api/v1/automation/trade-lifecycle?limit=25",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["positions_seen"], 1)
        self.assertEqual(response.json()["positions"][0]["recommended_action"], "hold")
        lifecycle.assert_called_once_with(db, limit=25)

    def test_trade_cases_route_returns_service_result(self) -> None:
        db = FakeRouteSession()

        def override_db() -> Iterator[FakeRouteSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        now = datetime.now(timezone.utc)
        result = TradeCasesResult(
            generated_at=now,
            fills_seen=2,
            matched_round_trips=1,
            open_positions=[],
            recent_round_trips=[{"symbol": "SPY260501C00500000", "realized_pnl": "35"}],
            totals={"realized_pnl": "35"},
            by_strategy=[{"strategy_name": "Confirmed Trend", "realized_pnl": "35"}],
            by_symbol=[{"symbol": "SPY260501C00500000", "realized_pnl": "35"}],
        )

        with patch(
            "app.api.routes.automation.get_trade_cases",
            return_value=result,
        ) as trade_cases:
            response = client.get(
                "/api/v1/automation/trade-cases?limit=25",
                headers={"Authorization": "Bearer change-me"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["matched_round_trips"], 1)
        self.assertEqual(response.json()["by_symbol"][0]["realized_pnl"], "35")
        trade_cases.assert_called_once_with(db, limit=25)


def build_reconciliation_result() -> BrokerReconciliationResult:
    now = datetime.now(timezone.utc)
    job_run = JobRun(
        id=uuid.uuid4(),
        job_name="reconcile_broker",
        status="succeeded",
        started_at=now,
        finished_at=now,
        details={"orders_seen": 2},
        error=None,
        created_at=now,
    )

    return BrokerReconciliationResult(
        job_run=job_run,
        orders_seen=2,
        orders_created=1,
        orders_updated=1,
        fills_seen=1,
        fills_created=1,
        positions_seen=1,
        position_snapshots_created=1,
    )


def build_signal_scan_result() -> SignalScanResult:
    now = datetime.now(timezone.utc)
    job_run = JobRun(
        id=uuid.uuid4(),
        job_name="scan_signals",
        status="succeeded",
        started_at=now,
        finished_at=now,
        details={"signals_created": 1},
        error=None,
        created_at=now,
    )

    return SignalScanResult(
        job_run=job_run,
        strategies_seen=2,
        strategies_scanned=1,
        signals_created=1,
        signals_skipped=1,
        errors=["Strategy skipped"],
        no_signal_reasons=["No trigger"],
        created_signal_ids=[uuid.uuid4()],
    )


def build_market_cycle_result() -> MarketCycleResult:
    now = datetime.now(timezone.utc)
    job_run = JobRun(
        id=uuid.uuid4(),
        job_name="market_cycle",
        status="succeeded",
        started_at=now,
        finished_at=now,
        details={"scan": {"signals_created": 1}},
        error=None,
        created_at=now,
    )

    return MarketCycleResult(
        job_run=job_run,
        scan_enabled=True,
        reconcile_enabled=True,
        preview_enabled=False,
        exit_enabled=False,
        news_enabled=False,
        submit_enabled=False,
        scan={"signals_created": 1},
        reconcile={"orders_seen": 2},
        preview={"status": "disabled"},
        exits={"status": "disabled"},
        news={"status": "disabled"},
        submit={"status": "disabled"},
        timings={"total_seconds": 1.23},
    )


def build_market_maintenance_result(phase: str) -> MarketMaintenanceResult:
    now = datetime.now(timezone.utc)
    job_run = JobRun(
        id=uuid.uuid4(),
        job_name=f"{phase}_maintenance",
        status="succeeded",
        started_at=now,
        finished_at=now,
        details={},
        error=None,
        created_at=now,
    )

    return MarketMaintenanceResult(
        job_run=job_run,
        phase=phase,
        cleanup={
            "signals_marked_stale": 1,
            "order_intents_marked_stale": 1,
            "signal_ids": [str(uuid.uuid4())],
            "order_intent_ids": [str(uuid.uuid4())],
        },
        reconcile={"orders_seen": 2, "fills_seen": 1, "positions_seen": 1},
        news={"status": "disabled"} if phase == "pre_market" else None,
        performance={
            "fills_seen": 2,
            "matched_round_trips": 1,
            "totals": {"realized_pnl": "25"},
        }
        if phase == "post_market"
        else None,
        readiness={
            "active_strategies": 1,
            "preview_enabled_strategies": 1,
            "submit_enabled_strategies": 1,
        },
        settings_snapshot={"paper_mode": True},
    )


def build_trading_data_reset_result() -> TradingDataResetResult:
    now = datetime.now(timezone.utc)
    job_run = JobRun(
        id=uuid.uuid4(),
        job_name="trading_data_reset",
        status="succeeded",
        started_at=now,
        finished_at=now,
        details={},
        error=None,
        created_at=now,
    )

    return TradingDataResetResult(
        job_run=job_run,
        dry_run=False,
        include_history=True,
        counts_before={
            "fills": 2,
            "broker_orders": 3,
            "order_intents": 4,
            "signals": 5,
            "position_snapshots": 6,
            "audit_logs": 7,
            "job_runs": 8,
        },
        deleted={
            "fills": 2,
            "broker_orders": 3,
            "order_intents": 4,
            "signals": 5,
            "position_snapshots": 6,
            "audit_logs": 7,
            "job_runs": 8,
        },
        kept_tables=["strategies"],
        confirmation_phrase="RESET_TRADING_DATA",
    )


def build_news_scan_result() -> NewsScanResult:
    now = datetime.now(timezone.utc)
    job_run = JobRun(
        id=uuid.uuid4(),
        job_name="news_scan",
        status="succeeded",
        started_at=now,
        finished_at=now,
        details={},
        error=None,
        created_at=now,
    )
    return NewsScanResult(
        job_run=job_run,
        market_items=[
            {
                "title": "Fed rates move stocks",
                "url": "https://example.test/market",
                "source": "Example",
                "published_at": now.isoformat(),
                "impact_keywords": ["fed", "rate"],
            }
        ],
        ticker_items={"SPY": []},
        owned_symbols=["SPY"],
        risk_assessment={
            "market_risk_level": "medium",
            "market_impact_keywords": ["fed", "rate"],
            "should_block_new_entries": False,
            "manual_review_symbols": [],
            "ticker_risks": {"SPY": {"risk_level": "low", "impact_keywords": [], "reasons": []}},
            "reasons": ["market: Fed rates move stocks"],
        },
        sources_checked=2,
        errors=[],
    )


def build_option_contract_selection_result() -> OptionContractSelectionRead:
    now = datetime.now(timezone.utc)
    return OptionContractSelectionRead(
        selected_contract=OptionContractRead(
            id="contract-id",
            symbol="SPY260417C00500000",
            name="SPY Apr 17 2026 500 Call",
            status="active",
            tradable=True,
            expiration_date=now.date(),
            root_symbol="SPY",
            underlying_symbol="SPY",
            option_type="call",
            style="american",
            strike_price=Decimal("500"),
            size=Decimal("100"),
            open_interest=None,
            open_interest_date=None,
            close_price=None,
            close_price_date=None,
        ),
        quote={
            "bid_price": "1.20",
            "ask_price": "1.30",
            "midpoint": "1.25",
        },
        selection_reason="Selected test contract",
        candidates_seen=3,
        selected_at=now,
    )


if __name__ == "__main__":
    unittest.main()
