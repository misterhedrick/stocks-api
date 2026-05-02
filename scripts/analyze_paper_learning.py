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
from app.services.learning_report import build_learning_report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize paper trades and non-trades for strategy tuning."
    )
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    with SessionLocal() as db:
        report = build_learning_report(db, limit=args.limit)

    print(json.dumps(asdict(report), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
