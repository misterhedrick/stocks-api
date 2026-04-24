from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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


@dataclass(slots=True)
class AlpacaOrderSubmission:
    order: AlpacaSubmittedOrder
    raw_response: dict[str, Any]


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


def coerce_alpaca_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return DATETIME_ADAPTER.validate_python(value)
