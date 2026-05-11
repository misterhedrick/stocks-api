from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


from app.services.option_contract_helpers import (
    _build_quote_context,
    _contract_availability_rejection,
    _contract_read,
    _contract_sort_key,
    _expiration_range,
    _log_selection_failure,
    _missing_oi_allowlist,
    _option_candidate_limit,
    _quote_rejection_reason,
    _select_quoted_contract,
    _selection_failure_diagnostics,
    _selection_reason,
)
from app.services.option_contract_types import (
    CandidateRejection,
    OptionContractNotFoundError,
    OptionContractSelectionError,
)

def select_option_contract(
    payload: OptionContractSelectionCreate,
    *,
    trading_client: AlpacaTradingClient | None = None,
    market_data_client: AlpacaMarketDataClient | None = None,
    deadline: float | None = None,
) -> OptionContractSelectionRead:
    trading = trading_client or AlpacaTradingClient.from_settings()
    today = datetime.now(ZoneInfo("America/New_York")).date()
    expiration_date_gte, expiration_date_lte = _expiration_range(payload, today=today)
    candidate_limit = _option_candidate_limit(payload.limit)
    contracts_page = trading.list_option_contracts(
        underlying_symbol=payload.underlying_symbol,
        option_type=payload.option_type,
        expiration_date=payload.expiration_date,
        expiration_date_gte=expiration_date_gte,
        expiration_date_lte=expiration_date_lte,
        limit=candidate_limit,
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
    candidates = candidates[:candidate_limit]

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
            candidates_evaluated=0,
            candidate_limit=candidate_limit,
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
        candidate_limit=candidate_limit,
        deadline=deadline,
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
