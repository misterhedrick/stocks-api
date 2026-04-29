from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timezone
from decimal import Decimal
import unittest
import uuid

from app.db.models import AuditLog, Strategy
from scripts.tune_paper_strategies import (
    list_strategy_summaries,
    moving_average_payload_from_args,
    patch_strategy_scanner,
    scanner_patch_from_args,
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


def build_strategy() -> Strategy:
    now = datetime.now(timezone.utc)
    return Strategy(
        id=uuid.uuid4(),
        name="Paper SPY moving average call preview",
        description="Existing",
        is_active=True,
        config={
            "scanner": {
                "type": "moving_average",
                "symbols": ["SPY"],
                "short_window": 5,
                "long_window": 20,
                "preview": {"enabled": True},
                "submit": {"enabled": False},
            }
        },
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
                confidence="0.6200",
            )
        )

        scanner = payload["config"]["scanner"]
        self.assertEqual(scanner["preview"]["target_strike"], "501")
        self.assertEqual(scanner["type"], "moving_average")
        self.assertFalse(scanner["submit"]["enabled"])

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

    def test_list_strategy_summaries_returns_scanner_controls(self) -> None:
        strategy = build_strategy()
        db = FakeTuningSession([strategy])

        summaries = list_strategy_summaries(db, active_only=True)

        self.assertEqual(summaries[0]["name"], strategy.name)
        self.assertEqual(summaries[0]["scanner_type"], "moving_average")
        self.assertTrue(summaries[0]["preview_enabled"])
        self.assertFalse(summaries[0]["submit_enabled"])


if __name__ == "__main__":
    unittest.main()
