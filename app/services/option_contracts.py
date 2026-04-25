from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from app.integrations.alpaca import (
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
    contracts_page = trading.list_option_contracts(
        underlying_symbol=payload.underlying_symbol,
        option_type=payload.option_type,
        expiration_date=payload.expiration_date,
        expiration_date_gte=payload.expiration_date_gte,
        expiration_date_lte=payload.expiration_date_lte,
        limit=payload.limit,
    )
    candidates = [
        contract
        for contract in contracts_page.contracts
        if contract.status == "active" and contract.tradable
    ]
    if not candidates:
        raise OptionContractNotFoundError(
            f"No active tradable {payload.option_type} contracts found for {payload.underlying_symbol}"
        )

    target_strike = payload.target_strike or payload.underlying_price
    selected = _select_contract(candidates, target_strike=target_strike)

    market_data = market_data_client or AlpacaMarketDataClient.from_settings()
    latest_quote = market_data.get_latest_option_quote(
        selected.symbol,
        feed=payload.data_feed,
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


def _select_contract(
    contracts: list[AlpacaOptionContract],
    *,
    target_strike: Decimal | None,
) -> AlpacaOptionContract:
    if target_strike is None:
        return sorted(
            contracts,
            key=lambda contract: (
                contract.expiration_date,
                contract.strike_price,
                contract.symbol,
            ),
        )[0]

    return sorted(
        contracts,
        key=lambda contract: (
            contract.expiration_date,
            abs(contract.strike_price - target_strike),
            contract.strike_price,
            contract.symbol,
        ),
    )[0]


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
    bid_price = quote.bid_price
    ask_price = quote.ask_price
    midpoint = _midpoint(bid_price, ask_price)
    spread = ask_price - bid_price if bid_price is not None and ask_price is not None else None
    estimated_price = _side_price(
        side,
        bid_price=bid_price,
        ask_price=ask_price,
        fallback=midpoint,
    )

    return {
        "bid_price": _decimal_to_string(bid_price),
        "bid_size": _decimal_to_string(quote.bid_size),
        "ask_price": _decimal_to_string(ask_price),
        "ask_size": _decimal_to_string(quote.ask_size),
        "midpoint": _decimal_to_string(midpoint),
        "spread": _decimal_to_string(spread),
        "estimated_price": _decimal_to_string(estimated_price),
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
