from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
import sys

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
from app.services.strategy_templates import (
    build_preview_first_strategy_payloads,
    required_template_symbols,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed preview-first paper strategies into the configured database."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print strategy payloads without writing to the database.",
    )
    parser.add_argument(
        "--deactivate-others",
        action="store_true",
        help="Mark existing strategies outside this seed set inactive.",
    )
    parser.add_argument(
        "--sample-prices",
        action="store_true",
        help="Use static sample prices instead of fetching latest Alpaca quotes.",
    )
    args = parser.parse_args()

    prices = (
        _sample_prices()
        if args.sample_prices
        else _latest_midpoint_prices(required_template_symbols())
    )
    payloads = build_preview_first_strategy_payloads(prices=prices)

    if args.dry_run:
        for payload in payloads:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return

    with SessionLocal() as db:
        created, updated, deactivated = seed_strategies(
            db,
            payloads,
            deactivate_others=args.deactivate_others,
        )
        db.commit()

    print(
        "Seeded paper strategies "
        f"(created={created}, updated={updated}, deactivated={deactivated})"
    )


def seed_strategies(
    db: Session,
    payloads: list[dict],
    *,
    deactivate_others: bool = False,
) -> tuple[int, int, int]:
    created = 0
    updated = 0
    deactivated = 0
    seed_names = {str(payload["name"]) for payload in payloads}

    try:
        for payload in payloads:
            existing = db.scalar(
                select(Strategy).where(Strategy.name == payload["name"])
            )
            if existing is None:
                strategy = Strategy(**payload)
                db.add(strategy)
                db.flush()
                record_audit_log(
                    db,
                    event_type="strategy.created",
                    entity_type="strategy",
                    entity_id=strategy.id,
                    message="Strategy created by paper seed script",
                    payload=_audit_payload(strategy),
                )
                created += 1
                continue

            existing.description = payload["description"]
            existing.is_active = payload["is_active"]
            existing.config = payload["config"]
            db.add(existing)
            record_audit_log(
                db,
                event_type="strategy.updated",
                entity_type="strategy",
                entity_id=existing.id,
                message="Strategy updated by paper seed script",
                payload=_audit_payload(existing),
            )
            updated += 1

        if deactivate_others:
            other_strategies = list(
                db.scalars(select(Strategy).where(Strategy.name.not_in(seed_names)))
            )
            for strategy in other_strategies:
                if not strategy.is_active:
                    continue
                strategy.is_active = False
                db.add(strategy)
                record_audit_log(
                    db,
                    event_type="strategy.updated",
                    entity_type="strategy",
                    entity_id=strategy.id,
                    message="Strategy deactivated by paper seed script",
                    payload=_audit_payload(strategy),
                )
                deactivated += 1
    except SQLAlchemyError:
        db.rollback()
        raise

    return created, updated, deactivated


def _latest_midpoint_prices(symbols: list[str]) -> dict[str, Decimal]:
    client = AlpacaMarketDataClient.from_settings()
    quotes = client.get_latest_stock_quotes(symbols, feed="iex")
    prices: dict[str, Decimal] = {}
    for symbol in symbols:
        latest_quote = quotes.get(symbol)
        if latest_quote is None:
            raise RuntimeError(f"No latest stock quote returned for {symbol}")
        bid_price = latest_quote.quote.bid_price
        ask_price = latest_quote.quote.ask_price
        if bid_price is not None and ask_price is not None:
            prices[symbol] = (bid_price + ask_price) / Decimal("2")
        elif ask_price is not None:
            prices[symbol] = ask_price
        elif bid_price is not None:
            prices[symbol] = bid_price
        else:
            raise RuntimeError(f"Latest stock quote for {symbol} had no bid or ask")
    return prices


def _sample_prices() -> dict[str, Decimal]:
    return {
        "SPY": Decimal("500.00"),
        "QQQ": Decimal("430.00"),
    }


def _audit_payload(strategy: Strategy) -> dict:
    return {
        "name": strategy.name,
        "description": strategy.description,
        "is_active": strategy.is_active,
        "config": strategy.config,
    }


if __name__ == "__main__":
    main()
