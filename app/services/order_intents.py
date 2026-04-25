from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.db.models import BrokerOrder, OrderIntent, Signal
from app.integrations.alpaca import (
    AlpacaLatestOptionQuote,
    AlpacaMarketDataClient,
    AlpacaOrderRejectedError,
    AlpacaTradingClient,
    coerce_alpaca_datetime,
)
from app.schemas.order_intents import OrderIntentPreviewCreate
from app.services.audit_logs import record_audit_log


class OrderIntentNotFoundError(LookupError):
    pass


class OrderIntentStateError(RuntimeError):
    def __init__(self, current_status: str) -> None:
        super().__init__(f"Order intent is in status '{current_status}'")
        self.current_status = current_status


class SignalNotFoundError(LookupError):
    pass


class OrderIntentPreviewError(RuntimeError):
    pass


def preview_order_intent_from_signal(
    db: Session,
    payload: OrderIntentPreviewCreate,
    *,
    market_data_client: AlpacaMarketDataClient | None = None,
) -> OrderIntent:
    signal = db.get(Signal, payload.signal_id)
    if signal is None:
        raise SignalNotFoundError(f"Signal '{payload.signal_id}' was not found")

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    latest_quote = client.get_latest_option_quote(
        payload.option_symbol,
        feed=payload.data_feed,
    )
    quote_preview = _build_quote_preview(
        latest_quote,
        side=payload.side,
        quantity=payload.quantity,
        supplied_limit_price=payload.limit_price,
    )

    limit_price = payload.limit_price
    if payload.order_type == "limit" and limit_price is None:
        limit_price = _decimal_from_preview(quote_preview.get("suggested_limit_price"))
        if limit_price is None:
            raise OrderIntentPreviewError(
                "Unable to derive a limit price from the latest option quote"
            )

    order_intent = OrderIntent(
        strategy_id=signal.strategy_id,
        signal_id=signal.id,
        underlying_symbol=signal.underlying_symbol or signal.symbol,
        option_symbol=payload.option_symbol,
        side=payload.side,
        quantity=payload.quantity,
        order_type=payload.order_type,
        limit_price=limit_price,
        time_in_force=payload.time_in_force,
        status="previewed",
        rationale=payload.rationale or signal.rationale,
        preview={
            "source": "alpaca_latest_option_quote",
            "data_feed": payload.data_feed,
            "signal": {
                "id": str(signal.id),
                "strategy_id": str(signal.strategy_id)
                if signal.strategy_id is not None
                else None,
                "signal_type": signal.signal_type,
                "direction": signal.direction,
                "confidence": str(signal.confidence)
                if signal.confidence is not None
                else None,
                "status": signal.status,
            },
            "quote": quote_preview,
        },
    )

    db.add(order_intent)
    db.flush()
    record_audit_log(
        db,
        event_type="order_intent.previewed",
        entity_type="order_intent",
        entity_id=order_intent.id,
        message="Order intent preview generated from signal",
        payload={
            "signal_id": str(signal.id),
            "strategy_id": str(signal.strategy_id)
            if signal.strategy_id is not None
            else None,
            "option_symbol": order_intent.option_symbol,
            "side": order_intent.side,
            "quantity": order_intent.quantity,
            "order_type": order_intent.order_type,
            "limit_price": str(order_intent.limit_price)
            if order_intent.limit_price is not None
            else None,
            "preview_source": order_intent.preview.get("source"),
        },
    )
    db.commit()
    db.refresh(order_intent)
    return order_intent


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


def _build_quote_preview(
    latest_quote: AlpacaLatestOptionQuote,
    *,
    side: str,
    quantity: int,
    supplied_limit_price: Decimal | None,
) -> dict[str, object]:
    quote = latest_quote.quote
    bid_price = quote.bid_price
    ask_price = quote.ask_price
    midpoint = _midpoint(bid_price, ask_price)
    spread = ask_price - bid_price if bid_price is not None and ask_price is not None else None
    suggested_limit_price = supplied_limit_price or midpoint
    estimated_price = supplied_limit_price or _side_price(
        side,
        bid_price=bid_price,
        ask_price=ask_price,
        fallback=midpoint,
    )
    estimated_notional = (
        estimated_price * Decimal(quantity) * Decimal("100")
        if estimated_price is not None
        else None
    )

    return {
        "symbol": latest_quote.symbol,
        "bid_price": _decimal_to_string(bid_price),
        "bid_size": _decimal_to_string(quote.bid_size),
        "ask_price": _decimal_to_string(ask_price),
        "ask_size": _decimal_to_string(quote.ask_size),
        "midpoint": _decimal_to_string(midpoint),
        "spread": _decimal_to_string(spread),
        "suggested_limit_price": _decimal_to_string(suggested_limit_price),
        "estimated_price": _decimal_to_string(estimated_price),
        "estimated_notional": _decimal_to_string(estimated_notional),
        "contract_multiplier": "100",
        "quote_timestamp": quote.timestamp.isoformat()
        if quote.timestamp is not None
        else None,
        "raw_quote": _json_safe_value(latest_quote.raw_response),
    }


def _midpoint(
    bid_price: Decimal | None,
    ask_price: Decimal | None,
) -> Decimal | None:
    if bid_price is None or ask_price is None:
        return None
    return ((bid_price + ask_price) / Decimal("2")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def _side_price(
    side: str,
    *,
    bid_price: Decimal | None,
    ask_price: Decimal | None,
    fallback: Decimal | None,
) -> Decimal | None:
    if side == "buy":
        return ask_price or fallback
    return bid_price or fallback


def _decimal_to_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _decimal_from_preview(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _json_safe_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    return value
