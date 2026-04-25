import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.models import Strategy
from app.db.session import get_db
from app.schemas.strategies import StrategyCreate, StrategyRead, StrategyUpdate
from app.services.audit_logs import record_audit_log

router = APIRouter(
    prefix="/strategies",
    tags=["strategies"],
    dependencies=[Depends(require_admin)],
)


@router.post("", response_model=StrategyRead, status_code=status.HTTP_201_CREATED)
def create_strategy(
    payload: StrategyCreate,
    db: Annotated[Session, Depends(get_db)],
) -> Strategy:
    strategy = Strategy(**payload.model_dump())
    try:
        db.add(strategy)
        db.flush()
        record_audit_log(
            db,
            event_type="strategy.created",
            entity_type="strategy",
            entity_id=strategy.id,
            message="Strategy created",
            payload=_strategy_audit_payload(strategy),
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A strategy with this name already exists",
        ) from exc

    db.refresh(strategy)
    return strategy


@router.get("", response_model=list[StrategyRead])
def list_strategies(
    db: Annotated[Session, Depends(get_db)],
    is_active: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[Strategy]:
    statement = select(Strategy).order_by(Strategy.created_at.desc()).limit(limit)

    if is_active is not None:
        statement = statement.where(Strategy.is_active == is_active)

    return list(db.scalars(statement))


@router.get("/{strategy_id}", response_model=StrategyRead)
def get_strategy(
    strategy_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> Strategy:
    return _get_strategy_or_404(db, strategy_id)


@router.patch("/{strategy_id}", response_model=StrategyRead)
def update_strategy(
    strategy_id: uuid.UUID,
    payload: StrategyUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> Strategy:
    strategy = _get_strategy_or_404(db, strategy_id)
    changes = payload.model_dump(exclude_unset=True)

    for field_name, value in changes.items():
        setattr(strategy, field_name, value)

    try:
        db.add(strategy)
        db.flush()
        record_audit_log(
            db,
            event_type="strategy.updated",
            entity_type="strategy",
            entity_id=strategy.id,
            message="Strategy updated",
            payload={
                "changes": _json_safe_changes(changes),
                "strategy": _strategy_audit_payload(strategy),
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A strategy with this name already exists",
        ) from exc

    db.refresh(strategy)
    return strategy


def _get_strategy_or_404(db: Session, strategy_id: uuid.UUID) -> Strategy:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy '{strategy_id}' was not found",
        )
    return strategy


def _strategy_audit_payload(strategy: Strategy) -> dict[str, Any]:
    return {
        "name": strategy.name,
        "description": strategy.description,
        "is_active": strategy.is_active,
        "config": strategy.config,
    }


def _json_safe_changes(changes: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in changes.items()
    }
