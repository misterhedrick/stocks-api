from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace

from app.db.models import BrokerOrder, Fill, JobRun, PositionSnapshot
from app.integrations.alpaca import (
    AlpacaFillActivity,
    AlpacaPosition,
    AlpacaSubmittedOrder,
    AlpacaTradingError,
)
from app.services.broker_reconciliation import reconcile_broker_state


class FakeReconciliationSession:
    def __init__(self, scalar_results: list[object | None] | None = None) -> None:
        self.scalar_results = scalar_results or []
        self.added: list[object] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.flush_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flush_count += 1

    def scalar(self, _: object) -> object | None:
        if self.scalar_results:
            return self.scalar_results.pop(0)
        return None

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class SuccessfulReconciliationClient:
    def list_orders(self, *, limit: int) -> list[tuple[AlpacaSubmittedOrder, dict]]:
        return [
            (
                AlpacaSubmittedOrder.model_validate(
                    {
                        "id": "alpaca-order-123",
                        "symbol": "SPY260417C00500000",
                        "qty": "1",
                        "side": "buy",
                        "type": "limit",
                        "limit_price": "1.25",
                        "status": "filled",
                        "submitted_at": "2026-04-23T16:00:00Z",
                        "filled_at": "2026-04-23T16:01:00Z",
                    }
                ),
                {
                    "id": "alpaca-order-123",
                    "symbol": "SPY260417C00500000",
                    "qty": "1",
                    "status": "filled",
                },
            )
        ]

    def list_fill_activities(self, *, page_size: int) -> list[tuple[AlpacaFillActivity, dict]]:
        return [
            (
                AlpacaFillActivity.model_validate(
                    {
                        "id": "fill-123",
                        "order_id": "alpaca-order-123",
                        "symbol": "SPY260417C00500000",
                        "side": "buy",
                        "qty": "1",
                        "price": "1.25",
                        "transaction_time": "2026-04-23T16:01:00Z",
                    }
                ),
                {
                    "id": "fill-123",
                    "order_id": "alpaca-order-123",
                    "symbol": "SPY260417C00500000",
                },
            )
        ]

    def list_positions(self) -> list[tuple[AlpacaPosition, dict]]:
        return [
            (
                AlpacaPosition.model_validate(
                    {
                        "symbol": "SPY260417C00500000",
                        "qty": "1",
                        "market_value": "125.00",
                        "cost_basis": "125.00",
                        "unrealized_pl": "0.00",
                    }
                ),
                {
                    "symbol": "SPY260417C00500000",
                    "qty": "1",
                },
            )
        ]


class FailingReconciliationClient:
    def list_orders(self, *, limit: int) -> list[tuple[AlpacaSubmittedOrder, dict]]:
        raise AlpacaTradingError("Alpaca is unavailable")


class BrokerReconciliationTests(unittest.TestCase):
    def test_reconcile_broker_state_persists_orders_fills_positions_and_job_run(self) -> None:
        db = FakeReconciliationSession(
            scalar_results=[
                None,
                None,
                SimpleNamespace(id=uuid.uuid4()),
            ]
        )

        result = reconcile_broker_state(
            db,
            trading_client=SuccessfulReconciliationClient(),
        )

        self.assertEqual(result.orders_seen, 1)
        self.assertEqual(result.orders_created, 1)
        self.assertEqual(result.fills_seen, 1)
        self.assertEqual(result.fills_created, 1)
        self.assertEqual(result.positions_seen, 1)
        self.assertEqual(result.position_snapshots_created, 1)
        self.assertEqual(result.job_run.status, "succeeded")
        self.assertEqual(db.commit_count, 1)

        added_types = [type(item) for item in db.added]
        self.assertIn(JobRun, added_types)
        self.assertIn(BrokerOrder, added_types)
        self.assertIn(Fill, added_types)
        self.assertIn(PositionSnapshot, added_types)

    def test_reconcile_broker_state_records_failed_job_run(self) -> None:
        db = FakeReconciliationSession()

        with self.assertRaises(AlpacaTradingError):
            reconcile_broker_state(
                db,
                trading_client=FailingReconciliationClient(),
            )

        self.assertEqual(db.rollback_count, 1)
        self.assertEqual(db.commit_count, 1)
        job_runs = [item for item in db.added if isinstance(item, JobRun)]
        self.assertEqual(job_runs[-1].status, "failed")
        self.assertIn("AlpacaTradingError", job_runs[-1].error)


if __name__ == "__main__":
    unittest.main()
