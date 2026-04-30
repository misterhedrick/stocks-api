from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.db.models import AuditLog, OrderIntent, PositionSnapshot, Strategy
from app.integrations.alpaca import (
    AlpacaLatestOptionQuote,
    AlpacaOptionQuote,
)
from app.services.position_exits import (
    evaluate_position_exits,
    get_position_management_statuses,
    preview_unmanaged_position_exits,
)


class FakePositionExitSession:
    def __init__(
        self,
        *,
        positions: list[PositionSnapshot],
        strategy: Strategy | None,
        entry_order_intent: OrderIntent | None = None,
        active_exit_count: int | None = 0,
        active_exit_order: OrderIntent | None = None,
    ) -> None:
        self.positions = positions
        self.strategy = strategy
        self.entry_order_intent = entry_order_intent
        self.active_exit_count = active_exit_count
        self.active_exit_order = active_exit_order
        self.added: list[object] = []
        self.commit_count = 0
        self.flush_count = 0
        self.scalar_calls = 0

    def scalars(self, _: object) -> list[PositionSnapshot]:
        return self.positions

    def scalar(self, _: object) -> object | None:
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return self.entry_order_intent
        if self.scalar_calls == 2 and self.active_exit_count is None:
            return self.active_exit_order
        return self.active_exit_count

    def get(self, model: type, record_id: uuid.UUID) -> object | None:
        if model is Strategy and self.strategy is not None and self.strategy.id == record_id:
            return self.strategy
        return None

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flush_count += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    def commit(self) -> None:
        self.commit_count += 1

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class SuccessfulMarketDataClient:
    def get_latest_option_quote(
        self,
        symbol: str,
        *,
        feed: str,
    ) -> AlpacaLatestOptionQuote:
        return AlpacaLatestOptionQuote(
            symbol=symbol,
            quote=AlpacaOptionQuote.model_validate(
                {
                    "bp": "1.20",
                    "bs": "10",
                    "ap": "1.30",
                    "as": "12",
                    "t": "2026-04-23T16:00:00Z",
                }
            ),
            raw_response={
                "bp": "1.20",
                "bs": "10",
                "ap": "1.30",
                "as": "12",
                "t": "2026-04-23T16:00:00Z",
            },
        )


def build_position(
    *,
    symbol: str = "SPY260429C00500000",
    unrealized_pl: Decimal = Decimal("35"),
    cost_basis: Decimal = Decimal("100"),
) -> PositionSnapshot:
    now = datetime.now(timezone.utc)
    return PositionSnapshot(
        id=uuid.uuid4(),
        symbol=symbol,
        quantity=Decimal("1"),
        market_value=cost_basis + unrealized_pl,
        cost_basis=cost_basis,
        unrealized_pl=unrealized_pl,
        raw_position={},
        captured_at=now,
        created_at=now,
    )


def build_strategy() -> Strategy:
    now = datetime.now(timezone.utc)
    return Strategy(
        id=uuid.uuid4(),
        name="Exit Strategy",
        description="Test strategy",
        is_active=True,
        config={
            "scanner": {
                "exit": {
                    "enabled": True,
                    "profit_target_percent": "30",
                    "stop_loss_percent": "20",
                    "max_days_to_expiration": 1,
                    "max_contracts_per_exit": 1,
                    "order_type": "limit",
                    "limit_price_source": "bid",
                    "time_in_force": "day",
                    "data_feed": "indicative",
                    "max_spread": "0.25",
                    "submit": {
                        "enabled": True,
                        "max_orders_per_cycle": 1,
                        "max_contracts_per_order": 1,
                        "allowed_sides": ["sell"],
                    },
                }
            }
        },
        created_at=now,
        updated_at=now,
    )


def build_entry_order_intent(strategy: Strategy) -> OrderIntent:
    return OrderIntent(
        id=uuid.uuid4(),
        strategy_id=strategy.id,
        signal_id=None,
        underlying_symbol="SPY",
        option_symbol="SPY260429C00500000",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=Decimal("1.00"),
        time_in_force="day",
        status="filled",
        preview={"source": "test_entry"},
    )


def build_exit_order_intent() -> OrderIntent:
    now = datetime.now(timezone.utc)
    return OrderIntent(
        id=uuid.uuid4(),
        strategy_id=None,
        signal_id=None,
        underlying_symbol="SPY",
        option_symbol="SPY260429C00500000",
        side="sell",
        quantity=1,
        order_type="limit",
        limit_price=Decimal("1.20"),
        time_in_force="day",
        status="previewed",
        preview={"source": "position_exit_evaluator"},
        created_at=now,
        updated_at=now,
    )


class PositionExitTests(unittest.TestCase):
    def test_evaluate_position_exits_creates_sell_intent_for_profit_target(self) -> None:
        strategy = build_strategy()
        position = build_position()
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
        )

        result = evaluate_position_exits(
            db,
            market_data_client=SuccessfulMarketDataClient(),
        )

        self.assertEqual(result.positions_seen, 1)
        self.assertEqual(result.positions_evaluated, 1)
        self.assertEqual(result.exits_created, 1)
        self.assertEqual(result.exits_skipped, 0)
        self.assertTrue(result.position_ownership[0]["managed"])
        self.assertEqual(result.position_ownership[0]["strategy_name"], strategy.name)
        order_intents = [item for item in db.added if isinstance(item, OrderIntent)]
        self.assertEqual(order_intents[-1].side, "sell")
        self.assertEqual(order_intents[-1].limit_price, Decimal("1.20"))
        self.assertEqual(order_intents[-1].preview["source"], "position_exit_evaluator")
        self.assertIn("profit_target_percent", order_intents[-1].preview["trigger_reason"])
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "order_intent.exit_previewed")
        self.assertEqual(db.commit_count, 1)

    def test_evaluate_position_exits_skips_when_no_rule_triggers(self) -> None:
        strategy = build_strategy()
        position = build_position(
            symbol="SPY260619C00500000",
            unrealized_pl=Decimal("5"),
            cost_basis=Decimal("100"),
        )
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
        )

        result = evaluate_position_exits(
            db,
            market_data_client=SuccessfulMarketDataClient(),
        )

        self.assertEqual(result.exits_created, 0)
        self.assertEqual(result.no_exit_reasons, [f"{position.symbol}: no exit rule triggered"])
        self.assertEqual(db.commit_count, 0)

    def test_evaluate_position_exits_skips_duplicate_active_exit(self) -> None:
        strategy = build_strategy()
        position = build_position()
        entry_order_intent = build_entry_order_intent(strategy)
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=entry_order_intent,
            active_exit_count=1,
        )

        result = evaluate_position_exits(
            db,
            market_data_client=SuccessfulMarketDataClient(),
        )

        self.assertEqual(result.exits_created, 0)
        self.assertEqual(result.exits_skipped, 1)
        self.assertIn("active exit order already exists", result.no_exit_reasons[0])

    def test_evaluate_position_exits_reports_inactive_strategy_ownership(self) -> None:
        strategy = build_strategy()
        strategy.is_active = False
        position = build_position()
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
        )

        result = evaluate_position_exits(
            db,
            market_data_client=SuccessfulMarketDataClient(),
        )

        self.assertEqual(result.exits_created, 0)
        self.assertFalse(result.position_ownership[0]["managed"])
        self.assertEqual(result.position_ownership[0]["strategy_name"], strategy.name)
        self.assertIn("is inactive", result.no_exit_reasons[0])

    def test_evaluate_position_exits_reports_unlinked_positions(self) -> None:
        position = build_position(symbol="SPY")
        db = FakePositionExitSession(
            positions=[position],
            strategy=None,
            entry_order_intent=None,
            active_exit_count=None,
        )

        result = evaluate_position_exits(
            db,
            market_data_client=SuccessfulMarketDataClient(),
        )

        self.assertEqual(result.exits_created, 0)
        self.assertFalse(result.position_ownership[0]["managed"])
        self.assertEqual(
            result.position_ownership[0]["reason"],
            "no linked entry order intent found",
        )

    def test_preview_unmanaged_position_exits_creates_manual_sell_preview(self) -> None:
        position = build_position()
        db = FakePositionExitSession(
            positions=[position],
            strategy=None,
            entry_order_intent=None,
            active_exit_count=None,
        )

        result = preview_unmanaged_position_exits(
            db,
            market_data_client=SuccessfulMarketDataClient(),
        )

        self.assertEqual(result.positions_seen, 1)
        self.assertEqual(result.positions_evaluated, 1)
        self.assertEqual(result.exits_created, 1)
        self.assertFalse(result.position_ownership[0]["managed"])
        order_intents = [item for item in db.added if isinstance(item, OrderIntent)]
        self.assertIsNone(order_intents[-1].strategy_id)
        self.assertEqual(order_intents[-1].side, "sell")
        self.assertEqual(order_intents[-1].limit_price, Decimal("1.20"))
        self.assertIn("manual unmanaged exit preview", order_intents[-1].preview["trigger_reason"])

    def test_preview_unmanaged_position_exits_skips_managed_positions(self) -> None:
        strategy = build_strategy()
        position = build_position()
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
            active_exit_count=None,
        )

        result = preview_unmanaged_position_exits(
            db,
            market_data_client=SuccessfulMarketDataClient(),
        )

        self.assertEqual(result.exits_created, 0)
        self.assertEqual(result.no_exit_reasons, [f"{position.symbol}: position is already managed"])

    def test_get_position_management_statuses_reports_unmanaged_position(self) -> None:
        position = build_position(symbol="SPY")
        db = FakePositionExitSession(
            positions=[position],
            strategy=None,
            entry_order_intent=None,
            active_exit_count=None,
        )

        statuses = get_position_management_statuses(db)

        self.assertEqual(statuses[0]["symbol"], "SPY")
        self.assertFalse(statuses[0]["ownership"]["managed"])
        self.assertEqual(statuses[0]["recommended_action"], "preview_unmanaged_exit")

    def test_get_position_management_statuses_reports_missing_exit_config(self) -> None:
        strategy = build_strategy()
        strategy.config = {"scanner": {"type": "price_threshold"}}
        position = build_position()
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
            active_exit_count=None,
        )

        statuses = get_position_management_statuses(db)

        self.assertTrue(statuses[0]["ownership"]["managed"])
        self.assertFalse(statuses[0]["exit_config_enabled"])
        self.assertEqual(statuses[0]["recommended_action"], "add_exit_config")

    def test_get_position_management_statuses_reports_exit_rule_triggered(self) -> None:
        strategy = build_strategy()
        position = build_position()
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
            active_exit_count=None,
        )

        statuses = get_position_management_statuses(db)

        self.assertTrue(statuses[0]["exit_config_enabled"])
        self.assertEqual(statuses[0]["recommended_action"], "exit_rule_triggered")

    def test_get_position_management_statuses_reports_hold(self) -> None:
        strategy = build_strategy()
        position = build_position(
            symbol="SPY260619C00500000",
            unrealized_pl=Decimal("5"),
            cost_basis=Decimal("100"),
        )
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
            active_exit_count=None,
        )

        statuses = get_position_management_statuses(db)

        self.assertEqual(statuses[0]["recommended_action"], "hold")

    def test_get_position_management_statuses_reports_active_exit_order(self) -> None:
        strategy = build_strategy()
        position = build_position()
        exit_order = build_exit_order_intent()
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
            active_exit_count=None,
            active_exit_order=exit_order,
        )

        statuses = get_position_management_statuses(db)

        self.assertEqual(statuses[0]["recommended_action"], "exit_pending")
        self.assertEqual(
            statuses[0]["active_exit_order"]["order_intent_id"],
            str(exit_order.id),
        )


if __name__ == "__main__":
    unittest.main()
