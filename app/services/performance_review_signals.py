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
