import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.models import OrderIntent
from app.db.session import get_db
from app.integrations.alpaca import (
    AlpacaOrderRejectedError,
    AlpacaTradingConfigurationError,
    AlpacaTradingError,
)
from app.schemas.order_intents import (
    BrokerOrderRead,
    OrderIntentCreate,
    OrderIntentRead,
    OrderIntentSubmissionRead,
)
from app.services.order_intents import (
    OrderIntentNotFoundError,
    OrderIntentStateError,
    submit_order_intent,
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
    order_intent = OrderIntent(**payload.model_dump())
    db.add(order_intent)
    db.commit()
    db.refresh(order_intent)
    return order_intent


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
