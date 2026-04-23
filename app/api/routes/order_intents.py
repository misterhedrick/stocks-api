from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.models import OrderIntent
from app.db.session import get_db
from app.schemas.order_intents import OrderIntentCreate, OrderIntentRead

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
