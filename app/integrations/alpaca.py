from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.core.config import settings
from app.db.models import OrderIntent

DATETIME_ADAPTER = TypeAdapter(datetime)


class AlpacaTradingConfigurationError(RuntimeError):
    pass


class AlpacaTradingError(RuntimeError):
    def __init__(self, detail: str, *, status_code: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class AlpacaOrderRejectedError(AlpacaTradingError):
    pass


class AlpacaSubmittedOrder(BaseModel):
    id: str
    client_order_id: str | None = None
    symbol: str
    qty: Decimal
    side: str
    type: str
    limit_price: Decimal | None = None
    status: str
    submitted_at: datetime | None = None
    filled_at: datetime | None = None

    model_config = ConfigDict(extra="allow")


class AlpacaPosition(BaseModel):
    symbol: str
    qty: Decimal
    market_value: Decimal | None = None
    cost_basis: Decimal | None = None
    unrealized_pl: Decimal | None = None

    model_config = ConfigDict(extra="allow")


class AlpacaFillActivity(BaseModel):
    id: str
    order_id: str | None = None
    symbol: str
    side: str
    qty: Decimal
    price: Decimal
    transaction_time: datetime = Field(alias="transaction_time")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class AlpacaOptionQuote(BaseModel):
    bid_price: Decimal | None = Field(default=None, alias="bp")
    bid_size: Decimal | None = Field(default=None, alias="bs")
    ask_price: Decimal | None = Field(default=None, alias="ap")
    ask_size: Decimal | None = Field(default=None, alias="as")
    timestamp: datetime | None = Field(default=None, alias="t")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class AlpacaStockQuote(BaseModel):
    bid_price: Decimal | None = Field(default=None, alias="bp")
    bid_size: Decimal | None = Field(default=None, alias="bs")
    ask_price: Decimal | None = Field(default=None, alias="ap")
    ask_size: Decimal | None = Field(default=None, alias="as")
    timestamp: datetime | None = Field(default=None, alias="t")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class AlpacaOptionContract(BaseModel):
    id: str
    symbol: str
    name: str | None = None
    status: str
    tradable: bool
    expiration_date: date
    root_symbol: str | None = None
    underlying_symbol: str
    type: str
    style: str | None = None
    strike_price: Decimal
    size: Decimal | None = None
    open_interest: Decimal | None = None
    open_interest_date: date | None = None
    close_price: Decimal | None = None
    close_price_date: date | None = None

    model_config = ConfigDict(extra="allow")


@dataclass(slots=True)
class AlpacaOrderSubmission:
    order: AlpacaSubmittedOrder
    raw_response: dict[str, Any]


@dataclass(slots=True)
class AlpacaLatestOptionQuote:
    symbol: str
    quote: AlpacaOptionQuote
    raw_response: dict[str, Any]


@dataclass(slots=True)
class AlpacaLatestStockQuote:
    symbol: str
    quote: AlpacaStockQuote
    raw_response: dict[str, Any]


class AlpacaStockBar(BaseModel):
    open: Decimal = Field(alias="o")
    high: Decimal = Field(alias="h")
    low: Decimal = Field(alias="l")
    close: Decimal = Field(alias="c")
    volume: Decimal | None = Field(default=None, alias="v")
    timestamp: datetime = Field(alias="t")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


@dataclass(slots=True)
class AlpacaStockBars:
    symbol: str
    bars: list[AlpacaStockBar]
    raw_response: list[dict[str, Any]]


@dataclass(slots=True)
class AlpacaOptionContractsPage:
    contracts: list[AlpacaOptionContract]
    raw_response: dict[str, Any]
    page_token: str | None
    limit: int | None


class AlpacaTradingClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str,
        timeout_seconds: int,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls) -> "AlpacaTradingClient":
        if not settings.alpaca_api_key or not settings.alpaca_api_secret:
            raise AlpacaTradingConfigurationError(
                "Alpaca API credentials are not configured"
            )

        return cls(
            api_key=settings.alpaca_api_key,
            api_secret=settings.alpaca_api_secret,
            base_url=settings.alpaca_trading_base_url,
            timeout_seconds=settings.alpaca_request_timeout_seconds,
        )

    def submit_order_intent(self, order_intent: OrderIntent) -> AlpacaOrderSubmission:
        payload = {
            "symbol": order_intent.option_symbol,
            "qty": str(order_intent.quantity),
            "side": order_intent.side,
            "type": order_intent.order_type,
            "time_in_force": order_intent.time_in_force,
            "client_order_id": str(order_intent.id),
        }

        if order_intent.limit_price is not None:
            payload["limit_price"] = str(order_intent.limit_price)

        try:
            with httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                headers={
                    "APCA-API-KEY-ID": self._api_key,
                    "APCA-API-SECRET-KEY": self._api_secret,
                },
            ) as client:
                response = client.post("/v2/orders", json=payload)
        except httpx.HTTPError as exc:
            raise AlpacaTradingError(
                f"Unable to reach Alpaca Trading API: {exc}"
            ) from exc

        raw_response = self._parse_dict_response(response)

        if response.status_code in {400, 403, 422}:
            raise AlpacaOrderRejectedError(
                self._extract_error_detail(raw_response),
                status_code=response.status_code,
            )

        if response.is_error:
            raise AlpacaTradingError(
                self._extract_error_detail(raw_response),
                status_code=response.status_code,
            )

        return AlpacaOrderSubmission(
            order=AlpacaSubmittedOrder.model_validate(raw_response),
            raw_response=raw_response,
        )

    def list_orders(
        self,
        *,
        status: str = "all",
        limit: int = 100,
        direction: str = "desc",
    ) -> list[tuple[AlpacaSubmittedOrder, dict[str, Any]]]:
        payload = self._get_json(
            "/v2/orders",
            params={
                "status": status,
                "limit": str(limit),
                "direction": direction,
                "nested": "false",
            },
        )
        if not isinstance(payload, list):
            raise AlpacaTradingError("Unexpected Alpaca orders response")

        return [
            (AlpacaSubmittedOrder.model_validate(item), item)
            for item in payload
            if isinstance(item, dict)
        ]

    def list_positions(self) -> list[tuple[AlpacaPosition, dict[str, Any]]]:
        payload = self._get_json("/v2/positions")
        if not isinstance(payload, list):
            raise AlpacaTradingError("Unexpected Alpaca positions response")

        return [
            (AlpacaPosition.model_validate(item), item)
            for item in payload
            if isinstance(item, dict)
        ]

    def list_fill_activities(
        self,
        *,
        page_size: int = 100,
        direction: str = "desc",
    ) -> list[tuple[AlpacaFillActivity, dict[str, Any]]]:
        payload = self._get_json(
            "/v2/account/activities/FILL",
            params={
                "page_size": str(page_size),
                "direction": direction,
            },
        )
        if not isinstance(payload, list):
            raise AlpacaTradingError("Unexpected Alpaca fill activities response")

        return [
            (AlpacaFillActivity.model_validate(item), item)
            for item in payload
            if isinstance(item, dict)
        ]

    def list_option_contracts(
        self,
        *,
        underlying_symbol: str,
        option_type: str,
        status: str = "active",
        expiration_date: date | None = None,
        expiration_date_gte: date | None = None,
        expiration_date_lte: date | None = None,
        limit: int = 100,
    ) -> AlpacaOptionContractsPage:
        params = {
            "underlying_symbols": underlying_symbol,
            "type": option_type,
            "status": status,
            "limit": str(limit),
        }
        if expiration_date is not None:
            params["expiration_date"] = expiration_date.isoformat()
        if expiration_date_gte is not None:
            params["expiration_date_gte"] = expiration_date_gte.isoformat()
        if expiration_date_lte is not None:
            params["expiration_date_lte"] = expiration_date_lte.isoformat()

        payload = self._get_json("/v2/options/contracts", params=params)
        if not isinstance(payload, dict):
            raise AlpacaTradingError("Unexpected Alpaca option contracts response")

        raw_contracts = payload.get("option_contracts")
        if not isinstance(raw_contracts, list):
            raise AlpacaTradingError("Unexpected Alpaca option contracts response")

        return AlpacaOptionContractsPage(
            contracts=[
                AlpacaOptionContract.model_validate(item)
                for item in raw_contracts
                if isinstance(item, dict)
            ],
            raw_response=payload,
            page_token=payload.get("page_token")
            if isinstance(payload.get("page_token"), str)
            else None,
            limit=payload.get("limit") if isinstance(payload.get("limit"), int) else None,
        )

    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Any:
        try:
            with httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                headers={
                    "APCA-API-KEY-ID": self._api_key,
                    "APCA-API-SECRET-KEY": self._api_secret,
                },
            ) as client:
                response = client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise AlpacaTradingError(
                f"Unable to reach Alpaca Trading API: {exc}"
            ) from exc

        raw_response = self._parse_any_response(response)

        if response.is_error:
            raise AlpacaTradingError(
                self._extract_error_detail(raw_response),
                status_code=response.status_code,
            )

        return raw_response

    def _parse_dict_response(self, response: httpx.Response) -> dict[str, Any]:
        payload = self._parse_any_response(response)
        if isinstance(payload, dict):
            return payload

        return {"message": str(payload)}

    def _parse_any_response(self, response: httpx.Response) -> Any:
        try:
            payload = response.json()
        except ValueError:
            return {"message": response.text.strip() or "Empty response from Alpaca"}
        return payload

    def _extract_error_detail(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return "Alpaca rejected the request"

        for key in ("message", "detail", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        return "Alpaca rejected the order submission"


class AlpacaMarketDataClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str,
        timeout_seconds: int,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls) -> "AlpacaMarketDataClient":
        if not settings.alpaca_api_key or not settings.alpaca_api_secret:
            raise AlpacaTradingConfigurationError(
                "Alpaca API credentials are not configured"
            )

        return cls(
            api_key=settings.alpaca_api_key,
            api_secret=settings.alpaca_api_secret,
            base_url=settings.alpaca_data_base_url,
            timeout_seconds=settings.alpaca_request_timeout_seconds,
        )

    def get_latest_option_quote(
        self,
        symbol: str,
        *,
        feed: str = "indicative",
    ) -> AlpacaLatestOptionQuote:
        payload = self._get_json(
            "/v1beta1/options/quotes/latest",
            params={
                "symbols": symbol,
                "feed": feed,
            },
        )
        if not isinstance(payload, dict):
            raise AlpacaTradingError("Unexpected Alpaca option quote response")

        quotes = payload.get("quotes")
        if not isinstance(quotes, dict):
            raise AlpacaTradingError("Unexpected Alpaca option quote response")

        raw_quote = quotes.get(symbol)
        if not isinstance(raw_quote, dict):
            raise AlpacaTradingError(f"No latest option quote returned for {symbol}")

        return AlpacaLatestOptionQuote(
            symbol=symbol,
            quote=AlpacaOptionQuote.model_validate(raw_quote),
            raw_response=raw_quote,
        )

    def get_latest_stock_quotes(
        self,
        symbols: list[str],
        *,
        feed: str = "iex",
    ) -> dict[str, AlpacaLatestStockQuote]:
        payload = self._get_json(
            "/v2/stocks/quotes/latest",
            params={
                "symbols": ",".join(symbols),
                "feed": feed,
            },
        )
        if not isinstance(payload, dict):
            raise AlpacaTradingError("Unexpected Alpaca stock quote response")

        quotes = payload.get("quotes")
        if not isinstance(quotes, dict):
            raise AlpacaTradingError("Unexpected Alpaca stock quote response")

        latest_quotes: dict[str, AlpacaLatestStockQuote] = {}
        for symbol in symbols:
            raw_quote = quotes.get(symbol)
            if isinstance(raw_quote, dict):
                latest_quotes[symbol] = AlpacaLatestStockQuote(
                    symbol=symbol,
                    quote=AlpacaStockQuote.model_validate(raw_quote),
                    raw_response=raw_quote,
                )

        return latest_quotes

    def get_stock_bars(
        self,
        symbols: list[str],
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        feed: str = "iex",
        limit: int = 1000,
    ) -> dict[str, AlpacaStockBars]:
        payload = self._get_json(
            "/v2/stocks/bars",
            params={
                "symbols": ",".join(symbols),
                "timeframe": timeframe,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "feed": feed,
                "limit": str(limit),
            },
        )
        if not isinstance(payload, dict):
            raise AlpacaTradingError("Unexpected Alpaca stock bars response")

        bars_by_symbol = payload.get("bars")
        if not isinstance(bars_by_symbol, dict):
            raise AlpacaTradingError("Unexpected Alpaca stock bars response")

        results: dict[str, AlpacaStockBars] = {}
        for symbol in symbols:
            raw_bars = bars_by_symbol.get(symbol)
            if not isinstance(raw_bars, list):
                continue
            raw_bar_dicts = [item for item in raw_bars if isinstance(item, dict)]
            results[symbol] = AlpacaStockBars(
                symbol=symbol,
                bars=[AlpacaStockBar.model_validate(item) for item in raw_bar_dicts],
                raw_response=raw_bar_dicts,
            )

        return results

    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Any:
        try:
            with httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                headers={
                    "APCA-API-KEY-ID": self._api_key,
                    "APCA-API-SECRET-KEY": self._api_secret,
                },
            ) as client:
                response = client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise AlpacaTradingError(
                f"Unable to reach Alpaca Market Data API: {exc}"
            ) from exc

        payload = _parse_any_response(response)

        if response.is_error:
            raise AlpacaTradingError(
                _extract_error_detail(payload),
                status_code=response.status_code,
            )

        return payload


def coerce_alpaca_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return DATETIME_ADAPTER.validate_python(value)


def _parse_any_response(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError:
        return {"message": response.text.strip() or "Empty response from Alpaca"}
    return payload


def _extract_error_detail(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "Alpaca rejected the request"

    for key in ("message", "detail", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return "Alpaca rejected the request"
