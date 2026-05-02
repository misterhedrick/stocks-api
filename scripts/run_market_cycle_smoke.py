from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.session import SessionLocal
from app.services.market_cycle import run_market_cycle


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one market-cycle smoke test against configured services."
    )
    parser.add_argument("--scan-limit", type=int, default=10)
    parser.add_argument("--order-limit", type=int, default=25)
    parser.add_argument("--fill-page-size", type=int, default=25)
    args = parser.parse_args()

    with SessionLocal() as db:
        result = run_market_cycle(
            db,
            scan_limit=args.scan_limit,
            order_limit=args.order_limit,
            fill_page_size=args.fill_page_size,
        )

    print("market_cycle_smoke_ok", f"job_run_id={result.job_run.id}")
    print(
        "switches",
        f"scan={result.scan_enabled}",
        f"preview={result.preview_enabled}",
        f"submit={result.submit_enabled}",
        f"reconcile={result.reconcile_enabled}",
    )
    _print_json_summary("scan", result.scan)
    _print_json_summary("preview", result.preview)
    _print_json_summary("submit", result.submit)
    _print_json_summary("reconcile", result.reconcile)


def _print_json_summary(label: str, payload: object) -> None:
    print(
        label,
        json.dumps(
            payload,
            default=str,
            ensure_ascii=True,
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    main()
