import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SignalCreate(BaseModel):
    strategy_id: uuid.UUID | None = None
    symbol: str = Field(min_length=1, max_length=32)
    underlying_symbol: str | None = Field(default=None, min_length=1, max_length=16)
    signal_type: str = Field(min_length=1, max_length=50)
    direction: str = Field(min_length=1, max_length=20)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    rationale: str | None = None
    market_context: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="new", min_length=1, max_length=30)
    rejected_reason: str | None = None


class SignalUpdate(BaseModel):
    strategy_id: uuid.UUID | None = None
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    underlying_symbol: str | None = Field(default=None, min_length=1, max_length=16)
    signal_type: str | None = Field(default=None, min_length=1, max_length=50)
    direction: str | None = Field(default=None, min_length=1, max_length=20)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    rationale: str | None = None
    market_context: dict[str, Any] | None = None
    status: str | None = Field(default=None, min_length=1, max_length=30)
    rejected_reason: str | None = None


class SignalRead(BaseModel):
    id: uuid.UUID
    strategy_id: uuid.UUID | None
    symbol: str
    underlying_symbol: str | None
    signal_type: str
    direction: str
    confidence: Decimal | None
    rationale: str | None
    market_context: dict[str, Any]
    status: str
    rejected_reason: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
