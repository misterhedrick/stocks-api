from __future__ import annotations

import unittest
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.db.models import OrderIntent, Signal, Strategy
from app.db.session import get_db
from app.main import app


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

    def refresh(self, obj: object) -> None:
        _hydrate_model_defaults(obj)


def _hydrate_model_defaults(obj: object) -> None:
    if hasattr(obj, "id") and getattr(obj, "id", None) is None:
        obj.id = uuid.uuid4()

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


if __name__ == "__main__":
    unittest.main()
