from __future__ import annotations

import unittest
import uuid
from decimal import Decimal

from pydantic import ValidationError

from app.db.models import OrderIntent
from app.integrations.alpaca import (
    AlpacaOrderRejectedError,
    AlpacaOrderSubmission,
    AlpacaSubmittedOrder,
)
from app.schemas.order_intents import OrderIntentCreate
from app.services.order_intents import submit_order_intent


class FakeSession:
    def __init__(self, order_intent: OrderIntent | None) -> None:
        self.order_intent = order_intent
        self.added: list[object] = []
        self.commit_count = 0

    def get(self, model: type[OrderIntent], order_intent_id: uuid.UUID) -> OrderIntent | None:
        if self.order_intent is None:
            return None
        if model is not OrderIntent:
            return None
        if self.order_intent.id != order_intent_id:
            return None
        return self.order_intent

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commit_count += 1

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class SuccessfulTradingClient:
    def submit_order_intent(self, order_intent: OrderIntent) -> AlpacaOrderSubmission:
        return AlpacaOrderSubmission(
            order=AlpacaSubmittedOrder.model_validate(
                {
                    "id": "alpaca-order-123",
                    "client_order_id": str(order_intent.id),
                    "symbol": order_intent.option_symbol,
                    "qty": str(order_intent.quantity),
                    "side": order_intent.side,
                    "type": order_intent.order_type,
                    "limit_price": str(order_intent.limit_price),
                    "status": "new",
                    "submitted_at": "2026-04-23T16:00:00Z",
                }
            ),
            raw_response={
                "id": "alpaca-order-123",
                "client_order_id": str(order_intent.id),
                "symbol": order_intent.option_symbol,
                "qty": str(order_intent.quantity),
                "side": order_intent.side,
                "type": order_intent.order_type,
                "limit_price": str(order_intent.limit_price),
                "status": "new",
                "submitted_at": "2026-04-23T16:00:00Z",
            },
        )


class RejectedTradingClient:
    def submit_order_intent(self, _: OrderIntent) -> AlpacaOrderSubmission:
        raise AlpacaOrderRejectedError(
            "insufficient options buying power",
            status_code=403,
        )


def build_previewed_order_intent() -> OrderIntent:
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


class OrderIntentSubmissionTests(unittest.TestCase):
    def test_submit_previewed_order_intent_creates_broker_order(self) -> None:
        order_intent = build_previewed_order_intent()
        db = FakeSession(order_intent)

        updated_order_intent, broker_order = submit_order_intent(
            db,
            order_intent.id,
            trading_client=SuccessfulTradingClient(),
        )

        self.assertEqual(updated_order_intent.status, "new")
        self.assertEqual(updated_order_intent.submitted_at.isoformat(), "2026-04-23T16:00:00+00:00")
        self.assertEqual(broker_order.alpaca_order_id, "alpaca-order-123")
        self.assertEqual(broker_order.status, "new")
        self.assertEqual(broker_order.symbol, order_intent.option_symbol)
        self.assertEqual(db.commit_count, 1)

    def test_broker_rejection_marks_order_intent_rejected(self) -> None:
        order_intent = build_previewed_order_intent()
        db = FakeSession(order_intent)

        with self.assertRaises(AlpacaOrderRejectedError):
            submit_order_intent(
                db,
                order_intent.id,
                trading_client=RejectedTradingClient(),
            )

        self.assertEqual(order_intent.status, "rejected")
        self.assertEqual(order_intent.rejection_reason, "insufficient options buying power")
        self.assertEqual(db.commit_count, 1)

    def test_order_intent_create_matches_supported_options_rules(self) -> None:
        valid_payload = OrderIntentCreate(
            underlying_symbol="SPY",
            option_symbol="SPY260417C00500000",
            side="buy",
            quantity=1,
            order_type="limit",
            limit_price=Decimal("1.25"),
            time_in_force="day",
        )
        self.assertEqual(valid_payload.time_in_force, "day")

        with self.assertRaises(ValidationError):
            OrderIntentCreate(
                underlying_symbol="SPY",
                option_symbol="SPY260417C00500000",
                side="buy",
                quantity=1,
                order_type="limit",
                limit_price=Decimal("1.25"),
                time_in_force="gtc",
            )

        with self.assertRaises(ValidationError):
            OrderIntentCreate(
                underlying_symbol="SPY",
                option_symbol="SPY260417C00500000",
                side="buy",
                quantity=1,
                order_type="market",
                limit_price=Decimal("1.25"),
                time_in_force="day",
            )


if __name__ == "__main__":
    unittest.main()
