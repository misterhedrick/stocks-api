from __future__ import annotations

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

    def list_option_contracts(self, **_: object) -> AlpacaOptionContractsPage:
        return AlpacaOptionContractsPage(
            contracts=self.contracts,
            raw_response={"option_contracts": []},
            page_token=None,
            limit=100,
        )


class FakeMarketDataClient:
    def get_latest_option_quote(
        self,
        symbol: str,
        *,
        feed: str,
    ) -> AlpacaLatestOptionQuote:
        return AlpacaLatestOptionQuote(
            symbol=symbol,
            quote=AlpacaOptionQuote.model_validate(
                {
                    "bp": "1.20",
                    "bs": "10",
                    "ap": "1.30",
                    "as": "12",
                    "t": "2026-04-23T16:00:00Z",
                }
            ),
            raw_response={
                "bp": "1.20",
                "bs": "10",
                "ap": "1.30",
                "as": "12",
                "t": "2026-04-23T16:00:00Z",
            },
        )


def build_contract(
    symbol: str,
    *,
    expiration_date: str,
    strike_price: str,
    status: str = "active",
    tradable: bool = True,
) -> AlpacaOptionContract:
    return AlpacaOptionContract.model_validate(
        {
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
    )


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
                    ),
                    build_contract(
                        "SPY260417C00505000",
                        expiration_date="2026-04-17",
                        strike_price="505",
                    ),
                    build_contract(
                        "SPY260417C00495000",
                        expiration_date="2026-04-17",
                        strike_price="495",
                    ),
                ]
            ),
            market_data_client=FakeMarketDataClient(),
        )

        self.assertEqual(result.selected_contract.symbol, "SPY260417C00505000")
        self.assertEqual(result.quote["bid_price"], "1.20")
        self.assertEqual(result.quote["ask_price"], "1.30")
        self.assertEqual(result.quote["midpoint"], "1.25")
        self.assertEqual(result.candidates_seen, 3)

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
