import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OrderIntentCreate(BaseModel):
    strategy_id: uuid.UUID | None = None
    signal_id: uuid.UUID | None = None
    underlying_symbol: str = Field(min_length=1, max_length=16)
    option_symbol: str = Field(min_length=1, max_length=64)
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0)
    order_type: Literal["market", "limit"] = "limit"
    limit_price: Decimal | None = Field(default=None, gt=0)
    time_in_force: Literal["day"] = "day"
    rationale: str | None = None
    preview: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_limit_price_for_limit_orders(self) -> "OrderIntentCreate":
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required for limit order intents")
        if self.order_type == "market" and self.limit_price is not None:
            raise ValueError("limit_price is only allowed for limit order intents")
        return self


class OrderIntentPreviewCreate(BaseModel):
    signal_id: uuid.UUID
    option_symbol: str = Field(min_length=1, max_length=64)
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0)
    order_type: Literal["market", "limit"] = "limit"
    limit_price: Decimal | None = Field(default=None, gt=0)
    time_in_force: Literal["day"] = "day"
    rationale: str | None = None
    data_feed: Literal["indicative", "opra"] = "indicative"

    @model_validator(mode="after")
    def reject_limit_price_for_market_orders(self) -> "OrderIntentPreviewCreate":
        if self.order_type == "market" and self.limit_price is not None:
            raise ValueError("limit_price is only allowed for limit order intents")
        return self


class OrderIntentRead(BaseModel):
    id: uuid.UUID
    strategy_id: uuid.UUID | None
    signal_id: uuid.UUID | None
    underlying_symbol: str
    option_symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: Decimal | None
    time_in_force: str
    status: str
    rationale: str | None
    preview: dict[str, Any]
    rejection_reason: str | None
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BrokerOrderRead(BaseModel):
    id: uuid.UUID
    order_intent_id: uuid.UUID | None
    alpaca_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    order_type: str
    limit_price: Decimal | None
    status: str
    submitted_at: datetime | None
    filled_at: datetime | None
    raw_response: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrderIntentSubmissionRead(BaseModel):
    order_intent: OrderIntentRead
    broker_order: BrokerOrderRead
