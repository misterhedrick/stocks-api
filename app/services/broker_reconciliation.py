from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter

logger = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BrokerOrder, Fill, JobRun, OrderIntent, PositionSnapshot
from app.integrations.alpaca import (
    ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE,
    AlpacaFillActivity,
    AlpacaPosition,
    AlpacaSubmittedOrder,
    AlpacaTradingClient,
)
from app.services.audit_logs import record_audit_log


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
    fill_page_size_requested: int = 100
    fill_page_size_used: int = 100
    fill_pages_fetched: int = 0
    fill_pagination_complete: bool = True
    fill_pagination_stop_reason: str = "not_run"
    orders_skipped_before_reset: int = 0
    fills_skipped_before_reset: int = 0
    positions_skipped_without_post_reset_activity: int = 0


@dataclass(slots=True)
class FillActivityPaginationResult:
    rows: list[tuple[AlpacaFillActivity, dict]]
    pages_fetched: int
    complete: bool
    stop_reason: str


def reconcile_broker_state(
    db: Session,
    *,
    trading_client: AlpacaTradingClient | None = None,
    order_limit: int = 100,
    fill_page_size: int = 100,
    deadline: float | None = None,
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
        reset_cutoff = _latest_trading_reset_cutoff(db)
        order_rows = client.list_orders(limit=order_limit)
        requested_fill_page_size = fill_page_size
        safe_fill_page_size = _safe_fill_page_size(fill_page_size)
        fill_page = _list_all_fill_activities(
            client,
            page_size=safe_fill_page_size,
            requested_page_size=requested_fill_page_size,
            deadline=deadline,
        )
        fill_rows = fill_page.rows
        position_rows = client.list_positions()

        orders_created = 0
        orders_updated = 0
        fills_created = 0
        orders_skipped_before_reset = 0
        fills_skipped_before_reset = 0
        positions_skipped_without_post_reset_activity = 0
        captured_at = datetime.now(timezone.utc)

        for order, raw_order in order_rows:
            if not _order_is_after_reset(order, reset_cutoff):
                orders_skipped_before_reset += 1
                continue
            created = _upsert_broker_order(db, order, raw_order)
            if created:
                orders_created += 1
            else:
                orders_updated += 1

        for fill, raw_fill in fill_rows:
            if reset_cutoff is not None and fill.transaction_time < reset_cutoff:
                fills_skipped_before_reset += 1
                continue
            if _insert_fill_if_new(db, fill, raw_fill):
                fills_created += 1

        for position, raw_position in position_rows:
            if not _position_has_post_reset_activity(db, position, reset_cutoff):
                positions_skipped_without_post_reset_activity += 1
                continue
            _create_position_snapshot(db, position, raw_position, captured_at)

        details = {
            "orders_seen": len(order_rows),
            "orders_created": orders_created,
            "orders_updated": orders_updated,
            "orders_skipped_before_reset": orders_skipped_before_reset,
            "fills_seen": len(fill_rows),
            "fills_created": fills_created,
            "fills_skipped_before_reset": fills_skipped_before_reset,
            "fill_page_size_requested": requested_fill_page_size,
            "fill_page_size_used": safe_fill_page_size,
            "fill_pages_fetched": fill_page.pages_fetched,
            "fill_pagination_complete": fill_page.complete,
            "fill_pagination_stop_reason": fill_page.stop_reason,
            "positions_seen": len(position_rows),
            "position_snapshots_created": (
                len(position_rows) - positions_skipped_without_post_reset_activity
            ),
            "positions_skipped_without_post_reset_activity": (
                positions_skipped_without_post_reset_activity
            ),
            "trading_data_reset_cutoff": (
                reset_cutoff.isoformat() if reset_cutoff is not None else None
            ),
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
            position_snapshots_created=(
                len(position_rows) - positions_skipped_without_post_reset_activity
            ),
            fill_page_size_requested=requested_fill_page_size,
            fill_page_size_used=safe_fill_page_size,
            fill_pages_fetched=fill_page.pages_fetched,
            fill_pagination_complete=fill_page.complete,
            fill_pagination_stop_reason=fill_page.stop_reason,
            orders_skipped_before_reset=orders_skipped_before_reset,
            fills_skipped_before_reset=fills_skipped_before_reset,
            positions_skipped_without_post_reset_activity=(
                positions_skipped_without_post_reset_activity
            ),
        )
    except Exception as exc:
        logger.error("Broker reconciliation failed: %s: %s", exc.__class__.__name__, exc)
        try:
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
        except Exception:
            logger.exception("Failed to record broker reconciliation failure for job_run %s", job_run.id)
        raise


def _safe_fill_page_size(page_size: int) -> int:
    if page_size < 1:
        return 1
    if page_size > ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE:
        logger.warning(
            "Requested Alpaca FILL page_size=%d exceeds max=%d; clamping to %d",
            page_size,
            ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE,
            ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE,
        )
        return ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE
    return page_size


def _list_all_fill_activities(
    client: AlpacaTradingClient,
    *,
    page_size: int,
    requested_page_size: int,
    deadline: float | None = None,
) -> FillActivityPaginationResult:
    all_rows: list[tuple[AlpacaFillActivity, dict]] = []
    page_token: str | None = None
    seen_page_tokens: set[str] = set()
    page_number = 0
    stop_reason = "budget_exceeded"

    logger.info(
        "Fetching Alpaca FILL activities with requested_page_size=%d page_size=%d",
        requested_page_size,
        page_size,
    )

    while True:
        if deadline is not None and perf_counter() >= deadline:
            logger.warning(
                "Alpaca FILL pagination stopped: runtime budget exceeded after %d page(s); total_fills_seen=%d",
                page_number,
                len(all_rows),
            )
            break
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
            stop_reason = "empty_page_no_next_page"
            logger.info(
                "Alpaca FILL pagination stopped: no next page available; total_fills_seen=%d",
                len(all_rows),
            )
            break

        all_rows.extend(rows)

        if len(rows) < page_size:
            stop_reason = "short_page_no_next_page"
            logger.info(
                "Alpaca FILL pagination stopped: final page returned fewer than page_size, no next page available; total_fills_seen=%d",
                len(all_rows),
            )
            break

        next_page_token = rows[-1][0].id
        if not next_page_token or next_page_token in seen_page_tokens:
            stop_reason = "missing_or_repeated_next_page_token"
            logger.warning(
                "Alpaca FILL pagination stopped: no new next page token; total_fills_seen=%d",
                len(all_rows),
            )
            break
        seen_page_tokens.add(next_page_token)
        page_token = next_page_token

    return FillActivityPaginationResult(
        rows=all_rows,
        pages_fetched=page_number,
        complete=stop_reason not in {"missing_or_repeated_next_page_token", "budget_exceeded"},
        stop_reason=stop_reason,
    )


def _latest_trading_reset_cutoff(db: Session) -> datetime | None:
    job_run = db.scalar(
        select(JobRun)
        .where(JobRun.job_name == "trading_data_reset")
        .where(JobRun.status == "succeeded")
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )
    if job_run is None:
        return None
    return job_run.started_at


def _order_is_after_reset(
    order: AlpacaSubmittedOrder,
    reset_cutoff: datetime | None,
) -> bool:
    if reset_cutoff is None:
        return True
    order_time = order.submitted_at or order.filled_at
    return order_time is not None and order_time >= reset_cutoff


def _position_has_post_reset_activity(
    db: Session,
    position: AlpacaPosition,
    reset_cutoff: datetime | None,
) -> bool:
    if reset_cutoff is None:
        return True
    broker_order_count = db.scalar(
        select(BrokerOrder.id)
        .where(BrokerOrder.symbol == position.symbol)
        .where(BrokerOrder.submitted_at.is_not(None))
        .where(BrokerOrder.submitted_at >= reset_cutoff)
        .limit(1)
    )
    if broker_order_count is not None:
        return True
    fill_count = db.scalar(
        select(Fill.id)
        .where(Fill.symbol == position.symbol)
        .where(Fill.filled_at >= reset_cutoff)
        .limit(1)
    )
    return fill_count is not None


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
