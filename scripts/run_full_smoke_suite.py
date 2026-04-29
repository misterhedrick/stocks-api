from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the full local smoke suite against configured services."
    )
    parser.add_argument(
        "--skip-paper-submit",
        action="store_true",
        help="Skip the broker-touching paper submit/cancel smoke.",
    )
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="Skip preview-first paper strategy seeding.",
    )
    parser.add_argument(
        "--skip-market-cycle",
        action="store_true",
        help="Skip the market-cycle smoke.",
    )
    args = parser.parse_args()

    _run("preflight", ["scripts/smoke_preflight.py"])

    if not args.skip_seed:
        _run("seed_paper_strategies", ["scripts/seed_paper_strategies.py"])

    if not args.skip_market_cycle:
        _run("market_cycle", ["scripts/run_market_cycle_smoke.py"])

    if not args.skip_paper_submit:
        _run("paper_submit_cancel", ["scripts/run_paper_submit_smoke.py"])

    _run(
        "unit_tests",
        [
            "-c",
            (
                "import os, subprocess, sys; "
                "os.environ['ADMIN_API_TOKEN']='change-me'; "
                "os.environ['MARKET_CYCLE_PREVIEW_ENABLED']='false'; "
                "os.environ['MARKET_CYCLE_SUBMIT_ENABLED']='false'; "
                "os.environ['TRADING_AUTOMATION_ENABLED']='false'; "
                "os.environ['MAX_AUTO_ORDERS_PER_DAY']='3'; "
                "raise SystemExit(subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests']))"
            ),
        ],
    )

    print("full_smoke_suite_ok")


def _run(label: str, args: list[str]) -> None:
    print(f"=== {label} ===", flush=True)
    completed = subprocess.run(
        [sys.executable, *args],
        cwd=ROOT_DIR,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
