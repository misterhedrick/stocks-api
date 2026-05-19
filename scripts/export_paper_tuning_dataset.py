from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.models import PaperReviewSnapshot
from app.db.session import SessionLocal


DEFAULT_MIN_SNAPSHOTS = 3
DEFAULT_MIN_CLOSED_TRADES = 5
DEFAULT_MIN_PREVIEW_REJECTIONS = 10
DEFAULT_MIN_NO_SIGNAL_REASONS = 20
CSV_FIELDS = [
    "row_type",
    "review_date",
    "scanner_type",
    "symbol",
    "snapshots",
    "priority_score",
    "readiness_status",
    "recommended_focus",
    "signals_seen",
    "submitted_signals",
    "created_signals",
    "preview_rejected",
    "diagnostics_seen",
    "option_candidate_count",
    "no_signal_reasons_seen",
    "closed_trade_cases",
    "open_trade_cases",
    "wins",
    "losses",
    "flats",
    "total_realized_pl",
    "average_return_percent",
    "top_preview_rejection_reasons",
    "top_option_diagnostic_reasons",
    "top_no_signal_reasons",
    "data_quality_warnings",
]


@dataclass(slots=True)
class EvidenceThresholds:
    min_snapshots: int = DEFAULT_MIN_SNAPSHOTS
    min_closed_trades: int = DEFAULT_MIN_CLOSED_TRADES
    min_preview_rejections: int = DEFAULT_MIN_PREVIEW_REJECTIONS
    min_no_signal_reasons: int = DEFAULT_MIN_NO_SIGNAL_REASONS


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Export saved paper-review snapshots as a scanner-level tuning dataset."
        )
    )
    parser.add_argument("--days", type=int, default=10, help="Recent snapshots to read.")
    parser.add_argument(
        "--format",
        choices=("json", "csv"),
        default="json",
        help="Output format.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write instead of printing to stdout.",
    )
    parser.add_argument("--min-snapshots", type=int, default=DEFAULT_MIN_SNAPSHOTS)
    parser.add_argument("--min-closed-trades", type=int, default=DEFAULT_MIN_CLOSED_TRADES)
    parser.add_argument(
        "--min-preview-rejections",
        type=int,
        default=DEFAULT_MIN_PREVIEW_REJECTIONS,
    )
    parser.add_argument(
        "--min-no-signal-reasons",
        type=int,
        default=DEFAULT_MIN_NO_SIGNAL_REASONS,
    )
    args = parser.parse_args()

    thresholds = EvidenceThresholds(
        min_snapshots=args.min_snapshots,
        min_closed_trades=args.min_closed_trades,
        min_preview_rejections=args.min_preview_rejections,
        min_no_signal_reasons=args.min_no_signal_reasons,
    )
    with SessionLocal() as db:
        snapshots = list(
            db.scalars(
                select(PaperReviewSnapshot)
                .order_by(
                    PaperReviewSnapshot.review_date.desc(),
                    PaperReviewSnapshot.generated_at.desc(),
                )
                .limit(args.days)
            )
        )

    dataset = build_tuning_dataset(snapshots, thresholds=thresholds)
    rendered = render_dataset(dataset, output_format=args.format)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote {args.format} tuning dataset to {args.output}")
        return
    print(rendered, end="" if rendered.endswith("\n") else "\n")


def build_tuning_dataset(
    snapshots: list[PaperReviewSnapshot],
    *,
    thresholds: EvidenceThresholds | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or EvidenceThresholds()
    ordered = sorted(snapshots, key=lambda item: (item.review_date, item.generated_at))
    daily_rows: list[dict[str, Any]] = []
    aggregate_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for snapshot in ordered:
        for candidate in _snapshot_candidates(snapshot):
            row = _daily_row(snapshot, candidate)
            daily_rows.append(row)
            key = (row["scanner_type"], row["symbol"])
            aggregate_groups.setdefault(key, []).append(row)

    aggregate_rows = [
        _aggregate_row(rows, thresholds=thresholds)
        for rows in aggregate_groups.values()
    ]
    aggregate_rows.sort(
        key=lambda item: (
            _readiness_rank(str(item["readiness_status"])),
            -int(item["priority_score"]),
            str(item["scanner_type"]),
        )
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "paper_review_snapshots.raw_payload.learning_report.refinement_candidates",
        "snapshot_count": len(ordered),
        "snapshot_window": _snapshot_window(ordered),
        "thresholds": {
            "min_snapshots": thresholds.min_snapshots,
            "min_closed_trades": thresholds.min_closed_trades,
            "min_preview_rejections": thresholds.min_preview_rejections,
            "min_no_signal_reasons": thresholds.min_no_signal_reasons,
        },
        "summary": _summary(aggregate_rows),
        "aggregate_rows": aggregate_rows,
        "daily_rows": daily_rows,
    }


def render_dataset(dataset: dict[str, Any], *, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(dataset, indent=2, sort_keys=True, default=str) + "\n"
    if output_format == "csv":
        rows = dataset.get("aggregate_rows", []) + dataset.get("daily_rows", [])
        return _csv_text(rows)
    raise ValueError("output_format must be json or csv")


def _snapshot_candidates(snapshot: PaperReviewSnapshot) -> list[dict[str, Any]]:
    raw_payload = snapshot.raw_payload if isinstance(snapshot.raw_payload, dict) else {}
    learning_report = raw_payload.get("learning_report")
    if not isinstance(learning_report, dict):
        return []
    candidates = learning_report.get("refinement_candidates")
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def _daily_row(snapshot: PaperReviewSnapshot, candidate: dict[str, Any]) -> dict[str, Any]:
    signals = _as_dict(candidate.get("signals"))
    signal_status = _as_dict(signals.get("status_counts"))
    option_selection = _as_dict(candidate.get("option_selection"))
    trade_cases = _as_dict(candidate.get("trade_cases"))
    no_signal = _as_dict(candidate.get("no_signal"))
    return {
        "row_type": "daily",
        "review_date": snapshot.review_date.isoformat(),
        "snapshot_id": str(snapshot.id),
        "scanner_type": str(candidate.get("scanner_type") or "unknown"),
        "symbol": str(candidate.get("symbol") or "ALL_SYMBOLS"),
        "snapshots": 1,
        "priority_score": _int(candidate.get("priority_score")),
        "readiness_status": "",
        "recommended_focus": list(candidate.get("recommended_focus") or []),
        "signals_seen": _int(signals.get("seen")),
        "submitted_signals": _int(signal_status.get("submitted")),
        "created_signals": _int(signal_status.get("created")),
        "preview_rejected": _int(signals.get("preview_rejected")),
        "diagnostics_seen": _int(option_selection.get("diagnostics_seen")),
        "option_candidate_count": _int(option_selection.get("candidate_count")),
        "no_signal_reasons_seen": _int(no_signal.get("reasons_seen")),
        "closed_trade_cases": _int(trade_cases.get("closed")),
        "open_trade_cases": _int(trade_cases.get("open")),
        "wins": _int(trade_cases.get("wins")),
        "losses": _int(trade_cases.get("losses")),
        "flats": _int(trade_cases.get("flats")),
        "total_realized_pl": _float(trade_cases.get("total_realized_pl")),
        "average_return_percent": _float(trade_cases.get("average_return_percent")),
        "top_preview_rejection_reasons": _top_counter(
            signals.get("preview_rejection_reasons")
        ),
        "top_option_diagnostic_reasons": _top_counter(option_selection.get("reason_counts")),
        "top_no_signal_reasons": _top_counter(no_signal.get("reasons")),
        "data_quality_warnings": [],
    }


def _aggregate_row(
    rows: list[dict[str, Any]],
    *,
    thresholds: EvidenceThresholds,
) -> dict[str, Any]:
    totals = {
        "signals_seen": sum(_int(row.get("signals_seen")) for row in rows),
        "submitted_signals": sum(_int(row.get("submitted_signals")) for row in rows),
        "created_signals": sum(_int(row.get("created_signals")) for row in rows),
        "preview_rejected": sum(_int(row.get("preview_rejected")) for row in rows),
        "diagnostics_seen": sum(_int(row.get("diagnostics_seen")) for row in rows),
        "option_candidate_count": sum(_int(row.get("option_candidate_count")) for row in rows),
        "no_signal_reasons_seen": sum(_int(row.get("no_signal_reasons_seen")) for row in rows),
        "closed_trade_cases": sum(_int(row.get("closed_trade_cases")) for row in rows),
        "open_trade_cases": sum(_int(row.get("open_trade_cases")) for row in rows),
        "wins": sum(_int(row.get("wins")) for row in rows),
        "losses": sum(_int(row.get("losses")) for row in rows),
        "flats": sum(_int(row.get("flats")) for row in rows),
    }
    total_realized_pl = sum(_float(row.get("total_realized_pl")) for row in rows)
    priority_score = max((_int(row.get("priority_score")) for row in rows), default=0)
    focus_counts = Counter(
        focus
        for row in rows
        for focus in row.get("recommended_focus", [])
        if isinstance(focus, str)
    )
    row = {
        "row_type": "aggregate",
        "review_date": f"{rows[0]['review_date']}..{rows[-1]['review_date']}",
        "scanner_type": rows[0]["scanner_type"],
        "symbol": rows[0]["symbol"],
        "snapshots": len(rows),
        "priority_score": priority_score,
        "readiness_status": "",
        "recommended_focus": [key for key, _ in focus_counts.most_common()],
        **totals,
        "total_realized_pl": _rounded(total_realized_pl),
        "average_return_percent": _average(
            [_float(item.get("average_return_percent")) for item in rows],
        ),
        "top_preview_rejection_reasons": _merge_top_counter(
            row.get("top_preview_rejection_reasons", {}) for row in rows
        ),
        "top_option_diagnostic_reasons": _merge_top_counter(
            row.get("top_option_diagnostic_reasons", {}) for row in rows
        ),
        "top_no_signal_reasons": _merge_top_counter(
            row.get("top_no_signal_reasons", {}) for row in rows
        ),
        "data_quality_warnings": [],
    }
    row["readiness_status"] = _readiness_status(row, thresholds=thresholds)
    row["data_quality_warnings"] = _data_quality_warnings(row, thresholds=thresholds)
    return row


def _readiness_status(row: dict[str, Any], *, thresholds: EvidenceThresholds) -> str:
    if _int(row["snapshots"]) < thresholds.min_snapshots:
        return "collect_more_snapshot_days"
    if _int(row["closed_trade_cases"]) >= thresholds.min_closed_trades:
        if _int(row["losses"]) > 0:
            return "ready_for_exit_or_risk_review"
        return "watch_trade_outcomes"
    if _int(row["preview_rejected"]) >= thresholds.min_preview_rejections:
        return "ready_for_option_filter_review"
    if _int(row["no_signal_reasons_seen"]) >= thresholds.min_no_signal_reasons:
        return "ready_for_signal_threshold_review"
    return "collect_more_evidence"


def _data_quality_warnings(
    row: dict[str, Any],
    *,
    thresholds: EvidenceThresholds,
) -> list[str]:
    warnings = []
    if _int(row["snapshots"]) < thresholds.min_snapshots:
        warnings.append("too_few_snapshot_days")
    if _int(row["signals_seen"]) == 0 and _int(row["no_signal_reasons_seen"]) == 0:
        warnings.append("no_signal_or_no_signal_reason_evidence")
    if _int(row["diagnostics_seen"]) == 0 and _int(row["preview_rejected"]) > 0:
        warnings.append("preview_rejections_without_option_diagnostics")
    if _int(row["closed_trade_cases"]) == 0:
        warnings.append("no_closed_trade_cases")
    return warnings


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "aggregate_rows": len(rows),
        "by_readiness_status": dict(
            sorted(Counter(str(row["readiness_status"]) for row in rows).items())
        ),
        "ready_rows": sum(
            1
            for row in rows
            if str(row["readiness_status"]).startswith("ready_for_")
        ),
    }


def _snapshot_window(snapshots: list[PaperReviewSnapshot]) -> dict[str, Any]:
    if not snapshots:
        return {"start_date": None, "end_date": None}
    return {
        "start_date": snapshots[0].review_date.isoformat(),
        "end_date": snapshots[-1].review_date.isoformat(),
    }


def _csv_text(rows: list[dict[str, Any]]) -> str:
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                field: _csv_value(row.get(field))
                for field in CSV_FIELDS
            }
        )
    return buffer.getvalue()


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


def _top_counter(value: Any, *, limit: int = 5) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counter: Counter[str] = Counter()
    for key, count in value.items():
        counter[str(key)] += _int(count, default=1)
    return dict(counter.most_common(limit))


def _merge_top_counter(values: Any, *, limit: int = 5) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, count in value.items():
            counter[str(key)] += _int(count, default=1)
    return dict(counter.most_common(limit))


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return _rounded(sum(values) / len(values))


def _rounded(value: float) -> float:
    return round(value, 4)


def _readiness_rank(status: str) -> int:
    ranks = {
        "ready_for_option_filter_review": 0,
        "ready_for_signal_threshold_review": 1,
        "ready_for_exit_or_risk_review": 2,
        "watch_trade_outcomes": 3,
        "collect_more_evidence": 4,
        "collect_more_snapshot_days": 5,
    }
    return ranks.get(status, 6)


if __name__ == "__main__":
    main()
