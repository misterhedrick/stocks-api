from __future__ import annotations

from datetime import date
import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException

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
        "root_symbol": "SPY",
        "underlying_symbol": "SPY",
        "type": "call",
        "style": "american",
        "strike_price": strike_price,
        "size": "100",
    }
    if open_interest is not None:
        payload["open_interest"] = open_interest
    return AlpacaOptionContract.model_validate(payload)


class OptionContractSelectionTests(unittest.TestCase):
    def test_select_option_contract_picks_nearest_expiration_and_target_strike(self) -> None:
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
        with self.assertLogs("app.services.option_contracts", level="INFO"):
            with self.assertRaises(OptionContractNotFoundError) as context:
                select_option_contract(
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
                            ),
                            build_contract(
                                "SPY260417C00505000",
                                expiration_date="2026-04-17",
                                strike_price="505",
                                open_interest="10",
                            ),
                        ]
                    ),
                    market_data_client=FakeMarketDataClient(),
                )

        diagnostics = context.exception.diagnostics
        self.assertEqual(diagnostics["underlying_symbol"], "SPY")
        self.assertEqual(diagnostics["reason_counts"]["missing_open_interest"], 1)
        self.assertEqual(diagnostics["reason_counts"]["low_open_interest"], 1)
        self.assertEqual(len(diagnostics["rejections"]), 2)

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


if __name__ == "__main__":
    unittest.main()
