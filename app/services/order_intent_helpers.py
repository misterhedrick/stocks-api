from __future__ import annotations

from datetime import datetime

from decimal import Decimal, ROUND_HALF_UP

from app.core.config import settings

from app.integrations.alpaca import AlpacaLatestOptionQuote

from app.schemas.order_intents import OrderIntentPreviewCreate

from app.schemas.options import OptionContractSelectionRead

from app.services.order_intent_types import OrderIntentPreviewError

def _build_quote_preview(
    latest_quote: AlpacaLatestOptionQuote,
    *,
    side: str,
    quantity: int,
    supplied_limit_price: Decimal | None,
) -> dict[str, object]:
    quote = latest_quote.quote
    bid_price = _usable_quote_price(quote.bid_price)
    ask_price = _usable_quote_price(quote.ask_price)
    midpoint = _midpoint(bid_price, ask_price)
    spread = ask_price - bid_price if bid_price is not None and ask_price is not None else None
    suggested_limit_price = supplied_limit_price or midpoint
    estimated_price = supplied_limit_price or _side_price(
        side,
        bid_price=bid_price,
        ask_price=ask_price,
        fallback=midpoint,
    )
    estimated_notional = (
        estimated_price * Decimal(quantity) * Decimal("100")
        if estimated_price is not None
        else None
    )

    return {
        "symbol": latest_quote.symbol,
        "bid_price": _decimal_to_string(bid_price),
        "bid_size": _decimal_to_string(quote.bid_size),
        "ask_price": _decimal_to_string(ask_price),
        "ask_size": _decimal_to_string(quote.ask_size),
        "midpoint": _decimal_to_string(midpoint),
        "spread": _decimal_to_string(spread),
        "suggested_limit_price": _decimal_to_string(suggested_limit_price),
        "estimated_price": _decimal_to_string(estimated_price),
        "estimated_notional": _decimal_to_string(estimated_notional),
        "contract_multiplier": "100",
        "quote_timestamp": quote.timestamp.isoformat()
        if quote.timestamp is not None
        else None,
        "raw_quote": _json_safe_value(latest_quote.raw_response),
    }

def _effective_max_estimated_notional(
    payload: OrderIntentPreviewCreate,
) -> Decimal | None:
    if payload.max_estimated_notional is not None:
        return payload.max_estimated_notional
    if (
        payload.contract_selection is not None
        and payload.contract_selection.max_estimated_notional is not None
    ):
        return payload.contract_selection.max_estimated_notional
    return settings.max_estimated_premium_per_order

def _effective_max_spread(payload: OrderIntentPreviewCreate) -> Decimal | None:
    if payload.max_spread is not None:
        return payload.max_spread
    if (
        payload.contract_selection is not None
        and payload.contract_selection.max_spread is not None
    ):
        return payload.contract_selection.max_spread
    return None

def _validate_preview_quote_constraints(
    quote_preview: dict[str, object],
    *,
    max_estimated_notional: Decimal | None,
    max_spread: Decimal | None,
    max_spread_percent: Decimal | None = None,
) -> None:
    estimated_notional = _decimal_from_preview(quote_preview.get("estimated_notional"))
    spread = _decimal_from_preview(quote_preview.get("spread"))

    if (
        max_estimated_notional is not None
        and estimated_notional is not None
        and estimated_notional > max_estimated_notional
    ):
        raise OrderIntentPreviewError(
            "Estimated notional "
            f"{estimated_notional} exceeds max {max_estimated_notional}"
        )
    if _spread_exceeds_limits(
        quote_preview,
        max_spread=max_spread,
        max_spread_percent=max_spread_percent,
    ):
        raise OrderIntentPreviewError(
            _spread_error_message(
                spread=spread,
                max_spread=max_spread,
                max_spread_percent=max_spread_percent,
            )
        )


def _spread_exceeds_limits(
    quote_preview: dict[str, object],
    *,
    max_spread: Decimal | None,
    max_spread_percent: Decimal | None = None,
) -> bool:
    spread = _decimal_from_preview(quote_preview.get("spread"))
    if spread is None:
        return False
    if max_spread is None and max_spread_percent is None:
        return False

    abs_ok = max_spread is None or spread <= max_spread
    effective_pct = _effective_max_spread_pct(max_spread_percent)
    pct_ok = True
    midpoint = _decimal_from_preview(quote_preview.get("midpoint"))
    if effective_pct is not None and midpoint is not None and midpoint > Decimal("0"):
        pct_ok = (spread / midpoint) <= effective_pct
    return not abs_ok and not pct_ok


def _effective_max_spread_pct(
    max_spread_percent: Decimal | None = None,
) -> Decimal | None:
    pct_candidates = [settings.options_max_spread_pct]
    if max_spread_percent is not None:
        pct_candidates.append(Decimal(str(max_spread_percent)) / Decimal("100"))
    return min(pct_candidates) if pct_candidates else None


def _spread_error_message(
    *,
    spread: Decimal | None,
    max_spread: Decimal | None,
    max_spread_percent: Decimal | None,
) -> str:
    effective_pct = _effective_max_spread_pct(max_spread_percent)
    return (
        f"Quote spread {spread} exceeds max {max_spread}"
        f" and pct max {effective_pct}"
    )

def _selection_preview(
    selection: OptionContractSelectionRead | None,
) -> dict[str, object] | None:
    if selection is None:
        return None
    return selection.model_dump(mode="json")

def _midpoint(
    bid_price: Decimal | None,
    ask_price: Decimal | None,
) -> Decimal | None:
    if bid_price is None or ask_price is None:
        return None
    return ((bid_price + ask_price) / Decimal("2")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

def _side_price(
    side: str,
    *,
    bid_price: Decimal | None,
    ask_price: Decimal | None,
    fallback: Decimal | None,
) -> Decimal | None:
    if side == "buy":
        return ask_price or fallback
    return bid_price or fallback

def _decimal_to_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)

def _usable_quote_price(value: Decimal | None) -> Decimal | None:
    if value is None or value <= Decimal("0"):
        return None
    return value

def _decimal_from_preview(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))

def _json_safe_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    return value
