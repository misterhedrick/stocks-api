from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AiTradeReview,
    BrokerOrder,
    Fill,
    JobRun,
    OptionSelectionDiagnostic,
    OrderIntent,
    PaperReviewSnapshot,
    Signal,
    Strategy,
    StrategyChangeSuggestion,
    TradeCase,
)

MARKET_TIMEZONE = ZoneInfo("America/New_York")


def build_daily_paper_review(
    db: Session,
    *,
    review_date: date | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    selected_date = review_date or datetime.now(MARKET_TIMEZONE).date()
    window_start, window_end = _market_day_window_utc(selected_date)

    job_runs = _job_runs(db, window_start=window_start, window_end=window_end, limit=limit)
    signals = _signals(db, window_start=window_start, window_end=window_end, limit=limit)
    order_intents = _order_intents(db, window_start=window_start, window_end=window_end, limit=limit)
    broker_orders = _broker_orders(db, window_start=window_start, window_end=window_end, limit=limit)
    fills = _fills(db, window_start=window_start, window_end=window_end, limit=limit)
    diagnostics = _option_selection_diagnostics(
        db,
        window_start=window_start,
        window_end=window_end,
        limit=limit,
    )
    trade_cases = _trade_cases(db, window_start=window_start, window_end=window_end, limit=limit)
    ai_reviews = _ai_reviews(db, window_start=window_start, window_end=window_end, limit=limit)
    suggestions = _strategy_change_suggestions(
        db,
        window_start=window_start,
        window_end=window_end,
        limit=limit,
    )
    snapshot = _paper_review_snapshot(db, selected_date)

    return {
        "review_date": selected_date.isoformat(),
        "timezone": MARKET_TIMEZONE.key,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "limit": limit,
        "summary": {
            "job_runs": len(job_runs),
            "signals": len(signals),
            "order_intents": len(order_intents),
            "broker_orders": len(broker_orders),
            "fills": len(fills),
            "option_selection_diagnostics": len(diagnostics),
            "trade_cases": len(trade_cases),
            "ai_trade_reviews": len(ai_reviews),
            "strategy_change_suggestions": len(suggestions),
            "paper_review_snapshot_found": snapshot is not None,
        },
        "jobs": _job_summary(job_runs),
        "signals": _signal_summary(signals),
        "previews": _order_intent_summary(order_intents),
        "orders": _broker_order_summary(broker_orders),
        "fills": _fill_summary(fills),
        "option_selection_diagnostics": _diagnostic_summary(diagnostics),
        "trade_cases": _trade_case_summary(trade_cases),
        "ai_reviews": _ai_review_summary(ai_reviews, suggestions),
        "paper_review_snapshot": _snapshot_summary(snapshot),
    }


def _market_day_window_utc(selected_date: date) -> tuple[datetime, datetime]:
    local_start = datetime.combine(selected_date, time.min, tzinfo=MARKET_TIMEZONE)
    local_end = datetime.combine(selected_date, time.max, tzinfo=MARKET_TIMEZONE)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _job_runs(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[JobRun]:
    statement = (
        select(JobRun)
        .where(JobRun.started_at >= window_start)
        .where(JobRun.started_at <= window_end)
        .order_by(JobRun.started_at.asc())
        .limit(limit)
    )
    return list(db.scalars(statement))


def _signals(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[tuple[Signal, str | None, dict[str, Any] | None]]:
    statement = (
        select(Signal, Strategy.name, Strategy.config)
        .join(Strategy, Signal.strategy_id == Strategy.id, isouter=True)
        .where(Signal.created_at >= window_start)
        .where(Signal.created_at <= window_end)
        .order_by(Signal.created_at.asc())
        .limit(limit)
    )
    return list(db.execute(statement))


def _order_intents(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[OrderIntent]:
    statement = (
        select(OrderIntent)
        .where(OrderIntent.created_at >= window_start)
        .where(OrderIntent.created_at <= window_end)
        .order_by(OrderIntent.created_at.asc())
        .limit(limit)
    )
    return list(db.scalars(statement))


def _broker_orders(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[BrokerOrder]:
    statement = (
        select(BrokerOrder)
        .where(BrokerOrder.created_at >= window_start)
        .where(BrokerOrder.created_at <= window_end)
        .order_by(BrokerOrder.created_at.asc())
        .limit(limit)
    )
    return list(db.scalars(statement))


def _fills(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[Fill]:
    statement = (
        select(Fill)
        .where(Fill.filled_at >= window_start)
        .where(Fill.filled_at <= window_end)
        .order_by(Fill.filled_at.asc())
        .limit(limit)
    )
    return list(db.scalars(statement))


def _option_selection_diagnostics(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[OptionSelectionDiagnostic]:
    statement = (
        select(OptionSelectionDiagnostic)
        .where(OptionSelectionDiagnostic.created_at >= window_start)
        .where(OptionSelectionDiagnostic.created_at <= window_end)
        .order_by(OptionSelectionDiagnostic.created_at.asc())
        .limit(limit)
    )
    return list(db.scalars(statement))


def _trade_cases(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[TradeCase]:
    statement = (
        select(TradeCase)
        .where(TradeCase.created_at >= window_start)
        .where(TradeCase.created_at <= window_end)
        .order_by(TradeCase.created_at.asc())
        .limit(limit)
    )
    return list(db.scalars(statement))


def _ai_reviews(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[AiTradeReview]:
    statement = (
        select(AiTradeReview)
        .where(AiTradeReview.created_at >= window_start)
        .where(AiTradeReview.created_at <= window_end)
        .order_by(AiTradeReview.created_at.asc())
        .limit(limit)
    )
    return list(db.scalars(statement))


def _strategy_change_suggestions(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[StrategyChangeSuggestion]:
    statement = (
        select(StrategyChangeSuggestion)
        .where(StrategyChangeSuggestion.created_at >= window_start)
        .where(StrategyChangeSuggestion.created_at <= window_end)
        .order_by(StrategyChangeSuggestion.created_at.asc())
        .limit(limit)
    )
    return list(db.scalars(statement))


def _paper_review_snapshot(db: Session, selected_date: date) -> PaperReviewSnapshot | None:
    return db.scalar(
        select(PaperReviewSnapshot)
        .where(PaperReviewSnapshot.review_date == selected_date)
        .order_by(PaperReviewSnapshot.generated_at.desc())
        .limit(1)
    )


def _job_summary(job_runs: list[JobRun]) -> dict[str, Any]:
    by_name: dict[str, Counter[str]] = defaultdict(Counter)
    latest_by_name: dict[str, dict[str, Any]] = {}
    for run in job_runs:
        by_name[run.job_name][run.status] += 1
        latest_by_name[run.job_name] = {
            "id": str(run.id),
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "error": run.error,
        }
    return {
        "by_name_status": _nested_counter_dict(by_name),
        "latest_by_name": latest_by_name,
        "failed": [
            {
                "id": str(run.id),
                "job_name": run.job_name,
                "started_at": run.started_at.isoformat(),
                "error": run.error,
            }
            for run in job_runs
            if run.status == "failed"
        ],
    }


def _signal_summary(
    signal_rows: list[tuple[Signal, str | None, dict[str, Any] | None]]) -> dict[str, Any]:
    by_status: Counter[str] = Counter()
    by_symbol: Counter[str] = Counter()
    by_scanner: Counter[str] = Counter()
    by_scanner_status: dict[str, Counter[str]] = defaultdict(Counter)
    no_signal_reasons: Counter[str] = Counter()
    preview_error_codes: Counter[str] = Counter()

    for signal, _, strategy_config in signal_rows:
        scanner_type = _scanner_type(strategy_config, signal.market_context)
        scanner_key = scanner_type or "unknown"
        by_status[signal.status] += 1
        by_symbol[signal.underlying_symbol or signal.symbol] += 1
        by_scanner[scanner_key] += 1
        by_scanner_status[scanner_key][signal.status] += 1
        if signal.rejected_reason:
            no_signal_reasons[signal.rejected_reason] += 1
        if signal.last_preview_error_code:
            preview_error_codes[signal.last_preview_error_code] += 1

    return {
        "total": len(signal_rows),
        "by_status": _counter_dict(by_status),
        "by_symbol": _counter_dict(by_symbol),
        "by_scanner_type": _counter_dict(by_scanner),
        "by_scanner_type_status": _nested_counter_dict(by_scanner_status),
        "rejected_reasons": _counter_dict(no_signal_reasons),
        "preview_error_codes": _counter_dict(preview_error_codes),
    }


def _order_intent_summary(order_intents: list[OrderIntent]) -> dict[str, Any]:
    return {
        "total": len(order_intents),
        "by_status": _counter_dict(Counter(intent.status for intent in order_intents)),
        "by_symbol": _counter_dict(Counter(intent.underlying_symbol for intent in order_intents)),
        "by_side": _counter_dict(Counter(intent.side for intent in order_intents)),
        "submitted": sum(1 for intent in order_intents if intent.submitted_at is not None),
        "rejection_reasons": _counter_dict(
            Counter(intent.rejection_reason for intent in order_intents if intent.rejection_reason)
        ),
    }


def _broker_order_summary(orders: list[BrokerOrder]) -> dict[str, Any]:
    return {
        "total": len(orders),
        "by_status": _counter_dict(Counter(order.status for order in orders)),
        "by_symbol": _counter_dict(Counter(order.symbol for order in orders)),
        "by_side": _counter_dict(Counter(order.side for order in orders)),
        "filled": sum(1 for order in orders if order.filled_at is not None),
    }


def _fill_summary(fills: list[Fill]) -> dict[str, Any]:
    notional = sum((fill.quantity * fill.price * Decimal("100") for fill in fills), Decimal("0"))
    return {
        "total": len(fills),
        "by_symbol": _counter_dict(Counter(fill.symbol for fill in fills)),
        "by_side": _counter_dict(Counter(fill.side for fill in fills)),
        "estimated_notional": _decimal_string(notional),
    }


def _diagnostic_summary(diagnostics: list[OptionSelectionDiagnostic]) -> dict[str, Any]:
    by_symbol: Counter[str] = Counter()
    by_scanner: Counter[str] = Counter()
    by_profile: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    by_symbol_reason: dict[str, Counter[str]] = defaultdict(Counter)
    by_scanner_reason: dict[str, Counter[str]] = defaultdict(Counter)

    for diagnostic in diagnostics:
        symbol = diagnostic.underlying_symbol or "unknown"
        scanner = diagnostic.scanner_type or "unknown"
        profile = diagnostic.preview_profile or "unknown"
        by_symbol[symbol] += 1
        by_scanner[scanner] += 1
        by_profile[profile] += 1
        for reason, count in (diagnostic.reason_counts or {}).items():
            try:
                reason_count = int(count)
            except (TypeError, ValueError):
                reason_count = 1
            reason_counts[str(reason)] += reason_count
            by_symbol_reason[symbol][str(reason)] += reason_count
            by_scanner_reason[scanner][str(reason)] += reason_count

    return {
        "total": len(diagnostics),
        "by_symbol": _counter_dict(by_symbol),
        "by_scanner_type": _counter_dict(by_scanner),
        "by_preview_profile": _counter_dict(by_profile),
        "reason_counts": _counter_dict(reason_counts),
        "by_symbol_reason": _nested_counter_dict(by_symbol_reason),
        "by_scanner_reason": _nested_counter_dict(by_scanner_reason),
    }


def _trade_case_summary(trade_cases: list[TradeCase]) -> dict[str, Any]:
    closed = [case for case in trade_cases if not case.is_open]
    realized_pl = sum((case.realized_pl or Decimal("0") for case in closed), Decimal("0"))
    return {
        "total": len(trade_cases),
        "open": sum(1 for case in trade_cases if case.is_open),
        "closed": len(closed),
        "by_symbol": _counter_dict(Counter(case.underlying_symbol or case.symbol for case in trade_cases)),
        "realized_pl": _decimal_string(realized_pl),
    }


def _ai_review_summary(
    reviews: list[AiTradeReview],
    suggestions: list[StrategyChangeSuggestion],
) -> dict[str, Any]:
    return {
        "reviews_total": len(reviews),
        "reviews_by_status": _counter_dict(Counter(review.review_status for review in reviews)),
        "suggestions_total": len(suggestions),
        "suggestions_by_status": _counter_dict(
            Counter(suggestion.status for suggestion in suggestions)
        ),
        "suggestions_by_type": _counter_dict(
            Counter(suggestion.suggestion_type for suggestion in suggestions)
        ),
    }


def _snapshot_summary(snapshot: PaperReviewSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    summary = snapshot.summary if isinstance(snapshot.summary, dict) else {}
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    return {
        "id": str(snapshot.id),
        "review_date": snapshot.review_date.isoformat(),
        "review_type": snapshot.review_type,
        "status": snapshot.status,
        "generated_at": snapshot.generated_at.isoformat(),
        "counts": counts,
    }


def _scanner_type(strategy_config: Any, market_context: Any) -> str | None:
    if isinstance(market_context, dict):
        strategy_type = market_context.get("strategy_type")
        if isinstance(strategy_type, str) and strategy_type.strip():
            return strategy_type.strip()
    if isinstance(strategy_config, dict):
        scanner = strategy_config.get("scanner")
        if isinstance(scanner, dict):
            scanner_type = scanner.get("type")
            if isinstance(scanner_type, str) and scanner_type.strip():
                return scanner_type.strip()
    return None


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted((str(key), int(value)) for key, value in counter.items()))


def _nested_counter_dict(counter: dict[str, Counter[str]]) -> dict[str, dict[str, int]]:
    return {str(key): _counter_dict(value) for key, value in sorted(counter.items())}


def _decimal_string(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f")
