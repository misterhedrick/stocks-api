from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import OrderIntent, PositionSnapshot, Strategy

from app.integrations.alpaca import AlpacaMarketDataClient

from app.services.audit_logs import record_audit_log

from app.services.order_intents import _build_quote_preview, _spread_exceeds_limits

from app.services.position_exit_rules import (
    _exit_limit_price,
    _latest_quote_for_position,
    _optional_int,
    _optional_positive_decimal,
    _string_config,
    _underlying_from_position,
)

def _create_exit_order_intent(
    db: Session,
    position: PositionSnapshot,
    strategy: Strategy | None,
    exit_config: dict[str, Any],
    *,
    trigger_reason: str,
    market_data_client: AlpacaMarketDataClient,
    max_quantity: Decimal | None = None,
) -> OrderIntent:
    max_contracts_per_exit = _optional_int(exit_config.get("max_contracts_per_exit"))
    if max_contracts_per_exit is not None and max_contracts_per_exit <= 0:
        raise ValueError("scanner.exit.max_contracts_per_exit must be greater than 0")
    available_quantity = min(position.quantity, max_quantity or position.quantity)
    quantity = min(
        int(available_quantity),
        max_contracts_per_exit or int(position.quantity),
    )
    if quantity <= 0:
        raise ValueError("exit quantity must be greater than 0")

    data_feed = _string_config(exit_config, "data_feed", default="indicative")
    latest_quote = _latest_quote_for_position(
        market_data_client,
        position.symbol,
        data_feed=data_feed,
    )
    quote_preview = _build_quote_preview(
        latest_quote,
        side="sell",
        quantity=quantity,
        supplied_limit_price=None,
    )

    max_spread = _optional_positive_decimal(exit_config.get("max_spread"))
    max_spread_percent = _optional_positive_decimal(exit_config.get("max_spread_percent"))
    if _spread_exceeds_limits(
        quote_preview,
        max_spread=max_spread,
        max_spread_percent=max_spread_percent,
    ):
        raise ValueError("quote spread exceeds scanner.exit spread limits")

    order_type = _string_config(exit_config, "order_type", default="limit")
    if order_type not in {"limit", "market"}:
        raise ValueError("scanner.exit.order_type must be limit or market")

    limit_price = None
    if order_type == "limit":
        limit_price = _exit_limit_price(quote_preview, exit_config)
        if limit_price is None:
            raise ValueError("unable to derive exit limit price from quote")

    order_intent = OrderIntent(
        strategy_id=strategy.id if strategy is not None else None,
        signal_id=None,
        underlying_symbol=_underlying_from_position(position),
        option_symbol=position.symbol,
        side="sell",
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        time_in_force=_string_config(exit_config, "time_in_force", default="day"),
        status="previewed",
        rationale=f"Exit {position.symbol}: {trigger_reason}",
        preview={
            "source": "position_exit_evaluator",
            "data_feed": data_feed,
            "trigger_reason": trigger_reason,
            "position": {
                "symbol": position.symbol,
                "quantity": str(position.quantity),
                "market_value": str(position.market_value)
                if position.market_value is not None
                else None,
                "cost_basis": str(position.cost_basis)
                if position.cost_basis is not None
                else None,
                "unrealized_pl": str(position.unrealized_pl)
                if position.unrealized_pl is not None
                else None,
                "captured_at": position.captured_at.isoformat(),
            },
            "position_ownership": {
                "strategy_id": str(strategy.id) if strategy is not None else None,
                "strategy_name": strategy.name if strategy is not None else None,
            },
            "quote": quote_preview,
        },
    )

    db.add(order_intent)
    db.flush()
    record_audit_log(
        db,
        event_type="order_intent.exit_previewed",
        entity_type="order_intent",
        entity_id=order_intent.id,
        message="Exit order intent preview generated from current position",
        payload={
            "strategy_id": str(strategy.id) if strategy is not None else None,
            "option_symbol": order_intent.option_symbol,
            "side": order_intent.side,
            "quantity": order_intent.quantity,
            "order_type": order_intent.order_type,
            "limit_price": str(order_intent.limit_price)
            if order_intent.limit_price is not None
            else None,
            "trigger_reason": trigger_reason,
        },
    )
    db.commit()
    db.refresh(order_intent)
    return order_intent
