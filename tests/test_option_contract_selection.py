from __future__ import annotations

import os
from datetime import date
import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException

from app.core.config import Settings
from app.api.routes.options import select_option_contract_route
from app.integrations.alpaca import (
    AlpacaLatestOptionQuote,
    AlpacaOptionContract,
    AlpacaOptionContractsPage,
    AlpacaOptionQuote,
    AlpacaTradingConfigurationError,
    AlpacaTradingError,
)
from app.schemas.options import OptionContractSelectionCreate
from app.services.option_contracts import (
    OptionContractNotFoundError,
    select_option_contract,
)


class FakeTradingClient:
    def __init__(self, contracts: list[AlpacaOptionContract]) -> None:
        self.contracts = contracts
        self.calls: list[dict[str, object]] = []

    def list_option_contracts(self, **kwargs: object) -> AlpacaOptionContractsPage:
        self.calls.append(kwargs)
        return AlpacaOptionContractsPage(
            contracts=self.contracts,
            raw_response={"option_contracts": []},
            page_token=None,
            limit=100,
        )


class FakeMarketDataClient:
    def __init__(self, quotes_by_symbol: dict[str, dict[str, str]] | None = None) -> None:
        self.quotes_by_symbol = quotes_by_symbol or {}

    def get_latest_option_quote(
        self,
        symbol: str,
        *,
        feed: str,
    ) -> AlpacaLatestOptionQuote:
        raw_quote = self.quotes_by_symbol.get(
            symbol,
            {
                "bp": "1.20",
                "bs": "10",
                "ap": "1.30",
                "as": "12",
                "t": "2026-04-23T16:00:00Z",
            },
        )
        return AlpacaLatestOptionQuote(
            symbol=symbol,
            quote=AlpacaOptionQuote.model_validate(raw_quote),
            raw_response=raw_quote,
        )


class QuoteUnavailableMarketDataClient:
    def get_latest_option_quote(
        self,
        symbol: str,
        *,
        feed: str,
    ) -> AlpacaLatestOptionQuote:
        raise AlpacaTradingError(f"quote unavailable for {symbol}")


def build_contract(
    symbol: str,
    *,
    expiration_date: str,
    strike_price: str,
    underlying_symbol: str = "SPY",
    status: str = "active",
    tradable: bool = True,
    open_interest: str | None = None,
) -> AlpacaOptionContract:
    payload = {
        "id": f"{symbol}-id",
        "symbol": symbol,
        "name": symbol,
        "status": status,
        "tradable": tradable,
        "expiration_date": expiration_date,
        "root_symbol": underlying_symbol,
        "underlying_symbol": underlying_symbol,
        "type": "call",
        "style": "american",
        "strike_price": strike_price,
        "size": "100",
    }
    if open_interest is not None:
        payload["open_interest"] = open_interest
    return AlpacaOptionContract.model_validate(payload)


class OptionContractSelectionTests(unittest.TestCase):
    def test_option_candidate_limit_defaults_to_100(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.options_max_candidates, 100)

    def test_diagnostic_candidate_limit_defaults_to_10(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.options_diagnostic_candidate_limit, 10)

    def test_select_option_contract_picks_by_open_interest_then_strike_then_dte(self) -> None:
        # With equal OI, strike distance wins before DTE distance.
        result = select_option_contract(
            OptionContractSelectionCreate(
                underlying_symbol="SPY",
                option_type="call",
                target_strike=Decimal("503"),
            ),
            trading_client=FakeTradingClient(
                [
                    build_contract(
                        "SPY260501C00510000",
                        expiration_date="2026-05-01",
                        strike_price="510",
                        open_interest="100",
                    ),
                    build_contract(
                        "SPY260417C00505000",
                        expiration_date="2026-04-17",
                        strike_price="505",
                        open_interest="100",
                    ),
                    build_contract(
                        "SPY260417C00495000",
                        expiration_date="2026-04-17",
                        strike_price="495",
                        open_interest="100",
                    ),
                ]
            ),
            market_data_client=FakeMarketDataClient(),
        )

        self.assertEqual(result.selected_contract.symbol, "SPY260417C00505000")
        self.assertEqual(result.quote["bid_price"], "1.20")
        self.assertEqual(result.quote["ask_price"], "1.30")
        self.assertEqual(result.quote["midpoint"], "1.25")
        self.assertEqual(result.quote["estimated_notional"], "130.00")
        self.assertEqual(result.candidates_seen, 3)

    def test_select_option_contract_resolves_relative_expiration_filters(self) -> None:
        trading_client = FakeTradingClient(
            [
                build_contract(
                    "SPY260501C00510000",
                    expiration_date="2026-05-01",
                    strike_price="510",
                    open_interest="100",
                )
            ]
        )

        result = select_option_contract(
            OptionContractSelectionCreate(
                underlying_symbol="SPY",
                option_type="call",
                min_days_to_expiration=1,
                max_days_to_expiration=7,
            ),
            trading_client=trading_client,
            market_data_client=FakeMarketDataClient(),
        )

        self.assertEqual(result.selected_contract.symbol, "SPY260501C00510000")
        self.assertEqual(trading_client.calls[-1]["expiration_date"], None)
        self.assertGreaterEqual(
            trading_client.calls[-1]["expiration_date_gte"],
            date.today(),
        )
        self.assertIsNotNone(trading_client.calls[-1]["expiration_date_lte"])

    def test_default_dte_window_applied_when_no_expiration_filters_set(self) -> None:
        trading_client = FakeTradingClient(
            [
                build_contract(
                    "SPY260521C00500000",
                    expiration_date="2026-05-21",
                    strike_price="500",
                    open_interest="100",
                )
            ]
        )

        select_option_contract(
            OptionContractSelectionCreate(
                underlying_symbol="SPY",
                option_type="call",
            ),
            trading_client=trading_client,
            market_data_client=FakeMarketDataClient(),
        )

        call = trading_client.calls[-1]
        # Default window should be applied (min/max DTE from settings)
        self.assertIsNotNone(call["expiration_date_gte"])
        self.assertIsNotNone(call["expiration_date_lte"])
        self.assertGreater(call["expiration_date_lte"], call["expiration_date_gte"])

    def test_select_option_contract_skips_quotes_over_notional_cap(self) -> None:
        result = select_option_contract(
            OptionContractSelectionCreate(
                underlying_symbol="SPY",
                option_type="call",
                max_estimated_notional=Decimal("250"),
            ),
            trading_client=FakeTradingClient(
                [
                    build_contract(
                        "SPY260417C00500000",
                        expiration_date="2026-04-17",
                        strike_price="500",
                        open_interest="100",
                    ),
                    build_contract(
                        "SPY260417C00720000",
                        expiration_date="2026-04-17",
                        strike_price="720",
                        open_interest="100",
                    ),
                ]
            ),
            market_data_client=FakeMarketDataClient(
                {
                    "SPY260417C00500000": {
                        "bp": "209.00",
                        "bs": "3",
                        "ap": "212.00",
                        "as": "2",
                        "t": "2026-04-23T16:00:00Z",
                    },
                    "SPY260417C00720000": {
                        "bp": "1.20",
                        "bs": "10",
                        "ap": "1.30",
                        "as": "12",
                        "t": "2026-04-23T16:00:00Z",
                    },
                }
            ),
        )

        self.assertEqual(result.selected_contract.symbol, "SPY260417C00720000")
        self.assertEqual(result.quote["estimated_notional"], "130.00")

    def test_select_option_contract_skips_contracts_below_open_interest_floor(self) -> None:
        result = select_option_contract(
            OptionContractSelectionCreate(
                underlying_symbol="SPY",
                option_type="call",
                min_open_interest=Decimal("100"),
            ),
            trading_client=FakeTradingClient(
                [
                    build_contract(
                        "SPY260417C00500000",
                        expiration_date="2026-04-17",
                        strike_price="500",
                        open_interest="10",
                    ),
                    build_contract(
                        "SPY260417C00505000",
                        expiration_date="2026-04-17",
                        strike_price="505",
                        open_interest="1000",
                    ),
                ]
            ),
            market_data_client=FakeMarketDataClient(),
        )

        self.assertEqual(result.selected_contract.symbol, "SPY260417C00505000")

    def test_select_option_contract_ignores_inactive_or_untradable_contracts(self) -> None:
        result = select_option_contract(
            OptionContractSelectionCreate(
                underlying_symbol="SPY",
                option_type="call",
                underlying_price=Decimal("500"),
            ),
            trading_client=FakeTradingClient(
                [
                    build_contract(
                        "SPY260417C00500000",
                        expiration_date="2026-04-17",
                        strike_price="500",
                        status="inactive",
                    ),
                    build_contract(
                        "SPY260417C00505000",
                        expiration_date="2026-04-17",
                        strike_price="505",
                        tradable=False,
                    ),
                    build_contract(
                        "SPY260424C00510000",
                        expiration_date="2026-04-24",
                        strike_price="510",
                        open_interest="100",
                    ),
                ]
            ),
            market_data_client=FakeMarketDataClient(),
        )

        self.assertEqual(result.selected_contract.symbol, "SPY260424C00510000")
        self.assertEqual(result.candidates_seen, 1)

    def test_select_option_contract_requires_candidates(self) -> None:
        with self.assertRaises(OptionContractNotFoundError):
            select_option_contract(
                OptionContractSelectionCreate(
                    underlying_symbol="SPY",
                    option_type="call",
                    target_strike=Decimal("500"),
                ),
                trading_client=FakeTradingClient([]),
                market_data_client=FakeMarketDataClient(),
            )

    def test_successful_selection_does_not_emit_failure_summary(self) -> None:
        with self.assertNoLogs("app.services.option_contracts", level="INFO"):
            result = select_option_contract(
                OptionContractSelectionCreate(
                    underlying_symbol="SPY",
                    option_type="call",
                ),
                trading_client=FakeTradingClient(
                    [
                        build_contract(
                            "SPY260417C00500000",
                            expiration_date="2026-04-17",
                            strike_price="500",
                            open_interest="100",
                        )
                    ]
                ),
                market_data_client=FakeMarketDataClient(),
            )

        self.assertEqual(result.selected_contract.symbol, "SPY260417C00500000")

    def test_failure_emits_grouped_rejection_reasons(self) -> None:
        # Use AAPL (not in the missing-OI allowlist) so missing OI is always rejected.
        with self.assertLogs("app.services.option_contracts", level="INFO"):
            with self.assertRaises(OptionContractNotFoundError) as context:
                select_option_contract(
                    OptionContractSelectionCreate(
                        underlying_symbol="AAPL",
                        option_type="call",
                        min_open_interest=Decimal("100"),
                    ),
                    trading_client=FakeTradingClient(
                        [
                            build_contract(
                                "AAPL260417C00500000",
                                expiration_date="2026-04-17",
                                strike_price="500",
                                underlying_symbol="AAPL",
                                # No open_interest → missing
                            ),
                            build_contract(
                                "AAPL260417C00505000",
                                expiration_date="2026-04-17",
                                strike_price="505",
                                underlying_symbol="AAPL",
                                open_interest="10",
                            ),
                        ]
                    ),
                    market_data_client=FakeMarketDataClient(),
                )

        diagnostics = context.exception.diagnostics
        self.assertEqual(diagnostics["underlying_symbol"], "AAPL")
        self.assertEqual(diagnostics["reason_counts"]["missing_open_interest"], 1)
        self.assertEqual(diagnostics["reason_counts"]["low_open_interest"], 1)
        self.assertEqual(len(diagnostics["rejections"]), 2)

    def test_failure_diagnostics_caps_top_rejected_candidates(self) -> None:
        with patch(
            "app.services.option_contracts.settings.options_diagnostic_candidate_limit",
            2,
        ):
            with self.assertRaises(OptionContractNotFoundError) as context:
                select_option_contract(
                    OptionContractSelectionCreate(
                        underlying_symbol="AAPL",
                        option_type="call",
                        min_open_interest=Decimal("100"),
                    ),
                    trading_client=FakeTradingClient(
                        [
                            build_contract(
                                f"AAPL260521C002{i}0000",
                                expiration_date="2026-05-21",
                                strike_price=f"20{i}",
                                underlying_symbol="AAPL",
                                open_interest="1",
                            )
                            for i in range(5)
                        ]
                    ),
                    market_data_client=FakeMarketDataClient(),
                )

        diagnostics = context.exception.diagnostics
        self.assertEqual(diagnostics["diagnostic_candidate_limit"], 2)
        self.assertEqual(len(diagnostics["top_rejected_candidates"]), 2)
        self.assertEqual(
            diagnostics["top_rejected_candidates"][0]["rejection_reasons"],
            ["low_open_interest"],
        )

    def test_candidate_limit_controls_query_and_evaluates_more_than_25_candidates(self) -> None:
        contracts = [
            build_contract(
                f"AAPL260521C002{i:02d}000",
                expiration_date="2026-05-21",
                strike_price=f"2{i:02d}",
                underlying_symbol="AAPL",
                open_interest="1",
            )
            for i in range(30)
        ]
        trading_client = FakeTradingClient(contracts)

        with patch("app.services.option_contracts.settings.options_max_candidates", 30):
            with self.assertRaises(OptionContractNotFoundError) as context:
                select_option_contract(
                    OptionContractSelectionCreate(
                        underlying_symbol="AAPL",
                        option_type="call",
                        min_open_interest=Decimal("100"),
                    ),
                    trading_client=trading_client,
                    market_data_client=FakeMarketDataClient(),
                )

        diagnostics = context.exception.diagnostics
        self.assertEqual(trading_client.calls[-1]["limit"], 30)
        self.assertEqual(diagnostics["candidate_limit"], 30)
        self.assertEqual(diagnostics["candidates_evaluated"], 30)
        self.assertEqual(diagnostics["reason_counts"]["low_open_interest"], 30)

    def test_candidate_limit_setting_overrides_lower_payload_limit(self) -> None:
        contracts = [
            build_contract(
                f"AAPL260521C002{i:02d}000",
                expiration_date="2026-05-21",
                strike_price=f"2{i:02d}",
                underlying_symbol="AAPL",
                open_interest="1",
            )
            for i in range(30)
        ]
        trading_client = FakeTradingClient(contracts)

        with patch("app.services.option_contracts.settings.options_max_candidates", 30):
            with self.assertRaises(OptionContractNotFoundError) as context:
                select_option_contract(
                    OptionContractSelectionCreate(
                        underlying_symbol="AAPL",
                        option_type="call",
                        min_open_interest=Decimal("100"),
                        limit=5,
                    ),
                    trading_client=trading_client,
                    market_data_client=FakeMarketDataClient(),
                )

        self.assertEqual(trading_client.calls[-1]["limit"], 30)
        self.assertEqual(context.exception.diagnostics["candidates_evaluated"], 30)

    def test_multiple_candidate_failures_are_aggregated_with_accurate_counts(self) -> None:
        with self.assertRaises(OptionContractNotFoundError) as context:
            select_option_contract(
                OptionContractSelectionCreate(
                    underlying_symbol="SPY",
                    option_type="call",
                    max_estimated_notional=Decimal("250"),
                    max_spread=Decimal("0.10"),
                    max_spread_percent=Decimal("5"),
                ),
                trading_client=FakeTradingClient(
                    [
                        build_contract(
                            "SPY260417C00500000",
                            expiration_date="2026-04-17",
                            strike_price="500",
                            open_interest="100",
                            tradable=False,
                        ),
                        build_contract(
                            "SPY260417C00505000",
                            expiration_date="2026-04-17",
                            strike_price="505",
                            open_interest="100",
                        ),
                        build_contract(
                            "SPY260417C00510000",
                            expiration_date="2026-04-17",
                            strike_price="510",
                            open_interest="100",
                        ),
                        build_contract(
                            "SPY260417C00515000",
                            expiration_date="2026-04-17",
                            strike_price="515",
                            open_interest="100",
                        ),
                    ]
                ),
                market_data_client=FakeMarketDataClient(
                    {
                        "SPY260417C00505000": {
                            "bp": "209.00",
                            "bs": "3",
                            "ap": "212.00",
                            "as": "2",
                            "t": "2026-04-23T16:00:00Z",
                        },
                        "SPY260417C00510000": {
                            "bp": "1.20",
                            "bs": "10",
                            "ap": "1.40",
                            "as": "12",
                            "t": "2026-04-23T16:00:00Z",
                        },
                        "SPY260417C00515000": {
                            "bp": "1.00",
                            "bs": "10",
                            "ap": "1.20",
                            "as": "12",
                            "t": "2026-04-23T16:00:00Z",
                        },
                    }
                ),
            )

        reason_counts = context.exception.diagnostics["reason_counts"]
        self.assertEqual(reason_counts["not_tradable"], 1)
        self.assertEqual(reason_counts["estimated_notional_above_max"], 1)
        self.assertEqual(reason_counts["spread_too_wide"], 2)

    def test_quote_unavailable_is_structured_reason(self) -> None:
        with self.assertRaises(OptionContractNotFoundError) as context:
            select_option_contract(
                OptionContractSelectionCreate(
                    underlying_symbol="SPY",
                    option_type="call",
                ),
                trading_client=FakeTradingClient(
                    [
                        build_contract(
                            "SPY260417C00500000",
                            expiration_date="2026-04-17",
                            strike_price="500",
                            open_interest="100",
                        )
                    ]
                ),
                market_data_client=QuoteUnavailableMarketDataClient(),
            )

        self.assertEqual(context.exception.diagnostics["reason_counts"]["quote_unavailable"], 1)

    # ------------------------------------------------------------------
    # New tests: relative spread OR logic
    # ------------------------------------------------------------------

    def test_relative_spread_passes_when_absolute_spread_fails(self) -> None:
        # bid=3.00, ask=3.40 → spread=0.40 > max_spread=0.35 (abs FAILS)
        # mid=3.20, spread_pct=0.40/3.20=12.5% ≤ options_max_spread_pct=15% (pct PASSES)
        # OR logic → contract is accepted.
        result = select_option_contract(
            OptionContractSelectionCreate(
                underlying_symbol="SPY",
                option_type="call",
                max_spread=Decimal("0.35"),
            ),
            trading_client=FakeTradingClient(
                [
                    build_contract(
                        "SPY260521C00500000",
                        expiration_date="2026-05-21",
                        strike_price="500",
                        open_interest="500",
                    )
                ]
            ),
            market_data_client=FakeMarketDataClient(
                {
                    "SPY260521C00500000": {
                        "bp": "3.00",
                        "bs": "20",
                        "ap": "3.40",
                        "as": "18",
                        "t": "2026-05-07T16:00:00Z",
                    }
                }
            ),
        )

        self.assertEqual(result.selected_contract.symbol, "SPY260521C00500000")

    def test_both_spread_checks_fail_rejects_contract(self) -> None:
        # bid=1.10, ask=1.60 → spread=0.50 > 0.35 (abs FAILS)
        # mid=1.35, spread_pct=0.50/1.35=37% > 15% (pct FAILS)
        # Both fail → rejected.
        with self.assertRaises(OptionContractNotFoundError) as context:
            select_option_contract(
                OptionContractSelectionCreate(
                    underlying_symbol="SPY",
                    option_type="call",
                    max_spread=Decimal("0.35"),
                ),
                trading_client=FakeTradingClient(
                    [
                        build_contract(
                            "SPY260521C00500000",
                            expiration_date="2026-05-21",
                            strike_price="500",
                            open_interest="500",
                        )
                    ]
                ),
                market_data_client=FakeMarketDataClient(
                    {
                        "SPY260521C00500000": {
                            "bp": "1.10",
                            "bs": "5",
                            "ap": "1.60",
                            "as": "5",
                            "t": "2026-05-07T16:00:00Z",
                        }
                    }
                ),
            )

        self.assertIn("spread_too_wide", context.exception.diagnostics["reason_counts"])

    # ------------------------------------------------------------------
    # New tests: missing OI allowlist
    # ------------------------------------------------------------------

    def test_spy_missing_oi_passes_with_good_quote(self) -> None:
        result = select_option_contract(
            OptionContractSelectionCreate(
                underlying_symbol="SPY",
                option_type="call",
                min_open_interest=Decimal("25"),
            ),
            trading_client=FakeTradingClient(
                [
                    build_contract(
                        "SPY260521C00500000",
                        expiration_date="2026-05-21",
                        strike_price="500",
                        underlying_symbol="SPY",
                        # No open_interest → missing; SPY is allowlisted
                    )
                ]
            ),
            market_data_client=FakeMarketDataClient(),
        )

        self.assertEqual(result.selected_contract.symbol, "SPY260521C00500000")

    def test_qqq_missing_oi_passes_with_good_quote(self) -> None:
        result = select_option_contract(
            OptionContractSelectionCreate(
                underlying_symbol="QQQ",
                option_type="call",
                min_open_interest=Decimal("25"),
            ),
            trading_client=FakeTradingClient(
                [
                    build_contract(
                        "QQQ260521C00400000",
                        expiration_date="2026-05-21",
                        strike_price="400",
                        underlying_symbol="QQQ",
                        # No open_interest → missing; QQQ is allowlisted
                    )
                ]
            ),
            market_data_client=FakeMarketDataClient(),
        )

        self.assertEqual(result.selected_contract.symbol, "QQQ260521C00400000")

    def test_aapl_missing_oi_fails_by_default(self) -> None:
        with self.assertRaises(OptionContractNotFoundError) as context:
            select_option_contract(
                OptionContractSelectionCreate(
                    underlying_symbol="AAPL",
                    option_type="call",
                    min_open_interest=Decimal("25"),
                ),
                trading_client=FakeTradingClient(
                    [
                        build_contract(
                            "AAPL260521C00200000",
                            expiration_date="2026-05-21",
                            strike_price="200",
                            underlying_symbol="AAPL",
                            # No open_interest; AAPL is NOT in the allowlist
                        )
                    ]
                ),
                market_data_client=FakeMarketDataClient(),
            )

        self.assertIn(
            "missing_open_interest", context.exception.diagnostics["reason_counts"]
        )

    def test_msft_missing_oi_fails_by_default(self) -> None:
        with self.assertRaises(OptionContractNotFoundError) as context:
            select_option_contract(
                OptionContractSelectionCreate(
                    underlying_symbol="MSFT",
                    option_type="call",
                    min_open_interest=Decimal("25"),
                ),
                trading_client=FakeTradingClient(
                    [
                        build_contract(
                            "MSFT260521C00450000",
                            expiration_date="2026-05-21",
                            strike_price="450",
                            underlying_symbol="MSFT",
                        )
                    ]
                ),
                market_data_client=FakeMarketDataClient(),
            )

        self.assertIn(
            "missing_open_interest", context.exception.diagnostics["reason_counts"]
        )

    # ------------------------------------------------------------------
    # New tests: DTE filtering
    # ------------------------------------------------------------------

    def test_dte_filter_avoids_near_term_contracts(self) -> None:
        # When no explicit expiration filters are set, the selector should apply
        # OPTIONS_MIN_DTE/OPTIONS_MAX_DTE defaults via expiration_date_gte/lte.
        trading_client = FakeTradingClient(
            [
                build_contract(
                    "SPY260521C00500000",
                    expiration_date="2026-05-21",
                    strike_price="500",
                    open_interest="200",
                )
            ]
        )

        select_option_contract(
            OptionContractSelectionCreate(
                underlying_symbol="SPY",
                option_type="call",
            ),
            trading_client=trading_client,
            market_data_client=FakeMarketDataClient(),
        )

        call = trading_client.calls[-1]
        # Expiration gte should be at least today (options_min_dte ≥ 0)
        self.assertIsNotNone(call["expiration_date_gte"])
        # Expiration lte should be bounded (options_max_dte)
        self.assertIsNotNone(call["expiration_date_lte"])
        # Min DTE filter keeps ultra-near contracts out
        self.assertGreaterEqual(call["expiration_date_gte"], date.today())

    # ------------------------------------------------------------------
    # New tests: expanded candidate scoring
    # ------------------------------------------------------------------

    def test_expanded_candidates_finds_valid_after_first_five_fail(self) -> None:
        # First 5 contracts fail (wide spread / high notional); 6th passes.
        failing_quotes = {
            f"SPY260521C005{i:02d}000": {
                "bp": "1.00",
                "bs": "5",
                "ap": "1.80",  # spread=0.80, mid=1.40, pct=57% — fails both
                "as": "5",
                "t": "2026-05-07T16:00:00Z",
            }
            for i in range(5)
        }
        passing_quote = {
            "SPY260521C00560000": {
                "bp": "1.20",
                "bs": "10",
                "ap": "1.30",  # spread=0.10, pct=7.7% — passes
                "as": "10",
                "t": "2026-05-07T16:00:00Z",
            }
        }

        failing_contracts = [
            build_contract(
                f"SPY260521C005{i:02d}000",
                expiration_date="2026-05-21",
                strike_price=f"5{i:02d}",
                open_interest="200",
            )
            for i in range(5)
        ]
        passing_contract = build_contract(
            "SPY260521C00560000",
            expiration_date="2026-05-21",
            strike_price="560",
            open_interest="300",
        )

        result = select_option_contract(
            OptionContractSelectionCreate(
                underlying_symbol="SPY",
                option_type="call",
                max_spread=Decimal("0.35"),
                underlying_price=Decimal("500"),
            ),
            trading_client=FakeTradingClient(failing_contracts + [passing_contract]),
            market_data_client=FakeMarketDataClient({**failing_quotes, **passing_quote}),
        )

        self.assertEqual(result.selected_contract.symbol, "SPY260521C00560000")

    def test_candidate_ranking_prefers_acceptable_open_interest_before_candidate_cap(self) -> None:
        with patch("app.services.option_contracts.settings.options_max_candidates", 1):
            result = select_option_contract(
                OptionContractSelectionCreate(
                    underlying_symbol="AAPL",
                    option_type="call",
                    target_strike=Decimal("200"),
                    min_open_interest=Decimal("100"),
                ),
                trading_client=FakeTradingClient(
                    [
                        build_contract(
                            "AAPL260521C00200000",
                            expiration_date="2026-05-21",
                            strike_price="200",
                            underlying_symbol="AAPL",
                            open_interest="1",
                        ),
                        build_contract(
                            "AAPL260521C00210000",
                            expiration_date="2026-05-21",
                            strike_price="210",
                            underlying_symbol="AAPL",
                            open_interest="500",
                        ),
                    ]
                ),
                market_data_client=FakeMarketDataClient(),
            )

        self.assertEqual(result.selected_contract.symbol, "AAPL260521C00210000")

    def test_selection_prefers_usable_two_sided_quote_over_unusable_quote(self) -> None:
        result = select_option_contract(
            OptionContractSelectionCreate(
                underlying_symbol="SPY",
                option_type="call",
                target_strike=Decimal("500"),
            ),
            trading_client=FakeTradingClient(
                [
                    build_contract(
                        "SPY260521C00500000",
                        expiration_date="2026-05-21",
                        strike_price="500",
                        open_interest="500",
                    ),
                    build_contract(
                        "SPY260521C00510000",
                        expiration_date="2026-05-21",
                        strike_price="510",
                        open_interest="500",
                    ),
                ]
            ),
            market_data_client=FakeMarketDataClient(
                {
                    "SPY260521C00500000": {
                        "bp": "0",
                        "bs": "5",
                        "ap": "0",
                        "as": "5",
                        "t": "2026-05-07T16:00:00Z",
                    },
                    "SPY260521C00510000": {
                        "bp": "1.20",
                        "bs": "10",
                        "ap": "1.30",
                        "as": "10",
                        "t": "2026-05-07T16:00:00Z",
                    },
                }
            ),
        )

        self.assertEqual(result.selected_contract.symbol, "SPY260521C00510000")

    def test_selection_prefers_lower_spread_and_notional_among_safe_candidates(self) -> None:
        result = select_option_contract(
            OptionContractSelectionCreate(
                underlying_symbol="SPY",
                option_type="call",
                target_strike=Decimal("500"),
                max_estimated_notional=Decimal("500"),
            ),
            trading_client=FakeTradingClient(
                [
                    build_contract(
                        "SPY260521C00500000",
                        expiration_date="2026-05-21",
                        strike_price="500",
                        open_interest="500",
                    ),
                    build_contract(
                        "SPY260521C00510000",
                        expiration_date="2026-05-21",
                        strike_price="510",
                        open_interest="500",
                    ),
                ]
            ),
            market_data_client=FakeMarketDataClient(
                {
                    "SPY260521C00500000": {
                        "bp": "2.00",
                        "bs": "10",
                        "ap": "2.30",
                        "as": "10",
                        "t": "2026-05-07T16:00:00Z",
                    },
                    "SPY260521C00510000": {
                        "bp": "1.20",
                        "bs": "10",
                        "ap": "1.30",
                        "as": "10",
                        "t": "2026-05-07T16:00:00Z",
                    },
                }
            ),
        )

        self.assertEqual(result.selected_contract.symbol, "SPY260521C00510000")

    def test_rejection_summary_groups_reasons_correctly(self) -> None:
        # Mix of rejection types: 2 low_oi, 1 spread_too_wide → grouped accurately.
        with self.assertRaises(OptionContractNotFoundError) as context:
            select_option_contract(
                OptionContractSelectionCreate(
                    underlying_symbol="AAPL",
                    option_type="call",
                    min_open_interest=Decimal("100"),
                    max_spread=Decimal("0.10"),
                ),
                trading_client=FakeTradingClient(
                    [
                        build_contract(
                            "AAPL260521C00200000",
                            expiration_date="2026-05-21",
                            strike_price="200",
                            underlying_symbol="AAPL",
                            open_interest="5",  # low OI
                        ),
                        build_contract(
                            "AAPL260521C00205000",
                            expiration_date="2026-05-21",
                            strike_price="205",
                            underlying_symbol="AAPL",
                            open_interest="3",  # low OI
                        ),
                        build_contract(
                            "AAPL260521C00210000",
                            expiration_date="2026-05-21",
                            strike_price="210",
                            underlying_symbol="AAPL",
                            open_interest="500",  # passes OI
                        ),
                    ]
                ),
                market_data_client=FakeMarketDataClient(
                    {
                        "AAPL260521C00210000": {
                            "bp": "1.10",
                            "bs": "5",
                            "ap": "1.60",  # spread=0.50 > 0.10, pct=38% > 15% → rejected
                            "as": "5",
                            "t": "2026-05-07T16:00:00Z",
                        }
                    }
                ),
            )

        counts = context.exception.diagnostics["reason_counts"]
        self.assertEqual(counts.get("low_open_interest", 0), 2)
        self.assertEqual(counts.get("spread_too_wide", 0), 1)

    # ------------------------------------------------------------------
    # Existing route tests (unchanged)
    # ------------------------------------------------------------------

    def test_select_option_contract_route_maps_not_found(self) -> None:
        with self.assertRaises(HTTPException) as context:
            with patch(
                "app.api.routes.options.select_option_contract",
                side_effect=OptionContractNotFoundError("No contracts"),
            ):
                select_option_contract_route(
                    OptionContractSelectionCreate(
                        underlying_symbol="SPY",
                        option_type="call",
                    )
                )

        self.assertEqual(context.exception.status_code, 404)

    def test_select_option_contract_route_maps_configuration_error(self) -> None:
        with self.assertRaises(HTTPException) as context:
            with patch(
                "app.api.routes.options.select_option_contract",
                side_effect=AlpacaTradingConfigurationError("missing credentials"),
            ):
                select_option_contract_route(
                    OptionContractSelectionCreate(
                        underlying_symbol="SPY",
                        option_type="call",
                    )
                )

        self.assertEqual(context.exception.status_code, 500)

    def test_select_option_contract_route_maps_alpaca_error(self) -> None:
        with self.assertRaises(HTTPException) as context:
            with patch(
                "app.api.routes.options.select_option_contract",
                side_effect=AlpacaTradingError("Alpaca unavailable"),
            ):
                select_option_contract_route(
                    OptionContractSelectionCreate(
                        underlying_symbol="SPY",
                        option_type="call",
                    )
                )

        self.assertEqual(context.exception.status_code, 502)

    def test_select_option_contract_route_maps_alpaca_validation_error(self) -> None:
        with self.assertRaises(HTTPException) as context:
            with patch(
                "app.api.routes.options.select_option_contract",
                side_effect=AlpacaTradingError("validation failed", status_code=422),
            ):
                select_option_contract_route(
                    OptionContractSelectionCreate(
                        underlying_symbol="SPY",
                        option_type="call",
                    )
                )

        self.assertEqual(context.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
