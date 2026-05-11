from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from time import perf_counter
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
from app.services.option_contract_types import (
    CandidateRejection,
    OptionContractNotFoundError,
    OptionContractSelectionError,
)

logger = logging.getLogger(__name__)

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
    candidate_limit: int,
    deadline: float | None = None,
) -> tuple[AlpacaOptionContract, AlpacaLatestOptionQuote]:
    rejected: list[CandidateRejection] = list(initial_rejections)
    accepted: list[
        tuple[int, AlpacaOptionContract, AlpacaLatestOptionQuote, dict[str, object]]
    ] = []
    for index, contract in enumerate(candidates):
        if deadline is not None and perf_counter() >= deadline:
            logger.warning(
                "Option contract quote loop stopped: budget exceeded after %d/%d candidates for %s",
                index,
                len(candidates),
                payload.underlying_symbol,
            )
            for skipped_contract in candidates[index:]:
                rejected.append(
                    CandidateRejection(
                        symbol=skipped_contract.symbol,
                        reason_code="budget_exceeded",
                        reason=f"{skipped_contract.symbol} skipped: runtime budget exceeded",
                        details=_contract_diagnostic_fields(
                            skipped_contract,
                            payload=payload,
                        ),
                    ),
                )
            break
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
                    details={
                        **_contract_diagnostic_fields(contract, payload=payload),
                        "error_type": exc.__class__.__name__,
                    },
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
            payload=payload,
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
        candidates_evaluated=len(candidates),
        candidate_limit=candidate_limit,
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
                **_contract_diagnostic_fields(contract),
                "status": contract.status,
                "tradable": contract.tradable,
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
    payload: OptionContractSelectionCreate | None = None,
) -> CandidateRejection | None:
    underlying = (contract.underlying_symbol or "").upper()
    allow_missing_oi = underlying in allow_missing_oi_symbols

    if min_open_interest is not None and contract.open_interest is None:
        if not allow_missing_oi:
            return CandidateRejection(
                symbol=contract.symbol,
                reason_code="missing_open_interest",
                reason=f"{contract.symbol} missing open interest",
                details={
                    **_contract_diagnostic_fields(contract, payload=payload),
                    "min_open_interest": str(min_open_interest),
                },
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
                    **_contract_diagnostic_fields(contract, payload=payload),
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
            details={
                **_contract_diagnostic_fields(contract, payload=payload),
                **_quote_diagnostic_fields(quote_context),
            },
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
                details={
                    **_contract_diagnostic_fields(contract, payload=payload),
                    **_quote_diagnostic_fields(quote_context),
                    "quote_size": str(side_size),
                    "min_quote_size": str(min_quote_size),
                },
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
                **_contract_diagnostic_fields(contract, payload=payload),
                **_quote_diagnostic_fields(quote_context),
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
                    **_contract_diagnostic_fields(contract, payload=payload),
                    **_quote_diagnostic_fields(quote_context),
                    "spread": str(spread),
                    "spread_pct": str(spread_pct) if spread_pct is not None else None,
                    "max_spread": str(max_spread) if max_spread is not None else None,
                    "effective_max_spread_pct": str(effective_pct) if effective_pct is not None else None,
                },
            )

    return None


from app.services.option_contract_diagnostics import (
    _build_quote_context,
    _candidate_diagnostic,
    _contract_diagnostic_fields,
    _contract_read,
    _contract_sort_key,
    _decimal_from_context,
    _log_selection_failure,
    _option_candidate_limit,
    _quoted_contract_sort_key,
    _quote_diagnostic_fields,
    _selection_failure_diagnostics,
    _selection_reason,
    _spread_pct_string,
)
