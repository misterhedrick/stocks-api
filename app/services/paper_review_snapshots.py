from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    BrokerOrder,
    Fill,
    OptionSelectionDiagnostic,
    OrderIntent,
    PaperReviewSnapshot,
    Signal,
    Strategy,
)
from app.services.learning_report import build_learning_report
from app.services.performance_review import PerformanceReviewResult, get_paper_performance_review


@dataclass(slots=True)
class PaperReviewSnapshotResult:
    snapshot: PaperReviewSnapshot
    created: bool
    review_date: date
    review_type: str
    signal_count: int
    order_intent_count: int
    fill_count: int
    diagnostic_count: int
    rejected_shadow_outcome_count: int
    refinement_candidate_count: int


def create_or_update_post_market_paper_review_snapshot(
    db: Session,
    *,
    review_date: date | None = None,
    generated_at: datetime | None = None,
    limit: int = 500,
    performance: PerformanceReviewResult | None = None,
) -> PaperReviewSnapshotResult:
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    generated = generated.astimezone(timezone.utc)

    selected_date = review_date or generated.date()
    window_start = datetime.combine(selected_date, time.min, tzinfo=timezone.utc)
    window_end = datetime.combine(selected_date, time.max, tzinfo=timezone.utc)

    if performance is None:
        performance = get_paper_performance_review(db, limit=limit)
    signals = _signal_details(db, window_start=window_start, window_end=window_end, limit=limit)
    previews = _order_intent_details(
        db,
        window_start=window_start,
        window_end=window_end,
        limit=limit,
    )
    orders = _broker_order_details(
        db,
        window_start=window_start,
        window_end=window_end,
        limit=limit,
    )
    fills = _fill_details(db, window_start=window_start, window_end=window_end, limit=limit)
    diagnostics = _diagnostic_details(
        db,
        window_start=window_start,
        window_end=window_end,
        limit=limit,
    )
    rejected_shadow_outcomes = _rejected_signal_shadow_outcomes(signals)
    learning_report = _learning_report_payload(db, limit=limit)
    refinement_candidates = learning_report.get("refinement_candidates", [])

    summary = {
        "performance": _performance_payload(performance),
        "counts": {
            "signals": len(signals),
            "order_intents": len(previews),
            "broker_orders": len(orders),
            "fills": len(fills),
            "option_selection_diagnostics": len(diagnostics),
            "rejected_shadow_outcomes": len(rejected_shadow_outcomes),
            "refinement_candidates": len(refinement_candidates)
            if isinstance(refinement_candidates, list)
            else 0,
        },
        "learning_report": {
            "retention_days": settings.paper_review_snapshot_retention_days,
            "refinement_candidate_count": len(refinement_candidates)
            if isinstance(refinement_candidates, list)
            else 0,
        },
    }
    signals_col = {
        "items": signals,
        "summary": performance.signal_summary,
        "no_signal_summary": performance.no_signal_summary,
    }
    previews_col = {
        "items": previews,
        "status_counts": _count_dict(item["status"] for item in previews),
    }
    orders_col = {
        "items": orders,
        "status_counts": _count_dict(item["status"] for item in orders),
    }
    fills_col = {
        "items": fills,
        "summary": {
            "fills_seen": performance.fills_seen,
            "matched_round_trips": performance.matched_round_trips,
            "totals": performance.totals,
            "by_strategy": performance.by_strategy,
            "by_symbol": performance.by_symbol,
            "open_positions": performance.open_positions,
            "recent_round_trips": performance.recent_round_trips,
        },
    }
    diagnostics_col = {
        "items": diagnostics,
        "summary": performance.option_selection_diagnostics,
    }
    rejected_outcomes_col = {
        "trade_comparison": performance.rejected_preview_outcomes,
        "shadow_market_movement": rejected_shadow_outcomes,
    }
    # raw_payload stores only what isn't in structured columns (learning_report + metadata).
    # Keeping item arrays out of raw_payload avoids doubling the in-memory footprint.
    raw_payload = {
        "review_date": selected_date.isoformat(),
        "review_type": "post_market",
        "generated_at": generated.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "learning_report": learning_report,
    }

    existing = db.scalar(
        select(PaperReviewSnapshot)
        .where(PaperReviewSnapshot.review_date == selected_date)
        .where(PaperReviewSnapshot.review_type == "post_market")
        .limit(1)
    )
    created = existing is None
    snapshot = existing or PaperReviewSnapshot(
        review_date=selected_date,
        review_type="post_market",
    )
    snapshot.status = "completed"
    snapshot.window_start = window_start
    snapshot.window_end = window_end
    snapshot.generated_at = generated
    snapshot.summary = summary
    snapshot.signals = signals_col
    snapshot.previews = previews_col
    snapshot.orders = orders_col
    snapshot.fills = fills_col
    snapshot.diagnostics = diagnostics_col
    snapshot.rejected_outcomes = rejected_outcomes_col
    snapshot.raw_payload = raw_payload
    db.add(snapshot)
    db.commit()
    # Detach from the session so SQLAlchemy drops the large JSON columns from its identity map.
    # Column values already loaded (including id) remain accessible on the detached object.
    db.expunge(snapshot)

    return PaperReviewSnapshotResult(
        snapshot=snapshot,
        created=created,
        review_date=selected_date,
        review_type="post_market",
        signal_count=len(signals),
        order_intent_count=len(previews),
        fill_count=len(fills),
        diagnostic_count=len(diagnostics),
        rejected_shadow_outcome_count=len(rejected_shadow_outcomes),
        refinement_candidate_count=len(refinement_candidates)
        if isinstance(refinement_candidates, list)
        else 0,
    )


def get_paper_review_snapshots(
    db: Session,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    statement = (
        select(PaperReviewSnapshot)
        .order_by(PaperReviewSnapshot.generated_at.desc())
        .limit(limit)
    )
    return [_snapshot_read_item(snapshot) for snapshot in db.scalars(statement)]


def _snapshot_read_item(snapshot: PaperReviewSnapshot) -> dict[str, Any]:
    return {
        "id": str(snapshot.id),
        "review_date": snapshot.review_date.isoformat(),
        "review_type": snapshot.review_type,
        "status": snapshot.status,
        "window_start": snapshot.window_start.isoformat()
        if snapshot.window_start
        else None,
        "window_end": snapshot.window_end.isoformat() if snapshot.window_end else None,
        "generated_at": snapshot.generated_at.isoformat(),
        "summary": snapshot.summary,
        "signals": snapshot.signals,
        "previews": snapshot.previews,
        "orders": snapshot.orders,
        "fills": snapshot.fills,
        "diagnostics": snapshot.diagnostics,
        "rejected_outcomes": snapshot.rejected_outcomes,
        "learning_report": _snapshot_learning_report(snapshot),
        "created_at": snapshot.created_at.isoformat(),
        "updated_at": snapshot.updated_at.isoformat(),
    }


def prune_old_paper_review_snapshots(
    db: Session,
    *,
    before_date: date,
    limit: int = 100,
) -> dict[str, Any]:
    snapshots = list(
        db.scalars(
            select(PaperReviewSnapshot)
            .where(PaperReviewSnapshot.review_date < before_date)
            .order_by(PaperReviewSnapshot.review_date.asc())
            .limit(limit)
        )
    )
    for snapshot in snapshots:
        db.delete(snapshot)
    db.commit()
    return {
        "before_date": before_date.isoformat(),
        "deleted": len(snapshots),
        "retention_days": settings.paper_review_snapshot_retention_days,
        "snapshot_ids": [str(snapshot.id) for snapshot in snapshots],
    }


def _learning_report_payload(db: Session, *, limit: int) -> dict[str, Any]:
    report = build_learning_report(db, limit=limit)
    return {
        "generated_at": report.generated_at.isoformat(),
        "totals": report.totals,
        "performance": report.performance,
        "signals_by_strategy": report.signals_by_strategy,
        "intents_by_strategy": report.intents_by_strategy,
        "non_trade_reasons": report.non_trade_reasons,
        "refinement_candidates": report.refinement_candidates,
        "job_failures": report.job_failures,
        "retention": {
            "storage": "paper_review_snapshots.raw_payload.learning_report",
            "retention_days": settings.paper_review_snapshot_retention_days,
        },
    }


def _snapshot_learning_report(snapshot: PaperReviewSnapshot) -> dict[str, Any] | None:
    raw_payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
    learning_report = raw_payload.get("learning_report")
    return learning_report if isinstance(learning_report, dict) else None


def _performance_payload(performance: object) -> dict[str, Any]:
    return {
        "generated_at": performance.generated_at.isoformat(),
        "fills_seen": performance.fills_seen,
        "matched_round_trips": performance.matched_round_trips,
        "totals": performance.totals,
        "by_strategy": performance.by_strategy,
        "by_symbol": performance.by_symbol,
        "open_positions": performance.open_positions,
        "signal_summary": performance.signal_summary,
        "no_signal_summary": performance.no_signal_summary,
        "option_selection_diagnostics": performance.option_selection_diagnostics,
        "rejected_preview_outcomes": performance.rejected_preview_outcomes,
    }


def _signal_details(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    statement = (
        select(Signal, Strategy.name, Strategy.config)
        .join(Strategy, Signal.strategy_id == Strategy.id, isouter=True)
        .where(Signal.created_at >= window_start)
        .where(Signal.created_at <= window_end)
        .order_by(Signal.created_at.asc())
        .limit(limit)
    )
    items = []
    for signal, strategy_name, strategy_config in db.execute(statement):
        market_context = signal.market_context if isinstance(signal.market_context, dict) else {}
        items.append(
            {
                "id": str(signal.id),
                "created_at": signal.created_at.isoformat(),
                "strategy_id": str(signal.strategy_id) if signal.strategy_id else None,
                "strategy_name": strategy_name,
                "scanner_type": _scanner_type(strategy_config, market_context),
                "symbol": signal.symbol,
                "underlying_symbol": signal.underlying_symbol,
                "signal_type": signal.signal_type,
                "direction": signal.direction,
                "confidence": _optional_decimal_string(signal.confidence),
                "status": signal.status,
                "rejected_reason": signal.rejected_reason,
                "preview_attempts": signal.preview_attempts,
                "last_previewed_at": signal.last_previewed_at.isoformat()
                if signal.last_previewed_at
                else None,
                "last_preview_error_code": signal.last_preview_error_code,
                "last_preview_error": signal.last_preview_error,
                "preview_rejection_reasons": signal.preview_rejection_reasons or {},
                "market_context": market_context,
                "snapshot_price": _extract_snapshot_price(market_context),
            }
        )
    return items


def _order_intent_details(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    statement = (
        select(OrderIntent, Strategy.name)
        .join(Strategy, OrderIntent.strategy_id == Strategy.id, isouter=True)
        .where(OrderIntent.created_at >= window_start)
        .where(OrderIntent.created_at <= window_end)
        .order_by(OrderIntent.created_at.asc())
        .limit(limit)
    )
    items = []
    for intent, strategy_name in db.execute(statement):
        items.append(
            {
                "id": str(intent.id),
                "created_at": intent.created_at.isoformat(),
                "strategy_id": str(intent.strategy_id) if intent.strategy_id else None,
                "strategy_name": strategy_name,
                "signal_id": str(intent.signal_id) if intent.signal_id else None,
                "underlying_symbol": intent.underlying_symbol,
                "option_symbol": intent.option_symbol,
                "side": intent.side,
                "quantity": intent.quantity,
                "order_type": intent.order_type,
                "limit_price": _optional_decimal_string(intent.limit_price),
                "time_in_force": intent.time_in_force,
                "status": intent.status,
                "submitted_at": intent.submitted_at.isoformat()
                if intent.submitted_at
                else None,
                "rejection_reason": intent.rejection_reason,
                "preview": intent.preview if isinstance(intent.preview, dict) else {},
            }
        )
    return items


def _broker_order_details(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    statement = (
        select(BrokerOrder)
        .where(BrokerOrder.created_at >= window_start)
        .where(BrokerOrder.created_at <= window_end)
        .order_by(BrokerOrder.created_at.asc())
        .limit(limit)
    )
    items = []
    for order in db.scalars(statement):
        items.append(
            {
                "id": str(order.id),
                "created_at": order.created_at.isoformat(),
                "order_intent_id": str(order.order_intent_id)
                if order.order_intent_id
                else None,
                "alpaca_order_id": order.alpaca_order_id,
                "symbol": order.symbol,
                "side": order.side,
                "quantity": _decimal_string(order.quantity),
                "order_type": order.order_type,
                "limit_price": _optional_decimal_string(order.limit_price),
                "status": order.status,
                "submitted_at": order.submitted_at.isoformat()
                if order.submitted_at
                else None,
                "filled_at": order.filled_at.isoformat() if order.filled_at else None,
            }
        )
    return items


def _fill_details(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    statement = (
        select(Fill, BrokerOrder.order_intent_id)
        .join(BrokerOrder, Fill.broker_order_id == BrokerOrder.id, isouter=True)
        .where(Fill.filled_at >= window_start)
        .where(Fill.filled_at <= window_end)
        .order_by(Fill.filled_at.asc())
        .limit(limit)
    )
    items = []
    for fill, order_intent_id in db.execute(statement):
        items.append(
            {
                "id": str(fill.id),
                "broker_order_id": str(fill.broker_order_id)
                if fill.broker_order_id
                else None,
                "order_intent_id": str(order_intent_id) if order_intent_id else None,
                "alpaca_fill_id": fill.alpaca_fill_id,
                "symbol": fill.symbol,
                "side": fill.side,
                "quantity": _decimal_string(fill.quantity),
                "price": _decimal_string(fill.price),
                "filled_at": fill.filled_at.isoformat(),
            }
        )
    return items


def _diagnostic_details(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    statement = (
        select(OptionSelectionDiagnostic)
        .where(OptionSelectionDiagnostic.created_at >= window_start)
        .where(OptionSelectionDiagnostic.created_at <= window_end)
        .order_by(OptionSelectionDiagnostic.created_at.asc())
        .limit(limit)
    )
    items = []
    for diagnostic in db.scalars(statement):
        items.append(
            {
                "id": str(diagnostic.id),
                "created_at": diagnostic.created_at.isoformat(),
                "signal_id": str(diagnostic.signal_id)
                if diagnostic.signal_id
                else None,
                "strategy_id": str(diagnostic.strategy_id)
                if diagnostic.strategy_id
                else None,
                "strategy_name": diagnostic.strategy_name,
                "underlying_symbol": diagnostic.underlying_symbol,
                "scanner_type": diagnostic.scanner_type,
                "preview_profile": diagnostic.preview_profile,
                "candidate_count": diagnostic.candidate_count,
                "reason_counts": diagnostic.reason_counts,
                "summary": diagnostic.summary,
                "market_context": diagnostic.market_context,
            }
        )
    return items


def _rejected_signal_shadow_outcomes(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signals_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for signal in signals:
        key = (
            str(signal.get("scanner_type") or "unknown"),
            str(signal.get("underlying_symbol") or signal.get("symbol") or "").upper(),
        )
        signals_by_key.setdefault(key, []).append(signal)

    outcomes: list[dict[str, Any]] = []
    for key, grouped_signals in signals_by_key.items():
        ordered = sorted(grouped_signals, key=lambda item: str(item.get("created_at")))
        for index, signal in enumerate(ordered):
            if signal.get("status") != "preview_rejected" and not signal.get("preview_rejection_reasons"):
                continue
            rejected_price = _decimal_or_none(signal.get("snapshot_price"))
            later = _first_later_signal_with_price(ordered[index + 1 :])
            later_price = _decimal_or_none(later.get("snapshot_price")) if later else None
            move_percent = (
                (later_price - rejected_price) / rejected_price * Decimal("100")
                if rejected_price is not None
                and later_price is not None
                and rejected_price != 0
                else None
            )
            outcomes.append(
                {
                    "signal_id": signal.get("id"),
                    "created_at": signal.get("created_at"),
                    "scanner_type": key[0],
                    "symbol": key[1],
                    "direction": signal.get("direction"),
                    "rejected_price": _optional_decimal_string(rejected_price),
                    "later_signal_id": later.get("id") if later else None,
                    "later_signal_at": later.get("created_at") if later else None,
                    "later_price": _optional_decimal_string(later_price),
                    "underlying_move_percent": _optional_decimal_string(move_percent),
                    "directional_outcome": _directional_outcome(
                        str(signal.get("direction") or ""),
                        move_percent,
                    ),
                    "preview_rejection_reasons": signal.get("preview_rejection_reasons")
                    or {},
                }
            )
    return outcomes


def _first_later_signal_with_price(signals: list[dict[str, Any]]) -> dict[str, Any] | None:
    for signal in signals:
        if _decimal_or_none(signal.get("snapshot_price")) is not None:
            return signal
    return None


def _directional_outcome(direction: str, move_percent: Decimal | None) -> str:
    if move_percent is None:
        return "unknown"
    normalized = direction.strip().lower()
    if move_percent == 0:
        return "flat"
    if normalized == "bullish":
        return "would_have_helped" if move_percent > 0 else "would_have_hurt"
    if normalized == "bearish":
        return "would_have_helped" if move_percent < 0 else "would_have_hurt"
    return "unknown"


def _scanner_type(strategy_config: Any, market_context: dict[str, Any]) -> str | None:
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


def _extract_snapshot_price(market_context: dict[str, Any]) -> str | None:
    for key in ("latest_close", "current_price", "price", "close"):
        value = market_context.get(key)
        if _decimal_or_none(value) is not None:
            return str(value)
    return None


def _count_dict(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _decimal_string(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f")


def _optional_decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return _decimal_string(Decimal(str(value)))
