from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.api.alpaca_errors import alpaca_error_status_code
from app.core.security import require_admin
from app.db.models import JobRun
from app.db.session import get_db
from app.integrations.alpaca import (
    ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE,
    AlpacaTradingConfigurationError,
    AlpacaTradingError,
)
from app.schemas.jobs import (
    AiTradeReviewWriterRead,
    BrokerReconciliationRead,
    JobRunRead,
    MarketMaintenanceRead,
    MarketCycleRead,
    NewsScanRead,
    PositionExitEvaluationRead,
    SignalScanRead,
    TradeCasePopulationRead,
    PatchStrategyDteRead,
    TradingDataResetRead,
)
from app.services.ai_trade_review import write_ai_trade_reviews_from_paper_evidence
from app.services.broker_reconciliation import reconcile_broker_state
from app.services.market_cycle import (
    normalize_market_entry_symbol,
    run_market_cycle,
    run_market_entry_cycle,
)
from app.services.market_maintenance import (
    patch_strategy_dte,
    run_market_maintenance,
    run_post_market_maintenance,
    run_pre_market_maintenance,
)
from app.services.news_scanner import NewsFetchError, scan_market_news
from app.services.position_exits import evaluate_position_exits, preview_unmanaged_position_exits
from app.services.signal_scanner import scan_signals
from app.services.trade_cases import populate_trade_cases_from_closed_round_trips
from app.services.trading_reset import (
    TradingDataResetConfirmationError,
    run_trading_data_reset,
)

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(require_admin)],
)

public_router = APIRouter(prefix="/jobs", tags=["jobs"])

@router.post(
    "/reconcile-broker",
    response_model=BrokerReconciliationRead,
    status_code=status.HTTP_200_OK,
)
def reconcile_broker_route(
    db: Annotated[Session, Depends(get_db)],
    order_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    fill_page_size: Annotated[int, Query(ge=1, le=ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE)] = ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE,
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
            status_code=alpaca_error_status_code(exc),
            detail=exc.detail,
        ) from exc

    return BrokerReconciliationRead(
        job_run=JobRunRead.model_validate(result.job_run),
        orders_seen=result.orders_seen,
        orders_created=result.orders_created,
        orders_updated=result.orders_updated,
        fills_seen=result.fills_seen,
        fills_created=result.fills_created,
        fill_page_size_requested=result.fill_page_size_requested,
        fill_page_size_used=result.fill_page_size_used,
        fill_pages_fetched=result.fill_pages_fetched,
        fill_pagination_complete=result.fill_pagination_complete,
        fill_pagination_stop_reason=result.fill_pagination_stop_reason,
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
    "/evaluate-exits",
    response_model=PositionExitEvaluationRead,
    status_code=status.HTTP_200_OK,
)
def evaluate_exits_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> PositionExitEvaluationRead:
    try:
        result = evaluate_position_exits(db, limit=limit)
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

    return PositionExitEvaluationRead(
        positions_seen=result.positions_seen,
        positions_evaluated=result.positions_evaluated,
        exits_created=result.exits_created,
        exits_skipped=result.exits_skipped,
        errors=result.errors,
        no_exit_reasons=result.no_exit_reasons,
        position_ownership=result.position_ownership,
        order_intent_ids=result.order_intent_ids,
    )


@router.post(
    "/preview-unmanaged-exits",
    response_model=PositionExitEvaluationRead,
    status_code=status.HTTP_200_OK,
)
def preview_unmanaged_exits_route(
    db: Annotated[Session, Depends(get_db)],
    symbol: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> PositionExitEvaluationRead:
    try:
        result = preview_unmanaged_position_exits(db, symbol=symbol, limit=limit)
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

    return PositionExitEvaluationRead(
        positions_seen=result.positions_seen,
        positions_evaluated=result.positions_evaluated,
        exits_created=result.exits_created,
        exits_skipped=result.exits_skipped,
        errors=result.errors,
        no_exit_reasons=result.no_exit_reasons,
        position_ownership=result.position_ownership,
        order_intent_ids=result.order_intent_ids,
    )


@router.post(
    "/check-news",
    response_model=NewsScanRead,
    status_code=status.HTTP_200_OK,
)
def check_news_route(
    db: Annotated[Session, Depends(get_db)],
    market_limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ticker_limit: Annotated[int, Query(ge=1, le=25)] = 5,
) -> NewsScanRead:
    try:
        result = scan_market_news(
            db,
            market_limit=market_limit,
            ticker_limit=ticker_limit,
        )
    except NewsFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return NewsScanRead(
        job_run=JobRunRead.model_validate(result.job_run),
        market_items=result.market_items,
        ticker_items=result.ticker_items,
        owned_symbols=result.owned_symbols,
        risk_assessment=result.risk_assessment,
        sources_checked=result.sources_checked,
        errors=result.errors,
    )


