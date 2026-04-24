from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BrokerOrder, Fill, JobRun, PositionSnapshot
from app.integrations.alpaca import (
    AlpacaFillActivity,
    AlpacaPosition,
    AlpacaSubmittedOrder,
    AlpacaTradingClient,
)


@dataclass(slots=True)
class BrokerReconciliationResult:
    job_run: JobRun
    orders_seen: int
    orders_created: int
    orders_updated: int
    fills_seen: int
    fills_created: int
    positions_seen: int
    position_snapshots_created: int


def reconcile_broker_state(
    db: Session,
    *,
    trading_client: AlpacaTradingClient | None = None,
    order_limit: int = 100,
    fill_page_size: int = 100,
) -> BrokerReconciliationResult:
    started_at = datetime.now(timezone.utc)
    job_run = JobRun(
        job_name="reconcile_broker",
        status="running",
        started_at=started_at,
        details={},
    )
    db.add(job_run)
    db.flush()

    try:
        client = trading_client or AlpacaTradingClient.from_settings()
        order_rows = client.list_orders(limit=order_limit)
        fill_rows = client.list_fill_activities(page_size=fill_page_size)
        position_rows = client.list_positions()

        orders_created = 0
        orders_updated = 0
        fills_created = 0
        captured_at = datetime.now(timezone.utc)

        for order, raw_order in order_rows:
            created = _upsert_broker_order(db, order, raw_order)
            if created:
                orders_created += 1
            else:
                orders_updated += 1

        for fill, raw_fill in fill_rows:
            if _insert_fill_if_new(db, fill, raw_fill):
                fills_created += 1

        for position, raw_position in position_rows:
            _create_position_snapshot(db, position, raw_position, captured_at)

        details = {
            "orders_seen": len(order_rows),
            "orders_created": orders_created,
            "orders_updated": orders_updated,
            "fills_seen": len(fill_rows),
            "fills_created": fills_created,
            "positions_seen": len(position_rows),
            "position_snapshots_created": len(position_rows),
        }
        job_run.status = "succeeded"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = details
        job_run.error = None

        db.add(job_run)
        db.commit()
        db.refresh(job_run)

        return BrokerReconciliationResult(
            job_run=job_run,
            orders_seen=len(order_rows),
            orders_created=orders_created,
            orders_updated=orders_updated,
            fills_seen=len(fill_rows),
            fills_created=fills_created,
            positions_seen=len(position_rows),
            position_snapshots_created=len(position_rows),
        )
    except Exception as exc:
        db.rollback()
        job_run.status = "failed"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = {}
        job_run.error = f"{exc.__class__.__name__}: {exc}"
        db.add(job_run)
        db.commit()
        db.refresh(job_run)
        raise


def _upsert_broker_order(
    db: Session,
    order: AlpacaSubmittedOrder,
    raw_order: dict,
) -> bool:
    broker_order = db.scalar(
        select(BrokerOrder).where(BrokerOrder.alpaca_order_id == order.id)
    )
    created = broker_order is None

    if broker_order is None:
        broker_order = BrokerOrder(alpaca_order_id=order.id)

    broker_order.symbol = order.symbol
    broker_order.side = order.side
    broker_order.quantity = order.qty
    broker_order.order_type = order.type
    broker_order.limit_price = order.limit_price
    broker_order.status = order.status
    broker_order.submitted_at = order.submitted_at
    broker_order.filled_at = order.filled_at
    broker_order.raw_response = raw_order

    db.add(broker_order)
    return created


def _insert_fill_if_new(
    db: Session,
    fill: AlpacaFillActivity,
    raw_fill: dict,
) -> bool:
    existing_fill = db.scalar(select(Fill).where(Fill.alpaca_fill_id == fill.id))
    if existing_fill is not None:
        return False

    broker_order_id = None
    if fill.order_id is not None:
        broker_order = db.scalar(
            select(BrokerOrder).where(BrokerOrder.alpaca_order_id == fill.order_id)
        )
        if broker_order is not None:
            broker_order_id = broker_order.id

    db.add(
        Fill(
            broker_order_id=broker_order_id,
            alpaca_fill_id=fill.id,
            symbol=fill.symbol,
            side=fill.side,
            quantity=fill.qty,
            price=fill.price,
            filled_at=fill.transaction_time,
            raw_response=raw_fill,
        )
    )
    return True


def _create_position_snapshot(
    db: Session,
    position: AlpacaPosition,
    raw_position: dict,
    captured_at: datetime,
) -> None:
    db.add(
        PositionSnapshot(
            symbol=position.symbol,
            quantity=position.qty,
            market_value=position.market_value,
            cost_basis=position.cost_basis,
            unrealized_pl=position.unrealized_pl,
            raw_position=raw_position,
            captured_at=captured_at,
        )
    )
