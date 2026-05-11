from __future__ import annotations

import logging
import uuid
from collections import Counter
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.utils import current_trading_day_start_utc
from app.db.models import OrderIntent, Signal, Strategy
from app.services.market_cycle_steps import _error_category, _normalize_symbol
from app.services.market_cycle_submit import _preview_payload_for_signal
from app.services.market_cycle_submit_config import (
    _submit_config_for_strategy,
    _validate_trade_windows,
)
from app.services.order_intents import preview_order_intent_from_signal

logger = logging.getLogger(__name__)

def _preview_created_signals(
    db: Session,
    signal_ids: list[uuid.UUID],
    *,
    cycle_started: float,
    phase_timeout: int,
    symbol: str | None = None,
) -> dict[str, Any]:
    symbol_filter = _normalize_symbol(symbol)
    deadline = cycle_started + phase_timeout if phase_timeout > 0 else None
    previews_created = 0
    previews_skipped = 0
    errors: list[str] = []
    order_intent_ids: list[str] = []
    skipped_reasons: Counter[str] = Counter()

    for i, signal_id in enumerate(signal_ids):
        if deadline is not None and perf_counter() >= deadline:
            remaining = len(signal_ids) - i
            previews_skipped += remaining
            errors.append(
                f"Skipped {remaining} signal(s): runtime budget exceeded"
            )
            logger.warning(
                "market_cycle preview loop stopped: budget exceeded after %d/%d signals",
                i,
                len(signal_ids),
            )
            break

        signal = db.get(Signal, signal_id)
        if signal is None:
            previews_skipped += 1
            skipped_reasons["not_found"] += 1
            errors.append(f"Signal '{signal_id}' was not found")
            continue
        if symbol_filter is not None and not _signal_matches_symbol(signal, symbol_filter):
            previews_skipped += 1
            skipped_reasons["symbol_mismatch"] += 1
            errors.append(
                f"Signal '{signal_id}' skipped: symbol does not match {symbol_filter}"
            )
            continue

        if _signal_preview_attempts_exhausted(signal):
            previews_skipped += 1
            skipped_reasons["max_preview_attempts"] += 1
            errors.append(
                f"Signal '{signal_id}': max preview attempts reached "
                f"({signal.preview_attempts}/{_options_preview_max_attempts()})"
            )
            _mark_signal_preview_rejected(db, signal)
            continue

        strategy = db.get(Strategy, signal.strategy_id) if signal.strategy_id else None
        if strategy is None:
            previews_skipped += 1
            skipped_reasons["missing_strategy"] += 1
            errors.append(f"Signal '{signal_id}' has no strategy")
            continue

        delay_reason = _entry_preview_delay_reason(strategy)
        if delay_reason is not None:
            previews_skipped += 1
            skipped_reasons["delayed"] += 1
            errors.append(f"Signal '{signal_id}': {delay_reason}")
            continue

        try:
            payload = _preview_payload_for_signal(signal, strategy)
        except ValueError as exc:
            previews_skipped += 1
            skipped_reasons["invalid_preview_config"] += 1
            errors.append(f"Signal '{signal_id}': {exc}")
            continue

        try:
            order_intent = preview_order_intent_from_signal(db, payload, deadline=deadline)
        except Exception as exc:
            previews_skipped += 1
            skipped_reasons[_preview_failure_reason_key(exc)] += 1
            _record_signal_preview_failure(db, signal, exc)
            errors.append(f"Signal '{signal_id}': {exc.__class__.__name__}: {exc}")
            continue

        previews_created += 1
        order_intent_ids.append(str(order_intent.id))
        logger.info(
            "market_cycle preview_created intent_id=%s signal_id=%s ticker=%s option_symbol=%s",
            order_intent.id,
            signal.id,
            order_intent.underlying_symbol,
            order_intent.option_symbol,
        )

    return {
        "status": "completed",
        "symbol": symbol_filter,
        "signals_seen": len(signal_ids),
        "previews_created": previews_created,
        "previews_skipped": previews_skipped,
        "errors": errors,
        "skipped_reasons": dict(skipped_reasons),
        "order_intent_ids": order_intent_ids,
    }


def _record_signal_preview_failure(db: Session, signal: Signal, exc: Exception) -> None:
    now = datetime.now(timezone.utc)
    diagnostics = getattr(exc, "diagnostics", None)
    reason_counts = {}
    if isinstance(diagnostics, dict) and isinstance(diagnostics.get("reason_counts"), dict):
        reason_counts = dict(diagnostics["reason_counts"])

    signal.preview_attempts = int(signal.preview_attempts or 0) + 1
    signal.last_previewed_at = now
    signal.last_preview_error = _concise_error_message(exc)
    signal.last_preview_error_code = exc.__class__.__name__
    signal.preview_rejection_reasons = reason_counts or None

    if _signal_preview_attempts_exhausted(signal):
        signal.status = "preview_rejected"
        signal.rejected_reason = signal.last_preview_error
        logger.info(
            "market_cycle signal preview rejected after max attempts: signal_id=%s attempts=%d error_code=%s reasons=%s",
            signal.id,
            signal.preview_attempts,
            signal.last_preview_error_code,
            reason_counts,
        )

    db.add(signal)
    db.commit()


def _mark_signal_preview_rejected(db: Session, signal: Signal) -> None:
    if signal.status != "preview_rejected":
        signal.status = "preview_rejected"
        if not signal.rejected_reason:
            signal.rejected_reason = (
                f"Max preview attempts reached ({signal.preview_attempts}/{_options_preview_max_attempts()})"
            )
        db.add(signal)
        db.commit()


def _signal_preview_attempts_exhausted(signal: Signal) -> bool:
    return int(signal.preview_attempts or 0) >= _options_preview_max_attempts()


def _options_preview_max_attempts() -> int:
    try:
        return max(int(settings.options_preview_max_attempts), 1)
    except (TypeError, ValueError):
        return 3


def _preview_failure_reason_key(exc: Exception) -> str:
    if exc.__class__.__name__ == "OptionContractNotFoundError":
        return "option_contract_not_found"
    return _error_category(str(exc))


def _concise_error_message(exc: Exception, *, max_length: int = 500) -> str:
    text = f"{exc.__class__.__name__}: {exc}"
    text = " ".join(text.split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def _entry_preview_delay_reason(strategy: Strategy) -> str | None:
    if not settings.market_cycle_submit_enabled:
        return None

    try:
        submit_config = _submit_config_for_strategy(strategy)
    except ValueError:
        return None

    try:
        _validate_trade_windows(submit_config, now=datetime.now(timezone.utc))
    except ValueError as exc:
        return f"auto-preview delayed until scanner.submit.trade_windows opens: {exc}"
    return None


def _signal_ids_for_preview(
    db: Session,
    created_signal_ids: list[uuid.UUID],
    *,
    limit: int,
    symbol: str | None = None,
) -> list[uuid.UUID]:
    symbol_filter = _normalize_symbol(symbol)
    signal_ids = list(created_signal_ids)
    seen = set(signal_ids)
    pending_limit = max(limit - len(signal_ids), 0)
    if pending_limit == 0:
        return signal_ids

    has_order_intent = (
        select(OrderIntent.id)
        .where(OrderIntent.signal_id == Signal.id)
        .exists()
    )
    pending_statement = (
        select(Signal.id)
        .where(Signal.status == "new")
        .where(Signal.preview_attempts < _options_preview_max_attempts())
        .where(~has_order_intent)
        .where(Signal.created_at >= current_trading_day_start_utc())
        .order_by(Signal.created_at.asc())
        .limit(pending_limit)
    )
    if symbol_filter is not None:
        pending_statement = pending_statement.where(_signal_symbol_clause(symbol_filter))
    pending_signal_ids = db.scalars(pending_statement)
    for signal_id in pending_signal_ids:
        if signal_id not in seen:
            signal_ids.append(signal_id)
            seen.add(signal_id)
    return signal_ids


def _signal_symbol_clause(symbol: str):
    return or_(
        func.upper(Signal.symbol) == symbol,
        func.upper(Signal.underlying_symbol) == symbol,
    )


def _signal_matches_symbol(signal: Signal, symbol: str) -> bool:
    return any(
        isinstance(value, str) and value.strip().upper() == symbol
        for value in (signal.symbol, signal.underlying_symbol)
    )


def _order_intent_matches_symbol(order_intent: OrderIntent, symbol: str) -> bool:
    return isinstance(order_intent.underlying_symbol, str) and (
        order_intent.underlying_symbol.strip().upper() == symbol
    )


