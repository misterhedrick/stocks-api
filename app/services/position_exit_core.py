from __future__ import annotations

import logging

from datetime import datetime

from typing import Any

import uuid

from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.integrations.alpaca import AlpacaMarketDataClient

from app.services.position_exit_lookup import (
    _exit_config_for_strategy,
    _has_active_exit_order,
    _latest_active_exit_order,
    _latest_position_snapshots,
    resolve_position_ownership,
)

from app.services.position_exit_orders import _create_exit_order_intent

from app.services.position_exit_rules import (
    _default_unmanaged_exit_config,
    _entry_fill_time,
    _exit_rule_diagnostics,
    _exit_trigger_reason,
    _peak_unrealized_pl_percent,
    _position_recommendation,
)

from app.services.position_exit_types import ExitEvaluationResult, PositionManagementStatus

logger = logging.getLogger("app.services.position_exits")

def evaluate_position_exits(
    db: Session,
    *,
    limit: int = 100,
    market_data_client: AlpacaMarketDataClient | None = None,
) -> ExitEvaluationResult:
    positions = _latest_position_snapshots(db, limit=limit)
    client = market_data_client or AlpacaMarketDataClient.from_settings()

    positions_evaluated = 0
    exits_created = 0
    exits_skipped = 0
    errors: list[str] = []
    no_exit_reasons: list[str] = []
    position_ownership: list[dict[str, Any]] = []
    order_intent_ids: list[uuid.UUID] = []
    exit_evaluations: list[dict[str, Any]] = []
    today = datetime.now(ZoneInfo("America/New_York")).date()

    for position in positions:
        evaluation = _base_exit_evaluation(position)
        exit_evaluations.append(evaluation)
        if position.quantity <= 0:
            reason = f"{position.symbol}: quantity is not long"
            no_exit_reasons.append(reason)
            evaluation.update({"action": "skip", "reason": reason})
            continue

        ownership = resolve_position_ownership(db, position)
        ownership_payload = ownership.as_dict()
        position_ownership.append(ownership_payload)
        evaluation["ownership"] = ownership_payload
        if not ownership.managed or ownership.strategy is None:
            reason = f"{position.symbol}: {ownership.reason}"
            no_exit_reasons.append(reason)
            evaluation.update({"action": "skip", "reason": reason})
            continue

        strategy = ownership.strategy
        exit_config = _exit_config_for_strategy(strategy)
        if exit_config is None:
            reason = (
                f"{position.symbol}: linked strategy '{strategy.name}' scanner.exit is not enabled"
            )
            no_exit_reasons.append(reason)
            evaluation.update({"action": "skip", "reason": reason})
            continue

        positions_evaluated += 1
        entry_time = _entry_fill_time(db, ownership)
        peak_unrealized_pl_percent = _peak_unrealized_pl_percent(
            db,
            position,
            entry_time=entry_time,
        )
        evaluation["rule_diagnostics"] = _exit_rule_diagnostics(
            position,
            exit_config,
            today=today,
            entry_time=entry_time,
            peak_unrealized_pl_percent=peak_unrealized_pl_percent,
        )
        trigger_reason = _exit_trigger_reason(
            position,
            exit_config,
            today=today,
            entry_time=entry_time,
            peak_unrealized_pl_percent=peak_unrealized_pl_percent,
        )
        evaluation["trigger_reason"] = trigger_reason
        if trigger_reason is None:
            reason = f"{position.symbol}: no exit rule triggered"
            no_exit_reasons.append(reason)
            evaluation.update({"action": "hold", "reason": reason})
            continue

        active_exit_order = _latest_active_exit_order(db, position.symbol)
        if active_exit_order is not None:
            exits_skipped += 1
            active_exit_id = active_exit_order.get("order_intent_id")
            if active_exit_order.get("status") == "previewed" and active_exit_id:
                order_intent_ids.append(uuid.UUID(str(active_exit_id)))
                reason = (
                    f"{position.symbol}: previewed exit order already exists and will be submitted"
                )
                evaluation.update(
                    {
                        "action": "exit_pending_submit",
                        "reason": reason,
                        "order_intent_id": str(active_exit_id),
                    }
                )
                continue
            reason = f"{position.symbol}: active exit order already exists"
            no_exit_reasons.append(reason)
            evaluation.update({"action": "exit_pending", "reason": reason})
            continue

        try:
            order_intent = _create_exit_order_intent(
                db,
                position,
                strategy,
                exit_config,
                trigger_reason=trigger_reason,
                market_data_client=client,
                max_quantity=ownership.open_quantity,
            )
        except Exception as exc:
            exits_skipped += 1
            error = f"{position.symbol}: {exc.__class__.__name__}: {exc}"
            errors.append(error)
            evaluation.update(
                {
                    "action": "error",
                    "reason": error,
                    "error_type": exc.__class__.__name__,
                }
            )
            logger.warning(
                "Exit order creation failed for %s: %s: %s",
                position.symbol,
                exc.__class__.__name__,
                exc,
            )
            continue

        exits_created += 1
        order_intent_ids.append(order_intent.id)
        evaluation.update(
            {
                "action": "exit_previewed",
                "reason": trigger_reason,
                "order_intent_id": str(order_intent.id),
                "order_type": order_intent.order_type,
                "limit_price": str(order_intent.limit_price)
                if order_intent.limit_price is not None
                else None,
            }
        )

    return ExitEvaluationResult(
        positions_seen=len(positions),
        positions_evaluated=positions_evaluated,
        exits_created=exits_created,
        exits_skipped=exits_skipped,
        errors=errors,
        no_exit_reasons=no_exit_reasons,
        position_ownership=position_ownership,
        order_intent_ids=order_intent_ids,
        exit_evaluations=exit_evaluations,
    )

def preview_unmanaged_position_exits(
    db: Session,
    *,
    symbol: str | None = None,
    limit: int = 100,
    market_data_client: AlpacaMarketDataClient | None = None,
) -> ExitEvaluationResult:
    positions = _latest_position_snapshots(db, limit=limit)
    if symbol is not None:
        normalized_symbol = symbol.strip().upper()
        positions = [
            position
            for position in positions
            if position.symbol.upper() == normalized_symbol
        ]

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    positions_evaluated = 0
    exits_created = 0
    exits_skipped = 0
    errors: list[str] = []
    no_exit_reasons: list[str] = []
    position_ownership: list[dict[str, Any]] = []
    order_intent_ids: list[uuid.UUID] = []
    exit_evaluations: list[dict[str, Any]] = []

    for position in positions:
        evaluation = _base_exit_evaluation(position)
        exit_evaluations.append(evaluation)
        if position.quantity <= 0:
            reason = f"{position.symbol}: quantity is not long"
            no_exit_reasons.append(reason)
            evaluation.update({"action": "skip", "reason": reason})
            continue

        ownership = resolve_position_ownership(db, position)
        ownership_payload = ownership.as_dict()
        position_ownership.append(ownership_payload)
        evaluation["ownership"] = ownership_payload
        if ownership.managed:
            reason = f"{position.symbol}: position is already managed"
            no_exit_reasons.append(reason)
            evaluation.update({"action": "skip", "reason": reason})
            continue

        positions_evaluated += 1
        if _has_active_exit_order(db, position.symbol):
            exits_skipped += 1
            reason = f"{position.symbol}: active exit order already exists"
            no_exit_reasons.append(reason)
            evaluation.update({"action": "exit_pending", "reason": reason})
            continue

        try:
            order_intent = _create_exit_order_intent(
                db,
                position,
                None,
                _default_unmanaged_exit_config(),
                trigger_reason=f"manual unmanaged exit preview: {ownership.reason}",
                market_data_client=client,
            )
        except Exception as exc:
            exits_skipped += 1
            error = f"{position.symbol}: {exc.__class__.__name__}: {exc}"
            errors.append(error)
            evaluation.update(
                {
                    "action": "error",
                    "reason": error,
                    "error_type": exc.__class__.__name__,
                }
            )
            continue

        exits_created += 1
        order_intent_ids.append(order_intent.id)
        evaluation.update(
            {
                "action": "exit_previewed",
                "reason": order_intent.preview.get("trigger_reason"),
                "order_intent_id": str(order_intent.id),
                "order_type": order_intent.order_type,
                "limit_price": str(order_intent.limit_price)
                if order_intent.limit_price is not None
                else None,
            }
        )

    return ExitEvaluationResult(
        positions_seen=len(positions),
        positions_evaluated=positions_evaluated,
        exits_created=exits_created,
        exits_skipped=exits_skipped,
        errors=errors,
        no_exit_reasons=no_exit_reasons,
        position_ownership=position_ownership,
        order_intent_ids=order_intent_ids,
        exit_evaluations=exit_evaluations,
    )

def get_position_management_statuses(
    db: Session,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for position in _latest_position_snapshots(db, limit=limit):
        ownership = resolve_position_ownership(db, position)
        exit_config = (
            _exit_config_for_strategy(ownership.strategy)
            if ownership.strategy is not None
            else None
        )
        active_exit_order = _latest_active_exit_order(db, position.symbol)
        recommended_action, reason = _position_recommendation(
            db,
            position,
            ownership,
            exit_config,
            active_exit_order,
        )
        statuses.append(
            PositionManagementStatus(
                symbol=position.symbol,
                quantity=str(position.quantity),
                market_value=str(position.market_value)
                if position.market_value is not None
                else None,
                cost_basis=str(position.cost_basis)
                if position.cost_basis is not None
                else None,
                unrealized_pl=str(position.unrealized_pl)
                if position.unrealized_pl is not None
                else None,
                captured_at=position.captured_at.isoformat(),
                ownership=ownership.as_dict(),
                exit_config_enabled=exit_config is not None,
                active_exit_order=active_exit_order,
                recommended_action=recommended_action,
                reason=reason,
            ).as_dict()
        )
    return statuses


def _base_exit_evaluation(position: object) -> dict[str, Any]:
    return {
        "symbol": position.symbol,
        "quantity": str(position.quantity),
        "market_value": str(position.market_value)
        if position.market_value is not None
        else None,
        "cost_basis": str(position.cost_basis)
        if position.cost_basis is not None
        else None,
        "unrealized_pl": str(position.unrealized_pl)
        if position.unrealized_pl is not None
        else None,
        "captured_at": position.captured_at.isoformat(),
    }
