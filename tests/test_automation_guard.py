from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from app.db.models import BrokerOrder, OrderIntent
from app.services.automation_guard import can_auto_submit_order_intent


class FakeAutomationGuardSession:
    def __init__(self, *, scalar_results: list[object | None] | None = None) -> None:
        self.scalar_results = scalar_results or []

    def scalar(self, _: object) -> object | None:
        if self.scalar_results:
            return self.scalar_results.pop(0)
        return 0

    def get(self, *_: object) -> object | None:
        return None


def build_order_intent() -> OrderIntent:
    return OrderIntent(
        id=uuid.uuid4(),
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
        submitted_at=datetime.now(timezone.utc),
        raw_response={},
    )


class AutomationGuardTests(unittest.TestCase):
    def allowed_settings(self):
        return patch.multiple(
            "app.services.automation_guard.settings",
            trading_automation_enabled=True,
            market_cycle_submit_enabled=True,
            auto_submit_requires_paper=True,
            alpaca_paper=True,
            max_auto_orders_per_cycle=1,
            max_auto_orders_per_day=3,
            max_open_positions=3,
            max_open_positions_per_symbol=1,
            max_contracts_per_order=1,
            max_estimated_premium_per_order=Decimal("250"),
        )

    def test_blocks_when_trading_automation_disabled(self) -> None:
        with self.allowed_settings(), patch(
            "app.services.automation_guard.settings.trading_automation_enabled",
            False,
        ):
            decision = can_auto_submit_order_intent(
                FakeAutomationGuardSession(scalar_results=[0, 0, 0, 0]),
                build_order_intent(),
            )

        self.assertFalse(decision.allowed)
        self.assertIn("TRADING_AUTOMATION_ENABLED is false", decision.reasons)

    def test_blocks_when_paper_required_and_alpaca_paper_false(self) -> None:
        with self.allowed_settings(), patch(
            "app.services.automation_guard.settings.alpaca_paper",
            False,
        ):
            decision = can_auto_submit_order_intent(
                FakeAutomationGuardSession(scalar_results=[0, 0, 0, 0]),
                build_order_intent(),
            )

        self.assertFalse(decision.allowed)
        self.assertIn(
            "AUTO_SUBMIT_REQUIRES_PAPER is true and ALPACA_PAPER is false",
            decision.reasons,
        )

    def test_blocks_when_order_intent_status_is_not_previewed(self) -> None:
        order_intent = build_order_intent()
        order_intent.status = "submitted"

        with self.allowed_settings():
            decision = can_auto_submit_order_intent(
                FakeAutomationGuardSession(scalar_results=[0, 0, 0, 0]),
                order_intent,
            )

        self.assertFalse(decision.allowed)
        self.assertIn("order intent status is not previewed", decision.reasons)

    def test_blocks_when_order_intent_already_has_broker_order(self) -> None:
        order_intent = build_order_intent()
        order_intent.broker_orders = [build_broker_order(order_intent)]

        with self.allowed_settings():
            decision = can_auto_submit_order_intent(
                FakeAutomationGuardSession(scalar_results=[0, 0, 0]),
                order_intent,
            )

        self.assertFalse(decision.allowed)
        self.assertIn("order intent already has a broker_order", decision.reasons)

    def test_blocks_when_quantity_exceeds_max_contracts_per_order(self) -> None:
        order_intent = build_order_intent()
        order_intent.quantity = 2

        with self.allowed_settings():
            decision = can_auto_submit_order_intent(
                FakeAutomationGuardSession(scalar_results=[0, 0, 0, 0]),
                order_intent,
            )

        self.assertFalse(decision.allowed)
        self.assertIn(
            "order intent quantity exceeds MAX_CONTRACTS_PER_ORDER",
            decision.reasons,
        )

    def test_blocks_when_estimated_premium_exceeds_limit(self) -> None:
        order_intent = build_order_intent()
        order_intent.limit_price = Decimal("3.00")

        with self.allowed_settings():
            decision = can_auto_submit_order_intent(
                FakeAutomationGuardSession(scalar_results=[0, 0, 0, 0]),
                order_intent,
            )

        self.assertFalse(decision.allowed)
        self.assertIn(
            "estimated premium exceeds MAX_ESTIMATED_PREMIUM_PER_ORDER",
            decision.reasons,
        )
        self.assertEqual(decision.limits_snapshot["estimated_premium"], "300.00")

    def test_blocks_when_max_daily_auto_orders_is_reached(self) -> None:
        with self.allowed_settings():
            decision = can_auto_submit_order_intent(
                FakeAutomationGuardSession(scalar_results=[3, 0, 0, 0]),
                build_order_intent(),
            )

        self.assertFalse(decision.allowed)
        self.assertIn("MAX_AUTO_ORDERS_PER_DAY reached", decision.reasons)

    def test_allows_when_all_gates_pass(self) -> None:
        with self.allowed_settings():
            decision = can_auto_submit_order_intent(
                FakeAutomationGuardSession(scalar_results=[0, 0, 0, 0]),
                build_order_intent(),
            )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reasons, [])
        self.assertTrue(decision.limits_snapshot["price_available"])


if __name__ == "__main__":
    unittest.main()
