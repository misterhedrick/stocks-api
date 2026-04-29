from __future__ import annotations

import argparse
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import sys

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.models import Signal, Strategy
from app.db.session import SessionLocal
from app.integrations.alpaca import (
    AlpacaMarketDataClient,
    AlpacaOrderRejectedError,
    AlpacaTradingClient,
)
from app.schemas.order_intents import OrderIntentPreviewCreate
from app.services.audit_logs import record_audit_log
from app.services.broker_reconciliation import reconcile_broker_state
from app.services.order_intents import (
    cancel_order_intent,
    OrderIntentStateError,
    preview_order_intent_from_signal,
    submit_order_intent,
)


STRATEGY_NAME = "Paper E2E Submit Smoke"
UNDERLYING = "SPY"
MAX_ASK = Decimal("1.00")
MAX_SPREAD = Decimal("0.50")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a one-contract Alpaca paper submit smoke test."
    )
    parser.add_argument(
        "--skip-cancel",
        action="store_true",
        help="Leave the accepted paper order open instead of requesting cancel.",
    )
    parser.add_argument(
        "--skip-reconcile",
        action="store_true",
        help="Skip broker reconciliation after submit/cancel.",
    )
    args = parser.parse_args()

    trading_client = AlpacaTradingClient.from_settings()
    market_data_client = AlpacaMarketDataClient.from_settings()
    option_symbol = _find_cheap_option_symbol(trading_client, market_data_client)

    with SessionLocal() as db:
        strategy = _get_or_create_strategy(db)
        signal = Signal(
            strategy_id=strategy.id,
            symbol=UNDERLYING,
            underlying_symbol=UNDERLYING,
            signal_type="paper_submit_smoke",
            direction="bullish",
            confidence=Decimal("0.5000"),
            rationale="Paper submit smoke test",
            market_context={
                "source": "scripts/run_paper_submit_smoke.py",
                "selected_option_symbol": option_symbol,
            },
            status="new",
        )
        db.add(signal)
        db.flush()
        record_audit_log(
            db,
            event_type="signal.created",
            entity_type="signal",
            entity_id=signal.id,
            message="Signal created by paper submit smoke test",
            payload={
                "strategy_id": str(strategy.id),
                "symbol": signal.symbol,
                "option_symbol": option_symbol,
            },
        )
        db.commit()
        db.refresh(signal)

        order_intent = preview_order_intent_from_signal(
            db,
            OrderIntentPreviewCreate(
                signal_id=signal.id,
                option_symbol=option_symbol,
                side="buy",
                quantity=1,
                order_type="limit",
                time_in_force="day",
                max_estimated_notional=Decimal("150.00"),
                max_spread=MAX_SPREAD,
                rationale="Paper submit smoke test",
            ),
            market_data_client=market_data_client,
        )
        print(
            "preview_created",
            f"order_intent_id={order_intent.id}",
            f"symbol={order_intent.option_symbol}",
            f"limit_price={order_intent.limit_price}",
        )

        try:
            submitted_intent, broker_order = submit_order_intent(
                db,
                order_intent.id,
                trading_client=trading_client,
            )
        except AlpacaOrderRejectedError as exc:
            print(
                "submit_rejected",
                f"order_intent_id={order_intent.id}",
                f"status_code={exc.status_code}",
                f"reason={exc.detail}",
            )
            return

        print(
            "submit_accepted",
            f"order_intent_id={submitted_intent.id}",
            f"broker_order_id={broker_order.id}",
            f"alpaca_order_id={broker_order.alpaca_order_id}",
            f"status={submitted_intent.status}",
        )

        if not args.skip_reconcile:
            _print_reconciliation(db, "post_submit_reconciliation")

        if not args.skip_cancel:
            try:
                canceled_intent, canceled_broker = cancel_order_intent(
                    db,
                    submitted_intent.id,
                    trading_client=trading_client,
                )
                print(
                    "cancel_requested",
                    f"order_intent_id={canceled_intent.id}",
                    f"broker_order_id={canceled_broker.id}",
                    f"status={canceled_intent.status}",
                )
            except OrderIntentStateError as exc:
                if exc.current_status != "filled":
                    raise
                print(
                    "cancel_skipped_terminal",
                    f"order_intent_id={submitted_intent.id}",
                    f"status={exc.current_status}",
                )
            if not args.skip_reconcile:
                _print_reconciliation(db, "post_cancel_reconciliation")


def _print_reconciliation(db, label: str) -> None:
    result = reconcile_broker_state(db, order_limit=25, fill_page_size=25)
    print(
        label,
        f"job_run_id={result.job_run.id}",
        f"orders_seen={result.orders_seen}",
        f"orders_updated={result.orders_updated}",
        f"fills_seen={result.fills_seen}",
        f"positions_seen={result.positions_seen}",
    )


def _get_or_create_strategy(db) -> Strategy:
    strategy = db.scalar(select(Strategy).where(Strategy.name == STRATEGY_NAME))
    if strategy is not None:
        return strategy

    strategy = Strategy(
        name=STRATEGY_NAME,
        description="One-contract paper smoke test for the submit path.",
        is_active=False,
        config={
            "purpose": "paper_submit_smoke",
            "submit": {
                "quantity": 1,
                "max_estimated_notional": "150.00",
                "max_spread": str(MAX_SPREAD),
            },
        },
    )
    db.add(strategy)
    db.flush()
    record_audit_log(
        db,
        event_type="strategy.created",
        entity_type="strategy",
        entity_id=strategy.id,
        message="Strategy created by paper submit smoke test",
        payload={
            "name": strategy.name,
            "description": strategy.description,
            "is_active": strategy.is_active,
            "config": strategy.config,
        },
    )
    db.commit()
    db.refresh(strategy)
    return strategy


def _find_cheap_option_symbol(
    trading_client: AlpacaTradingClient,
    market_data_client: AlpacaMarketDataClient,
) -> str:
    today = date.today()
    contracts_page = trading_client.list_option_contracts(
        underlying_symbol=UNDERLYING,
        option_type="call",
        expiration_date_gte=today + timedelta(days=1),
        expiration_date_lte=today + timedelta(days=45),
        limit=500,
    )
    candidates = sorted(
        [
            contract
            for contract in contracts_page.contracts
            if contract.status == "active" and contract.tradable
        ],
        key=lambda contract: (contract.expiration_date, -contract.strike_price),
    )
    rejection_reasons: list[str] = []
    for contract in candidates:
        latest_quote = market_data_client.get_latest_option_quote(
            contract.symbol,
            feed="indicative",
        )
        quote = latest_quote.quote
        if quote.ask_price is None or quote.ask_price <= 0:
            rejection_reasons.append(f"{contract.symbol}: no usable ask")
            continue
        spread = (
            quote.ask_price - quote.bid_price
            if quote.bid_price is not None
            else Decimal("0")
        )
        if quote.ask_price <= MAX_ASK and spread <= MAX_SPREAD:
            print(
                "contract_selected",
                f"symbol={contract.symbol}",
                f"expiration={contract.expiration_date}",
                f"strike={contract.strike_price}",
                f"ask={quote.ask_price}",
                f"spread={spread}",
            )
            return contract.symbol
        rejection_reasons.append(
            f"{contract.symbol}: ask={quote.ask_price} spread={spread}"
        )

    raise RuntimeError(
        "No cheap option contract found for paper smoke test: "
        + "; ".join(rejection_reasons[:10])
    )


if __name__ == "__main__":
    main()
