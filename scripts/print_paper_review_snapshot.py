from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.models import PaperReviewSnapshot
from app.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print a readable report for the latest paper review snapshot."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=8,
        help="Maximum rows to print per report section.",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        snapshot = db.scalar(
            select(PaperReviewSnapshot)
            .order_by(PaperReviewSnapshot.generated_at.desc())
            .limit(1)
        )

    if snapshot is None:
        print("No paper review snapshots found.")
        return

    print(format_snapshot_report(snapshot, limit=args.limit))


def format_snapshot_report(snapshot: PaperReviewSnapshot, *, limit: int = 8) -> str:
    summary = _as_dict(snapshot.summary)
    signals = _as_dict(snapshot.signals)
    diagnostics = _as_dict(snapshot.diagnostics)
    rejected = _as_dict(snapshot.rejected_outcomes)

    lines = [
        "Paper Review Snapshot",
        "=====================",
        f"ID:           {snapshot.id}",
        f"Review date:  {snapshot.review_date}",
        f"Review type:  {snapshot.review_type}",
        f"Status:       {snapshot.status}",
        f"Generated at: {snapshot.generated_at}",
        "",
    ]

    lines.extend(_counts_section(summary.get("counts", {})))
    performance = _as_dict(summary.get("performance"))
    lines.extend(_table_section("Performance Totals", performance.get("totals", {})))
    lines.extend(
        _rows_section(
            "By Strategy",
            performance.get("by_strategy", []),
            ["strategy_name", "matched_round_trips", "realized_pnl", "win_rate_percent"],
            limit=limit,
        )
    )
    lines.extend(
        _rows_section(
            "By Symbol",
            performance.get("by_symbol", []),
            ["symbol", "matched_round_trips", "realized_pnl", "win_rate_percent"],
            limit=limit,
        )
    )
    lines.extend(_signal_section(signals, limit=limit))
    lines.extend(_diagnostic_section(diagnostics, limit=limit))
    lines.extend(_rejected_section(rejected, limit=limit))
    return "\n".join(lines).rstrip() + "\n"


def _counts_section(counts: Any) -> list[str]:
    counts = _as_dict(counts)
    lines = ["Counts", "------"]
    if not counts:
        return lines + ["No count data.", ""]
    for key in sorted(counts):
        lines.append(f"{key}: {counts[key]}")
    return lines + [""]


def _table_section(title: str, values: Any) -> list[str]:
    values = _as_dict(values)
    lines = [title, "-" * len(title)]
    if not values:
        return lines + ["No data.", ""]
    for key in sorted(values):
        lines.append(f"{key}: {values[key]}")
    return lines + [""]


def _rows_section(
    title: str,
    rows: Any,
    columns: list[str],
    *,
    limit: int,
) -> list[str]:
    normalized = [row for row in _as_list(rows) if isinstance(row, dict)][:limit]
    lines = [title, "-" * len(title)]
    if not normalized:
        return lines + ["No rows.", ""]

    widths = {
        column: max(len(column), *[len(str(row.get(column, ""))) for row in normalized])
        for column in columns
    }
    header = "  ".join(column.ljust(widths[column]) for column in columns)
    lines.append(header)
    lines.append("  ".join("-" * widths[column] for column in columns))
    for row in normalized:
        lines.append(
            "  ".join(str(row.get(column, "")).ljust(widths[column]) for column in columns)
        )
    return lines + [""]


def _signal_section(signals: dict[str, Any], *, limit: int) -> list[str]:
    summary = _as_dict(signals.get("summary"))
    rows = []
    for row in _as_list(summary.get("by_scanner_type")):
        if not isinstance(row, dict):
            continue
        by_status = _as_dict(row.get("by_status"))
        rows.append(
            {
                **row,
                "submitted": by_status.get("submitted", 0),
                "created": by_status.get("created", 0),
            }
        )
    lines = _rows_section(
        "Signals By Scanner",
        rows,
        ["scanner_type", "signals_seen", "submitted", "preview_rejected"],
        limit=limit,
    )

    no_signal = _as_dict(signals.get("no_signal_summary"))
    reason_counts = _as_dict(no_signal.get("top_reasons"))
    lines.extend(["No-Signal Reasons", "-----------------"])
    if not reason_counts:
        lines.extend(["No rows.", ""])
        return lines
    for reason, count in _top_items(reason_counts, limit=limit):
        lines.append(f"{reason}: {count}")
    lines.append("")
    return lines


def _diagnostic_section(diagnostics: dict[str, Any], *, limit: int) -> list[str]:
    summary = _as_dict(diagnostics.get("summary"))
    reason_counts = _as_dict(summary.get("reason_counts"))
    lines = ["Option-Selection Diagnostics", "----------------------------"]
    if not reason_counts:
        return lines + ["No rows.", ""]
    for reason, count in _top_items(reason_counts, limit=limit):
        lines.append(f"{reason}: {count}")
    return lines + [""]


def _rejected_section(rejected: dict[str, Any], *, limit: int) -> list[str]:
    lines = _rows_section(
        "Rejected Preview Trade Comparisons",
        rejected.get("trade_comparison"),
        ["scanner_type", "symbol", "rejected_signals", "matched_round_trips"],
        limit=limit,
    )
    lines.extend(
        _rows_section(
            "Rejected Signal Shadow Outcomes",
            rejected.get("shadow_market_movement"),
            ["scanner_type", "symbol", "directional_outcome", "underlying_move_percent"],
            limit=limit,
        )
    )
    return lines


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _top_items(values: dict[str, Any], *, limit: int) -> Iterable[tuple[str, Any]]:
    return sorted(values.items(), key=lambda item: (-_numeric(item[1]), item[0]))[:limit]


def _numeric(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    main()
