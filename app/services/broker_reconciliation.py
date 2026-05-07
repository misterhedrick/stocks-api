from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BrokerOrder, Fill, JobRun, OrderIntent, PositionSnapshot
from app.integrations.alpaca import (
    AlpacaFillActivity,
    AlpacaPosition,
    AlpacaSubmittedOrder,
    AlpacaTradingClient,
)
from app.services.audit_logs import record_audit_log

ALPACA_FILL_PAGE_SIZE_MAX = 100


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
        requested_fill_page_size = fill_page_size
        safe_fill_page_size = _safe_fill_page_size(fill_page_size)
        fill_rows = _list_all_fill_activities(
            client,
            page_size=safe_fill_page_size,
            requested_page_size=requested_fill_page_size,
        )
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
            "fill_page_size_requested": requested_fill_page_size,
            "fill_page_size_used": safe_fill_page_size,
            "positions_seen": len(position_rows),
            "position_snapshots_created": len(position_rows),
        }
        job_run.status = "succeeded"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = details
        job_run.error = None

        logger.info(
            "Broker reconciliation succeeded: %d orders, %d fills, %d positions",
            len(order_rows),
            len(fill_rows),
            len(position_rows),
        )
        db.add(job_run)
        record_audit_log(
            db,
            event_type="broker_reconciliation.succeeded",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Broker reconciliation succeeded",
            payload=details,
        )
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
        logger.error("Broker reconciliation failed: %s: %s", exc.__class__.__name__, exc)
        db.rollback()
        job_run.status = "failed"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = {}
        job_run.error = f"{exc.__class__.__name__}: {exc}"
        db.add(job_run)
        record_audit_log(
            db,
            event_type="broker_reconciliation.failed",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Broker reconciliation failed",
            payload={"error": job_run.error},
        )
        db.commit()
        db.refresh(job_run)
        raise


def _safe_fill_page_size(page_size: int) -> int:
    if page_size < 1:
        return 1
    if page_size > ALPACA_FILL_PAGE_SIZE_MAX:
        logger.warning(
            "Requested Alpaca FILL page_size=%d exceeds max=%d; clamping to %d",
            page_size,
            ALPACA_FILL_PAGE_SIZE_MAX,
            ALPACA_FILL_PAGE_SIZE_MAX,
        )
        return ALPACA_FILL_PAGE_SIZE_MAX
    return page_size


def _list_all_fill_activities(
    client: AlpacaTradingClient,
    *,
    page_size: int,
    requested_page_size: int,
) -> list[tuple[AlpacaFillActivity, dict]]:
    all_rows: list[tuple[AlpacaFillActivity, dict]] = []
    page_token: str | None = None
    seen_page_tokens: set[str] = set()
    page_number = 0

    logger.info(
        "Fetching Alpaca FILL activities with requested_page_size=%d page_size=%d",
        requested_page_size,
        page_size,
    )

    while True:
        page_number += 1
        rows = client.list_fill_activities(
            page_size=page_size,
            page_token=page_token,
        )
        logger.info(
            "Fetched Alpaca FILL activities page=%d page_size=%d returned=%d page_token=%s",
            page_number,
            page_size,
            len(rows),
            page_token,
        )
        if not rows:
            logger.info(
                "Alpaca FILL pagination stopped: no next page available; total_fills_seen=%d",
                len(all_rows),
            )
            break

        all_rows.extend(rows)

        if len(rows) < page_size:
            logger.info(
                "Alpaca FILL pagination stopped: final page returned fewer than page_size, no next page available; total_fills_seen=%d",
                len(all_rows),
            )
            break

        next_page_token = rows[-1][0].id
        if not next_page_token or next_page_token in seen_page_tokens:
            logger.warning(
                "Alpaca FILL pagination stopped: no new next page token; total_fills_seen=%d",
                len(all_rows),
            )
            break
        seen_page_tokens.add(next_page_token)
        page_token = next_page_token

    return all_rows


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

    order_intent = _find_order_intent_for_broker_order(db, broker_order, order)
    if order_intent is not None:
        broker_order.order_intent_id = order_intent.id

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
    if order_intent is not None:
        order_intent.status = order.status
        order_intent.submitted_at = order.submitted_at
        db.add(order_intent)
    return created


def _find_order_intent_for_broker_order(
    db: Session,
    broker_order: BrokerOrder,
    order: AlpacaSubmittedOrder,
) -> OrderIntent | None:
    if broker_order.order_intent is not None:
        return broker_order.order_intent
    if broker_order.order_intent_id is not None:
        return db.get(OrderIntent, broker_order.order_intent_id)
    if order.client_order_id is None:
        return None

    try:
        order_intent_id = uuid.UUID(order.client_order_id)
    except ValueError:
        return None
    return db.get(OrderIntent, order_intent_id)


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
