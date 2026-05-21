from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from time import perf_counter
from typing import Any
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.utils import current_trading_day_start_utc
from app.db.models import BrokerOrder, JobRun, OrderIntent, Signal, Strategy
from app.services.entry_quality import evaluate_entry_quality
from app.schemas.options import OptionContractSelectionCreate
from app.schemas.order_intents import OrderIntentPreviewCreate
from app.services.automation_guard import can_auto_submit_order_intent
from app.services.audit_logs import record_audit_log
from app.services.broker_reconciliation import reconcile_broker_state
from app.services.news_scanner import scan_market_news
from app.services.order_intents import preview_order_intent_from_signal, submit_order_intent
from app.services.position_exits import evaluate_position_exits
from app.services.signal_scanner import scan_signals

from app.services.market_cycle_submit_config import (
    _submit_config_for_order_intent,
    _trading_day_timezone,
    _validate_submit_limits,
)
from app.services.market_cycle_steps import _elapsed_seconds, _normalize_symbol


def _order_intent_matches_symbol(order_intent: OrderIntent, symbol: str) -> bool:
    intent_underlying = order_intent.underlying_symbol
    if isinstance(intent_underlying, str) and intent_underlying.strip().upper() == symbol:
        return True
    preview = order_intent.preview or {}
    underlying_symbol = preview.get("underlying_symbol") or preview.get("symbol")
    if isinstance(underlying_symbol, str) and underlying_symbol.strip().upper() == symbol:
        return True
    contract = preview.get("selected_contract")
    if isinstance(contract, dict):
        contract_underlying = contract.get("underlying_symbol")
        if isinstance(contract_underlying, str) and contract_underlying.strip().upper() == symbol:
            return True
    return False

def _submit_previewed_order_intents(
    db: Session,
    order_intent_ids: list[uuid.UUID],
    *,
    cycle_id: str | None = None,
    cycle_started: float,
    phase_timeout: int,
    symbol: str | None = None,
) -> dict[str, Any]:
    symbol_filter = _normalize_symbol(symbol)
    deadline = cycle_started + phase_timeout if phase_timeout > 0 else None
    submitted = 0
    rejected = 0
    skipped = 0
    errors: list[str] = []
    broker_order_ids: list[str] = []
    submitted_order_intent_ids: list[str] = []
    skipped_reasons: Counter[str] = Counter()

    orders_submitted_by_strategy: dict[uuid.UUID, int] = {}
    contracts_submitted_by_strategy: dict[uuid.UUID, int] = {}
    contracts_submitted_by_strategy_symbol: dict[tuple[uuid.UUID, str], int] = {}
    for i, order_intent_id in enumerate(order_intent_ids):
        if deadline is not None and perf_counter() >= deadline:
            remaining = len(order_intent_ids) - i
            skipped += remaining
            skipped_reasons["runtime_budget_exceeded"] += remaining
            errors.append(
                f"Skipped {remaining} order intent(s): runtime budget exceeded"
            )
            logger.warning(
                "market_cycle submit loop stopped: budget exceeded after %d/%d order intents",
                i,
                len(order_intent_ids),
            )
            break
        now = datetime.now(timezone.utc)
        order_intent = db.get(OrderIntent, order_intent_id)
        if order_intent is None:
            skipped += 1
            skipped_reasons["not_found"] += 1
            errors.append(f"Order intent '{order_intent_id}' was not found")
            logger.warning(
                "market_cycle submit_candidate_skipped reason=not_found id=%s",
                order_intent_id,
            )
            continue
        if symbol_filter is not None and not _order_intent_matches_symbol(
            order_intent,
            symbol_filter,
        ):
            skipped += 1
            skipped_reasons["symbol_mismatch"] += 1
            errors.append(
                f"Order intent '{order_intent_id}' skipped: symbol does not match {symbol_filter}"
            )
            logger.warning(
                "market_cycle submit_candidate_skipped reason=symbol_mismatch id=%s symbol=%s underlying_symbol=%s",
                order_intent_id,
                symbol_filter,
                order_intent.underlying_symbol,
            )
            continue

        strategy = db.get(Strategy, order_intent.strategy_id) if order_intent.strategy_id else None
        if strategy is None:
            skipped += 1
            skipped_reasons["missing_strategy"] += 1
            errors.append(f"Order intent '{order_intent_id}' has no strategy")
            logger.warning(
                "market_cycle submit_candidate_skipped reason=missing_strategy id=%s strategy_id=%s",
                order_intent_id,
                order_intent.strategy_id,
            )
            continue

        try:
            submit_config = _submit_config_for_order_intent(strategy, order_intent)
            logger.info(
                "market_cycle submit_candidate intent_id=%s strategy_id=%s strategy_name=%s underlying_symbol=%s "
                "option_symbol=%s side=%s status=%s global_submit_enabled=%s strategy_submit_enabled=%s "
                "allowed_sides=%s trade_windows=%s current_time_utc=%s current_time_et=%s",
                order_intent.id,
                strategy.id,
                strategy.name,
                order_intent.underlying_symbol,
                order_intent.option_symbol,
                order_intent.side,
                order_intent.status,
                settings.market_cycle_submit_enabled,
                submit_config.get("enabled"),
                submit_config.get("allowed_sides"),
                submit_config.get("trade_windows"),
                now.isoformat(),
                _current_time_et(now),
            )
            if order_intent.status != "previewed":
                skipped += 1
                skipped_reasons["ineligible_status"] += 1
                message = f"ineligible_status status={order_intent.status}"
                errors.append(f"Order intent '{order_intent_id}': {message}")
                logger.warning(
                    "market_cycle submit_candidate_skipped reason=ineligible_status status=%s id=%s",
                    order_intent.status,
                    order_intent.id,
                )
                continue
            guard_decision = can_auto_submit_order_intent(
                db,
                order_intent,
                cycle_id=cycle_id,
            )
            logger.info(
                "market_cycle submit_guard_decision intent_id=%s strategy_id=%s strategy_name=%s underlying_symbol=%s "
                "option_symbol=%s side=%s status=%s allowed=%s reasons=%s limits_snapshot=%s "
                "trade_windows=%s current_time_utc=%s current_time_et=%s submit_enabled_config=%s allowed_sides=%s",
                order_intent.id,
                strategy.id,
                strategy.name,
                order_intent.underlying_symbol,
                order_intent.option_symbol,
                order_intent.side,
                order_intent.status,
                guard_decision.allowed,
                guard_decision.reasons,
                guard_decision.limits_snapshot,
                submit_config.get("trade_windows"),
                now.isoformat(),
                _current_time_et(now),
                submit_config.get("enabled"),
                submit_config.get("allowed_sides"),
            )
            if not guard_decision.allowed:
                skipped += 1
                reason_key = _skip_reason_key(guard_decision.reasons)
                skipped_reasons[reason_key] += 1
                message = "; ".join(guard_decision.reasons)
                errors.append(f"Order intent '{order_intent_id}': {message}")
                logger.warning(
                    "market_cycle submit_candidate_skipped reason=%s id=%s status=%s",
                    reason_key,
                    order_intent.id,
                    order_intent.status,
                )
                record_audit_log(
                    db,
                    event_type="order_intent.auto_submit_skipped",
                    entity_type="order_intent",
                    entity_id=order_intent.id,
                    message="Auto-submit skipped by automation guard",
                    payload={
                        "order_intent_id": str(order_intent.id),
                        "strategy_id": str(strategy.id),
                        "cycle_id": cycle_id,
                        "reasons": guard_decision.reasons,
                        "limits_snapshot": guard_decision.limits_snapshot,
                    },
                )
                continue
            _validate_submit_limits(
                db,
                order_intent,
                strategy.id,
                submit_config,
                orders_submitted_by_strategy.get(strategy.id, 0),
                contracts_submitted_by_strategy.get(strategy.id, 0),
                contracts_submitted_by_strategy_symbol.get(
                    (strategy.id, order_intent.option_symbol),
                    0,
                ),
                now=now,
            )
            signal = db.get(Signal, order_intent.signal_id) if order_intent.signal_id else None
            quality_decision = evaluate_entry_quality(
                db,
                order_intent=order_intent,
                strategy=strategy,
                signal=signal,
                now=now,
            )
            logger.info(
                "market_cycle submit_quality_decision intent_id=%s strategy_id=%s strategy_name=%s "
                "underlying_symbol=%s option_symbol=%s allowed=%s score=%s reasons=%s snapshot=%s",
                order_intent.id,
                strategy.id,
                strategy.name,
                order_intent.underlying_symbol,
                order_intent.option_symbol,
                quality_decision.allowed,
                quality_decision.score,
                quality_decision.reasons,
                quality_decision.snapshot,
            )
            if not quality_decision.allowed:
                skipped += 1
                reason_key = _skip_reason_key(quality_decision.reasons)
                skipped_reasons[reason_key] += 1
                message = "; ".join(quality_decision.reasons)
                errors.append(f"Order intent '{order_intent_id}': {message}")
                order_intent.status = "rejected"
                order_intent.rejection_reason = f"entry_quality_gate: {message}"
                db.add(order_intent)
                record_audit_log(
                    db,
                    event_type="order_intent.auto_submit_quality_rejected",
                    entity_type="order_intent",
                    entity_id=order_intent.id,
                    message="Auto-submit rejected by entry quality gate",
                    payload={
                        "order_intent_id": str(order_intent.id),
                        "strategy_id": str(strategy.id),
                        "cycle_id": cycle_id,
                        "reasons": quality_decision.reasons,
                        "quality": quality_decision.snapshot,
                    },
                )
                db.commit()
                logger.warning(
                    "market_cycle submit_candidate_skipped reason=%s id=%s status=%s quality_score=%s",
                    reason_key,
                    order_intent.id,
                    order_intent.status,
                    quality_decision.score,
                )
                continue
        except ValueError as exc:
            skipped += 1
            reason_key = _skip_reason_key([str(exc)])
            skipped_reasons[reason_key] += 1
            errors.append(f"Order intent '{order_intent_id}': {exc}")
            logger.warning(
                "market_cycle submit_candidate_skipped reason=%s id=%s status=%s error=%s",
                reason_key,
                order_intent_id,
                getattr(order_intent, "status", None),
                exc,
            )
            continue

        try:
            _, broker_order = submit_order_intent(db, order_intent.id)
        except Exception as exc:
            rejected += 1
            errors.append(f"Order intent '{order_intent_id}': {exc.__class__.__name__}: {exc}")
            logger.error(
                "Order intent submission failed: %s %s: %s",
                order_intent_id,
                exc.__class__.__name__,
                exc,
            )
            record_audit_log(
                db,
                event_type="order_intent.submit_failed",
                entity_type="order_intent",
                entity_id=order_intent.id,
                message="Order intent submission failed during market cycle",
                payload={
                    "order_intent_id": str(order_intent.id),
                    "strategy_id": str(strategy.id),
                    "cycle_id": cycle_id,
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                },
            )
            continue

        submitted += 1
        submitted_order_intent_ids.append(str(order_intent.id))
        orders_submitted_by_strategy[strategy.id] = (
            orders_submitted_by_strategy.get(strategy.id, 0) + 1
        )
        contracts_submitted_by_strategy[strategy.id] = (
            contracts_submitted_by_strategy.get(strategy.id, 0) + order_intent.quantity
        )
        strategy_symbol_key = (strategy.id, order_intent.option_symbol)
        contracts_submitted_by_strategy_symbol[strategy_symbol_key] = (
            contracts_submitted_by_strategy_symbol.get(strategy_symbol_key, 0)
            + order_intent.quantity
        )
        broker_order_ids.append(str(broker_order.id))

    return {
        "status": "completed",
        "symbol": symbol_filter,
        "candidates_seen": len(order_intent_ids),
        "order_intents_seen": len(order_intent_ids),
        "submitted": submitted,
        "rejected": rejected,
        "skipped": skipped,
        "errors": errors,
        "skipped_reasons": dict(skipped_reasons),
        "submitted_order_intent_ids": submitted_order_intent_ids,
        "broker_order_ids": broker_order_ids,
    }


def _order_intent_ids_from_preview(preview: dict[str, Any] | None) -> list[uuid.UUID]:
    if not isinstance(preview, dict):
        return []

    order_intent_ids = []
    raw_values = preview.get("order_intent_ids", [])
    if not raw_values and int(preview.get("previews_created") or 0) > 0:
        logger.warning(
            "market_cycle preview produced previews_created=%s but no order_intent_ids key/value",
            preview.get("previews_created"),
        )

    for value in raw_values:
        try:
            order_intent_ids.append(uuid.UUID(str(value)))
        except ValueError:
            logger.warning(
                "market_cycle ignored invalid preview order_intent_id value=%s",
                value,
            )
            continue
    return order_intent_ids


def _current_time_et(now: datetime) -> str:
    return now.astimezone(ZoneInfo("America/New_York")).isoformat()


def _remaining_budget_seconds(cycle_started: float, phase_timeout: int) -> float | None:
    if phase_timeout <= 0:
        return None
    return max(phase_timeout - _elapsed_seconds(cycle_started), 0.0)


def _skip_reason_key(reasons: list[str]) -> str:
    text = "; ".join(reasons).lower()
    if "outside scanner.submit.trade_windows" in text or "trade_windows" in text:
        return "outside_trade_window"
    if "status" in text and "previewed" in text:
        return "ineligible_status"
    if "trading_automation_enabled" in text:
        return "trading_automation_disabled"
    if "market_cycle_submit_enabled" in text:
        return "submit disabled by global config"
    if "auto_submit_requires_paper" in text:
        return "paper_mode_required"
    if "broker_order" in text:
        return "already_has_broker_order"
    if "max_auto_orders_per_day" in text:
        return "max_auto_orders_per_day"
    if "max_auto_orders_per_symbol_per_day" in text:
        return "max_auto_orders_per_symbol_per_day"
    if "max_auto_orders_per_cycle" in text:
        return "max_auto_orders_per_cycle"
    if "max_open_positions_per_symbol" in text:
        return "max_open_positions_per_symbol"
    if "max_open_positions" in text:
        return "max_open_positions"
    if "max_contracts_per_order" in text:
        return "max_contracts_per_order"
    if "max_estimated_premium_per_order" in text:
        return "max_estimated_premium_per_order"
    if "allowed_sides" in text or "order side is not allowed" in text:
        return "side_not_allowed"
    if "scanner.submit.enabled" in text:
        return "strategy_submit_disabled"
    if "scanner.submit config" in text:
        return "missing_strategy_submit_config"
    if "entry quality" in text or "auto-submit minimum" in text:
        return "entry_quality_gate"
    if "signal-only" in text:
        return "scanner_signal_only"
    if "cooldown" in text:
        return "stop_loss_cooldown"
    if "spread percent" in text or "open interest" in text:
        return "option_quality_gate"
    if "market regime is a filter" in text:
        return "scanner_filter_only"
    return "guard_blocked" if reasons else "unknown"


