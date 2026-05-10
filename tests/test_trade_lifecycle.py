from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest
import uuid

from app.db.models import BrokerOrder, Fill, JobRun, OrderIntent, PositionSnapshot, Strategy
from app.services.trade_lifecycle import get_trade_cases, get_trade_lifecycle


class FakeTradeLifecycleSession:
    def __init__(
        self,
        *,
        positions: list[PositionSnapshot],
        strategy: Strategy,
        entry_order_intent: OrderIntent,
        broker_orders: list[BrokerOrder],
        fills: list[Fill],
        latest_reconciliation: JobRun | None = None,
    ) -> None:
        self.positions = positions
        self.strategy = strategy
        self.entry_order_intent = entry_order_intent
        self.broker_orders = broker_orders
        self.fills = fills
        self.latest_reconciliation = latest_reconciliation
        self.scalar_calls = 0
        self.scalars_calls = 0

    def scalars(self, _: object) -> list[object]:
        self.scalars_calls += 1
        if self.scalars_calls == 1:
            return self.positions
        if self.scalars_calls == 2:
            return self.broker_orders
        return self.fills

    def scalar(self, _: object) -> object | None:
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return self.latest_reconciliation
        if self.scalar_calls == 2:
            return self.entry_order_intent
        return None

    def get(self, model: type, record_id: uuid.UUID) -> object | None:
        if model is Strategy and record_id == self.strategy.id:
            return self.strategy
        if model is OrderIntent and record_id == self.entry_order_intent.id:
            return self.entry_order_intent
        return None


class FakeTradeCasesSession:
    def __init__(self, rows: list[tuple]) -> None:
        self.result_sets = [rows, [], [], [], []]

    def execute(self, _: object) -> list[tuple]:
        if not self.result_sets:
            return []
        return self.result_sets.pop(0)


def build_strategy() -> Strategy:
    now = datetime.now(timezone.utc)
    return Strategy(
        id=uuid.uuid4(),
        name="Lifecycle Strategy",
        description="Test",
        is_active=True,
        config={
            "scanner": {
                "exit": {
                    "enabled": True,
                    "profit_target_percent": "30",
                    "stop_loss_percent": "20",
                    "max_days_to_expiration": 1,
                }
            }
        },
        created_at=now,
        updated_at=now,
    )


def build_entry_intent(strategy: Strategy) -> OrderIntent:
    now = datetime.now(timezone.utc)
    return OrderIntent(
        id=uuid.uuid4(),
        strategy_id=strategy.id,
        signal_id=None,
        underlying_symbol="SPY",
        option_symbol="SPY260619C00500000",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=Decimal("1.00"),
        time_in_force="day",
        status="submitted",
        rationale="Lifecycle entry",
        preview={},
        submitted_at=now,
        created_at=now,
        updated_at=now,
    )


def build_position() -> PositionSnapshot:
    now = datetime.now(timezone.utc)
    return PositionSnapshot(
        id=uuid.uuid4(),
        symbol="SPY260619C00500000",
        quantity=Decimal("1"),
        market_value=Decimal("125"),
        cost_basis=Decimal("100"),
        unrealized_pl=Decimal("25"),
        raw_position={},
        captured_at=now,
        created_at=now,
    )


def build_broker_order(order_intent: OrderIntent) -> BrokerOrder:
    now = datetime.now(timezone.utc)
    return BrokerOrder(
        id=uuid.uuid4(),
        order_intent_id=order_intent.id,
        alpaca_order_id="alpaca-entry-1",
        symbol=order_intent.option_symbol,
        side="buy",
        quantity=Decimal("1"),
        order_type="limit",
        limit_price=Decimal("1.00"),
        status="filled",
        submitted_at=now,
        filled_at=now,
        raw_response={},
        created_at=now,
        updated_at=now,
    )


def build_fill(broker_order: BrokerOrder) -> Fill:
    now = datetime.now(timezone.utc)
    return Fill(
        id=uuid.uuid4(),
        broker_order_id=broker_order.id,
        alpaca_fill_id="fill-entry-1",
        symbol=broker_order.symbol,
        side="buy",
        quantity=Decimal("1"),
        price=Decimal("1.00"),
        filled_at=now,
        raw_response={},
        created_at=now,
    )


class TradeLifecycleTests(unittest.TestCase):
    def test_get_trade_lifecycle_links_position_to_entry_intent_order_and_fills(self) -> None:
        strategy = build_strategy()
        entry_intent = build_entry_intent(strategy)
        broker_order = build_broker_order(entry_intent)
        fill = build_fill(broker_order)
        db = FakeTradeLifecycleSession(
            positions=[build_position()],
            strategy=strategy,
            entry_order_intent=entry_intent,
            broker_orders=[broker_order],
            fills=[fill],
        )

        result = get_trade_lifecycle(db)

        self.assertEqual(result.positions_seen, 1)
        self.assertEqual(result.managed_positions, 1)
        position = result.positions[0]
        self.assertEqual(position["symbol"], "SPY260619C00500000")
        self.assertEqual(position["underlying_symbol"], "SPY")
        self.assertTrue(position["ownership"]["managed"])
        self.assertEqual(position["entry_order_intent"]["id"], str(entry_intent.id))
        self.assertEqual(position["entry_broker_orders"][0]["alpaca_order_id"], "alpaca-entry-1")
        self.assertEqual(position["entry_fills"][0]["alpaca_fill_id"], "fill-entry-1")
        self.assertEqual(position["entry_fill_summary"]["filled_notional"], "100")
        self.assertEqual(position["recommended_action"], "hold")

    def test_get_trade_cases_wraps_performance_review(self) -> None:
        strategy_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        fill_id_1 = uuid.uuid4()
        fill_id_2 = uuid.uuid4()
        db = FakeTradeCasesSession(
            [
                (
                    fill_id_1,
                    now,
                    "SPY260501C00500000",
                    "buy",
                    Decimal("1"),
                    Decimal("1.00"),
                    strategy_id,
                    "Lifecycle Strategy",
                    uuid.uuid4(),
                ),
                (
                    fill_id_2,
                    now,
                    "SPY260501C00500000",
                    "sell",
                    Decimal("1"),
                    Decimal("1.25"),
                    strategy_id,
                    "Lifecycle Strategy",
                    uuid.uuid4(),
                ),
            ]
        )

        result = get_trade_cases(db)

        self.assertEqual(result.matched_round_trips, 1)
        self.assertEqual(result.totals["realized_pnl"], "25")
        self.assertEqual(result.by_symbol[0]["symbol"], "SPY260501C00500000")
        self.assertEqual(result.recent_round_trips[0]["realized_pnl"], "25")


if __name__ == "__main__":
    unittest.main()
