from __future__ import annotations

from decimal import Decimal
import unittest
import uuid

from app.db.models import AuditLog, Strategy
from app.services.strategy_templates import build_preview_first_strategy_payloads
from scripts.seed_paper_strategies import seed_strategies


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
    def test_build_preview_first_strategy_payloads_are_preview_only(self) -> None:
        payloads = build_preview_first_strategy_payloads(
            prices={"SPY": Decimal("500.20"), "QQQ": Decimal("430.40")}
        )

        self.assertEqual(len(payloads), 3)
        for payload in payloads:
            scanner = payload["config"]["scanner"]
            self.assertTrue(payload["is_active"])
            self.assertTrue(scanner["preview"]["enabled"])
            self.assertFalse(scanner["submit"]["enabled"])
            self.assertEqual(scanner["preview"]["quantity"], 1)
            self.assertEqual(scanner["preview"]["max_estimated_notional"], "250.00")
            self.assertEqual(scanner["preview"]["max_spread"], "0.25")

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
