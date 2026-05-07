from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
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
from app.services.preview_profiles import resolve_preview_profile_limits

logger = logging.getLogger(__name__)


class OptionContractSelectionError(RuntimeError):
    pass


class OptionContractNotFoundError(LookupError):
    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or {}


@dataclass(slots=True)
class CandidateRejection:
    symbol: str | None
    reason_code: str
    reason: str
    details: dict[str, Any]


def select_option_contract(
    payload: OptionContractSelectionCreate,
    *,
    trading_client: AlpacaTradingClient | None = None,
    market_data_client: AlpacaMarketDataClient | None = None,
) -> OptionContractSelectionRead:
    trading = trading_client or AlpacaTradingClient.from_settings()
    today = datetime.now(ZoneInfo("America/New_York")).date()
    expiration_date_gte, expiration_date_lte = _expiration_range(payload, today=today)
    contracts_page = trading.list_option_contracts(
        underlying_symbol=payload.underlying_symbol,
        option_type=payload.option_type,
        expiration_date=payload.expiration_date,
        expiration_date_gte=expiration_date_gte,
        expiration_date_lte=expiration_date_lte,
        limit=payload.limit,
    )
    target_strike = payload.target_strike or payload.underlying_price
    prefiltered_rejections: list[CandidateRejection] = []
    available_contracts: list[AlpacaOptionContract] = []
    for contract in contracts_page.contracts:
        availability_rejection = _contract_availability_rejection(contract)
        if availability_rejection is not None:
            prefiltered_rejections.append(availability_rejection)
        else:
            available_contracts.append(contract)

    target_dte = settings.options_target_dte
    candidates = sorted(
        available_contracts,
        key=lambda contract: _contract_sort_key(
            contract,
            target_strike=target_strike,
            target_dte=target_dte,
            today=today,
        ),
    )
    candidates = candidates[: settings.options_max_candidates]

    if not candidates:
        reason = "no_expiration_strike_match" if not contracts_page.contracts else "not_tradable"
        rejections = prefiltered_rejections or [
            CandidateRejection(
                symbol=None,
                reason_code=reason,
                reason=(
                    f"No {payload.option_type} contracts matched expiration/strike filters "
                    f"for {payload.underlying_symbol}"
                ),
                details={
                    "underlying_symbol": payload.underlying_symbol,
                    "option_type": payload.option_type,
                    "expiration_date": payload.expiration_date.isoformat()
                    if payload.expiration_date is not None
                    else None,
                    "expiration_date_gte": expiration_date_gte.isoformat()
                    if expiration_date_gte is not None
                    else None,
                    "expiration_date_lte": expiration_date_lte.isoformat()
                    if expiration_date_lte is not None
                    else None,
                    "target_strike": str(target_strike) if target_strike is not None else None,
                },
            )
        ]
        diagnostics = _selection_failure_diagnostics(
            payload,
            candidates_seen=len(contracts_page.contracts),
            rejected=rejections,
        )
        _log_selection_failure(diagnostics)
        raise OptionContractNotFoundError(
            f"No active tradable {payload.option_type} contracts found for {payload.underlying_symbol}",
            diagnostics=diagnostics,
        )

    limits = resolve_preview_profile_limits(
        payload.preview_profile,
        max_estimated_notional=payload.max_estimated_notional,
        max_spread=payload.max_spread,
        max_spread_percent=payload.max_spread_percent,
        min_open_interest=payload.min_open_interest,
    )

    allow_missing_oi = _missing_oi_allowlist()
    market_data = market_data_client or AlpacaMarketDataClient.from_settings()
    selected, latest_quote = _select_quoted_contract(
        candidates,
        market_data_client=market_data,
        feed=payload.data_feed,
        side=payload.side,
        max_estimated_notional=limits.max_estimated_notional,
        max_spread=limits.max_spread,
        max_spread_percent=limits.max_spread_percent,
        max_spread_pct=settings.options_max_spread_pct,
        min_open_interest=Decimal(limits.min_open_interest)
        if limits.min_open_interest is not None
        else None,
        min_quote_size=payload.min_quote_size,
        allow_missing_oi_symbols=allow_missing_oi,
        initial_rejections=prefiltered_rejections,
        payload=payload,
        candidates_seen=len(contracts_page.contracts),
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
    max_spread_percent: Decimal | None,
    max_spread_pct: Decimal | None,
    min_open_interest: Decimal | None,
    min_quote_size: Decimal | None,
    allow_missing_oi_symbols: frozenset[str],
    initial_rejections: list[CandidateRejection],
    payload: OptionContractSelectionCreate,
    candidates_seen: int,
) -> tuple[AlpacaOptionContract, AlpacaLatestOptionQuote]:
    rejected: list[CandidateRejection] = list(initial_rejections)
    accepted: list[
        tuple[int, AlpacaOptionContract, AlpacaLatestOptionQuote, dict[str, object]]
    ] = []
    for index, contract in enumerate(candidates):
        try:
            latest_quote = market_data_client.get_latest_option_quote(
                contract.symbol,
                feed=feed,
            )
        except Exception as exc:
            rejected.append(
                CandidateRejection(
                    symbol=contract.symbol,
                    reason_code="quote_unavailable",
                    reason=f"{contract.symbol} quote unavailable: {exc}",
                    details={"error_type": exc.__class__.__name__},
                )
            )
            continue
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
            max_spread_percent=max_spread_percent,
            max_spread_pct=max_spread_pct,
            min_open_interest=min_open_interest,
            min_quote_size=min_quote_size,
            allow_missing_oi_symbols=allow_missing_oi_symbols,
        )
        if rejection_reason is None:
            accepted.append((index, contract, latest_quote, quote_context))
            continue
        rejected.append(rejection_reason)

    if accepted:
        _, selected, latest_quote, _ = sorted(accepted, key=_quoted_contract_sort_key)[0]
        return selected, latest_quote

    diagnostics = _selection_failure_diagnostics(
        payload,
        candidates_seen=candidates_seen,
        rejected=rejected,
    )
    _log_selection_failure(diagnostics)
    raise OptionContractNotFoundError(
        "No option contract matched quote constraints for "
        + payload.underlying_symbol
        + ": "
        + ", ".join(
            f"{code}×{count}"
            for code, count in sorted(
                Counter(r.reason_code for r in rejected).items()
            )
        ),
        diagnostics=diagnostics,
    )


def _contract_availability_rejection(
    contract: AlpacaOptionContract,
) -> CandidateRejection | None:
    if contract.status != "active" or not contract.tradable:
        return CandidateRejection(
            symbol=contract.symbol,
            reason_code="not_tradable",
            reason=f"{contract.symbol} is not active/tradable",
            details={
                "status": contract.status,
                "tradable": contract.tradable,
                "expiration_date": contract.expiration_date.isoformat(),
                "strike_price": str(contract.strike_price),
            },
        )
    return None


def _expiration_range(
    payload: OptionContractSelectionCreate,
    *,
    today: date | None = None,
) -> tuple[date | None, date | None]:
    has_relative = (
        payload.min_days_to_expiration is not None
        or payload.max_days_to_expiration is not None
    )
    has_absolute = (
        payload.expiration_date_gte is not None
        or payload.expiration_date_lte is not None
    )

    if not has_relative and not has_absolute:
        # No explicit filters: apply default DTE window from settings.
        base_date = today or datetime.now(ZoneInfo("America/New_York")).date()
        return (
            base_date + timedelta(days=settings.options_min_dte),
            base_date + timedelta(days=settings.options_max_dte),
        )

    if has_absolute and not has_relative:
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


def _missing_oi_allowlist() -> frozenset[str]:
    raw = settings.options_allow_missing_oi_symbols
    return frozenset(s.strip().upper() for s in raw.split(",") if s.strip())


def _quote_rejection_reason(
    contract: AlpacaOptionContract,
    quote_context: dict[str, object],
    *,
    max_estimated_notional: Decimal | None,
    max_spread: Decimal | None,
    max_spread_percent: Decimal | None,
    max_spread_pct: Decimal | None,
    min_open_interest: Decimal | None,
    min_quote_size: Decimal | None,
    allow_missing_oi_symbols: frozenset[str],
) -> CandidateRejection | None:
    underlying = (contract.underlying_symbol or "").upper()
    allow_missing_oi = underlying in allow_missing_oi_symbols

    if min_open_interest is not None and contract.open_interest is None:
        if not allow_missing_oi:
            return CandidateRejection(
                symbol=contract.symbol,
                reason_code="missing_open_interest",
                reason=f"{contract.symbol} missing open interest",
                details={"min_open_interest": str(min_open_interest)},
            )
        # Allowlisted symbol: skip missing-OI rejection; quote quality still checked below.
    elif min_open_interest is not None and contract.open_interest is not None:
        if contract.open_interest < min_open_interest:
            return CandidateRejection(
                symbol=contract.symbol,
                reason_code="low_open_interest",
                reason=(
                    f"{contract.symbol} open interest {contract.open_interest} "
                    f"is below min {min_open_interest}"
                ),
                details={
                    "open_interest": str(contract.open_interest),
                    "min_open_interest": str(min_open_interest),
                },
            )

    bid_price = _decimal_from_context(quote_context.get("bid_price"))
    ask_price = _decimal_from_context(quote_context.get("ask_price"))
    if bid_price is None or ask_price is None:
        raw_quote = quote_context.get("raw_quote")
        reason_code = "missing_quote" if not raw_quote else "no_usable_two_sided_quote"
        return CandidateRejection(
            symbol=contract.symbol,
            reason_code=reason_code,
            reason=f"{contract.symbol} had no usable two-sided quote",
            details={"bid_price": str(bid_price), "ask_price": str(ask_price)},
        )

    bid_size = _decimal_from_context(quote_context.get("bid_size"))
    ask_size = _decimal_from_context(quote_context.get("ask_size"))
    if min_quote_size is not None:
        side_size = ask_size if quote_context.get("side") == "buy" else bid_size
        if side_size is None or side_size < min_quote_size:
            return CandidateRejection(
                symbol=contract.symbol,
                reason_code="quote_size_too_low",
                reason=f"{contract.symbol} quote size {side_size} is below min {min_quote_size}",
                details={"quote_size": str(side_size), "min_quote_size": str(min_quote_size)},
            )

    estimated_notional = _decimal_from_context(quote_context.get("estimated_notional"))
    spread = _decimal_from_context(quote_context.get("spread"))
    midpoint = _decimal_from_context(quote_context.get("midpoint"))

    if (
        max_estimated_notional is not None
        and estimated_notional is not None
        and estimated_notional > max_estimated_notional
    ):
        return CandidateRejection(
            symbol=contract.symbol,
            reason_code="estimated_notional_above_max",
            reason=(
                f"{contract.symbol} estimated notional {estimated_notional} "
                f"exceeds max {max_estimated_notional}"
            ),
            details={
                "estimated_notional": str(estimated_notional),
                "max_estimated_notional": str(max_estimated_notional),
            },
        )

    # Spread check: OR logic — accept if absolute spread is acceptable OR relative spread is
    # acceptable.  Only reject when both thresholds are exceeded.
    if spread is not None and (max_spread is not None or max_spread_pct is not None or max_spread_percent is not None):
        abs_ok = max_spread is None or spread <= max_spread

        spread_pct: Decimal | None = None
        if midpoint is not None and midpoint > Decimal("0"):
            spread_pct = spread / midpoint

        # Determine effective relative-spread threshold (fraction, e.g. 0.15).
        # max_spread_pct (fraction) takes precedence; fall back to max_spread_percent/100.
        pct_candidates = []
        if max_spread_pct is not None:
            pct_candidates.append(max_spread_pct)
        if max_spread_percent is not None:
            pct_candidates.append(max_spread_percent / Decimal("100"))
        effective_pct = min(pct_candidates) if pct_candidates else None

        pct_ok = (
            effective_pct is None
            or spread_pct is None
            or spread_pct <= effective_pct
        )

        if not abs_ok and not pct_ok:
            pct_display = (
                f"{float(spread_pct) * 100:.1f}%"
                if spread_pct is not None
                else "unknown%"
            )
            return CandidateRejection(
                symbol=contract.symbol,
                reason_code="spread_too_wide",
                reason=(
                    f"{contract.symbol} spread {spread} ({pct_display}) exceeds "
                    f"abs max {max_spread} and pct max {effective_pct}"
                ),
                details={
                    "spread": str(spread),
                    "spread_pct": str(spread_pct) if spread_pct is not None else None,
                    "max_spread": str(max_spread) if max_spread is not None else None,
                    "effective_max_spread_pct": str(effective_pct) if effective_pct is not None else None,
                },
            )

    return None


def _selection_failure_diagnostics(
    payload: OptionContractSelectionCreate,
    *,
    candidates_seen: int,
    rejected: list[CandidateRejection],
) -> dict[str, Any]:
    reason_counts = Counter(item.reason_code for item in rejected)
    return {
        "underlying_symbol": payload.underlying_symbol,
        "option_type": payload.option_type,
        "side": payload.side,
        "scanner_type": None,
        "preview_profile": payload.preview_profile,
        "candidates_seen": candidates_seen,
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
    reason_counts = diagnostics.get("reason_counts") or {}
    summary = ", ".join(f"{k}×{v}" for k, v in sorted(reason_counts.items()))
    logger.info(
        "Option contract selection failed: %s %s — %d candidate(s) checked, rejections: [%s]",
        symbol,
        option_type,
        seen,
        summary or "none",
        extra={"option_selection_diagnostics": diagnostics},
    )


def _quoted_contract_sort_key(
    item: tuple[int, AlpacaOptionContract, AlpacaLatestOptionQuote, dict[str, object]],
) -> tuple:
    candidate_rank, contract, _, quote_context = item
    spread = _decimal_from_context(quote_context.get("spread")) or Decimal("999999")
    midpoint = _decimal_from_context(quote_context.get("midpoint")) or Decimal("0")
    spread_percent = (
        (spread / midpoint) * Decimal("100") if midpoint > Decimal("0") else Decimal("999999")
    )
    bid_size = _decimal_from_context(quote_context.get("bid_size")) or Decimal("0")
    ask_size = _decimal_from_context(quote_context.get("ask_size")) or Decimal("0")
    open_interest = contract.open_interest or Decimal("0")
    return (
        candidate_rank,
        spread_percent,
        spread,
        -(bid_size + ask_size),
        -open_interest,
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

    if target_strike is None:
        return (dte_score, contract.strike_price, contract.symbol)

    return (
        dte_score,
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
            "Selected the contract nearest to the target DTE among active tradable contracts"
        )
    return (
        "Selected the contract nearest to the target DTE with strike closest to "
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
