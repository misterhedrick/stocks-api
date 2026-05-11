from __future__ import annotations

import logging
from collections import Counter
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.core.config import settings
from app.integrations.alpaca import (
    AlpacaLatestOptionQuote,
    AlpacaOptionContract,
    AlpacaOptionQuote,
)
from app.schemas.options import (
    OptionContractRead,
    OptionContractSelectionCreate,
)
from app.services.option_contract_types import CandidateRejection

logger = logging.getLogger("app.services.option_contracts")

def _selection_failure_diagnostics(
    payload: OptionContractSelectionCreate,
    *,
    candidates_seen: int,
    candidates_evaluated: int,
    candidate_limit: int,
    rejected: list[CandidateRejection],
) -> dict[str, Any]:
    reason_counts = Counter(item.reason_code for item in rejected)
    diagnostic_limit = _diagnostic_candidate_limit()
    return {
        "underlying_symbol": payload.underlying_symbol,
        "option_type": payload.option_type,
        "side": payload.side,
        "scanner_type": None,
        "preview_profile": payload.preview_profile,
        "candidates_seen": candidates_seen,
        "candidates_evaluated": candidates_evaluated,
        "candidate_limit": candidate_limit,
        "reason_counts": dict(sorted(reason_counts.items())),
        "rejections": [
            {
                "symbol": item.symbol,
                "reason_code": item.reason_code,
                "reason": item.reason,
                "details": item.details,
            }
            for item in rejected
        ],
        "top_rejected_candidates": [
            _candidate_diagnostic(item)
            for item in rejected[:diagnostic_limit]
        ],
        "diagnostic_candidate_limit": diagnostic_limit,
        "limits": {
            "max_estimated_notional": str(payload.max_estimated_notional)
            if payload.max_estimated_notional is not None
            else None,
            "max_spread": str(payload.max_spread) if payload.max_spread is not None else None,
            "max_spread_pct": str(settings.options_max_spread_pct),
            "min_open_interest": str(payload.min_open_interest)
            if payload.min_open_interest is not None
            else None,
        },
    }


def _log_selection_failure(diagnostics: dict[str, Any]) -> None:
    symbol = diagnostics.get("underlying_symbol", "?")
    option_type = diagnostics.get("option_type", "?")
    seen = diagnostics.get("candidates_seen", 0)
    evaluated = diagnostics.get("candidates_evaluated", 0)
    candidate_limit = diagnostics.get("candidate_limit", "?")
    reason_counts = diagnostics.get("reason_counts") or {}
    summary = ", ".join(f"{k}×{v}" for k, v in sorted(reason_counts.items()))
    summary = (
        f"candidates_evaluated={evaluated}, candidate_limit={candidate_limit}, "
        + (summary or "none")
    )
    logger.info(
        "Option contract selection failed: %s %s — %d candidate(s) checked, rejections: [%s]",
        symbol,
        option_type,
        seen,
        summary,
        extra={"option_selection_diagnostics": diagnostics},
    )
    top_candidates = diagnostics.get("top_rejected_candidates") or []
    if top_candidates:
        logger.info(
            "Option contract selection top rejected candidates: %s",
            top_candidates,
            extra={"option_selection_rejected_candidates": top_candidates},
        )


def _quoted_contract_sort_key(
    item: tuple[int, AlpacaOptionContract, AlpacaLatestOptionQuote, dict[str, object]],
) -> tuple:
    candidate_rank, contract, _, quote_context = item
    spread = _decimal_from_context(quote_context.get("spread")) or Decimal("999999")
    midpoint = _decimal_from_context(quote_context.get("midpoint")) or Decimal("0")
    estimated_notional = (
        _decimal_from_context(quote_context.get("estimated_notional"))
        or Decimal("999999999")
    )
    spread_percent = (
        (spread / midpoint) * Decimal("100") if midpoint > Decimal("0") else Decimal("999999")
    )
    bid_size = _decimal_from_context(quote_context.get("bid_size")) or Decimal("0")
    ask_size = _decimal_from_context(quote_context.get("ask_size")) or Decimal("0")
    open_interest = contract.open_interest or Decimal("0")
    return (
        spread_percent,
        spread,
        estimated_notional,
        -(bid_size + ask_size),
        -open_interest,
        candidate_rank,
        contract.expiration_date,
        contract.strike_price,
        contract.symbol,
    )


def _contract_sort_key(
    contract: AlpacaOptionContract,
    *,
    target_strike: Decimal | None,
    target_dte: int | None = None,
    today: date | None = None,
) -> tuple:
    if target_dte is not None and today is not None:
        contract_dte = (contract.expiration_date - today).days
        dte_score = abs(contract_dte - target_dte)
    else:
        dte_score = 0

    open_interest = contract.open_interest
    min_open_interest = Decimal(settings.options_min_open_interest)
    oi_score = 0
    if open_interest is None:
        oi_score = 2
    elif open_interest < min_open_interest:
        oi_score = 1

    open_interest_rank = -(open_interest or Decimal("0"))

    if target_strike is None:
        return (oi_score, dte_score, open_interest_rank, contract.strike_price, contract.symbol)

    return (
        oi_score,
        abs(contract.strike_price - target_strike),
        dte_score,
        open_interest_rank,
        contract.strike_price,
        contract.symbol,
    )


def _selection_reason(
    contract: AlpacaOptionContract,
    target_strike: Decimal | None,
) -> str:
    if target_strike is None:
        return (
            "Selected the contract nearest to the target DTE among active tradable contracts"
        )
    return (
        "Selected the contract nearest to the target DTE with strike closest to "
        f"{target_strike}; selected {contract.strike_price}"
    )


def _diagnostic_candidate_limit() -> int:
    try:
        return max(int(settings.options_diagnostic_candidate_limit), 0)
    except (TypeError, ValueError):
        return 10


def _option_candidate_limit(payload_limit: int | None = None) -> int:
    try:
        return max(int(settings.options_max_candidates), 1)
    except (TypeError, ValueError):
        return 100


def _candidate_diagnostic(rejection: CandidateRejection) -> dict[str, Any]:
    details = rejection.details or {}
    return {
        "signal_id": details.get("signal_id"),
        "underlying_symbol": details.get("underlying_symbol"),
        "strategy": details.get("strategy"),
        "option_symbol": rejection.symbol,
        "expiration": details.get("expiration"),
        "strike": details.get("strike"),
        "call_put": details.get("call_put"),
        "bid": details.get("bid"),
        "ask": details.get("ask"),
        "mid": details.get("mid"),
        "spread": details.get("spread"),
        "spread_pct": details.get("spread_pct"),
        "open_interest": details.get("open_interest"),
        "volume": details.get("volume"),
        "estimated_notional": details.get("estimated_notional"),
        "underlying_price": details.get("underlying_price"),
        "distance_from_underlying": details.get("distance_from_underlying"),
        "moneyness": details.get("moneyness"),
        "rejection_reasons": [rejection.reason_code],
    }


def _contract_diagnostic_fields(
    contract: AlpacaOptionContract,
    *,
    payload: OptionContractSelectionCreate | None = None,
) -> dict[str, Any]:
    underlying_price = payload.underlying_price if payload is not None else None
    distance = None
    moneyness = None
    if underlying_price is not None:
        distance_value = contract.strike_price - underlying_price
        distance = str(distance_value)
        if underlying_price > Decimal("0"):
            moneyness = str(distance_value / underlying_price)
    return {
        "underlying_symbol": contract.underlying_symbol,
        "expiration": contract.expiration_date.isoformat(),
        "strike": str(contract.strike_price),
        "call_put": contract.type,
        "open_interest": str(contract.open_interest) if contract.open_interest is not None else None,
        "volume": str(getattr(contract, "volume", None))
        if getattr(contract, "volume", None) is not None
        else None,
        "underlying_price": str(underlying_price) if underlying_price is not None else None,
        "distance_from_underlying": distance,
        "moneyness": moneyness,
    }


def _quote_diagnostic_fields(quote_context: dict[str, object]) -> dict[str, Any]:
    return {
        "bid": quote_context.get("bid_price"),
        "ask": quote_context.get("ask_price"),
        "mid": quote_context.get("midpoint"),
        "spread": quote_context.get("spread"),
        "spread_pct": _spread_pct_string(quote_context),
        "estimated_notional": quote_context.get("estimated_notional"),
    }


def _spread_pct_string(quote_context: dict[str, object]) -> str | None:
    spread = _decimal_from_context(quote_context.get("spread"))
    midpoint = _decimal_from_context(quote_context.get("midpoint"))
    if spread is None or midpoint is None or midpoint <= Decimal("0"):
        return None
    return str(spread / midpoint)


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
        "side": side,
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
