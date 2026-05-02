from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.session import SessionLocal
from app.services.market_cycle import run_market_cycle


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the reconcile-first exit-only market cycle locally."
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--order-limit", type=int, default=100)
    parser.add_argument("--fill-page-size", type=int, default=100)
    parser.add_argument("--phase-timeout-seconds", type=int, default=None)
    args = parser.parse_args()

    kwargs = {}
    if args.phase_timeout_seconds is not None:
        kwargs["phase_timeout_seconds"] = args.phase_timeout_seconds

    with SessionLocal() as db:
        result = run_market_cycle(
            db,
            scan_limit=args.limit,
            order_limit=args.order_limit,
            fill_page_size=args.fill_page_size,
            scan_enabled_override=False,
            preview_enabled_override=False,
            news_enabled_override=False,
            exit_enabled_override=True,
            reconcile_before_exit=True,
            **kwargs,
        )

    print(json.dumps(asdict(result), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
