from __future__ import annotations

import argparse
import json
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.models import Strategy
from app.db.session import SessionLocal
from app.integrations.alpaca import AlpacaMarketDataClient
from app.services.audit_logs import record_audit_log
from app.services.strategy_templates import build_moving_average_strategy_payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List and tune preview-first paper strategy configs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List strategy scanner summaries.")
    list_parser.add_argument("--active-only", action="store_true")

    seed_parser = subparsers.add_parser(
        "seed-moving-average",
        help="Create or update one preview-first moving-average paper strategy.",
    )
    seed_parser.add_argument("--name")
    seed_parser.add_argument("--symbol", default="SPY")
    seed_parser.add_argument("--option-type", choices=["call", "put"], default="call")
    seed_parser.add_argument(
        "--trigger",
        choices=["bullish_cross", "bearish_cross", "bullish_trend", "bearish_trend"],
        default="bullish_trend",
    )
    seed_parser.add_argument("--short-window", type=int, default=5)
    seed_parser.add_argument("--long-window", type=int, default=20)
    seed_parser.add_argument("--lookback-minutes", type=int, default=1440)
    seed_parser.add_argument("--timeframe", default="5Min")
    seed_parser.add_argument("--confidence", default="0.6200")
    seed_parser.add_argument(
        "--target-strike",
        help="Override target strike. Defaults to latest IEX midpoint rounded to whole dollars.",
    )
    seed_parser.add_argument(
        "--sample-price",
        help="Use this underlying price instead of Alpaca market data.",
    )
    seed_parser.add_argument("--dry-run", action="store_true")

    patch_parser = subparsers.add_parser(
        "patch-scanner",
        help="Merge a JSON object into an existing strategy scanner config.",
    )
    patch_parser.add_argument("--name", required=True)
    patch_parser.add_argument(
        "--scanner-json",
        required=True,
        help="JSON object to deep-merge into config.scanner.",
    )
    patch_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    with SessionLocal() as db:
        if args.command == "list":
            summaries = list_strategy_summaries(db, active_only=args.active_only)
            print(json.dumps(summaries, indent=2, sort_keys=True, default=str))
            return

        if args.command == "seed-moving-average":
            payload = moving_average_payload_from_args(args)
            if args.dry_run:
                print(json.dumps(payload, indent=2, sort_keys=True))
                return
            created = upsert_strategy(db, payload, source="strategy_tuning_script")
            db.commit()
            action = "created" if created else "updated"
            print(f"Moving-average strategy {action}: {payload['name']}")
            return

        if args.command == "patch-scanner":
            scanner_patch = _json_object(args.scanner_json)
            strategy = patch_strategy_scanner(
                db,
                name=args.name,
                scanner_patch=scanner_patch,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                print(json.dumps(_strategy_summary(strategy), indent=2, sort_keys=True, default=str))
                return
            db.commit()
            print(f"Patched scanner config for: {strategy.name}")


def list_strategy_summaries(
    db: Session,
    *,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    statement = select(Strategy).order_by(Strategy.name.asc())
    if active_only:
        statement = statement.where(Strategy.is_active == True)  # noqa: E712
    return [_strategy_summary(strategy) for strategy in db.scalars(statement)]


def moving_average_payload_from_args(args: argparse.Namespace) -> dict[str, Any]:
    symbol = args.symbol.strip().upper()
    target_strike = (
        Decimal(args.target_strike)
        if args.target_strike is not None
        else _whole_dollar(_price_for_symbol(symbol, sample_price=args.sample_price))
    )
    return build_moving_average_strategy_payload(
        symbol=symbol,
        target_strike=target_strike,
        name=args.name,
        option_type=args.option_type,
        trigger=args.trigger,
        short_window=args.short_window,
        long_window=args.long_window,
        lookback_minutes=args.lookback_minutes,
        timeframe=args.timeframe,
        confidence=args.confidence,
    )


def upsert_strategy(db: Session, payload: dict[str, Any], *, source: str) -> bool:
    existing = db.scalar(select(Strategy).where(Strategy.name == payload["name"]))
    created = existing is None
    strategy = Strategy(**payload) if created else existing
    if not created:
        strategy.description = payload["description"]
        strategy.is_active = payload["is_active"]
        strategy.config = payload["config"]

    try:
        db.add(strategy)
        db.flush()
        record_audit_log(
            db,
            event_type="strategy.created" if created else "strategy.updated",
            entity_type="strategy",
            entity_id=strategy.id,
            message=(
                "Strategy created by tuning script"
                if created
                else "Strategy updated by tuning script"
            ),
            payload={"source": source, "strategy": _strategy_audit_payload(strategy)},
        )
    except SQLAlchemyError:
        db.rollback()
        raise
    return created


def patch_strategy_scanner(
    db: Session,
    *,
    name: str,
    scanner_patch: dict[str, Any],
    dry_run: bool = False,
) -> Strategy:
    strategy = db.scalar(select(Strategy).where(Strategy.name == name))
    if strategy is None:
        raise RuntimeError(f"Strategy '{name}' was not found")

    config = deepcopy(strategy.config) if isinstance(strategy.config, dict) else {}
    scanner_config = config.get("scanner")
    if not isinstance(scanner_config, dict):
        scanner_config = {}
    config["scanner"] = _deep_merge(scanner_config, scanner_patch)

    if dry_run:
        return Strategy(
            id=strategy.id,
            name=strategy.name,
            description=strategy.description,
            is_active=strategy.is_active,
            config=config,
            created_at=strategy.created_at,
            updated_at=strategy.updated_at,
        )

    strategy.config = config
    try:
        db.add(strategy)
        db.flush()
        record_audit_log(
            db,
            event_type="strategy.updated",
            entity_type="strategy",
            entity_id=strategy.id,
            message="Strategy scanner patched by tuning script",
            payload={
                "source": "strategy_tuning_script",
                "scanner_patch": scanner_patch,
                "strategy": _strategy_audit_payload(strategy),
            },
        )
    except SQLAlchemyError:
        db.rollback()
        raise
    return strategy


def _strategy_summary(strategy: Strategy) -> dict[str, Any]:
    config = strategy.config if isinstance(strategy.config, dict) else {}
    scanner = config.get("scanner") if isinstance(config.get("scanner"), dict) else {}
    preview = scanner.get("preview") if isinstance(scanner.get("preview"), dict) else {}
    submit = scanner.get("submit") if isinstance(scanner.get("submit"), dict) else {}
    exit_config = scanner.get("exit") if isinstance(scanner.get("exit"), dict) else {}
    return {
        "id": str(strategy.id),
        "name": strategy.name,
        "is_active": strategy.is_active,
        "scanner_type": scanner.get("type"),
        "scanner_symbols": scanner.get("symbols", []),
        "preview_enabled": preview.get("enabled") is True,
        "submit_enabled": submit.get("enabled") is True,
        "exit_enabled": exit_config.get("enabled") is True,
        "scanner": scanner,
        "updated_at": strategy.updated_at,
    }


def _strategy_audit_payload(strategy: Strategy) -> dict[str, Any]:
    return {
        "name": strategy.name,
        "description": strategy.description,
        "is_active": strategy.is_active,
        "config": strategy.config,
    }


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _json_object(raw_json: str) -> dict[str, Any]:
    value = json.loads(raw_json)
    if not isinstance(value, dict):
        raise RuntimeError("scanner JSON must be an object")
    return value


def _price_for_symbol(symbol: str, *, sample_price: str | None) -> Decimal:
    if sample_price is not None:
        return Decimal(sample_price)

    client = AlpacaMarketDataClient.from_settings()
    latest_quote = client.get_latest_stock_quotes([symbol], feed="iex").get(symbol)
    if latest_quote is None:
        raise RuntimeError(f"No latest stock quote returned for {symbol}")

    bid_price = latest_quote.quote.bid_price
    ask_price = latest_quote.quote.ask_price
    if bid_price is not None and ask_price is not None:
        return (bid_price + ask_price) / Decimal("2")
    if ask_price is not None:
        return ask_price
    if bid_price is not None:
        return bid_price
    raise RuntimeError(f"Latest stock quote for {symbol} had no bid or ask")


def _whole_dollar(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


if __name__ == "__main__":
    main()
