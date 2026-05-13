from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.models import JobRun
from app.db.session import SessionLocal
from app.services.automation_status import get_automation_status
from app.services.learning_report import build_learning_report
from app.services.performance_review import get_paper_performance_review
from app.services.position_exits import get_position_management_statuses


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print a compact market-day operating dashboard."
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    with SessionLocal() as db:
        dashboard = build_dashboard(db, limit=args.limit)

    if args.json:
        print(json.dumps(dashboard, indent=2, sort_keys=True, default=str))
        return

    _print_text_dashboard(dashboard)


def build_dashboard(db, *, limit: int = 100) -> dict[str, Any]:
    automation = get_automation_status(db)
    positions = get_position_management_statuses(db, limit=limit)
    performance = get_paper_performance_review(db, limit=max(limit, 500))
    learning = build_learning_report(db, limit=max(limit, 500))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": automation.operational_summary.get("effective_mode"),
        "switches": automation.switches.model_dump(),
        "readiness": automation.operational_summary.get("paper_trading_readiness", {}),
        "latest_jobs": _latest_jobs(db, limit=10),
        "exit_attention": _exit_attention(db, limit=10),
        "positions": {
            "seen": len(positions),
            "by_action": _count_by(
                [_field(position, "recommended_action") for position in positions]
            ),
            "items": [
                {
                    "symbol": _field(position, "symbol"),
                    "quantity": _field(position, "quantity"),
                    "unrealized_pl": _field(position, "unrealized_pl"),
                    "action": _field(position, "recommended_action"),
                    "reason": _field(position, "reason"),
                }
                for position in positions[:25]
            ],
        },
        "performance": {
            "fills_seen": performance.fills_seen,
            "matched_round_trips": performance.matched_round_trips,
            "totals": performance.totals,
            "top_strategies": performance.by_strategy[:10],
            "top_symbols": performance.by_symbol[:10],
            "open_positions": performance.open_positions[:25],
        },
        "learning": {
            "totals": learning.totals,
            "top_non_trade_reasons": learning.non_trade_reasons[:10],
            "signals_by_strategy": learning.signals_by_strategy[:10],
            "intents_by_strategy": learning.intents_by_strategy[:10],
            "job_failures": learning.job_failures[:10],
        },
    }


def _latest_jobs(db, *, limit: int) -> list[dict[str, Any]]:
    statement = select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)
    jobs = []
    for job_run in db.scalars(statement):
        details = job_run.details if isinstance(job_run.details, dict) else {}
        jobs.append(
            {
                "job_run_id": str(job_run.id),
                "job_name": job_run.job_name,
                "status": job_run.status,
                "started_at": job_run.started_at.isoformat(),
                "finished_at": job_run.finished_at.isoformat()
                if job_run.finished_at is not None
                else None,
                "error": job_run.error,
                "timings": details.get("timings") if isinstance(details, dict) else None,
                "diagnostics": details.get("diagnostics")
                if isinstance(details, dict)
                else None,
            }
        )
    return jobs


def _exit_attention(db, *, limit: int) -> list[dict[str, Any]]:
    statement = (
        select(JobRun)
        .where(JobRun.job_name == "market_cycle")
        .order_by(JobRun.started_at.desc())
        .limit(limit * 3)
    )
    alerts = []
    for job_run in db.scalars(statement):
        details = job_run.details if isinstance(job_run.details, dict) else {}
        exits = details.get("exits") if isinstance(details, dict) else None
        if not isinstance(exits, dict) or exits.get("status") == "disabled":
            continue
        errors = exits.get("errors", []) or []
        reasons = exits.get("no_exit_reasons", []) or []
        if not errors and not reasons and exits.get("status") != "skipped":
            continue
        alerts.append(
            {
                "job_run_id": str(job_run.id),
                "started_at": job_run.started_at.isoformat(),
                "status": exits.get("status"),
                "positions_seen": exits.get("positions_seen"),
                "positions_evaluated": exits.get("positions_evaluated"),
                "exits_created": exits.get("exits_created"),
                "errors": errors[:5],
                "no_exit_reasons": reasons[:5],
            }
        )
        if len(alerts) >= limit:
            break
    return alerts


def _count_by(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _field(item: object, name: str) -> object:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _print_text_dashboard(dashboard: dict[str, Any]) -> None:
    print(f"Market Day Dashboard @ {dashboard['generated_at']}")
    print(f"Mode: {dashboard['mode']}")
    readiness = dashboard["readiness"]
    print(f"Ready now: {readiness.get('ready_to_auto_submit_now')}")
    if readiness.get("blockers"):
        print("Blockers:")
        for blocker in readiness["blockers"]:
            print(f"  - {blocker}")
    if readiness.get("warnings"):
        print("Warnings:")
        for warning in readiness["warnings"]:
            print(f"  - {warning}")

    print("\nLatest jobs:")
    for job in dashboard["latest_jobs"][:5]:
        total = (job.get("timings") or {}).get("total_seconds")
        print(f"  - {job['job_name']} {job['status']} total={total} id={job['job_run_id']}")

    print("\nPositions:")
    print(f"  Seen: {dashboard['positions']['seen']}")
    print(f"  By action: {dashboard['positions']['by_action']}")

    print("\nPerformance:")
    performance = dashboard["performance"]
    print(f"  Fills: {performance['fills_seen']}")
    print(f"  Round trips: {performance['matched_round_trips']}")
    print(f"  Totals: {performance['totals']}")

    print("\nTop non-trade reasons:")
    for reason in dashboard["learning"]["top_non_trade_reasons"][:5]:
        print(f"  - {reason['source']} {reason['strategy_name']}: {reason['count']} {reason['reason']}")

    print("\nExit attention:")
    for alert in dashboard["exit_attention"][:5]:
        print(f"  - {alert['started_at']} status={alert['status']} exits={alert['exits_created']}")


if __name__ == "__main__":
    main()
