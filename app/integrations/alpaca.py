from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter

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

        raw_response = self._parse_json_response(response)

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

    def _parse_json_response(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {"message": response.text.strip() or "Empty response from Alpaca"}

        if isinstance(payload, dict):
            return payload

        return {"message": str(payload)}

    def _extract_error_detail(self, payload: dict[str, Any]) -> str:
        for key in ("message", "detail", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        return "Alpaca rejected the order submission"


def coerce_alpaca_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return DATETIME_ADAPTER.validate_python(value)
