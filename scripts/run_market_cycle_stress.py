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
        description="Run a no-submit market-cycle stress test with phase timings."
    )
    parser.add_argument("--scan-limit", type=int, default=70)
    parser.add_argument("--order-limit", type=int, default=25)
    parser.add_argument("--fill-page-size", type=int, default=25)
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--no-reconcile", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as db:
        result = run_market_cycle(
            db,
            scan_limit=args.scan_limit,
            order_limit=args.order_limit,
            fill_page_size=args.fill_page_size,
            preview_enabled_override=not args.no_preview,
            reconcile_enabled_override=not args.no_reconcile,
            exit_enabled_override=False,
            news_enabled_override=False,
            submit_enabled_override=False,
        )

    summary = {
        "job_run_id": str(result.job_run.id),
        "scan": result.scan,
        "preview": result.preview,
        "submit": result.submit,
        "reconcile": result.reconcile,
        "timings": result.timings,
    }
    print(json.dumps(summary, default=str, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
