from __future__ import annotations

from decimal import Decimal
import unittest
import uuid

from app.db.models import AuditLog, Strategy
from app.services.strategy_templates import (
    build_macd_crossover_strategy_payload,
    build_moving_average_strategy_payload,
    build_momentum_rate_of_change_strategy_payload,
    build_preview_first_strategy_payloads,
)
from scripts.seed_paper_strategies import seed_strategies
from scripts.seed_paper_trade_universe import _strategy_payloads as universe_strategy_payloads


class FakeSeedSession:
    def __init__(self, existing: Strategy | None = None) -> None:
        self.existing = existing
        self.added: list[object] = []
        self.flush_count = 0
        self.rollback_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)
        if isinstance(obj, Strategy):
            self.existing = obj

    def flush(self) -> None:
        self.flush_count += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    def rollback(self) -> None:
        self.rollback_count += 1

    def scalar(self, _: object) -> Strategy | None:
        return self.existing

    def scalars(self, _: object) -> list[Strategy]:
        return []


class StrategyTemplateTests(unittest.TestCase):
    def test_build_preview_first_strategy_payloads_auto_submit_entries(self) -> None:
        payloads = build_preview_first_strategy_payloads(
            prices={"SPY": Decimal("500.20"), "QQQ": Decimal("430.40")}
        )

        self.assertEqual(len(payloads), 5)
        for payload in payloads:
            scanner = payload["config"]["scanner"]
            self.assertTrue(payload["is_active"])
            self.assertTrue(scanner["preview"]["enabled"])
            self.assertTrue(scanner["submit"]["enabled"])
            self.assertEqual(scanner["preview"]["quantity"], 1)
            self.assertEqual(scanner["preview"]["limit"], 20)
            self.assertEqual(scanner["preview"]["min_days_to_expiration"], 2)
            self.assertEqual(scanner["preview"]["max_days_to_expiration"], 30)
            self.assertLessEqual(
                Decimal(scanner["preview"]["max_estimated_notional"]),
                Decimal("5000.00"),
            )
            self.assertLessEqual(
                Decimal(scanner["preview"]["max_spread"]),
                Decimal("0.35"),
            )
            self.assertEqual(scanner["preview"]["max_spread_percent"], "35")
            self.assertEqual(scanner["preview"]["min_open_interest"], 25)
            if scanner["type"] == "moving_average":
                self.assertTrue(scanner["market_regime"]["enabled"])

    def test_build_moving_average_strategy_payload_auto_submits(self) -> None:
        payload = build_moving_average_strategy_payload(
            symbol="spy",
            target_strike=Decimal("500"),
            trigger="bullish_cross",
            short_window=3,
            long_window=15,
        )

        scanner = payload["config"]["scanner"]
        self.assertEqual(payload["name"], "Paper SPY moving average call preview")
        self.assertEqual(scanner["type"], "moving_average")
        self.assertEqual(scanner["symbols"], ["SPY"])
        self.assertEqual(scanner["trigger"], "bullish_cross")
        self.assertEqual(scanner["short_window"], 3)
        self.assertEqual(scanner["long_window"], 15)
        self.assertTrue(scanner["preview"]["enabled"])
        self.assertTrue(scanner["submit"]["enabled"])

    def test_build_momentum_rate_of_change_strategy_payload_uses_data_gathering_controls(self) -> None:
        payload = build_momentum_rate_of_change_strategy_payload(
            symbol="spy",
            target_strike=Decimal("500"),
        )

        scanner = payload["config"]["scanner"]
        self.assertEqual(payload["name"], "Paper SPY momentum rate-of-change call preview")
        self.assertEqual(scanner["type"], "momentum_rate_of_change")
        self.assertEqual(scanner["lookback_minutes"], 30)
        self.assertEqual(scanner["change_above_percent"], "0.175")
        self.assertEqual(scanner["change_below_percent"], "-0.175")
        self.assertEqual(scanner["max_extension_percent"], "2.0")
        self.assertTrue(scanner["require_latest_candle_confirmation"])
        self.assertEqual(scanner["preview"]["max_estimated_notional"], "5000")
        self.assertEqual(scanner["preview"]["max_spread"], "0.35")
        self.assertEqual(scanner["exit"]["profit_target_percent"], "25")
        self.assertEqual(scanner["exit"]["stop_loss_percent"], "10")
        self.assertEqual(scanner["exit"]["stop_loss_min_dollars"], "20")
        self.assertEqual(scanner["exit"]["trailing_profit_activation_percent"], "15")
        self.assertEqual(scanner["exit"]["trailing_profit_giveback_percent"], "10")
        self.assertTrue(scanner["submit"]["enabled"])

    def test_build_macd_crossover_strategy_payload_requires_histogram_confirmation(self) -> None:
        payload = build_macd_crossover_strategy_payload(
            symbol="spy",
            target_strike=Decimal("500"),
        )

        scanner = payload["config"]["scanner"]
        self.assertEqual(scanner["type"], "macd_crossover")
        self.assertTrue(scanner["require_price_confirmation"])
        self.assertTrue(scanner["require_histogram_confirmation"])

    def test_submit_trade_windows_are_10_00_to_16_00_et(self) -> None:
        payload = build_moving_average_strategy_payload(
            symbol="SPY",
            target_strike=Decimal("500"),
        )
        windows = payload["config"]["scanner"]["submit"]["trade_windows"]
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["timezone"], "America/New_York")
        self.assertEqual(windows[0]["start"], "10:00")
        self.assertEqual(windows[0]["end"], "16:00")

    def test_paper_universe_seed_builds_global_scanner_type_strategies(self) -> None:
        payloads = universe_strategy_payloads(
            ["SPY", "QQQ", "MSFT"],
            prices={
                "SPY": Decimal("500"),
                "QQQ": Decimal("430"),
                "MSFT": Decimal("420"),
            },
            max_notional_per_order="5000.00",
            max_spread="0.35",
            max_spread_percent="35",
            min_open_interest=25,
            min_quote_size=1,
            max_orders_per_cycle=100,
            max_orders_per_day=500,
            max_open_contracts_per_symbol=100,
            max_open_contracts_per_strategy=100,
            trade_window_start="10:00",
            trade_window_end="16:00",
        )

        self.assertEqual(len(payloads), 9)
        names = {payload["name"] for payload in payloads}
        self.assertIn("momentum_rate_of_change", names)
        for payload in payloads:
            scanner = payload["config"]["scanner"]
            preview = scanner["preview"]
            self.assertEqual(scanner["symbols"], ["SPY", "QQQ", "MSFT"])
            self.assertNotIn("direction", scanner)
            self.assertNotIn("underlying_symbol", preview)
            self.assertNotIn("option_type", preview)
            self.assertNotIn("target_strike", preview)

    def test_seed_strategies_creates_new_strategy_and_audit_log(self) -> None:
        payloads = build_preview_first_strategy_payloads(
            prices={"SPY": Decimal("500.20"), "QQQ": Decimal("430.40")}
        )[:1]
        db = FakeSeedSession()

        created, updated, deactivated = seed_strategies(db, payloads)

        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual((created, updated, deactivated), (1, 0, 0))
        self.assertEqual(db.flush_count, 1)
        self.assertEqual(audit_logs[-1].event_type, "strategy.created")

    def test_seed_strategies_updates_existing_strategy(self) -> None:
        payloads = build_preview_first_strategy_payloads(
            prices={"SPY": Decimal("500.20"), "QQQ": Decimal("430.40")}
        )[:1]
        existing = Strategy(
            id=uuid.uuid4(),
            name=payloads[0]["name"],
            description="Old description",
            is_active=False,
            config={},
        )
        db = FakeSeedSession(existing)

        created, updated, deactivated = seed_strategies(db, payloads)

        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual((created, updated, deactivated), (0, 1, 0))
        self.assertTrue(existing.is_active)
        self.assertEqual(existing.config, payloads[0]["config"])
        self.assertEqual(audit_logs[-1].event_type, "strategy.updated")


if __name__ == "__main__":
    unittest.main()
