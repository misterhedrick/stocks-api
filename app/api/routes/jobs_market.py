from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.alpaca_errors import alpaca_error_status_code
from app.core.security import require_admin
from app.db.session import get_db
from app.integrations.alpaca import (
    ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE,
    AlpacaTradingConfigurationError,
    AlpacaTradingError,
)
from app.schemas.jobs import JobRunRead, MarketCycleRead
from app.services.market_cycle import (
    normalize_market_entry_symbol,
    run_market_cycle,
    run_market_entry_cycle,
)

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(require_admin)],
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
    fill_page_size: Annotated[
        int,
        Query(ge=1, le=ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE),
    ] = ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE,
    phase_timeout_seconds: Annotated[int | None, Query(ge=0, le=600)] = None,
) -> MarketCycleRead:
    try:
        cycle_kwargs = {}
        if phase_timeout_seconds is not None:
            cycle_kwargs["phase_timeout_seconds"] = phase_timeout_seconds
        result = run_market_cycle(
            db,
            scan_limit=scan_limit,
            order_limit=order_limit,
            fill_page_size=fill_page_size,
            exit_enabled_override=False,
            **cycle_kwargs,
        )
    except AlpacaTradingConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except AlpacaTradingError as exc:
        raise HTTPException(
            status_code=alpaca_error_status_code(exc),
            detail=exc.detail,
        ) from exc

    return _market_cycle_read(result)


@router.post(
    "/market-entry-cycle",
    response_model=MarketCycleRead,
    status_code=status.HTTP_200_OK,
)
def market_entry_cycle_route(
    db: Annotated[Session, Depends(get_db)],
    symbol: Annotated[str, Query(min_length=1, max_length=16)],
    scan_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    order_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    fill_page_size: Annotated[
        int,
        Query(ge=1, le=ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE),
    ] = ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE,
    phase_timeout_seconds: Annotated[int | None, Query(ge=0, le=600)] = None,
) -> MarketCycleRead:
    try:
        normalized_symbol = normalize_market_entry_symbol(symbol)
        cycle_kwargs = {}
        if phase_timeout_seconds is not None:
            cycle_kwargs["phase_timeout_seconds"] = phase_timeout_seconds
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    try:
        result = run_market_entry_cycle(
            db,
            symbol=normalized_symbol,
            scan_limit=scan_limit,
            order_limit=order_limit,
            fill_page_size=fill_page_size,
            **cycle_kwargs,
        )
    except AlpacaTradingConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except AlpacaTradingError as exc:
        raise HTTPException(
            status_code=alpaca_error_status_code(exc),
            detail=exc.detail,
        ) from exc

    return _market_cycle_read(result)


@router.post(
    "/market-cycle-exits",
    response_model=MarketCycleRead,
    status_code=status.HTTP_200_OK,
)
def market_cycle_exits_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    order_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    fill_page_size: Annotated[
        int,
        Query(ge=1, le=ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE),
    ] = ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE,
    submit_enabled: bool | None = None,
    phase_timeout_seconds: Annotated[int | None, Query(ge=0, le=600)] = None,
) -> MarketCycleRead:
    try:
        cycle_kwargs = {}
        if phase_timeout_seconds is not None:
            cycle_kwargs["phase_timeout_seconds"] = phase_timeout_seconds
        result = run_market_cycle(
            db,
            scan_limit=limit,
            order_limit=order_limit,
            fill_page_size=fill_page_size,
            scan_enabled_override=False,
            preview_enabled_override=False,
            news_enabled_override=False,
            exit_enabled_override=True,
            submit_enabled_override=submit_enabled,
            reconcile_before_exit=True,
            **cycle_kwargs,
        )
    except AlpacaTradingConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except AlpacaTradingError as exc:
        raise HTTPException(
            status_code=alpaca_error_status_code(exc),
            detail=exc.detail,
        ) from exc

    return _market_cycle_read(result)


@router.post(
    "/market-cycle-stress",
    response_model=MarketCycleRead,
    status_code=status.HTTP_200_OK,
)
def market_cycle_stress_route(
    db: Annotated[Session, Depends(get_db)],
    scan_limit: Annotated[int, Query(ge=1, le=500)] = 130,
    order_limit: Annotated[int, Query(ge=1, le=500)] = 25,
    fill_page_size: Annotated[
        int,
        Query(ge=1, le=ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE),
    ] = 25,
    preview_enabled: bool = True,
    reconcile_enabled: bool = True,
    phase_timeout_seconds: Annotated[int | None, Query(ge=0, le=600)] = None,
) -> MarketCycleRead:
    try:
        cycle_kwargs = {}
        if phase_timeout_seconds is not None:
            cycle_kwargs["phase_timeout_seconds"] = phase_timeout_seconds
        result = run_market_cycle(
            db,
            scan_limit=scan_limit,
            order_limit=order_limit,
            fill_page_size=fill_page_size,
            preview_enabled_override=preview_enabled,
            reconcile_enabled_override=reconcile_enabled,
            exit_enabled_override=False,
            news_enabled_override=False,
            submit_enabled_override=False,
            **cycle_kwargs,
        )
    except AlpacaTradingConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except AlpacaTradingError as exc:
        raise HTTPException(
            status_code=alpaca_error_status_code(exc),
            detail=exc.detail,
        ) from exc

    return _market_cycle_read(result)


def _market_cycle_read(result) -> MarketCycleRead:
    return MarketCycleRead(
        job_run=JobRunRead.model_validate(result.job_run),
        scan_enabled=result.scan_enabled,
        reconcile_enabled=result.reconcile_enabled,
        preview_enabled=result.preview_enabled,
        exit_enabled=result.exit_enabled,
        news_enabled=result.news_enabled,
        submit_enabled=result.submit_enabled,
        scan=result.scan,
        reconcile=result.reconcile,
        preview=result.preview,
        exits=result.exits,
        news=result.news,
        submit=result.submit,
        timings=result.timings,
        phase_timeout_seconds=result.phase_timeout_seconds,
        diagnostics=result.diagnostics,
    )
