import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.models import OrderIntent, Signal, Strategy
from app.db.session import get_db
from app.integrations.alpaca import (
    AlpacaOrderRejectedError,
    AlpacaTradingConfigurationError,
    AlpacaTradingError,
)
from app.schemas.order_intents import (
    BrokerOrderRead,
    OrderIntentCreate,
    OrderIntentPreviewCreate,
    OrderIntentRead,
    OrderIntentSubmissionRead,
)
from app.services.audit_logs import record_audit_log
from app.services.order_intents import (
    OrderIntentPreviewError,
    OrderIntentNotFoundError,
    OrderIntentStateError,
    SignalNotFoundError,
    preview_order_intent_from_signal,
    submit_order_intent,
)
from app.services.option_contracts import (
    OptionContractNotFoundError,
    OptionContractSelectionError,
)

router = APIRouter(
    prefix="/order-intents",
    tags=["order_intents"],
    dependencies=[Depends(require_admin)],
)


@router.post("", response_model=OrderIntentRead, status_code=status.HTTP_201_CREATED)
def create_order_intent(
    payload: OrderIntentCreate,
    db: Annotated[Session, Depends(get_db)],
) -> OrderIntent:
    _validate_order_intent_references(db, payload)

    order_intent = OrderIntent(**payload.model_dump())
    db.add(order_intent)
    db.flush()
    record_audit_log(
        db,
        event_type="order_intent.created",
        entity_type="order_intent",
        entity_id=order_intent.id,
        message="Order intent created",
        payload={
            "underlying_symbol": order_intent.underlying_symbol,
            "option_symbol": order_intent.option_symbol,
            "side": order_intent.side,
            "quantity": order_intent.quantity,
            "order_type": order_intent.order_type,
            "limit_price": str(order_intent.limit_price)
            if order_intent.limit_price is not None
            else None,
            "time_in_force": order_intent.time_in_force,
            "status": order_intent.status,
        },
    )
    db.commit()
    db.refresh(order_intent)
    return order_intent


@router.post("/preview", response_model=OrderIntentRead, status_code=status.HTTP_201_CREATED)
def preview_order_intent(
    payload: OrderIntentPreviewCreate,
    db: Annotated[Session, Depends(get_db)],
) -> OrderIntent:
    try:
        return preview_order_intent_from_signal(db, payload)
    except SignalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except OptionContractNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except OptionContractSelectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except OrderIntentPreviewError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except AlpacaTradingConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except AlpacaTradingError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.detail,
        ) from exc


@router.get("", response_model=list[OrderIntentRead])
def list_order_intents(
    db: Annotated[Session, Depends(get_db)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[OrderIntent]:
    statement = select(OrderIntent).order_by(OrderIntent.created_at.desc()).limit(limit)

    if status_filter is not None:
        statement = statement.where(OrderIntent.status == status_filter)

    return list(db.scalars(statement))


@router.post(
    "/{order_intent_id}/submit",
    response_model=OrderIntentSubmissionRead,
    status_code=status.HTTP_200_OK,
)
def submit_order_intent_route(
    order_intent_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> OrderIntentSubmissionRead:
    try:
        order_intent, broker_order = submit_order_intent(db, order_intent_id)
    except OrderIntentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except OrderIntentStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only previewed order intents can be submitted. Current status: {exc.current_status}",
        ) from exc
    except AlpacaTradingConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except AlpacaOrderRejectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.detail,
        ) from exc
    except AlpacaTradingError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.detail,
        ) from exc

    return OrderIntentSubmissionRead(
        order_intent=OrderIntentRead.model_validate(order_intent),
        broker_order=BrokerOrderRead.model_validate(broker_order),
    )


def _validate_order_intent_references(
    db: Session,
    payload: OrderIntentCreate,
) -> None:
    strategy = None
    signal = None

    if payload.strategy_id is not None:
        strategy = db.get(Strategy, payload.strategy_id)
        if strategy is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Strategy '{payload.strategy_id}' was not found",
            )

    if payload.signal_id is not None:
        signal = db.get(Signal, payload.signal_id)
        if signal is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Signal '{payload.signal_id}' was not found",
            )

    if strategy is not None and signal is not None and signal.strategy_id != strategy.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order intent strategy_id must match the signal's strategy_id",
        )
