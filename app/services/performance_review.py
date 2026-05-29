from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.performance_review_helpers import (
    _fill_records,
    _match_round_trips,
    _close_expired_missing_position_lots,
    _signal_records,
    _option_selection_diagnostic_records,
    _no_signal_summary,
    _open_position_summaries,
    _totals,
    _strategy_summaries,
    _symbol_summaries,
    _signal_summary,
    _diagnostic_summary,
    _rejected_preview_outcomes,
)


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


def get_performance_review(
    db: Session,
    *,
    limit: int = 500,
) -> PerformanceReviewResult:
    fill_records = _fill_records(db, limit=limit)
    round_trips, open_lots, unmatched_closing_fills, ignored_fills = (
        _match_round_trips(fill_records)
    )
    round_trips.extend(_close_expired_missing_position_lots(db, open_lots))

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
