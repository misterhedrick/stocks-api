from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from app.db.models import AuditLog, JobRun, OrderIntent, PositionSnapshot, Strategy
from app.integrations.alpaca import (
    AlpacaLatestOptionQuote,
    AlpacaOptionQuote,
)
from app.services.position_exits import (
    evaluate_position_exits,
    get_position_management_statuses,
    preview_unmanaged_position_exits,
    resolve_position_ownership,
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
        latest_reconciliation: JobRun | None = None,
        position_history: list[PositionSnapshot] | None = None,
    ) -> None:
        self.positions = positions
        self.position_history = position_history or positions
        self.strategy = strategy
        self.entry_order_intent = entry_order_intent
        self.active_exit_count = active_exit_count
        self.active_exit_order = active_exit_order
        self.latest_reconciliation = latest_reconciliation
        self.added: list[object] = []
        self.commit_count = 0
        self.flush_count = 0
        self.scalar_calls = 0
        self.scalars_calls = 0

    def scalars(self, _: object) -> list[PositionSnapshot]:
        self.scalars_calls += 1
        if self.scalars_calls == 1:
            return self.positions
        return self.position_history

    def scalar(self, _: object) -> object | None:
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return self.latest_reconciliation
        if self.scalar_calls == 2:
            return self.entry_order_intent
        if (
            self.scalar_calls == 3
            and self.active_exit_order is not None
            and self.active_exit_count is None
        ):
            return self.active_exit_order
        if self.scalar_calls == 4 and self.active_exit_order is not None:
            return self.active_exit_order
        if self.scalar_calls == 4:
            return None
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


class HighPremiumWideAbsoluteMarketDataClient:
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
                    "bp": "35.80",
                    "bs": "3",
                    "ap": "36.99",
                    "as": "2",
                    "t": "2026-04-23T16:00:00Z",
                }
            ),
            raw_response={
                "bp": "35.80",
                "bs": "3",
                "ap": "36.99",
                "as": "2",
                "t": "2026-04-23T16:00:00Z",
            },
        )


class FakeOwnershipSession:
    def __init__(
        self,
        *,
        fill_rows: list[tuple],
        order_intents: dict[uuid.UUID, OrderIntent],
        strategies: dict[uuid.UUID, Strategy],
    ) -> None:
        self.fill_rows = fill_rows
        self.order_intents = order_intents
        self.strategies = strategies

    def execute(self, _: object) -> list[tuple]:
        return self.fill_rows

    def get(self, model: type, record_id: uuid.UUID) -> object | None:
        if model is OrderIntent:
            return self.order_intents.get(record_id)
        if model is Strategy:
            return self.strategies.get(record_id)
        return None

    def scalar(self, _: object) -> object | None:
        return None


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
                    "trailing_profit_activation_percent": "15",
                    "trailing_profit_giveback_percent": "10",
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


def build_named_strategy(name: str) -> Strategy:
    strategy = build_strategy()
    strategy.id = uuid.uuid4()
    strategy.name = name
    return strategy


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


def build_reconciliation_window(
    *,
    started_at: datetime,
    finished_at: datetime,
) -> JobRun:
    now = datetime.now(timezone.utc)
    return JobRun(
        id=uuid.uuid4(),
        job_name="reconcile_broker",
        status="succeeded",
        started_at=started_at,
        finished_at=finished_at,
        details={},
        created_at=now,
    )


class PositionExitTests(unittest.TestCase):
    def test_resolve_position_ownership_uses_latest_open_lot_not_latest_buy(self) -> None:
        moving_average = build_named_strategy("moving_average")
        momentum = build_named_strategy("momentum_rate_of_change")
        moving_average_intent = build_entry_order_intent(moving_average)
        momentum_intent = build_entry_order_intent(momentum)
        symbol = moving_average_intent.option_symbol
        moving_average_intent.option_symbol = symbol
        momentum_intent.option_symbol = symbol
        rows = [
            (
                uuid.uuid4(),
                datetime(2026, 5, 22, 14, 19, tzinfo=timezone.utc),
                "buy",
                Decimal("1"),
                Decimal("2.68"),
                moving_average_intent.id,
                moving_average.id,
            ),
            (
                uuid.uuid4(),
                datetime(2026, 5, 22, 14, 24, tzinfo=timezone.utc),
                "buy",
                Decimal("1"),
                Decimal("2.83"),
                momentum_intent.id,
                momentum.id,
            ),
            (
                uuid.uuid4(),
                datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc),
                "sell",
                Decimal("1"),
                Decimal("2.50"),
                momentum_intent.id,
                momentum.id,
            ),
        ]
        db = FakeOwnershipSession(
            fill_rows=rows,
            order_intents={
                moving_average_intent.id: moving_average_intent,
                momentum_intent.id: momentum_intent,
            },
            strategies={moving_average.id: moving_average, momentum.id: momentum},
        )

        ownership = resolve_position_ownership(db, build_position(symbol=symbol))

        self.assertTrue(ownership.managed)
        self.assertEqual(ownership.strategy_name, "moving_average")
        self.assertEqual(ownership.order_intent_id, moving_average_intent.id)
        self.assertEqual(ownership.open_quantity, Decimal("1"))

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
        self.assertEqual(result.exit_evaluations[0]["action"], "exit_previewed")
        self.assertIn("profit_target_percent", result.exit_evaluations[0]["trigger_reason"])
        self.assertEqual(
            result.exit_evaluations[0]["rule_diagnostics"]["thresholds"][
                "profit_target_percent"
            ],
            "30",
        )
        order_intents = [item for item in db.added if isinstance(item, OrderIntent)]
        self.assertEqual(order_intents[-1].side, "sell")
        self.assertEqual(order_intents[-1].limit_price, Decimal("1.20"))
        self.assertEqual(order_intents[-1].preview["source"], "position_exit_evaluator")
        self.assertIn("profit_target_percent", order_intents[-1].preview["trigger_reason"])
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "order_intent.exit_previewed")
        self.assertEqual(db.commit_count, 1)

    def test_evaluate_position_exits_accepts_high_premium_relative_spread(self) -> None:
        strategy = build_strategy()
        position = build_position(cost_basis=Decimal("3000"), unrealized_pl=Decimal("1000"))
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
        )

        result = evaluate_position_exits(
            db,
            market_data_client=HighPremiumWideAbsoluteMarketDataClient(),
        )

        self.assertEqual(result.exits_created, 1)
        order_intents = [item for item in db.added if isinstance(item, OrderIntent)]
        self.assertEqual(order_intents[-1].limit_price, Decimal("35.80"))
        self.assertEqual(order_intents[-1].preview["quote"]["spread"], "1.19")

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
        self.assertEqual(result.exit_evaluations[0]["action"], "hold")
        self.assertEqual(
            result.exit_evaluations[0]["rule_diagnostics"]["unrealized_pl_percent"],
            "5.00",
        )
        self.assertGreater(result.exit_evaluations[0]["rule_diagnostics"]["days_to_expiration"], 1)
        self.assertEqual(db.commit_count, 0)

    def test_evaluate_position_exits_trails_profitable_giveback(self) -> None:
        strategy = build_strategy()
        position = build_position(
            symbol="SPY260619C00500000",
            unrealized_pl=Decimal("10"),
            cost_basis=Decimal("100"),
        )
        peak_position = build_position(
            symbol=position.symbol,
            unrealized_pl=Decimal("25"),
            cost_basis=Decimal("100"),
        )
        db = FakePositionExitSession(
            positions=[position],
            position_history=[peak_position, position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
        )

        result = evaluate_position_exits(
            db,
            market_data_client=SuccessfulMarketDataClient(),
        )

        self.assertEqual(result.exits_created, 1)
        self.assertIn("trailing_profit_giveback_percent", result.exit_evaluations[0]["trigger_reason"])
        self.assertEqual(
            result.exit_evaluations[0]["rule_diagnostics"]["peak_unrealized_pl_percent"],
            "25.00",
        )
        order_intents = [item for item in db.added if isinstance(item, OrderIntent)]
        self.assertIn(
            "trailing_profit_giveback_percent",
            order_intents[-1].preview["trigger_reason"],
        )

    def test_evaluate_position_exits_skips_duplicate_active_exit(self) -> None:
        strategy = build_strategy()
        position = build_position()
        entry_order_intent = build_entry_order_intent(strategy)
        active_exit_order = build_exit_order_intent()
        active_exit_order.status = "new"
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=entry_order_intent,
            active_exit_order=active_exit_order,
        )

        result = evaluate_position_exits(
            db,
            market_data_client=SuccessfulMarketDataClient(),
        )

        self.assertEqual(result.exits_created, 0)
        self.assertEqual(result.exits_skipped, 1)
        self.assertIn("active exit order already exists", result.no_exit_reasons[0])
        self.assertEqual(result.exit_evaluations[0]["action"], "exit_pending")

    def test_evaluate_position_exits_retries_previewed_active_exit_submit(self) -> None:
        strategy = build_strategy()
        position = build_position()
        exit_order = build_exit_order_intent()
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
            active_exit_order=exit_order,
        )

        result = evaluate_position_exits(
            db,
            market_data_client=SuccessfulMarketDataClient(),
        )

        self.assertEqual(result.exits_created, 0)
        self.assertEqual(result.exits_skipped, 1)
        self.assertEqual(result.order_intent_ids, [exit_order.id])
        self.assertEqual(result.exit_evaluations[0]["action"], "exit_pending_submit")
        self.assertEqual(result.exit_evaluations[0]["order_intent_id"], str(exit_order.id))

    def test_evaluate_position_exits_replaces_active_exit_for_protective_trigger(self) -> None:
        strategy = build_strategy()
        position = build_position(
            symbol="SPY260619C00500000",
            unrealized_pl=Decimal("-40"),
            cost_basis=Decimal("100"),
        )
        entry_order_intent = build_entry_order_intent(strategy)
        entry_order_intent.option_symbol = position.symbol
        active_exit_order = build_exit_order_intent()
        active_exit_order.option_symbol = position.symbol
        active_exit_order.status = "submitted"
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=entry_order_intent,
            active_exit_order=active_exit_order,
        )

        with patch(
            "app.services.position_exit_core.cancel_order_intent",
            return_value=(active_exit_order, object()),
        ) as cancel_order_intent_mock:
            result = evaluate_position_exits(
                db,
                market_data_client=SuccessfulMarketDataClient(),
            )

        self.assertEqual(result.exits_created, 1)
        self.assertEqual(result.exits_skipped, 0)
        self.assertEqual(result.exit_evaluations[0]["action"], "exit_replaced")
        self.assertEqual(
            result.exit_evaluations[0]["replaced_order_intent_id"],
            str(active_exit_order.id),
        )
        self.assertIn("stop_loss_percent", result.exit_evaluations[0]["reason"])
        cancel_order_intent_mock.assert_called_once_with(db, active_exit_order.id)
        order_intents = [item for item in db.added if isinstance(item, OrderIntent)]
        self.assertEqual(order_intents[-1].side, "sell")
        self.assertEqual(order_intents[-1].option_symbol, position.symbol)
        self.assertIn("stop_loss_percent", order_intents[-1].preview["trigger_reason"])

    def test_evaluate_position_exits_ignores_stale_positions_before_latest_reconcile(self) -> None:
        strategy = build_strategy()
        stale_position = build_position()
        stale_position.captured_at = datetime(2026, 4, 30, 14, 0, tzinfo=timezone.utc)
        latest_reconciliation = build_reconciliation_window(
            started_at=datetime(2026, 4, 30, 18, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 4, 30, 18, 1, tzinfo=timezone.utc),
        )
        db = FakePositionExitSession(
            positions=[],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
            latest_reconciliation=latest_reconciliation,
        )

        result = evaluate_position_exits(
            db,
            market_data_client=SuccessfulMarketDataClient(),
        )

        self.assertEqual(result.positions_seen, 0)
        self.assertEqual(result.exits_created, 0)

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


class StopLossMinDollarsTest(unittest.TestCase):
    def _strategy_with_exit(self, exit_overrides: dict) -> Strategy:
        now = datetime.now(timezone.utc)
        config = {
            "scanner": {
                "exit": {
                    "enabled": True,
                    "profit_target_percent": "30",
                    "stop_loss_percent": "10",
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
                    **exit_overrides,
                }
            }
        }
        return Strategy(
            id=uuid.uuid4(),
            name="Test",
            description="Test",
            is_active=True,
            config=config,
            created_at=now,
            updated_at=now,
        )

    def test_stop_fires_when_both_percent_and_dollar_floor_met(self) -> None:
        # Position down 15% on $200 cost basis = $30 loss, floor is $20
        strategy = self._strategy_with_exit({"stop_loss_min_dollars": "20"})
        position = build_position(unrealized_pl=Decimal("-30"), cost_basis=Decimal("200"))
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
            active_exit_count=None,
        )
        statuses = get_position_management_statuses(db)
        self.assertEqual(statuses[0]["recommended_action"], "exit_rule_triggered")

    def test_stop_suppressed_when_dollar_floor_not_met(self) -> None:
        # Position down 50% on $10 cost basis = $5 loss, floor is $20 — should hold
        strategy = self._strategy_with_exit({"stop_loss_min_dollars": "20"})
        position = build_position(
            symbol="SPY260619C00500000",
            unrealized_pl=Decimal("-5"),
            cost_basis=Decimal("10"),
        )
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
            active_exit_count=None,
        )
        statuses = get_position_management_statuses(db)
        self.assertEqual(statuses[0]["recommended_action"], "hold")

    def test_stop_fires_without_dollar_floor_set(self) -> None:
        # No stop_loss_min_dollars — pure percent stop should behave as before
        strategy = self._strategy_with_exit({})
        position = build_position(unrealized_pl=Decimal("-20"), cost_basis=Decimal("100"))
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
            active_exit_count=None,
        )
        statuses = get_position_management_statuses(db)
        self.assertEqual(statuses[0]["recommended_action"], "exit_rule_triggered")

    def test_stop_suppressed_when_percent_not_met_even_if_dollar_floor_met(self) -> None:
        # Position down only 3% on $1000 cost basis = $30 loss (> $20 floor),
        # but 3% < 10% threshold — should hold
        strategy = self._strategy_with_exit({"stop_loss_min_dollars": "20"})
        position = build_position(
            symbol="SPY260619C00500000",
            unrealized_pl=Decimal("-30"),
            cost_basis=Decimal("1000"),
        )
        db = FakePositionExitSession(
            positions=[position],
            strategy=strategy,
            entry_order_intent=build_entry_order_intent(strategy),
            active_exit_count=None,
        )
        statuses = get_position_management_statuses(db)
        self.assertEqual(statuses[0]["recommended_action"], "hold")


if __name__ == "__main__":
    unittest.main()
