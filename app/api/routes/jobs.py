from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.models import JobRun
from app.db.session import get_db
from app.integrations.alpaca import (
    AlpacaTradingConfigurationError,
    AlpacaTradingError,
)
from app.schemas.jobs import (
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
from app.services.broker_reconciliation import reconcile_broker_state
from app.services.market_cycle import run_market_cycle
from app.services.market_maintenance import (
    patch_strategy_dte,
    run_market_maintenance,
    run_post_market_maintenance,
    run_pre_market_maintenance,
)
from app.services.news_scanner import NewsFetchError, scan_market_news
from app.services.position_exits import evaluate_position_exits
from app.services.position_exits import preview_unmanaged_position_exits
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
            status_code=status.HTTP_502_BAD_GATEWAY,
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
            status_code=status.HTTP_502_BAD_GATEWAY,
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
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.detail,
        ) from exc

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


@router.post(
    "/market-cycle-exits",
    response_model=MarketCycleRead,
    status_code=status.HTTP_200_OK,
)
def market_cycle_exits_route(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    order_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    fill_page_size: Annotated[int, Query(ge=1, le=500)] = 100,
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
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.detail,
        ) from exc

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


@router.post(
    "/market-cycle-stress",
    response_model=MarketCycleRead,
    status_code=status.HTTP_200_OK,
)
def market_cycle_stress_route(
    db: Annotated[Session, Depends(get_db)],
    scan_limit: Annotated[int, Query(ge=1, le=500)] = 130,
    order_limit: Annotated[int, Query(ge=1, le=500)] = 25,
    fill_page_size: Annotated[int, Query(ge=1, le=500)] = 25,
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
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.detail,
        ) from exc

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


@router.post(
    "/market-maintenance",
    response_model=MarketMaintenanceRead,
    status_code=status.HTTP_200_OK,
)
def market_maintenance_route(
    db: Annotated[Session, Depends(get_db)],
    phase: Annotated[Literal["auto", "pre_market", "post_market"], Query()] = "auto",
    order_limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    fill_page_size: Annotated[int | None, Query(ge=1, le=500)] = None,
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
            status_code=status.HTTP_502_BAD_GATEWAY,
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
    )


@router.post(
    "/pre-market-maintenance",
    response_model=MarketMaintenanceRead,
    status_code=status.HTTP_200_OK,
)
def pre_market_maintenance_route(
    db: Annotated[Session, Depends(get_db)],
    order_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    fill_page_size: Annotated[int, Query(ge=1, le=500)] = 100,
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
            status_code=status.HTTP_502_BAD_GATEWAY,
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
    )


@router.post(
    "/post-market-maintenance",
    response_model=MarketMaintenanceRead,
    status_code=status.HTTP_200_OK,
)
def post_market_maintenance_route(
    db: Annotated[Session, Depends(get_db)],
    order_limit: Annotated[int, Query(ge=1, le=500)] = 500,
    fill_page_size: Annotated[int, Query(ge=1, le=100)] = 100,
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
            status_code=status.HTTP_502_BAD_GATEWAY,
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
