import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.models import Signal, Strategy
from app.db.session import get_db
from app.schemas.signals import SignalCreate, SignalRead, SignalUpdate
from app.services.audit_logs import record_audit_log

router = APIRouter(
    prefix="/signals",
    tags=["signals"],
    dependencies=[Depends(require_admin)],
)


@router.post("", response_model=SignalRead, status_code=status.HTTP_201_CREATED)
def create_signal(
    payload: SignalCreate,
    db: Annotated[Session, Depends(get_db)],
) -> Signal:
    if payload.strategy_id is not None:
        _get_strategy_or_404(db, payload.strategy_id)

    signal = Signal(**payload.model_dump())
    db.add(signal)
    db.flush()
    record_audit_log(
        db,
        event_type="signal.created",
        entity_type="signal",
        entity_id=signal.id,
        message="Signal created",
        payload=_signal_audit_payload(signal),
    )
    db.commit()
    db.refresh(signal)
    return signal


@router.get("", response_model=list[SignalRead])
def list_signals(
    db: Annotated[Session, Depends(get_db)],
    strategy_id: Annotated[uuid.UUID | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    symbol: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[Signal]:
    statement = select(Signal).order_by(Signal.created_at.desc()).limit(limit)

    if strategy_id is not None:
        statement = statement.where(Signal.strategy_id == strategy_id)
    if status_filter is not None:
        statement = statement.where(Signal.status == status_filter)
    if symbol is not None:
        statement = statement.where(Signal.symbol == symbol)

    return list(db.scalars(statement))


@router.get("/{signal_id}", response_model=SignalRead)
def get_signal(
    signal_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> Signal:
    return _get_signal_or_404(db, signal_id)


@router.patch("/{signal_id}", response_model=SignalRead)
def update_signal(
    signal_id: uuid.UUID,
    payload: SignalUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> Signal:
    signal = _get_signal_or_404(db, signal_id)
    changes = payload.model_dump(exclude_unset=True)

    strategy_id = changes.get("strategy_id")
    if strategy_id is not None:
        _get_strategy_or_404(db, strategy_id)

    for field_name, value in changes.items():
        setattr(signal, field_name, value)

    db.add(signal)
    db.flush()
    record_audit_log(
        db,
        event_type="signal.updated",
        entity_type="signal",
        entity_id=signal.id,
        message="Signal updated",
        payload={
            "changes": _json_safe_changes(changes),
            "signal": _signal_audit_payload(signal),
        },
    )
    db.commit()
    db.refresh(signal)
    return signal


def _get_strategy_or_404(db: Session, strategy_id: uuid.UUID) -> Strategy:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy '{strategy_id}' was not found",
        )
    return strategy


def _get_signal_or_404(db: Session, signal_id: uuid.UUID) -> Signal:
    signal = db.get(Signal, signal_id)
    if signal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Signal '{signal_id}' was not found",
        )
    return signal


def _signal_audit_payload(signal: Signal) -> dict[str, Any]:
    return {
        "strategy_id": str(signal.strategy_id) if signal.strategy_id is not None else None,
        "symbol": signal.symbol,
        "underlying_symbol": signal.underlying_symbol,
        "signal_type": signal.signal_type,
        "direction": signal.direction,
        "confidence": _decimal_to_string(signal.confidence),
        "status": signal.status,
        "rejected_reason": signal.rejected_reason,
        "market_context": signal.market_context,
    }


def _json_safe_changes(changes: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _decimal_to_string(value) if isinstance(value, Decimal) else value
        for key, value in changes.items()
    }


def _decimal_to_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)
