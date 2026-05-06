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

from app.core.config import settings
from app.db.models import Strategy
from app.db.session import SessionLocal
from app.integrations.alpaca import AlpacaMarketDataClient
from app.services.audit_logs import record_audit_log
from app.services.strategy_templates import (
    build_breakout_price_threshold_strategy_payload,
    build_macd_crossover_strategy_payload,
    build_mean_reversion_strategy_payload,
    build_momentum_rate_of_change_strategy_payload,
    build_moving_average_strategy_payload,
    build_rsi_reversal_strategy_payload,
    build_support_resistance_strategy_payload,
    build_trend_confirmation_strategy_payload,
    build_volatility_squeeze_strategy_payload,
    build_volume_confirmed_breakout_strategy_payload,
)


DEFAULT_UNIVERSE = (
    "SPY",
    "QQQ",
    "NVDA",
    "AAPL",
    "MSFT",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed and enable a broader liquid paper-trading ticker universe."
    )
    parser.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        help="Ticker to seed. May be passed more than once. Defaults to a liquid universe.",
    )
    parser.add_argument("--sample-price", action="append", default=[])
    parser.add_argument(
        "--max-notional-per-order",
        default=_money_string(settings.paper_strategy_max_estimated_notional),
    )
    parser.add_argument("--max-spread", default="0.20")
    parser.add_argument("--max-spread-percent", default="20")
    parser.add_argument(
        "--min-open-interest",
        type=int,
        default=settings.paper_strategy_min_open_interest,
    )
    parser.add_argument("--min-quote-size", type=int, default=1)
    parser.add_argument("--max-orders-per-cycle", type=int, default=100)
    parser.add_argument("--max-orders-per-day", type=int, default=500)
    parser.add_argument("--max-open-contracts-per-symbol", type=int, default=100)
    parser.add_argument("--max-open-contracts-per-strategy", type=int, default=100)
    parser.add_argument("--trade-window-start", default="09:45")
    parser.add_argument("--trade-window-end", default="15:30")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    symbols = _clean_symbols(args.symbols or DEFAULT_UNIVERSE)
    sample_prices = _sample_prices(args.sample_price)
    prices = _prices_for_symbols(symbols, sample_prices=sample_prices)

    payloads = _strategy_payloads(
        symbols,
        prices=prices,
        max_notional_per_order=_money_string(args.max_notional_per_order),
        max_spread=str(args.max_spread),
        max_spread_percent=str(args.max_spread_percent),
        min_open_interest=args.min_open_interest,
        min_quote_size=args.min_quote_size,
        max_orders_per_cycle=args.max_orders_per_cycle,
        max_orders_per_day=args.max_orders_per_day,
        max_open_contracts_per_symbol=args.max_open_contracts_per_symbol,
        max_open_contracts_per_strategy=args.max_open_contracts_per_strategy,
        trade_window_start=args.trade_window_start,
        trade_window_end=args.trade_window_end,
    )

    if args.dry_run:
        print(json.dumps(payloads, indent=2, sort_keys=True))
        return

    with SessionLocal() as db:
        results = []
        for payload in payloads:
            created = _upsert_strategy(db, payload)
            results.append(
                {
                    "name": payload["name"],
                    "symbol": payload["config"]["scanner"]["symbols"][0],
                    "created": created,
                    "preview_profile": payload["config"]["scanner"]["preview"].get(
                        "preview_profile"
                    ),
                    "submit_enabled": payload["config"]["scanner"]["submit"]["enabled"],
                    "max_notional_per_order": payload["config"]["scanner"]["submit"][
                        "max_notional_per_order"
                    ],
                }
            )
        db.commit()

    print(
        json.dumps(
            {
                "symbols": symbols,
                "strategies_seeded": len(results),
                "results": results,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _strategy_payloads(
    symbols: list[str],
    *,
    prices: dict[str, Decimal],
    max_notional_per_order: str,
    max_spread: str,
    max_spread_percent: str,
    min_open_interest: int,
    min_quote_size: int,
    max_orders_per_cycle: int,
    max_orders_per_day: int,
    max_open_contracts_per_symbol: int | None,
    max_open_contracts_per_strategy: int,
    trade_window_start: str,
    trade_window_end: str,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for symbol in symbols:
        target_strike = _whole_dollar(prices[symbol])
        payloads.extend(
            [
                build_moving_average_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="call",
                    trigger="bullish_trend",
                ),
                build_moving_average_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="put",
                    trigger="bearish_trend",
                ),
                build_trend_confirmation_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="call",
                    direction="bullish",
                ),
                build_trend_confirmation_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="put",
                    direction="bearish",
                ),
                build_momentum_rate_of_change_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="call",
                    direction="bullish",
                ),
                build_momentum_rate_of_change_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="put",
                    direction="bearish",
                ),
                build_rsi_reversal_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="call",
                    direction="bullish",
                ),
                build_rsi_reversal_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="put",
                    direction="bearish",
                ),
                build_macd_crossover_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="call",
                    direction="bullish",
                ),
                build_macd_crossover_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="put",
                    direction="bearish",
                ),
                build_mean_reversion_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="call",
                    direction="bullish",
                ),
                build_mean_reversion_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="put",
                    direction="bearish",
                ),
                build_breakout_price_threshold_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="call",
                    direction="bullish",
                ),
                build_breakout_price_threshold_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="put",
                    direction="bearish",
                ),
                build_volume_confirmed_breakout_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="call",
                    direction="bullish",
                ),
                build_volume_confirmed_breakout_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="put",
                    direction="bearish",
                ),
                build_volatility_squeeze_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="call",
                    direction="bullish",
                ),
                build_volatility_squeeze_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="put",
                    direction="bearish",
                ),
                build_support_resistance_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="call",
                    direction="bullish",
                ),
                build_support_resistance_strategy_payload(
                    symbol=symbol,
                    target_strike=target_strike,
                    option_type="put",
                    direction="bearish",
                ),
            ]
        )

    submit_config = _submit_config(
        max_notional_per_order=max_notional_per_order,
        max_orders_per_cycle=max_orders_per_cycle,
        max_orders_per_day=max_orders_per_day,
        max_open_contracts_per_symbol=max_open_contracts_per_symbol,
        max_open_contracts_per_strategy=max_open_contracts_per_strategy,
        trade_window_start=trade_window_start,
        trade_window_end=trade_window_end,
    )
    for payload in payloads:
        scanner = payload["config"]["scanner"]
        scanner_type = scanner.get("type")
        scanner["preview"]["preview_profile"] = _preview_profile_for_type(scanner_type)
        scanner["preview"]["max_estimated_notional"] = max_notional_per_order
        scanner["preview"]["max_spread"] = max_spread
        scanner["preview"]["max_spread_percent"] = max_spread_percent
        scanner["preview"]["min_open_interest"] = min_open_interest
        scanner["preview"]["min_quote_size"] = min_quote_size
        scanner["submit"] = deepcopy(submit_config)
    return payloads


def _submit_config(
    *,
    max_notional_per_order: str,
    max_orders_per_cycle: int,
    max_orders_per_day: int,
    max_open_contracts_per_symbol: int | None,
    max_open_contracts_per_strategy: int,
    trade_window_start: str,
    trade_window_end: str,
) -> dict[str, Any]:
    return {
        "enabled": True,
        "max_orders_per_cycle": max_orders_per_cycle,
        "max_contracts_per_order": 1,
        "max_contracts_per_cycle": max_orders_per_cycle,
        "max_notional_per_order": max_notional_per_order,
        "max_open_contracts_per_symbol": max_open_contracts_per_symbol,
        "max_open_contracts_per_strategy": max_open_contracts_per_strategy,
        "max_orders_per_trading_day": max_orders_per_day,
        "trading_day_timezone": "America/New_York",
        "trade_windows": [
            {
                "timezone": "America/New_York",
                "start": trade_window_start,
                "end": trade_window_end,
            }
        ],
        "allowed_sides": ["buy"],
    }


def _upsert_strategy(db: Session, payload: dict[str, Any]) -> bool:
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
                "Strategy created by paper universe seed"
                if created
                else "Strategy updated by paper universe seed"
            ),
            payload={
                "source": "seed_paper_trade_universe",
                "name": strategy.name,
                "config": strategy.config,
            },
        )
    except SQLAlchemyError:
        db.rollback()
        raise
    return created


def _prices_for_symbols(
    symbols: list[str],
    *,
    sample_prices: dict[str, Decimal],
) -> dict[str, Decimal]:
    missing_symbols = [symbol for symbol in symbols if symbol not in sample_prices]
    prices = dict(sample_prices)
    if missing_symbols:
        client = AlpacaMarketDataClient.from_settings()
        quotes = client.get_latest_stock_quotes(missing_symbols, feed="iex")
        for symbol in missing_symbols:
            quote = quotes.get(symbol)
            if quote is None:
                raise RuntimeError(f"No latest stock quote returned for {symbol}")
            prices[symbol] = _price_from_quote(symbol, quote)
    return prices


def _price_from_quote(symbol: str, latest_quote: object) -> Decimal:
    quote = latest_quote.quote
    bid_price = _usable_quote_price(quote.bid_price)
    ask_price = _usable_quote_price(quote.ask_price)
    if bid_price is not None and ask_price is not None:
        return (bid_price + ask_price) / Decimal("2")
    if ask_price is not None:
        return ask_price
    if bid_price is not None:
        return bid_price
    raise RuntimeError(f"Latest stock quote for {symbol} had no bid or ask")


def _usable_quote_price(value: Decimal | None) -> Decimal | None:
    if value is None or value <= Decimal("0"):
        return None
    return value


def _sample_prices(raw_values: list[str]) -> dict[str, Decimal]:
    prices: dict[str, Decimal] = {}
    for raw_value in raw_values:
        if "=" not in raw_value:
            raise RuntimeError("--sample-price must use SYMBOL=PRICE")
        symbol, price = raw_value.split("=", 1)
        prices[symbol.strip().upper()] = Decimal(price.strip())
    return prices


def _clean_symbols(raw_symbols: list[str] | tuple[str, ...]) -> list[str]:
    symbols = []
    for raw_symbol in raw_symbols:
        symbol = raw_symbol.strip().upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _money_string(value: str | int | Decimal) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _whole_dollar(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _preview_profile_for_type(scanner_type: object) -> str:
    if isinstance(scanner_type, str) and scanner_type.strip():
        return scanner_type.strip()
    return "default"


if __name__ == "__main__":
    main()
