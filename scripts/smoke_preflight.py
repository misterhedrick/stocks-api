from __future__ import annotations

import argparse
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import settings
from app.db.session import check_database_connection, check_database_schema
from app.integrations.alpaca import AlpacaMarketDataClient, AlpacaTradingClient
from app.services.broker_reconciliation import reconcile_broker_state
from app.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check local settings, database, Alpaca reads, and reconciliation."
    )
    parser.parse_args()

    print("smoke_preflight_started")
    _check_settings()
    _check_database()
    _check_alpaca_market_data()
    _check_alpaca_trading_reads()
    print("smoke_preflight_ok")


def _check_settings() -> None:
    database = urlparse(settings.sqlalchemy_database_url)
    print(
        "settings_ok",
        f"paper={settings.alpaca_paper}",
        f"db_host={database.hostname}",
        f"db_name={(database.path or '').lstrip('/')}",
        f"alpaca_key_set={bool(settings.alpaca_api_key)}",
        f"alpaca_secret_set={bool(settings.alpaca_api_secret)}",
    )


def _check_database() -> None:
    check_database_connection()
    check_database_schema()
    print("database_ok")


def _check_alpaca_market_data() -> None:
    market_data = AlpacaMarketDataClient.from_settings()
    quotes = market_data.get_latest_stock_quotes(["SPY", "QQQ"], feed="iex")
    if "SPY" not in quotes or "QQQ" not in quotes:
        raise RuntimeError("Expected latest stock quotes for SPY and QQQ")
    print("alpaca_market_data_ok", f"stock_quotes={len(quotes)}")


def _check_alpaca_trading_reads() -> None:
    trading = AlpacaTradingClient.from_settings()
    contracts = trading.list_option_contracts(
        underlying_symbol="SPY",
        option_type="call",
        limit=10,
    )
    orders = trading.list_orders(limit=5)
    positions = trading.list_positions()
    print(
        "alpaca_trading_reads_ok",
        f"contracts_seen={len(contracts.contracts)}",
        f"orders_seen={len(orders)}",
        f"positions_seen={len(positions)}",
    )

    with SessionLocal() as db:
        result = reconcile_broker_state(db, order_limit=10, fill_page_size=10)
    print(
        "reconciliation_ok",
        f"job_run_id={result.job_run.id}",
        f"orders_seen={result.orders_seen}",
        f"fills_seen={result.fills_seen}",
        f"positions_seen={result.positions_seen}",
    )


if __name__ == "__main__":
    main()
