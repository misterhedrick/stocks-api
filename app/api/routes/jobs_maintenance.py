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
    "/market-maintenance",
    response_model=MarketMaintenanceRead,
    status_code=status.HTTP_200_OK,
)
def market_maintenance_route(
    db: Annotated[Session, Depends(get_db)],
    phase: Annotated[Literal["auto", "pre_market", "post_market"], Query()] = "auto",
    order_limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    fill_page_size: Annotated[int | None, Query(ge=1, le=ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE)] = None,
    stale_after_hours: Annotated[int | None, Query(ge=0, le=72)] = None,
    news_enabled: bool = True,
) -> MarketMaintenanceRead:
    try:
        result = run_market_maintenance(
            db,
            phase=phase,
            order_limit=order_limit,
            fill_page_size=fill_page_size,
            stale_after_hours=stale_after_hours,
            news_enabled=news_enabled,
        )
    except NewsFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
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

    return MarketMaintenanceRead(
        job_run=JobRunRead.model_validate(result.job_run),
        phase=result.phase,
        cleanup=result.cleanup,
        reconcile=result.reconcile,
        news=result.news,
        performance=result.performance,
        readiness=result.readiness,
        settings_snapshot=result.settings_snapshot,
        trade_cases=result.trade_cases,
        paper_review_snapshot=result.paper_review_snapshot,
        ai_trade_reviews=result.ai_trade_reviews,
    )


@router.post(
    "/pre-market-maintenance",
    response_model=MarketMaintenanceRead,
    status_code=status.HTTP_200_OK,
)
def pre_market_maintenance_route(
    db: Annotated[Session, Depends(get_db)],
    order_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    fill_page_size: Annotated[int, Query(ge=1, le=ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE)] = ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE,
    stale_after_hours: Annotated[int, Query(ge=0, le=72)] = 12,
    news_enabled: bool = True,
) -> MarketMaintenanceRead:
    try:
        result = run_pre_market_maintenance(
            db,
            order_limit=order_limit,
            fill_page_size=fill_page_size,
            stale_after_hours=stale_after_hours,
            news_enabled=news_enabled,
        )
    except NewsFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
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

    return MarketMaintenanceRead(
        job_run=JobRunRead.model_validate(result.job_run),
        phase=result.phase,
        cleanup=result.cleanup,
        reconcile=result.reconcile,
        news=result.news,
        performance=result.performance,
        readiness=result.readiness,
        settings_snapshot=result.settings_snapshot,
        trade_cases=result.trade_cases,
        paper_review_snapshot=result.paper_review_snapshot,
        ai_trade_reviews=result.ai_trade_reviews,
    )


@router.post(
    "/post-market-maintenance",
    response_model=MarketMaintenanceRead,
    status_code=status.HTTP_200_OK,
)
def post_market_maintenance_route(
    db: Annotated[Session, Depends(get_db)],
    order_limit: Annotated[int, Query(ge=1, le=500)] = 500,
    fill_page_size: Annotated[int, Query(ge=1, le=ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE)] = ALPACA_ACCOUNT_ACTIVITIES_MAX_PAGE_SIZE,
    stale_after_hours: Annotated[int, Query(ge=0, le=72)] = 0,
) -> MarketMaintenanceRead:
    try:
        result = run_post_market_maintenance(
            db,
            order_limit=order_limit,
            fill_page_size=fill_page_size,
            stale_after_hours=stale_after_hours,
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

    return MarketMaintenanceRead(
        job_run=JobRunRead.model_validate(result.job_run),
        phase=result.phase,
        cleanup=result.cleanup,
        reconcile=result.reconcile,
        news=result.news,
        performance=result.performance,
        readiness=result.readiness,
        settings_snapshot=result.settings_snapshot,
        trade_cases=result.trade_cases,
        paper_review_snapshot=result.paper_review_snapshot,
        ai_trade_reviews=result.ai_trade_reviews,
    )


@router.post(
    "/reset-trading-data",
    response_model=TradingDataResetRead,
    status_code=status.HTTP_200_OK,
)
def reset_trading_data_route(
    db: Annotated[Session, Depends(get_db)],
    dry_run: bool = True,
    include_history: bool = True,
    confirm: Annotated[str | None, Query(max_length=64)] = None,
) -> TradingDataResetRead:
    try:
        result = run_trading_data_reset(
            db,
            dry_run=dry_run,
            include_history=include_history,
            confirm=confirm,
        )
    except TradingDataResetConfirmationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return TradingDataResetRead(
        job_run=JobRunRead.model_validate(result.job_run),
        dry_run=result.dry_run,
        include_history=result.include_history,
        counts_before=result.counts_before,
        deleted=result.deleted,
        kept_tables=result.kept_tables,
        confirmation_phrase=result.confirmation_phrase,
    )


@router.post(
    "/populate-trade-cases",
    response_model=TradeCasePopulationRead,
    status_code=status.HTTP_200_OK,
)
def populate_trade_cases_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> TradeCasePopulationRead:
    result = populate_trade_cases_from_closed_round_trips(db, limit=limit)
    return TradeCasePopulationRead(
        job_run=JobRunRead.model_validate(result.job_run),
        round_trips_seen=result.round_trips_seen,
        inserted=result.inserted,
        updated=result.updated,
        skipped=result.skipped,
        errors=result.errors,
    )


@router.post(
    "/write-ai-trade-reviews",
    response_model=AiTradeReviewWriterRead,
    status_code=status.HTTP_200_OK,
)
def write_ai_trade_reviews_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> AiTradeReviewWriterRead:
    result = write_ai_trade_reviews_from_paper_evidence(db, limit=limit)
    return AiTradeReviewWriterRead(
        job_run=JobRunRead.model_validate(result.job_run),
        trade_cases_seen=result.trade_cases_seen,
        reviews_created=result.reviews_created,
        reviews_skipped=result.reviews_skipped,
        suggestions_created=result.suggestions_created,
        errors=result.errors,
    )


@router.post(
    "/patch-strategy-dte",
    response_model=PatchStrategyDteRead,
    status_code=status.HTTP_200_OK,
)
def patch_strategy_dte_route(
    db: Annotated[Session, Depends(get_db)],
    min_dte: Annotated[int, Query(ge=0)] = 2,
    max_dte: Annotated[int, Query(ge=1)] = 30,
) -> PatchStrategyDteRead:
    result = patch_strategy_dte(db, min_dte=min_dte, max_dte=max_dte)
    return PatchStrategyDteRead(
        job_run=JobRunRead.model_validate(result.job_run),
        strategies_seen=result.strategies_seen,
        strategies_updated=result.strategies_updated,
        strategies_skipped=result.strategies_skipped,
    )


@public_router.get("/recent", status_code=status.HTTP_200_OK)
def recent_jobs_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    job_name: Annotated[str | None, Query()] = None,
) -> list[dict[str, Any]]:
    """Public read-only endpoint — returns recent job run summaries, no auth required."""
    statement = select(JobRun).order_by(desc(JobRun.started_at)).limit(limit)
    if job_name:
        statement = statement.where(JobRun.job_name == job_name)
    runs = list(db.scalars(statement))
    return [
        {
            "id": str(r.id),
            "job_name": r.job_name,
            "status": r.status,
            "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "details": r.details,
            "error": r.error,
        }
        for r in runs
    ]
