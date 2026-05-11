from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import OptionSelectionDiagnostic

MARKET_TIMEZONE = ZoneInfo("America/New_York")


def build_option_selection_diagnostics_summary(
    db: Session,
    *,
    review_date: date | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    selected_date = review_date or datetime.now(MARKET_TIMEZONE).date()
    window_start, window_end = _market_day_window_utc(selected_date)
    diagnostics = _diagnostics(
        db,
        window_start=window_start,
        window_end=window_end,
        limit=limit,
    )

    return {
        "review_date": selected_date.isoformat(),
        "timezone": MARKET_TIMEZONE.key,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "limit": limit,
        "total": len(diagnostics),
        "reason_counts": _reason_counts(diagnostics),
        "by_symbol": _grouped_summary(diagnostics, key_name="underlying_symbol"),
        "by_scanner_type": _grouped_summary(diagnostics, key_name="scanner_type"),
        "by_preview_profile": _grouped_summary(diagnostics, key_name="preview_profile"),
        "by_strategy": _strategy_summary(diagnostics),
        "groups": _combined_groups(diagnostics),
    }


def _market_day_window_utc(selected_date: date) -> tuple[datetime, datetime]:
    local_start = datetime.combine(selected_date, time.min, tzinfo=MARKET_TIMEZONE)
    local_end = datetime.combine(selected_date, time.max, tzinfo=MARKET_TIMEZONE)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _diagnostics(
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


def _reason_counts(diagnostics: list[OptionSelectionDiagnostic]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for diagnostic in diagnostics:
        counter.update(_clean_reason_counts(diagnostic.reason_counts))
    return _counter_dict(counter)


def _grouped_summary(
    diagnostics: list[OptionSelectionDiagnostic],
    *,
    key_name: str,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[OptionSelectionDiagnostic]] = defaultdict(list)
    for diagnostic in diagnostics:
        key = _diagnostic_value(diagnostic, key_name)
        grouped[key].append(diagnostic)

    return {
        key: _diagnostic_group_payload(items)
        for key, items in sorted(grouped.items())
    }


def _strategy_summary(
    diagnostics: list[OptionSelectionDiagnostic]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[OptionSelectionDiagnostic]] = defaultdict(list)
    for diagnostic in diagnostics:
        key = diagnostic.strategy_name or str(diagnostic.strategy_id or "unknown")
        grouped[key].append(diagnostic)

    return {
        key: _diagnostic_group_payload(items)
        for key, items in sorted(grouped.items())
    }


def _combined_groups(diagnostics: list[OptionSelectionDiagnostic]) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str, str, str],
        list[OptionSelectionDiagnostic],
    ] = defaultdict(list)
    for diagnostic in diagnostics:
        grouped[
            (
                diagnostic.underlying_symbol or "unknown",
                diagnostic.scanner_type or "unknown",
                diagnostic.preview_profile or "unknown",
                diagnostic.strategy_name or str(diagnostic.strategy_id or "unknown"),
            )
        ].append(diagnostic)

    return [
        {
            "underlying_symbol": symbol,
            "scanner_type": scanner_type,
            "preview_profile": preview_profile,
            "strategy": strategy,
            **_diagnostic_group_payload(items),
        }
        for (symbol, scanner_type, preview_profile, strategy), items in sorted(grouped.items())
    ]


def _diagnostic_group_payload(
    diagnostics: list[OptionSelectionDiagnostic],
) -> dict[str, Any]:
    reason_counter: Counter[str] = Counter()
    candidate_count = 0
    latest_at: str | None = None
    sample_ids: list[str] = []
    for diagnostic in diagnostics:
        reason_counter.update(_clean_reason_counts(diagnostic.reason_counts))
        candidate_count += int(diagnostic.candidate_count or 0)
        created_at = diagnostic.created_at.isoformat()
        latest_at = created_at if latest_at is None or created_at > latest_at else latest_at
        if len(sample_ids) < 5:
            sample_ids.append(str(diagnostic.id))

    return {
        "diagnostic_count": len(diagnostics),
        "candidate_count": candidate_count,
        "reason_counts": _counter_dict(reason_counter),
        "latest_at": latest_at,
        "sample_diagnostic_ids": sample_ids,
    }


def _diagnostic_value(diagnostic: OptionSelectionDiagnostic, key_name: str) -> str:
    value = getattr(diagnostic, key_name, None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _clean_reason_counts(value: Any) -> Counter[str]:
    counter: Counter[str] = Counter()
    if not isinstance(value, dict):
        return counter
    for reason, count in value.items():
        try:
            clean_count = int(count)
        except (TypeError, ValueError):
            clean_count = 1
        counter[str(reason)] += clean_count
    return counter


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted((str(key), int(value)) for key, value in counter.items()))
