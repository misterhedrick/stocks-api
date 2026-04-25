from __future__ import annotations

import unittest
import uuid

from fastapi import HTTPException

from app.api.routes.strategies import create_strategy, get_strategy, update_strategy
from app.db.models import AuditLog, Strategy
from app.schemas.strategies import StrategyCreate, StrategyUpdate


class FakeStrategySession:
    def __init__(self, strategy: Strategy | None = None) -> None:
        self.strategy = strategy
        self.added: list[object] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.flush_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)
        if isinstance(obj, Strategy):
            self.strategy = obj

    def flush(self) -> None:
        self.flush_count += 1
        if self.strategy is not None and getattr(self.strategy, "id", None) is None:
            self.strategy.id = uuid.uuid4()

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    def get(self, model: type[Strategy], strategy_id: uuid.UUID) -> Strategy | None:
        if model is not Strategy or self.strategy is None:
            return None
        if self.strategy.id != strategy_id:
            return None
        return self.strategy


def build_strategy() -> Strategy:
    return Strategy(
        id=uuid.uuid4(),
        name="Opening range options",
        description="Paper strategy",
        is_active=True,
        config={"underlying": "SPY"},
    )


class StrategyRouteTests(unittest.TestCase):
    def test_create_strategy_records_audit_log(self) -> None:
        db = FakeStrategySession()

        strategy = create_strategy(
            StrategyCreate(
                name="Opening range options",
                description="Paper strategy",
                config={"underlying": "SPY"},
            ),
            db,
        )

        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(strategy.name, "Opening range options")
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(audit_logs[-1].event_type, "strategy.created")
        self.assertEqual(audit_logs[-1].entity_id, strategy.id)
        self.assertEqual(audit_logs[-1].payload["config"], {"underlying": "SPY"})

    def test_update_strategy_records_audit_log(self) -> None:
        existing_strategy = build_strategy()
        db = FakeStrategySession(existing_strategy)

        strategy = update_strategy(
            existing_strategy.id,
            StrategyUpdate(is_active=False, config={"underlying": "QQQ"}),
            db,
        )

        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertFalse(strategy.is_active)
        self.assertEqual(strategy.config, {"underlying": "QQQ"})
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(audit_logs[-1].event_type, "strategy.updated")
        self.assertEqual(audit_logs[-1].payload["changes"]["is_active"], False)

    def test_get_strategy_returns_404_when_missing(self) -> None:
        db = FakeStrategySession()

        with self.assertRaises(HTTPException) as context:
            get_strategy(uuid.uuid4(), db)

        self.assertEqual(context.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
