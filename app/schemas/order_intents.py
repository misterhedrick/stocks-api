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
    time_in_force: Literal["day", "gtc"] = "day"
    rationale: str | None = None
    preview: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_limit_price_for_limit_orders(self) -> "OrderIntentCreate":
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required for limit order intents")
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
