from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timezone
from decimal import Decimal
import unittest
import uuid

from app.db.models import AuditLog, Strategy
from scripts.tune_strategies import (
    ENTRY_QUALITY_BATCH_2026_05_29,
    FRESH_PAPER_TUNING_BATCH_2026_06_11,
    RISK_BREAKOUT_QUALITY_BATCH_2026_06_17,
    apply_entry_quality_batch_2026_05_29,
    apply_fresh_paper_tuning_batch_2026_06_11,
    apply_risk_breakout_quality_batch_2026_06_17,
    list_strategy_summaries,
    momentum_rate_of_change_payload_from_args,
    moving_average_payload_from_args,
    patch_strategy_scanner,
    scanner_patch_from_args,
    set_strategy_submit_config,
    submit_config_from_args,
    upsert_strategy,
)


class FakeScalarResult:
    def __init__(self, values: list[Strategy]) -> None:
        self.values = values

    def __iter__(self):
        return iter(self.values)


class FakeTuningSession:
    def __init__(self, strategies: list[Strategy] | None = None) -> None:
        self.strategies = strategies or []
        self.added: list[object] = []
        self.flush_count = 0
        self.rollback_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)
        if isinstance(obj, Strategy) and obj not in self.strategies:
            self.strategies.append(obj)

    def flush(self) -> None:
        self.flush_count += 1
        now = datetime.now(timezone.utc)
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if isinstance(obj, Strategy):
                if getattr(obj, "created_at", None) is None:
                    obj.created_at = now
                if getattr(obj, "updated_at", None) is None:
                    obj.updated_at = now

    def rollback(self) -> None:
        self.rollback_count += 1

    def scalar(self, _: object) -> Strategy | None:
        return self.strategies[0] if self.strategies else None

    def scalars(self, _: object) -> FakeScalarResult:
        return FakeScalarResult(self.strategies)


def build_strategy(
    *,
    scanner_type: str = "moving_average",
    scanner_patch: dict | None = None,
) -> Strategy:
    now = datetime.now(timezone.utc)
    scanner = {
        "type": scanner_type,
        "symbols": ["SPY"],
        "short_window": 5,
        "long_window": 20,
        "preview": {"enabled": True},
        "submit": {"enabled": False},
    }
    if scanner_patch:
        scanner.update(scanner_patch)
    return Strategy(
        id=uuid.uuid4(),
        name=f"{scanner_type} strategy",
        description="Existing",
        is_active=True,
        config={"scanner": scanner},
        created_at=now,
        updated_at=now,
    )


class StrategyTuningScriptTests(unittest.TestCase):
    def test_moving_average_payload_from_args_uses_sample_price(self) -> None:
        payload = moving_average_payload_from_args(
            Namespace(
                symbol="spy",
                target_strike=None,
                sample_price="501.40",
                name=None,
                option_type="call",
                trigger="bullish_trend",
                short_window=5,
                long_window=20,
                lookback_minutes=1440,
                timeframe="5Min",
                min_change_percent="0.10",
                confidence="0.6200",
            )
        )

        scanner = payload["config"]["scanner"]
        self.assertEqual(scanner["preview"]["target_strike"], "501")
        self.assertEqual(scanner["type"], "moving_average")
        self.assertTrue(scanner["submit"]["enabled"])

    def test_momentum_rate_of_change_payload_from_args_uses_sample_price(self) -> None:
        payload = momentum_rate_of_change_payload_from_args(
            Namespace(
                symbol="spy",
                target_strike=None,
                sample_price="501.40",
                name=None,
                option_type="call",
                direction="bullish",
                timeframe="1Min",
                lookback_minutes=30,
                change_above_percent="0.20",
                change_below_percent="-0.20",
                short_average_type="ema",
                short_average_window=9,
                confidence="0.6500",
            )
        )

        scanner = payload["config"]["scanner"]
        self.assertEqual(scanner["preview"]["target_strike"], "501")
        self.assertEqual(scanner["type"], "momentum_rate_of_change")
        self.assertEqual(scanner["change_above_percent"], "0.20")
        self.assertEqual(scanner["change_below_percent"], "-0.20")
        self.assertEqual(scanner["max_extension_percent"], "1.00")
        self.assertEqual(scanner["preview"]["max_spread"], "0.35")
        self.assertTrue(scanner["submit"]["enabled"])

    def test_upsert_strategy_creates_and_audits(self) -> None:
        payload = moving_average_payload_from_args(
            Namespace(
                symbol="SPY",
                target_strike="500",
                sample_price=None,
                name=None,
                option_type="call",
                trigger="bullish_trend",
                short_window=5,
                long_window=20,
                lookback_minutes=1440,
                timeframe="5Min",
                min_change_percent="0.10",
                confidence="0.6200",
            )
        )
        db = FakeTuningSession()

        created = upsert_strategy(db, payload, source="test")

        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertTrue(created)
        self.assertEqual(db.flush_count, 1)
        self.assertEqual(audit_logs[-1].event_type, "strategy.created")

    def test_patch_strategy_scanner_deep_merges_config(self) -> None:
        strategy = build_strategy()
        db = FakeTuningSession([strategy])

        patched = patch_strategy_scanner(
            db,
            name=strategy.name,
            scanner_patch={
                "short_window": 8,
                "preview": {"max_spread": "0.20"},
            },
        )

        scanner = patched.config["scanner"]
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(scanner["short_window"], 8)
        self.assertEqual(scanner["long_window"], 20)
        self.assertTrue(scanner["preview"]["enabled"])
        self.assertEqual(scanner["preview"]["max_spread"], "0.20")
        self.assertEqual(audit_logs[-1].event_type, "strategy.updated")

    def test_scanner_patch_from_args_combines_json_and_flags(self) -> None:
        patch = scanner_patch_from_args(
            Namespace(
                scanner_json='{"preview": {"max_spread": "0.20"}}',
                short_window=8,
                long_window=None,
                lookback_minutes=1440,
                timeframe="5Min",
                trigger=None,
            )
        )

        self.assertEqual(patch["preview"]["max_spread"], "0.20")
        self.assertEqual(patch["short_window"], 8)
        self.assertEqual(patch["lookback_minutes"], 1440)
        self.assertEqual(patch["timeframe"], "5Min")

    def test_submit_config_from_args_builds_env_aligned_metadata(self) -> None:
        submit = submit_config_from_args(
            Namespace(
                enable=True,
                max_orders_per_cycle=100,
                max_contracts_per_order=1,
                max_contracts_per_cycle=100,
                max_notional_per_order="5000",
                max_open_contracts_per_symbol=100,
                max_open_contracts_per_strategy=100,
                max_orders_per_trading_day=500,
                trading_day_timezone="America/New_York",
                trade_window_timezone="America/New_York",
                trade_window_start="10:00",
                trade_window_end="16:00",
                allowed_sides=None,
            )
        )

        self.assertTrue(submit["enabled"])
        self.assertEqual(submit["max_notional_per_order"], "5000.00")
        self.assertEqual(submit["max_orders_per_trading_day"], 500)
        self.assertEqual(submit["max_open_contracts_per_strategy"], 100)
        self.assertEqual(submit["trade_windows"][0]["start"], "10:00")
        self.assertEqual(submit["trade_windows"][0]["end"], "16:00")
        self.assertEqual(submit["allowed_sides"], ["buy"])

    def test_set_strategy_submit_config_updates_submit_controls(self) -> None:
        strategy = build_strategy()
        db = FakeTuningSession([strategy])
        submit = {
            "enabled": True,
            "max_orders_per_cycle": 100,
            "max_contracts_per_order": 1,
            "max_contracts_per_cycle": 100,
            "max_notional_per_order": "5000.00",
            "max_open_contracts_per_symbol": 100,
            "max_open_contracts_per_strategy": 100,
            "max_orders_per_trading_day": 500,
            "trading_day_timezone": "America/New_York",
            "trade_windows": [
                {
                    "timezone": "America/New_York",
                    "start": "10:00",
                    "end": "16:00",
                }
            ],
            "allowed_sides": ["buy"],
        }

        updated = set_strategy_submit_config(
            db,
            name=strategy.name,
            submit_config=submit,
        )

        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertTrue(updated.config["scanner"]["submit"]["enabled"])
        self.assertEqual(
            updated.config["scanner"]["submit"]["max_notional_per_order"],
            "5000.00",
        )
        self.assertEqual(audit_logs[-1].event_type, "strategy.updated")

    def test_list_strategy_summaries_returns_scanner_controls(self) -> None:
        strategy = build_strategy()
        db = FakeTuningSession([strategy])

        summaries = list_strategy_summaries(db, active_only=True)

        self.assertEqual(summaries[0]["name"], strategy.name)
        self.assertEqual(summaries[0]["scanner_type"], "moving_average")
        self.assertTrue(summaries[0]["preview_enabled"])
        self.assertFalse(summaries[0]["submit_enabled"])

    def test_apply_entry_quality_batch_patches_targeted_scanner_keys(self) -> None:
        strategy = build_strategy(
            scanner_type="moving_average",
            scanner_patch={"trigger": "trend_state"},
        )
        db = FakeTuningSession([strategy])

        results = apply_entry_quality_batch_2026_05_29(db)

        self.assertEqual(
            ENTRY_QUALITY_BATCH_2026_05_29["moving_average"]["trigger"],
            "crossover",
        )
        self.assertEqual(results[0]["status"], "updated")
        self.assertEqual(
            results[0]["changed"]["trigger"],
            {"from": "trend_state", "to": "crossover"},
        )
        self.assertEqual(strategy.config["scanner"]["trigger"], "crossover")

    def test_apply_entry_quality_batch_watches_unpatched_scanners(self) -> None:
        strategy = build_strategy(scanner_type="mean_reversion")
        db = FakeTuningSession([strategy])

        results = apply_entry_quality_batch_2026_05_29(db)

        self.assertEqual(results[0]["scanner_type"], "mean_reversion")
        self.assertEqual(results[0]["status"], "watch")

    def test_apply_fresh_paper_batch_patches_targeted_scanner_keys(self) -> None:
        self.assertEqual(
            FRESH_PAPER_TUNING_BATCH_2026_06_11["mean_reversion"],
            {
                "bollinger_stddev": "2.50",
                "max_distance_to_middle_percent": "0.75",
            },
        )

        mean_reversion = build_strategy(
            scanner_type="mean_reversion",
            scanner_patch={
                "bollinger_stddev": "2.25",
                "max_distance_to_middle_percent": "1.50",
            },
        )
        results = apply_fresh_paper_tuning_batch_2026_06_11(
            FakeTuningSession([mean_reversion])
        )
        self.assertEqual(
            results[0]["changed"],
            {
                "bollinger_stddev": {"from": "2.25", "to": "2.50"},
                "max_distance_to_middle_percent": {"from": "1.50", "to": "0.75"},
            },
        )
        self.assertEqual(mean_reversion.config["scanner"]["bollinger_stddev"], "2.50")

        momentum = build_strategy(
            scanner_type="momentum_rate_of_change",
            scanner_patch={
                "change_above_percent": "0.50",
                "change_below_percent": "-0.50",
                "max_extension_percent": "1.25",
            },
        )
        results = apply_fresh_paper_tuning_batch_2026_06_11(
            FakeTuningSession([momentum])
        )
        self.assertEqual(
            results[0]["changed"],
            {
                "change_above_percent": {"from": "0.50", "to": "0.75"},
                "change_below_percent": {"from": "-0.50", "to": "-0.75"},
                "max_extension_percent": {"from": "1.25", "to": "1.00"},
            },
        )
        self.assertEqual(momentum.config["scanner"]["change_above_percent"], "0.75")

        support_resistance = build_strategy(
            scanner_type="support_resistance",
            scanner_patch={"max_distance_percent": "0.75"},
        )
        results = apply_fresh_paper_tuning_batch_2026_06_11(
            FakeTuningSession([support_resistance])
        )
        self.assertEqual(
            results[0]["changed"],
            {"max_distance_percent": {"from": "0.75", "to": "0.35"}},
        )
        self.assertEqual(
            support_resistance.config["scanner"]["max_distance_percent"],
            "0.35",
        )

        time_series = build_strategy(
            scanner_type="time_series_momentum",
            scanner_patch={"min_trend_percent": "1.50"},
        )
        results = apply_fresh_paper_tuning_batch_2026_06_11(
            FakeTuningSession([time_series])
        )
        self.assertEqual(
            results[0]["changed"],
            {"min_trend_percent": {"from": "1.50", "to": "2.00"}},
        )
        self.assertEqual(time_series.config["scanner"]["min_trend_percent"], "2.00")

    def test_apply_fresh_paper_batch_watches_unpatched_scanners(self) -> None:
        strategy = build_strategy(scanner_type="moving_average")
        db = FakeTuningSession([strategy])

        results = apply_fresh_paper_tuning_batch_2026_06_11(db)

        self.assertEqual(results[0]["scanner_type"], "moving_average")
        self.assertEqual(results[0]["status"], "watch")

    def test_apply_risk_breakout_quality_batch_patches_targeted_keys(self) -> None:
        self.assertEqual(
            RISK_BREAKOUT_QUALITY_BATCH_2026_06_17["mean_reversion"],
            {
                "exit": {
                    "stop_loss_percent": "8",
                    "max_hold_hours": 4,
                },
            },
        )

        mean_reversion = build_strategy(
            scanner_type="mean_reversion",
            scanner_patch={"exit": {"stop_loss_percent": "10"}},
        )
        results = apply_risk_breakout_quality_batch_2026_06_17(
            FakeTuningSession([mean_reversion])
        )
        self.assertEqual(
            results[0]["changed"],
            {
                "exit.stop_loss_percent": {"from": "10", "to": "8"},
                "exit.max_hold_hours": {"from": None, "to": 4},
            },
        )
        self.assertEqual(
            mean_reversion.config["scanner"]["exit"]["stop_loss_percent"],
            "8",
        )
        self.assertEqual(
            mean_reversion.config["scanner"]["exit"]["max_hold_hours"],
            4,
        )

        volatility_squeeze = build_strategy(
            scanner_type="volatility_squeeze",
            scanner_patch={
                "breakout_buffer_percent": "0.10",
                "max_breakout_distance_percent": "2.5",
            },
        )
        results = apply_risk_breakout_quality_batch_2026_06_17(
            FakeTuningSession([volatility_squeeze])
        )
        self.assertEqual(
            results[0]["changed"],
            {
                "breakout_buffer_percent": {"from": "0.10", "to": "0.20"},
                "max_breakout_distance_percent": {"from": "2.5", "to": "1.50"},
            },
        )

        breakout = build_strategy(
            scanner_type="breakout_price_threshold",
            scanner_patch={
                "breakout_buffer_percent": "0.15",
                "max_breakout_distance_percent": "2.0",
            },
        )
        results = apply_risk_breakout_quality_batch_2026_06_17(
            FakeTuningSession([breakout])
        )
        self.assertEqual(
            results[0]["changed"],
            {
                "breakout_buffer_percent": {"from": "0.15", "to": "0.20"},
                "max_breakout_distance_percent": {"from": "2.0", "to": "1.25"},
            },
        )

    def test_apply_risk_breakout_quality_batch_watches_unpatched_scanners(self) -> None:
        strategy = build_strategy(scanner_type="moving_average")
        db = FakeTuningSession([strategy])

        results = apply_risk_breakout_quality_batch_2026_06_17(db)

        self.assertEqual(results[0]["scanner_type"], "moving_average")
        self.assertEqual(results[0]["status"], "watch")


if __name__ == "__main__":
    unittest.main()
