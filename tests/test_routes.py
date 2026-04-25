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
from app.services.broker_reconciliation import BrokerReconciliationResult


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
        bad_auth = client.get(
            "/api/v1/order-intents",
            headers={"Authorization": "Bearer wrong-token"},
        )

        self.assertEqual(missing_auth.status_code, 401)
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
