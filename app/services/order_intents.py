from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import BrokerOrder, OrderIntent
from app.integrations.alpaca import (
    AlpacaOrderRejectedError,
    AlpacaTradingClient,
    coerce_alpaca_datetime,
)
from app.services.audit_logs import record_audit_log


class OrderIntentNotFoundError(LookupError):
    pass


class OrderIntentStateError(RuntimeError):
    def __init__(self, current_status: str) -> None:
        super().__init__(f"Order intent is in status '{current_status}'")
        self.current_status = current_status


def submit_order_intent(
    db: Session,
    order_intent_id: uuid.UUID,
    *,
    trading_client: AlpacaTradingClient | None = None,
) -> tuple[OrderIntent, BrokerOrder]:
    order_intent = db.get(OrderIntent, order_intent_id)
    if order_intent is None:
        raise OrderIntentNotFoundError(f"Order intent '{order_intent_id}' was not found")

    if order_intent.status != "previewed":
        raise OrderIntentStateError(order_intent.status)

    client = trading_client or AlpacaTradingClient.from_settings()

    try:
        submission = client.submit_order_intent(order_intent)
    except AlpacaOrderRejectedError as exc:
        order_intent.status = "rejected"
        order_intent.rejection_reason = exc.detail
        db.add(order_intent)
        record_audit_log(
            db,
            event_type="order_intent.rejected",
            entity_type="order_intent",
            entity_id=order_intent.id,
            message="Alpaca rejected order intent submission",
            payload={
                "option_symbol": order_intent.option_symbol,
                "side": order_intent.side,
                "quantity": order_intent.quantity,
                "order_type": order_intent.order_type,
                "status": order_intent.status,
                "rejection_reason": exc.detail,
                "alpaca_status_code": exc.status_code,
            },
        )
        db.commit()
        db.refresh(order_intent)
        raise

    submitted_at = submission.order.submitted_at or datetime.now(timezone.utc)
    filled_at = coerce_alpaca_datetime(submission.order.filled_at)

    broker_order = BrokerOrder(
        order_intent_id=order_intent.id,
        alpaca_order_id=submission.order.id,
        symbol=submission.order.symbol,
        side=submission.order.side,
        quantity=submission.order.qty,
        order_type=submission.order.type,
        limit_price=submission.order.limit_price,
        status=submission.order.status,
        submitted_at=submitted_at,
        filled_at=filled_at,
        raw_response=submission.raw_response,
    )

    order_intent.status = submission.order.status or "submitted"
    order_intent.submitted_at = submitted_at
    order_intent.rejection_reason = None

    db.add(broker_order)
    db.add(order_intent)
    db.flush()
    record_audit_log(
        db,
        event_type="order_intent.submitted",
        entity_type="order_intent",
        entity_id=order_intent.id,
        message="Order intent submitted to Alpaca",
        payload={
            "broker_order_id": str(broker_order.id),
            "alpaca_order_id": broker_order.alpaca_order_id,
            "option_symbol": order_intent.option_symbol,
            "side": order_intent.side,
            "quantity": order_intent.quantity,
            "order_type": order_intent.order_type,
            "status": order_intent.status,
        },
    )
    db.commit()
    db.refresh(order_intent)
    db.refresh(broker_order)
    return order_intent, broker_order
