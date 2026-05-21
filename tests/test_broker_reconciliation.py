from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.db.models import AuditLog, BrokerOrder, Fill, JobRun, OrderIntent, PositionSnapshot
from app.integrations.alpaca import (
    AlpacaFillActivity,
    AlpacaPosition,
    AlpacaSubmittedOrder,
    AlpacaTradingError,
)
from app.services.broker_reconciliation import reconcile_broker_state


class FakeReconciliationSession:
    def __init__(
        self,
        scalar_results: list[object | None] | None = None,
        records: dict[tuple[type, uuid.UUID], object] | None = None,
    ) -> None:
        self.scalar_results = scalar_results or []
        self.records = records or {}
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

    def get(self, model: type, record_id: uuid.UUID) -> object | None:
        return self.records.get((model, record_id))

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

    def list_fill_activities(
        self,
        *,
        page_size: int,
        page_token: str | None = None,
    ) -> list[tuple[AlpacaFillActivity, dict]]:
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


class ResetCutoffReconciliationClient(SuccessfulReconciliationClient):
    def list_orders(self, *, limit: int) -> list[tuple[AlpacaSubmittedOrder, dict]]:
        rows = [
            {
                "id": "old-order",
                "symbol": "OLD260417C00500000",
                "qty": "1",
                "side": "buy",
                "type": "limit",
                "limit_price": "1.25",
                "status": "filled",
                "submitted_at": "2026-04-23T16:00:00Z",
                "filled_at": "2026-04-23T16:01:00Z",
            },
            {
                "id": "new-order",
                "symbol": "SPY260417C00500000",
                "qty": "1",
                "side": "buy",
                "type": "limit",
                "limit_price": "1.25",
                "status": "filled",
                "submitted_at": "2026-05-21T16:00:00Z",
                "filled_at": "2026-05-21T16:01:00Z",
            },
        ]
        return [(AlpacaSubmittedOrder.model_validate(row), row) for row in rows]

    def list_fill_activities(
        self,
        *,
        page_size: int,
        page_token: str | None = None,
    ) -> list[tuple[AlpacaFillActivity, dict]]:
        rows = [
            {
                "id": "old-fill",
                "order_id": "old-order",
                "symbol": "OLD260417C00500000",
                "side": "buy",
                "qty": "1",
                "price": "1.25",
                "transaction_time": "2026-04-23T16:01:00Z",
            },
            {
                "id": "new-fill",
                "order_id": "new-order",
                "symbol": "SPY260417C00500000",
                "side": "buy",
                "qty": "1",
                "price": "1.25",
                "transaction_time": "2026-05-21T16:01:00Z",
            },
        ]
        return [(AlpacaFillActivity.model_validate(row), row) for row in rows]

    def list_positions(self) -> list[tuple[AlpacaPosition, dict]]:
        rows = [
            {
                "symbol": "OLD260417C00500000",
                "qty": "1",
                "market_value": "125.00",
                "cost_basis": "125.00",
                "unrealized_pl": "0.00",
            },
            {
                "symbol": "SPY260417C00500000",
                "qty": "1",
                "market_value": "125.00",
                "cost_basis": "125.00",
                "unrealized_pl": "0.00",
            },
        ]
        return [(AlpacaPosition.model_validate(row), row) for row in rows]


class FailingReconciliationClient:
    def list_orders(self, *, limit: int) -> list[tuple[AlpacaSubmittedOrder, dict]]:
        raise AlpacaTradingError("Alpaca is unavailable")


def build_fill(fill_id: str, order_id: str = "alpaca-order-123") -> tuple[AlpacaFillActivity, dict]:
    raw = {
        "id": fill_id,
        "order_id": order_id,
        "symbol": "SPY260417C00500000",
        "side": "buy",
        "qty": "1",
        "price": "1.25",
        "transaction_time": "2026-04-23T16:01:00Z",
    }
    return AlpacaFillActivity.model_validate(raw), raw


class PaginatedReconciliationClient(SuccessfulReconciliationClient):
    def __init__(self, pages: list[list[tuple[AlpacaFillActivity, dict]]]) -> None:
        self.pages = pages
        self.fill_calls: list[dict[str, object]] = []

    def list_fill_activities(
        self,
        *,
        page_size: int,
        page_token: str | None = None,
    ) -> list[tuple[AlpacaFillActivity, dict]]:
        self.fill_calls.append({"page_size": page_size, "page_token": page_token})
        index = len(self.fill_calls) - 1
        if index >= len(self.pages):
            return []
        return self.pages[index]

    def list_positions(self) -> list[tuple[AlpacaPosition, dict]]:
        return []


class BrokerReconciliationTests(unittest.TestCase):
    def test_reconcile_broker_state_persists_orders_fills_positions_and_job_run(self) -> None:
        db = FakeReconciliationSession(
            scalar_results=[
                None,
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
        self.assertIn(AuditLog, added_types)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "broker_reconciliation.succeeded")
        self.assertEqual(audit_logs[-1].payload["orders_seen"], 1)
        self.assertEqual(audit_logs[-1].payload["fill_page_size_used"], 100)

    def test_reconcile_broker_state_imports_multiple_fill_pages(self) -> None:
        client = PaginatedReconciliationClient(
            [
                [build_fill("fill-1"), build_fill("fill-2")],
                [build_fill("fill-3")],
            ]
        )
        db = FakeReconciliationSession(
            scalar_results=[
                None,
                None,
                None,
                SimpleNamespace(id=uuid.uuid4()),
                None,
                SimpleNamespace(id=uuid.uuid4()),
                None,
                SimpleNamespace(id=uuid.uuid4()),
            ]
        )

        result = reconcile_broker_state(
            db,
            trading_client=client,
            fill_page_size=2,
        )

        self.assertEqual(result.fills_seen, 3)
        self.assertEqual(result.fills_created, 3)
        self.assertEqual(result.fill_pages_fetched, 2)
        self.assertEqual(result.fill_page_size_used, 2)
        self.assertEqual(result.fill_pagination_stop_reason, "short_page_no_next_page")
        self.assertEqual(
            client.fill_calls,
            [
                {"page_size": 2, "page_token": None},
                {"page_size": 2, "page_token": "fill-2"},
            ],
        )

    def test_reconcile_broker_state_handles_empty_first_fill_page(self) -> None:
        client = PaginatedReconciliationClient([[]])
        db = FakeReconciliationSession(scalar_results=[None])

        result = reconcile_broker_state(db, trading_client=client)

        self.assertEqual(result.fills_seen, 0)
        self.assertEqual(result.fills_created, 0)
        self.assertEqual(result.fill_pages_fetched, 1)
        self.assertEqual(result.fill_pagination_stop_reason, "empty_page_no_next_page")
        self.assertEqual(client.fill_calls, [{"page_size": 100, "page_token": None}])

    def test_reconcile_broker_state_does_not_duplicate_existing_fills(self) -> None:
        existing_fill = Fill(
            id=uuid.uuid4(),
            alpaca_fill_id="fill-1",
            symbol="SPY260417C00500000",
            side="buy",
            quantity=1,
            price=1,
            filled_at=AlpacaFillActivity.model_validate(
                {
                    "id": "fill-time",
                    "order_id": "alpaca-order-123",
                    "symbol": "SPY260417C00500000",
                    "side": "buy",
                    "qty": "1",
                    "price": "1.25",
                    "transaction_time": "2026-04-23T16:01:00Z",
                }
            ).transaction_time,
            raw_response={},
        )
        client = PaginatedReconciliationClient([[build_fill("fill-1")]])
        db = FakeReconciliationSession(scalar_results=[None, None, existing_fill])

        result = reconcile_broker_state(db, trading_client=client)

        self.assertEqual(result.fills_seen, 1)
        self.assertEqual(result.fills_created, 0)
        fills_added = [item for item in db.added if isinstance(item, Fill)]
        self.assertEqual(fills_added, [])

    def test_reconcile_broker_state_clamps_fill_page_size_to_alpaca_max(self) -> None:
        client = PaginatedReconciliationClient([[]])
        db = FakeReconciliationSession(scalar_results=[None])

        result = reconcile_broker_state(
            db,
            trading_client=client,
            fill_page_size=500,
        )

        self.assertEqual(result.fills_seen, 0)
        self.assertEqual(result.fill_page_size_requested, 500)
        self.assertEqual(result.fill_page_size_used, 100)
        self.assertEqual(client.fill_calls, [{"page_size": 100, "page_token": None}])
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].payload["fill_page_size_requested"], 500)
        self.assertEqual(audit_logs[-1].payload["fill_page_size_used"], 100)

    def test_reconcile_broker_state_updates_linked_order_intent_status(self) -> None:
        order_intent = OrderIntent(
            id=uuid.uuid4(),
            underlying_symbol="SPY",
            option_symbol="SPY260417C00500000",
            side="buy",
            quantity=1,
            order_type="limit",
            status="pending_new",
            preview={},
        )
        broker_order = BrokerOrder(
            id=uuid.uuid4(),
            order_intent_id=order_intent.id,
            alpaca_order_id="alpaca-order-123",
            symbol="SPY260417C00500000",
            side="buy",
            quantity=1,
            order_type="limit",
            status="pending_new",
            raw_response={},
        )
        db = FakeReconciliationSession(
            scalar_results=[
                None,
                broker_order,
                None,
                SimpleNamespace(id=uuid.uuid4()),
            ],
            records={(OrderIntent, order_intent.id): order_intent},
        )

        reconcile_broker_state(
            db,
            trading_client=SuccessfulReconciliationClient(),
        )

        self.assertEqual(order_intent.status, "filled")
        self.assertEqual(order_intent.submitted_at.isoformat(), "2026-04-23T16:00:00+00:00")
        self.assertEqual(broker_order.status, "filled")
        self.assertIn(order_intent, db.added)

    def test_reconcile_broker_state_ignores_broker_history_before_reset(self) -> None:
        reset_job = SimpleNamespace(
            started_at=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        )
        db = FakeReconciliationSession(
            scalar_results=[
                reset_job,
                None,
                None,
                SimpleNamespace(id=uuid.uuid4()),
            ]
        )

        with patch(
            "app.services.broker_reconciliation._position_has_post_reset_activity",
            side_effect=[False, True],
        ):
            result = reconcile_broker_state(
                db,
                trading_client=ResetCutoffReconciliationClient(),
            )

        self.assertEqual(result.orders_seen, 2)
        self.assertEqual(result.orders_created, 1)
        self.assertEqual(result.orders_skipped_before_reset, 1)
        self.assertEqual(result.fills_seen, 2)
        self.assertEqual(result.fills_created, 1)
        self.assertEqual(result.fills_skipped_before_reset, 1)
        self.assertEqual(result.positions_seen, 2)
        self.assertEqual(result.position_snapshots_created, 1)
        self.assertEqual(result.positions_skipped_without_post_reset_activity, 1)

        broker_orders = [item for item in db.added if isinstance(item, BrokerOrder)]
        fills = [item for item in db.added if isinstance(item, Fill)]
        positions = [item for item in db.added if isinstance(item, PositionSnapshot)]
        self.assertEqual([item.alpaca_order_id for item in broker_orders], ["new-order"])
        self.assertEqual([item.alpaca_fill_id for item in fills], ["new-fill"])
        self.assertEqual([item.symbol for item in positions], ["SPY260417C00500000"])

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
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "broker_reconciliation.failed")
        self.assertIn("AlpacaTradingError", audit_logs[-1].payload["error"])


if __name__ == "__main__":
    unittest.main()
