from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    BrokerOrder,
    Fill,
    JobRun,
    OptionSelectionDiagnostic,
    OrderIntent,
    Signal,
    Strategy,
)


@dataclass(slots=True)
class FillRecord:
    fill_id: uuid.UUID
    filled_at: datetime
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    strategy_id: uuid.UUID | None
    strategy_name: str | None
    order_intent_id: uuid.UUID | None
    order_intent_side: str | None
    order_intent_rationale: str | None
    order_intent_preview: dict[str, Any]
    signal_id: uuid.UUID | None
    signal_underlying_symbol: str | None
    signal_type: str | None
    signal_direction: str | None
    signal_confidence: Decimal | None
    signal_rationale: str | None
    signal_market_context: dict[str, Any]


@dataclass(slots=True)
class PerformanceReviewResult:
    generated_at: datetime
    fills_seen: int
    matched_round_trips: int
    open_positions: list[dict[str, Any]]
    totals: dict[str, Any]
    by_strategy: list[dict[str, Any]]
    by_symbol: list[dict[str, Any]]
    recent_round_trips: list[dict[str, Any]]
    unmatched_closing_fills: list[dict[str, Any]] = field(default_factory=list)
    ignored_fills: list[dict[str, Any]] = field(default_factory=list)
    signal_summary: dict[str, Any] = field(default_factory=dict)
    no_signal_summary: dict[str, Any] = field(default_factory=dict)
    option_selection_diagnostics: dict[str, Any] = field(default_factory=dict)
    rejected_preview_outcomes: list[dict[str, Any]] = field(default_factory=list)


def get_paper_performance_review(
    db: Session,
    *,
    limit: int = 500,
) -> PerformanceReviewResult:
    fill_records = _fill_records(db, limit=limit)
    round_trips, open_lots, unmatched_closing_fills, ignored_fills = (
        _match_round_trips(fill_records)
    )

    signal_records = _signal_records(db, limit=limit)
    diagnostic_records = _option_selection_diagnostic_records(db, limit=limit)
    no_signal_summary = _no_signal_summary(db, limit=limit)

    return PerformanceReviewResult(
        generated_at=datetime.now(timezone.utc),
        fills_seen=len(fill_records),
        matched_round_trips=len(round_trips),
        open_positions=_open_position_summaries(open_lots),
        totals=_totals(round_trips),
        by_strategy=_strategy_summaries(round_trips),
        by_symbol=_symbol_summaries(round_trips),
        recent_round_trips=round_trips[-25:][::-1],
        unmatched_closing_fills=unmatched_closing_fills[-25:][::-1],
        ignored_fills=ignored_fills[-25:][::-1],
        signal_summary=_signal_summary(signal_records),
        no_signal_summary=no_signal_summary,
        option_selection_diagnostics=_diagnostic_summary(diagnostic_records),
        rejected_preview_outcomes=_rejected_preview_outcomes(
            signal_records,
            round_trips,
        ),
    )


def _fill_records(db: Session, *, limit: int) -> list[FillRecord]:
    statement = (
        select(
            Fill.id,
            Fill.filled_at,
            Fill.symbol,
            Fill.side,
            Fill.quantity,
            Fill.price,
            OrderIntent.strategy_id,
            Strategy.name,
            OrderIntent.id,
            OrderIntent.side,
            OrderIntent.rationale,
            OrderIntent.preview,
            Signal.id,
            Signal.underlying_symbol,
            Signal.signal_type,
            Signal.direction,
            Signal.confidence,
            Signal.rationale,
            Signal.market_context,
        )
        .select_from(Fill)
        .join(BrokerOrder, Fill.broker_order_id == BrokerOrder.id, isouter=True)
        .join(OrderIntent, BrokerOrder.order_intent_id == OrderIntent.id, isouter=True)
        .join(Strategy, OrderIntent.strategy_id == Strategy.id, isouter=True)
        .join(Signal, OrderIntent.signal_id == Signal.id, isouter=True)
        .order_by(Fill.filled_at.asc())
        .limit(limit)
    )
    return [
        _coerce_fill_record(row)
        for row in db.execute(statement)
    ]


def _coerce_fill_record(row: object) -> FillRecord:
    values = tuple(row)
    order_intent_preview = values[11] if len(values) > 11 else {}
    signal_market_context = values[18] if len(values) > 18 else {}
    return FillRecord(
        fill_id=values[0],
        filled_at=values[1],
        symbol=str(values[2]),
        side=str(values[3]).lower(),
        quantity=Decimal(str(values[4])),
        price=Decimal(str(values[5])),
        strategy_id=values[6],
        strategy_name=values[7],
        order_intent_id=values[8],
        order_intent_side=values[9] if len(values) > 9 else None,
        order_intent_rationale=values[10] if len(values) > 10 else None,
        order_intent_preview=(
            order_intent_preview if isinstance(order_intent_preview, dict) else {}
        ),
        signal_id=values[12] if len(values) > 12 else None,
        signal_underlying_symbol=values[13] if len(values) > 13 else None,
        signal_type=values[14] if len(values) > 14 else None,
        signal_direction=values[15] if len(values) > 15 else None,
        signal_confidence=values[16] if len(values) > 16 else None,
        signal_rationale=values[17] if len(values) > 17 else None,
        signal_market_context=(
            signal_market_context if isinstance(signal_market_context, dict) else {}
        ),
    )


def _match_round_trips(
    fills: list[FillRecord],
) -> tuple[
    list[dict[str, Any]],
    dict[tuple[str, uuid.UUID | None], list[dict[str, Any]]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    open_lots: dict[tuple[str, uuid.UUID | None], list[dict[str, Any]]] = {}
    round_trips: list[dict[str, Any]] = []
    unmatched_closing_fills: list[dict[str, Any]] = []
    ignored_fills: list[dict[str, Any]] = []

    for fill in fills:
        key = (fill.symbol, fill.strategy_id)
        if fill.side == "buy":
            open_lots.setdefault(key, []).append(
                {
                    "symbol": fill.symbol,
                    "strategy_id": fill.strategy_id,
                    "strategy_name": fill.strategy_name,
                    "quantity": fill.quantity,
                    "remaining_quantity": fill.quantity,
                    "entry_price": fill.price,
                    "entry_fill_id": fill.fill_id,
                    "entry_at": fill.filled_at,
                    "order_intent_id": fill.order_intent_id,
                    "entry_context": _fill_learning_context(fill),
                }
            )
            continue

        if fill.side == "sell_short":
            unmatched_closing_fills.append(
                {
                    **_fill_summary_for_learning(fill),
                    "reason": "sell_short fill is not matched as a long-option exit",
                }
            )
            continue

        if fill.side != "sell":
            ignored_fills.append(
                {
                    **_fill_summary_for_learning(fill),
                    "reason": "unsupported fill side",
                }
            )
            continue

        remaining_sell_quantity = fill.quantity
        lots = open_lots.get(key, [])
        matched_any = False
        while remaining_sell_quantity > 0 and lots:
            matched_any = True
            lot = lots[0]
            matched_quantity = min(remaining_sell_quantity, lot["remaining_quantity"])
            entry_price = Decimal(str(lot["entry_price"]))
            exit_price = fill.price
            multiplier = Decimal("100")
            entry_notional = entry_price * matched_quantity * multiplier
            exit_notional = exit_price * matched_quantity * multiplier
            realized_pnl = exit_notional - entry_notional
            holding_seconds = int(
                (fill.filled_at - lot["entry_at"]).total_seconds()
            )

            round_trips.append(
                {
                    "symbol": fill.symbol,
                    "underlying_symbol": fill.signal_underlying_symbol,
                    "strategy_id": str(fill.strategy_id)
                    if fill.strategy_id is not None
                    else None,
                    "strategy_name": fill.strategy_name,
                    "quantity": _decimal_string(matched_quantity),
                    "entry_price": _decimal_string(entry_price),
                    "exit_price": _decimal_string(exit_price),
                    "entry_notional": _decimal_string(entry_notional),
                    "exit_notional": _decimal_string(exit_notional),
                    "realized_pnl": _decimal_string(realized_pnl),
                    "return_percent": _decimal_string(
                        (realized_pnl / entry_notional * Decimal("100"))
                        if entry_notional != 0
                        else Decimal("0")
                    ),
                    "entry_at": lot["entry_at"].isoformat(),
                    "exit_at": fill.filled_at.isoformat(),
                    "holding_seconds": holding_seconds,
                    "entry_fill_id": str(lot["entry_fill_id"]),
                    "exit_fill_id": str(fill.fill_id),
                    "entry_order_intent_id": str(lot["order_intent_id"])
                    if lot["order_intent_id"] is not None
                    else None,
                    "exit_order_intent_id": str(fill.order_intent_id)
                    if fill.order_intent_id is not None
                    else None,
                    "entry_context": lot["entry_context"],
                    "exit_context": _fill_learning_context(fill),
                }
            )

            lot["remaining_quantity"] -= matched_quantity
            remaining_sell_quantity -= matched_quantity
            if lot["remaining_quantity"] <= 0:
                lots.pop(0)

        if remaining_sell_quantity > 0 or not matched_any:
            unmatched_closing_fills.append(
                {
                    **_fill_summary_for_learning(fill),
                    "unmatched_quantity": _decimal_string(remaining_sell_quantity),
                    "reason": "no open buy lot for symbol and strategy",
                }
            )

    return round_trips, open_lots, unmatched_closing_fills, ignored_fills


def _fill_learning_context(fill: FillRecord) -> dict[str, Any]:
    return {
        "order_intent": {
            "id": str(fill.order_intent_id) if fill.order_intent_id is not None else None,
            "side": fill.order_intent_side,
            "rationale": fill.order_intent_rationale,
            "preview": fill.order_intent_preview,
        },
        "signal": {
            "id": str(fill.signal_id) if fill.signal_id is not None else None,
            "underlying_symbol": fill.signal_underlying_symbol,
            "signal_type": fill.signal_type,
            "direction": fill.signal_direction,
            "confidence": _optional_decimal_string(fill.signal_confidence),
            "rationale": fill.signal_rationale,
            "market_context": fill.signal_market_context,
        },
    }


def _fill_summary_for_learning(fill: FillRecord) -> dict[str, Any]:
    return {
        "fill_id": str(fill.fill_id),
        "filled_at": fill.filled_at.isoformat(),
        "symbol": fill.symbol,
        "side": fill.side,
        "quantity": _decimal_string(fill.quantity),
        "price": _decimal_string(fill.price),
        "strategy_id": str(fill.strategy_id) if fill.strategy_id is not None else None,
        "strategy_name": fill.strategy_name,
        "order_intent_id": str(fill.order_intent_id)
        if fill.order_intent_id is not None
        else None,
    }


def _open_position_summaries(
    open_lots: dict[tuple[str, uuid.UUID | None], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    summaries = []
    for (symbol, strategy_id), lots in open_lots.items():
        remaining_quantity = sum(
            (Decimal(str(lot["remaining_quantity"])) for lot in lots),
            Decimal("0"),
        )
        if remaining_quantity <= 0:
            continue
        cost_basis = sum(
            Decimal(str(lot["entry_price"]))
            * Decimal(str(lot["remaining_quantity"]))
            * Decimal("100")
            for lot in lots
        )
        strategy_name = next(
            (
                lot.get("strategy_name")
                for lot in lots
                if lot.get("strategy_name") is not None
            ),
            None,
        )
        summaries.append(
            {
                "symbol": symbol,
                "strategy_id": str(strategy_id) if strategy_id is not None else None,
                "strategy_name": strategy_name,
                "open_quantity": _decimal_string(remaining_quantity),
                "cost_basis": _decimal_string(cost_basis),
                "average_entry_price": _decimal_string(
                    cost_basis / remaining_quantity / Decimal("100")
                ),
                "open_lots": len(lots),
            }
        )
    return sorted(
        summaries,
        key=lambda item: (str(item["strategy_name"] or ""), str(item["symbol"])),
    )


def _totals(round_trips: list[dict[str, Any]]) -> dict[str, Any]:
    wins = [
        Decimal(str(item["realized_pnl"]))
        for item in round_trips
        if Decimal(str(item["realized_pnl"])) > 0
    ]
    losses = [
        Decimal(str(item["realized_pnl"]))
        for item in round_trips
        if Decimal(str(item["realized_pnl"])) < 0
    ]
    realized_pnl = sum(
        (Decimal(str(item["realized_pnl"])) for item in round_trips),
        Decimal("0"),
    )
    total_entry_notional = sum(
        (Decimal(str(item["entry_notional"])) for item in round_trips),
        Decimal("0"),
    )
    return {
        "realized_pnl": _decimal_string(realized_pnl),
        "total_entry_notional": _decimal_string(total_entry_notional),
        "return_percent": _decimal_string(
            realized_pnl / total_entry_notional * Decimal("100")
            if total_entry_notional != 0
            else Decimal("0")
        ),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "flat_trades": len(round_trips) - len(wins) - len(losses),
        "win_rate_percent": _decimal_string(
            Decimal(len(wins)) / Decimal(len(round_trips)) * Decimal("100")
            if round_trips
            else Decimal("0")
        ),
        "average_win": _decimal_string(sum(wins, Decimal("0")) / Decimal(len(wins)))
        if wins
        else "0",
        "average_loss": _decimal_string(
            sum(losses, Decimal("0")) / Decimal(len(losses))
        )
        if losses
        else "0",
    }


def _strategy_summaries(round_trips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
    for item in round_trips:
        key = (item.get("strategy_id"), item.get("strategy_name"))
        grouped.setdefault(key, []).append(item)

    summaries = []
    for (strategy_id, strategy_name), items in grouped.items():
        totals = _totals(items)
        summaries.append(
            {
                "strategy_id": strategy_id,
                "strategy_name": strategy_name,
                "matched_round_trips": len(items),
                **totals,
            }
        )
    return sorted(
        summaries,
        key=lambda item: Decimal(str(item["realized_pnl"])),
        reverse=True,
    )


def _symbol_summaries(round_trips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in round_trips:
        grouped.setdefault(str(item.get("symbol")), []).append(item)

    summaries = []
    for symbol, items in grouped.items():
        totals = _totals(items)
        strategy_names = sorted(
            {
                str(item["strategy_name"])
                for item in items
                if item.get("strategy_name") is not None
            }
        )
        summaries.append(
            {
                "symbol": symbol,
                "matched_round_trips": len(items),
                "strategy_names": strategy_names,
                **totals,
            }
        )
    return sorted(
        summaries,
        key=lambda item: Decimal(str(item["realized_pnl"])),
        reverse=True,
    )


@dataclass(slots=True)
class SignalReviewRecord:
    signal_id: uuid.UUID
    created_at: datetime
    strategy_id: uuid.UUID | None
    strategy_name: str | None
    scanner_type: str | None
    symbol: str
    underlying_symbol: str | None
    signal_type: str
    direction: str
    status: str
    preview_attempts: int
    last_preview_error_code: str | None
    preview_rejection_reasons: dict[str, Any]
    market_context: dict[str, Any]


@dataclass(slots=True)
class DiagnosticReviewRecord:
    diagnostic_id: uuid.UUID
    created_at: datetime
    signal_id: uuid.UUID | None
    strategy_id: uuid.UUID | None
    strategy_name: str | None
    underlying_symbol: str
    scanner_type: str | None
    preview_profile: str | None
    candidate_count: int
    reason_counts: dict[str, Any]


def _signal_records(db: Session, *, limit: int) -> list[SignalReviewRecord]:
    statement = (
        select(
            Signal.id,
            Signal.created_at,
            Signal.strategy_id,
            Strategy.name,
            Strategy.config,
            Signal.symbol,
            Signal.underlying_symbol,
            Signal.signal_type,
            Signal.direction,
            Signal.status,
            Signal.preview_attempts,
            Signal.last_preview_error_code,
            Signal.preview_rejection_reasons,
            Signal.market_context,
        )
        .select_from(Signal)
        .join(Strategy, Signal.strategy_id == Strategy.id, isouter=True)
        .order_by(Signal.created_at.desc())
        .limit(limit)
    )
    return [_coerce_signal_record(row) for row in db.execute(statement)]


def _coerce_signal_record(row: object) -> SignalReviewRecord:
    values = tuple(row)
    strategy_config = values[4] if len(values) > 4 else {}
    market_context = values[13] if len(values) > 13 else {}
    return SignalReviewRecord(
        signal_id=values[0],
        created_at=values[1],
        strategy_id=values[2],
        strategy_name=values[3],
        scanner_type=_scanner_type_from_context(strategy_config, market_context),
        symbol=str(values[5]),
        underlying_symbol=values[6],
        signal_type=str(values[7]),
        direction=str(values[8]),
        status=str(values[9]),
        preview_attempts=int(values[10] or 0),
        last_preview_error_code=values[11],
        preview_rejection_reasons=values[12] if isinstance(values[12], dict) else {},
        market_context=market_context if isinstance(market_context, dict) else {},
    )


def _option_selection_diagnostic_records(
    db: Session,
    *,
    limit: int,
) -> list[DiagnosticReviewRecord]:
    statement = (
        select(
            OptionSelectionDiagnostic.id,
            OptionSelectionDiagnostic.created_at,
            OptionSelectionDiagnostic.signal_id,
            OptionSelectionDiagnostic.strategy_id,
            OptionSelectionDiagnostic.strategy_name,
            OptionSelectionDiagnostic.underlying_symbol,
            OptionSelectionDiagnostic.scanner_type,
            OptionSelectionDiagnostic.preview_profile,
            OptionSelectionDiagnostic.candidate_count,
            OptionSelectionDiagnostic.reason_counts,
        )
        .order_by(OptionSelectionDiagnostic.created_at.desc())
        .limit(limit)
    )
    return [_coerce_diagnostic_record(row) for row in db.execute(statement)]


def _coerce_diagnostic_record(row: object) -> DiagnosticReviewRecord:
    values = tuple(row)
    reason_counts = values[9] if len(values) > 9 else {}
    return DiagnosticReviewRecord(
        diagnostic_id=values[0],
        created_at=values[1],
        signal_id=values[2],
        strategy_id=values[3],
        strategy_name=values[4],
        underlying_symbol=str(values[5]),
        scanner_type=values[6],
        preview_profile=values[7],
        candidate_count=int(values[8] or 0),
        reason_counts=reason_counts if isinstance(reason_counts, dict) else {},
    )


def _signal_summary(signals: list[SignalReviewRecord]) -> dict[str, Any]:
    return {
        "signals_seen": len(signals),
        "by_status": _count_by(signals, lambda item: item.status),
        "by_scanner_type": _signal_group_summaries(
            signals,
            key_fn=lambda item: item.scanner_type or "unknown",
            key_name="scanner_type",
        ),
        "by_symbol": _signal_group_summaries(
            signals,
            key_fn=lambda item: item.underlying_symbol or item.symbol,
            key_name="symbol",
        ),
        "preview_rejection_reasons": _reason_totals(
            item.preview_rejection_reasons for item in signals
        ),
    }


def _signal_group_summaries(
    signals: list[SignalReviewRecord],
    *,
    key_fn: Any,
    key_name: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[SignalReviewRecord]] = {}
    for signal in signals:
        grouped.setdefault(str(key_fn(signal)), []).append(signal)

    summaries = []
    for key, items in grouped.items():
        preview_rejected = [item for item in items if item.status == "preview_rejected"]
        summaries.append(
            {
                key_name: key,
                "signals_seen": len(items),
                "by_status": _count_by(items, lambda item: item.status),
                "preview_rejected": len(preview_rejected),
                "preview_rejection_reasons": _reason_totals(
                    item.preview_rejection_reasons for item in preview_rejected
                ),
            }
        )
    return sorted(
        summaries,
        key=lambda item: (-int(item["signals_seen"]), str(item[key_name])),
    )


def _diagnostic_summary(diagnostics: list[DiagnosticReviewRecord]) -> dict[str, Any]:
    return {
        "diagnostics_seen": len(diagnostics),
        "total_candidates_checked": sum(item.candidate_count for item in diagnostics),
        "reason_counts": _reason_totals(item.reason_counts for item in diagnostics),
        "by_scanner_type": _diagnostic_group_summaries(
            diagnostics,
            key_fn=lambda item: item.scanner_type or "unknown",
            key_name="scanner_type",
        ),
        "by_symbol": _diagnostic_group_summaries(
            diagnostics,
            key_fn=lambda item: item.underlying_symbol,
            key_name="symbol",
        ),
    }


def _diagnostic_group_summaries(
    diagnostics: list[DiagnosticReviewRecord],
    *,
    key_fn: Any,
    key_name: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[DiagnosticReviewRecord]] = {}
    for diagnostic in diagnostics:
        grouped.setdefault(str(key_fn(diagnostic)), []).append(diagnostic)

    summaries = []
    for key, items in grouped.items():
        summaries.append(
            {
                key_name: key,
                "diagnostics_seen": len(items),
                "candidate_count": sum(item.candidate_count for item in items),
                "reason_counts": _reason_totals(item.reason_counts for item in items),
            }
        )
    return sorted(
        summaries,
        key=lambda item: (-int(item["diagnostics_seen"]), str(item[key_name])),
    )


def _no_signal_summary(db: Session, *, limit: int) -> dict[str, Any]:
    strategy_scanner_types = _strategy_scanner_type_map(db)
    statement = (
        select(JobRun.job_name, JobRun.details)
        .where(JobRun.job_name.in_(["scan_signals", "market_cycle", "market_entry_cycle"]))
        .where(JobRun.status == "succeeded")
        .order_by(JobRun.started_at.desc())
        .limit(limit)
    )

    job_runs_seen = 0
    grouped: dict[str, dict[str, int]] = {}
    top_reasons: dict[str, int] = {}
    for row in db.execute(statement):
        job_runs_seen += 1
        values = tuple(row)
        details = values[1] if len(values) > 1 and isinstance(values[1], dict) else {}
        for raw_reason in _extract_no_signal_reasons(details):
            strategy_name, reason = _split_no_signal_reason(raw_reason)
            scanner_type = _scanner_type_for_strategy_name(
                strategy_name,
                strategy_scanner_types,
            )
            scanner_reasons = grouped.setdefault(scanner_type, {})
            scanner_reasons[reason] = scanner_reasons.get(reason, 0) + 1
            top_reasons[reason] = top_reasons.get(reason, 0) + 1

    by_scanner_type = [
        {
            "scanner_type": scanner_type,
            "reasons_seen": sum(reasons.values()),
            "reasons": dict(
                sorted(reasons.items(), key=lambda item: (-item[1], item[0]))
            ),
        }
        for scanner_type, reasons in grouped.items()
    ]
    return {
        "job_runs_seen": job_runs_seen,
        "reasons_seen": sum(top_reasons.values()),
        "top_reasons": dict(
            sorted(top_reasons.items(), key=lambda item: (-item[1], item[0]))
        ),
        "by_scanner_type": sorted(
            by_scanner_type,
            key=lambda item: (-int(item["reasons_seen"]), str(item["scanner_type"])),
        ),
    }


def _strategy_scanner_type_map(db: Session) -> dict[str, str]:
    statement = select(Strategy.name, Strategy.config)
    strategy_map: dict[str, str] = {}
    for row in db.execute(statement):
        values = tuple(row)
        strategy_name = values[0] if values else None
        scanner_type = _scanner_type_from_context(
            values[1] if len(values) > 1 else {},
            {},
        )
        if isinstance(strategy_name, str) and scanner_type:
            strategy_map[strategy_name] = scanner_type
    return strategy_map


def _extract_no_signal_reasons(details: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    direct = details.get("no_signal_reasons")
    if isinstance(direct, list):
        reasons.extend(str(item) for item in direct if item)

    scan = details.get("scan")
    if isinstance(scan, dict):
        nested = scan.get("no_signal_reasons")
        if isinstance(nested, list):
            reasons.extend(str(item) for item in nested if item)
    return reasons


def _split_no_signal_reason(reason: str) -> tuple[str | None, str]:
    if ":" not in reason:
        return None, reason.strip()
    prefix, detail = reason.split(":", 1)
    return prefix.strip() or None, detail.strip() or reason.strip()


def _scanner_type_for_strategy_name(
    strategy_name: str | None,
    strategy_scanner_types: dict[str, str],
) -> str:
    if strategy_name is None:
        return "unknown"
    if strategy_name in strategy_scanner_types:
        return strategy_scanner_types[strategy_name]
    for known_name, scanner_type in sorted(
        strategy_scanner_types.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if strategy_name.startswith(f"{known_name}."):
            return scanner_type
    return "unknown"


def _rejected_preview_outcomes(
    signals: list[SignalReviewRecord],
    round_trips: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rejected = [
        item
        for item in signals
        if item.status == "preview_rejected" or item.preview_rejection_reasons
    ]
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for signal in rejected:
        scanner_type = signal.scanner_type or "unknown"
        symbol = signal.underlying_symbol or signal.symbol
        key = (scanner_type, symbol)
        bucket = grouped.setdefault(
            key,
            {
                "scanner_type": scanner_type,
                "symbol": symbol,
                "rejected_signals": 0,
                "preview_rejection_reasons": {},
                "later_matched_round_trips": 0,
                "later_realized_pnl": Decimal("0"),
                "later_winning_trades": 0,
                "later_losing_trades": 0,
                "sample_rejected_signal_ids": [],
                "_later_trade_keys": set(),
            },
        )
        bucket["rejected_signals"] += 1
        _merge_reason_counts(
            bucket["preview_rejection_reasons"],
            signal.preview_rejection_reasons,
        )
        if len(bucket["sample_rejected_signal_ids"]) < 5:
            bucket["sample_rejected_signal_ids"].append(str(signal.signal_id))

        later_trades = [
            trade
            for trade in round_trips
            if _round_trip_scanner_type(trade) == scanner_type
            and _round_trip_underlying_symbol(trade) == symbol
            and _parse_datetime(str(trade.get("entry_at"))) >= signal.created_at
        ]
        for trade in later_trades:
            trade_key = str(trade.get("entry_fill_id") or trade.get("entry_order_intent_id") or id(trade))
            if trade_key in bucket["_later_trade_keys"]:
                continue
            bucket["_later_trade_keys"].add(trade_key)
            bucket["later_matched_round_trips"] += 1
            pnl = Decimal(str(trade.get("realized_pnl", "0")))
            bucket["later_realized_pnl"] += pnl
            if pnl > 0:
                bucket["later_winning_trades"] += 1
            elif pnl < 0:
                bucket["later_losing_trades"] += 1

    summaries = []
    for bucket in grouped.values():
        bucket.pop("_later_trade_keys", None)
        later_count = int(bucket["later_matched_round_trips"])
        summaries.append(
            {
                **bucket,
                "later_realized_pnl": _decimal_string(bucket["later_realized_pnl"]),
                "later_win_rate_percent": _decimal_string(
                    Decimal(bucket["later_winning_trades"])
                    / Decimal(later_count)
                    * Decimal("100")
                    if later_count
                    else Decimal("0")
                ),
            }
        )
    return sorted(
        summaries,
        key=lambda item: (-int(item["rejected_signals"]), str(item["scanner_type"]), str(item["symbol"])),
    )


def _scanner_type_from_context(
    strategy_config: Any,
    market_context: Any,
) -> str | None:
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


def _round_trip_scanner_type(round_trip: dict[str, Any]) -> str:
    signal = round_trip.get("entry_context", {}).get("signal", {})
    market_context = signal.get("market_context", {}) if isinstance(signal, dict) else {}
    if isinstance(market_context, dict):
        strategy_type = market_context.get("strategy_type")
        if isinstance(strategy_type, str) and strategy_type.strip():
            return strategy_type.strip()
    return "unknown"


def _round_trip_underlying_symbol(round_trip: dict[str, Any]) -> str:
    signal = round_trip.get("entry_context", {}).get("signal", {})
    if isinstance(signal, dict):
        underlying = signal.get("underlying_symbol")
        if isinstance(underlying, str) and underlying.strip():
            return underlying.strip().upper()
    return str(round_trip.get("symbol") or "").strip().upper()


def _count_by(items: list[Any], key_fn: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(key_fn(item) or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _reason_totals(reason_sets: Any) -> dict[str, int]:
    totals: dict[str, int] = {}
    for reasons in reason_sets:
        if isinstance(reasons, dict):
            _merge_reason_counts(totals, reasons)
    return dict(sorted(totals.items(), key=lambda item: (-item[1], item[0])))


def _merge_reason_counts(target: dict[str, int], reasons: dict[str, Any]) -> None:
    for reason, count in reasons.items():
        try:
            increment = int(count)
        except (TypeError, ValueError):
            increment = 1
        target[str(reason)] = target.get(str(reason), 0) + increment


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _decimal_string(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f")


def _optional_decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return _decimal_string(Decimal(str(value)))
