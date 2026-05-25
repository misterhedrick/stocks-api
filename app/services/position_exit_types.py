from __future__ import annotations

from dataclasses import dataclass

from typing import Any

import re

import uuid
from decimal import Decimal

from app.db.models import JobRun, OrderIntent, Strategy

@dataclass(slots=True)
class ExitEvaluationResult:
    positions_seen: int
    positions_evaluated: int
    exits_created: int
    exits_skipped: int
    errors: list[str]
    no_exit_reasons: list[str]
    position_ownership: list[dict[str, Any]]
    order_intent_ids: list[uuid.UUID]
    exit_evaluations: list[dict[str, Any]]

@dataclass(slots=True)
class PositionOwnership:
    symbol: str
    managed: bool
    reason: str
    strategy: Strategy | None = None
    strategy_id: uuid.UUID | None = None
    strategy_name: str | None = None
    order_intent_id: uuid.UUID | None = None
    open_quantity: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "managed": self.managed,
            "reason": self.reason,
            "strategy_id": str(self.strategy_id) if self.strategy_id else None,
            "strategy_name": self.strategy_name,
            "order_intent_id": str(self.order_intent_id)
            if self.order_intent_id
            else None,
            "open_quantity": str(self.open_quantity)
            if self.open_quantity is not None
            else None,
        }

@dataclass(slots=True)
class PositionManagementStatus:
    symbol: str
    quantity: str
    market_value: str | None
    cost_basis: str | None
    unrealized_pl: str | None
    captured_at: str
    ownership: dict[str, Any]
    exit_config_enabled: bool
    active_exit_order: dict[str, Any] | None
    recommended_action: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "market_value": self.market_value,
            "cost_basis": self.cost_basis,
            "unrealized_pl": self.unrealized_pl,
            "captured_at": self.captured_at,
            "ownership": self.ownership,
            "exit_config_enabled": self.exit_config_enabled,
            "active_exit_order": self.active_exit_order,
            "recommended_action": self.recommended_action,
            "reason": self.reason,
        }

ACTIVE_EXIT_ORDER_STATUSES = {
    "previewed",
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "submitted",
}

BROKER_ACTIVE_EXIT_ORDER_STATUSES = ACTIVE_EXIT_ORDER_STATUSES - {"previewed"}

ENTRY_BROKER_ORDER_STATUSES = {
    "new",
    "accepted",
    "pending_new",
    "partially_filled",
    "filled",
    "submitted",
}

OPTION_EXPIRATION_PATTERN = re.compile(r"(\d{6})([CP])\d{8}$")
