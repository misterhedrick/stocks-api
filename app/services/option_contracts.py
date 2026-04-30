from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from app.integrations.alpaca import (
    AlpacaLatestOptionQuote,
    AlpacaMarketDataClient,
    AlpacaOptionContract,
    AlpacaOptionQuote,
    AlpacaTradingClient,
)
from app.schemas.options import (
    OptionContractRead,
    OptionContractSelectionCreate,
    OptionContractSelectionRead,
)


class OptionContractSelectionError(RuntimeError):
    pass


class OptionContractNotFoundError(LookupError):
    pass


def select_option_contract(
    payload: OptionContractSelectionCreate,
    *,
    trading_client: AlpacaTradingClient | None = None,
    market_data_client: AlpacaMarketDataClient | None = None,
) -> OptionContractSelectionRead:
    trading = trading_client or AlpacaTradingClient.from_settings()
    expiration_date_gte, expiration_date_lte = _expiration_range(payload)
    contracts_page = trading.list_option_contracts(
        underlying_symbol=payload.underlying_symbol,
        option_type=payload.option_type,
        expiration_date=payload.expiration_date,
        expiration_date_gte=expiration_date_gte,
        expiration_date_lte=expiration_date_lte,
        limit=payload.limit,
    )
    target_strike = payload.target_strike or payload.underlying_price
    candidates = sorted(
        [
            contract
            for contract in contracts_page.contracts
            if contract.status == "active" and contract.tradable
        ],
        key=lambda contract: _contract_sort_key(contract, target_strike=target_strike),
    )
    if not candidates:
        raise OptionContractNotFoundError(
            f"No active tradable {payload.option_type} contracts found for {payload.underlying_symbol}"
        )

    market_data = market_data_client or AlpacaMarketDataClient.from_settings()
    selected, latest_quote = _select_quoted_contract(
        candidates,
        market_data_client=market_data,
        feed=payload.data_feed,
        side=payload.side,
        max_estimated_notional=payload.max_estimated_notional,
        max_spread=payload.max_spread,
    )

    return OptionContractSelectionRead(
        selected_contract=_contract_read(selected),
        quote=_build_quote_context(
            latest_quote.quote,
            side=payload.side,
            raw_quote=latest_quote.raw_response,
        ),
        selection_reason=_selection_reason(selected, target_strike),
        candidates_seen=len(candidates),
        selected_at=datetime.now(timezone.utc),
    )


def _select_quoted_contract(
    candidates: list[AlpacaOptionContract],
    *,
    market_data_client: AlpacaMarketDataClient,
    feed: str,
    side: str,
    max_estimated_notional: Decimal | None,
    max_spread: Decimal | None,
) -> tuple[AlpacaOptionContract, AlpacaLatestOptionQuote]:
    rejected_reasons: list[str] = []
    for contract in candidates:
        latest_quote = market_data_client.get_latest_option_quote(
            contract.symbol,
            feed=feed,
        )
        quote_context = _build_quote_context(
            latest_quote.quote,
            side=side,
            raw_quote=latest_quote.raw_response,
        )
        rejection_reason = _quote_rejection_reason(
            contract,
            quote_context,
            max_estimated_notional=max_estimated_notional,
            max_spread=max_spread,
        )
        if rejection_reason is None:
            return contract, latest_quote
        rejected_reasons.append(rejection_reason)

    raise OptionContractNotFoundError(
        "No active tradable option contract matched the quote constraints: "
        + "; ".join(rejected_reasons[:5])
    )


def _expiration_range(
    payload: OptionContractSelectionCreate,
    *,
    today: date | None = None,
) -> tuple[date | None, date | None]:
    if (
        payload.min_days_to_expiration is None
        and payload.max_days_to_expiration is None
    ):
        return payload.expiration_date_gte, payload.expiration_date_lte

    base_date = today or datetime.now(ZoneInfo("America/New_York")).date()
    expiration_date_gte = (
        base_date + timedelta(days=payload.min_days_to_expiration)
        if payload.min_days_to_expiration is not None
        else None
    )
    expiration_date_lte = (
        base_date + timedelta(days=payload.max_days_to_expiration)
        if payload.max_days_to_expiration is not None
        else None
    )
    return expiration_date_gte, expiration_date_lte


def _quote_rejection_reason(
    contract: AlpacaOptionContract,
    quote_context: dict[str, object],
    *,
    max_estimated_notional: Decimal | None,
    max_spread: Decimal | None,
) -> str | None:
    estimated_notional = _decimal_from_context(quote_context.get("estimated_notional"))
    spread = _decimal_from_context(quote_context.get("spread"))

    if (
        max_estimated_notional is not None
        and estimated_notional is not None
        and estimated_notional > max_estimated_notional
    ):
        return (
            f"{contract.symbol} estimated notional {estimated_notional} "
            f"exceeds max {max_estimated_notional}"
        )
    if max_spread is not None and spread is not None and spread > max_spread:
        return f"{contract.symbol} spread {spread} exceeds max {max_spread}"
    return None


def _contract_sort_key(
    contract: AlpacaOptionContract,
    *,
    target_strike: Decimal | None,
) -> tuple:
    if target_strike is None:
        return (
            contract.expiration_date,
            contract.strike_price,
            contract.symbol,
        )

    return (
        contract.expiration_date,
        abs(contract.strike_price - target_strike),
        contract.strike_price,
        contract.symbol,
    )


def _selection_reason(
    contract: AlpacaOptionContract,
    target_strike: Decimal | None,
) -> str:
    if target_strike is None:
        return (
            "Selected the earliest expiration and lowest strike among active tradable contracts"
        )
    return (
        "Selected the earliest expiration with strike closest to "
        f"{target_strike}; selected {contract.strike_price}"
    )


def _contract_read(contract: AlpacaOptionContract) -> OptionContractRead:
    return OptionContractRead(
        id=contract.id,
        symbol=contract.symbol,
        name=contract.name,
        status=contract.status,
        tradable=contract.tradable,
        expiration_date=contract.expiration_date,
        root_symbol=contract.root_symbol,
        underlying_symbol=contract.underlying_symbol,
        option_type=contract.type,
        style=contract.style,
        strike_price=contract.strike_price,
        size=contract.size,
        open_interest=contract.open_interest,
        open_interest_date=contract.open_interest_date,
        close_price=contract.close_price,
        close_price_date=contract.close_price_date,
    )


def _build_quote_context(
    quote: AlpacaOptionQuote,
    *,
    side: str,
    raw_quote: dict,
) -> dict[str, object]:
    bid_price = _usable_quote_price(quote.bid_price)
    ask_price = _usable_quote_price(quote.ask_price)
    midpoint = _midpoint(bid_price, ask_price)
    spread = ask_price - bid_price if bid_price is not None and ask_price is not None else None
    estimated_price = _side_price(
        side,
        bid_price=bid_price,
        ask_price=ask_price,
        fallback=midpoint,
    )
    estimated_notional = estimated_price * Decimal("100") if estimated_price is not None else None

    return {
        "bid_price": _decimal_to_string(bid_price),
        "bid_size": _decimal_to_string(quote.bid_size),
        "ask_price": _decimal_to_string(ask_price),
        "ask_size": _decimal_to_string(quote.ask_size),
        "midpoint": _decimal_to_string(midpoint),
        "spread": _decimal_to_string(spread),
        "estimated_price": _decimal_to_string(estimated_price),
        "estimated_notional": _decimal_to_string(estimated_notional),
        "contract_multiplier": "100",
        "quote_timestamp": quote.timestamp.isoformat()
        if quote.timestamp is not None
        else None,
        "raw_quote": raw_quote,
    }


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


def _decimal_from_context(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
