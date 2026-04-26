from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.session import get_db
from app.integrations.alpaca import (
    AlpacaTradingConfigurationError,
    AlpacaTradingError,
)
from app.schemas.jobs import (
    BrokerReconciliationRead,
    JobRunRead,
    MarketCycleRead,
    SignalScanRead,
)
from app.services.broker_reconciliation import reconcile_broker_state
from app.services.market_cycle import run_market_cycle
from app.services.signal_scanner import scan_signals

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(require_admin)],
)


@router.post(
    "/reconcile-broker",
    response_model=BrokerReconciliationRead,
    status_code=status.HTTP_200_OK,
)
def reconcile_broker_route(
    db: Annotated[Session, Depends(get_db)],
    order_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    fill_page_size: Annotated[int, Query(ge=1, le=500)] = 100,
) -> BrokerReconciliationRead:
    try:
        result = reconcile_broker_state(
            db,
            order_limit=order_limit,
            fill_page_size=fill_page_size,
        )
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

    return BrokerReconciliationRead(
        job_run=JobRunRead.model_validate(result.job_run),
        orders_seen=result.orders_seen,
        orders_created=result.orders_created,
        orders_updated=result.orders_updated,
        fills_seen=result.fills_seen,
        fills_created=result.fills_created,
        positions_seen=result.positions_seen,
        position_snapshots_created=result.position_snapshots_created,
    )


@router.post(
    "/scan-signals",
    response_model=SignalScanRead,
    status_code=status.HTTP_200_OK,
)
def scan_signals_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SignalScanRead:
    result = scan_signals(db, limit=limit)
    return SignalScanRead(
        job_run=JobRunRead.model_validate(result.job_run),
        strategies_seen=result.strategies_seen,
        strategies_scanned=result.strategies_scanned,
        signals_created=result.signals_created,
        signals_skipped=result.signals_skipped,
        errors=result.errors,
        no_signal_reasons=result.no_signal_reasons,
        created_signal_ids=result.created_signal_ids,
    )


@router.post(
    "/market-cycle",
    response_model=MarketCycleRead,
    status_code=status.HTTP_200_OK,
)
def market_cycle_route(
    db: Annotated[Session, Depends(get_db)],
    scan_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    order_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    fill_page_size: Annotated[int, Query(ge=1, le=500)] = 100,
) -> MarketCycleRead:
    try:
        result = run_market_cycle(
            db,
            scan_limit=scan_limit,
            order_limit=order_limit,
            fill_page_size=fill_page_size,
        )
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

    return MarketCycleRead(
        job_run=JobRunRead.model_validate(result.job_run),
        scan_enabled=result.scan_enabled,
        reconcile_enabled=result.reconcile_enabled,
        preview_enabled=result.preview_enabled,
        submit_enabled=result.submit_enabled,
        scan=result.scan,
        reconcile=result.reconcile,
        preview=result.preview,
        submit=result.submit,
    )
