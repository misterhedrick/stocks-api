from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.models import OrderIntent
from app.db.session import get_db
from app.schemas.order_intents import OrderIntentCreate, OrderIntentRead
from app.services import order_intents as order_intent_service

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
    return order_intent_service.create_order_intent(db, payload)


@router.get("", response_model=list[OrderIntentRead])
def list_order_intents(
    db: Annotated[Session, Depends(get_db)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[OrderIntent]:
    return order_intent_service.list_order_intents(db, status_filter=status_filter, limit=limit)
