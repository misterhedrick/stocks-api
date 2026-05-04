from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OptionContractSelectionCreate(BaseModel):
    underlying_symbol: str = Field(min_length=1, max_length=16)
    option_type: Literal["call", "put"]
    side: Literal["buy", "sell"] = "buy"
    expiration_date: date | None = None
    expiration_date_gte: date | None = None
    expiration_date_lte: date | None = None
    min_days_to_expiration: int | None = Field(default=None, ge=0)
    max_days_to_expiration: int | None = Field(default=None, ge=0)
    target_strike: Decimal | None = Field(default=None, gt=0)
    underlying_price: Decimal | None = Field(default=None, gt=0)
    max_estimated_notional: Decimal | None = Field(default=None, gt=0)
    max_spread: Decimal | None = Field(default=None, ge=0)
    max_spread_percent: Decimal | None = Field(default=None, ge=0)
    min_open_interest: Decimal | None = Field(default=None, ge=0)
    min_quote_size: Decimal | None = Field(default=None, ge=0)
    preview_profile: str | None = Field(default=None, min_length=1, max_length=64)
    data_feed: Literal["indicative", "opra"] = "indicative"
    limit: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def validate_expiration_filters(self) -> "OptionContractSelectionCreate":
        if self.expiration_date is not None and (
            self.expiration_date_gte is not None
            or self.expiration_date_lte is not None
            or self.min_days_to_expiration is not None
            or self.max_days_to_expiration is not None
        ):
            raise ValueError(
                "expiration_date cannot be combined with expiration date range filters"
            )
        if (
            self.min_days_to_expiration is not None
            or self.max_days_to_expiration is not None
        ) and (
            self.expiration_date_gte is not None
            or self.expiration_date_lte is not None
        ):
            raise ValueError(
                "relative expiration filters cannot be combined with explicit expiration date range filters"
            )
        if (
            self.expiration_date_gte is not None
            and self.expiration_date_lte is not None
            and self.expiration_date_gte > self.expiration_date_lte
        ):
            raise ValueError("expiration_date_gte must be before expiration_date_lte")
        if (
            self.min_days_to_expiration is not None
            and self.max_days_to_expiration is not None
            and self.min_days_to_expiration > self.max_days_to_expiration
        ):
            raise ValueError(
                "min_days_to_expiration must be less than or equal to max_days_to_expiration"
            )
        return self


class OptionContractRead(BaseModel):
    id: str
    symbol: str
    name: str | None
    status: str
    tradable: bool
    expiration_date: date
    root_symbol: str | None
    underlying_symbol: str
    option_type: str
    style: str | None
    strike_price: Decimal
    size: Decimal | None
    open_interest: Decimal | None
    open_interest_date: date | None
    close_price: Decimal | None
    close_price_date: date | None


class OptionContractSelectionRead(BaseModel):
    selected_contract: OptionContractRead
    quote: dict[str, Any]
    selection_reason: str
    candidates_seen: int
    selected_at: datetime

    model_config = ConfigDict(from_attributes=True)
