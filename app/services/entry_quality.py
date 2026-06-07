from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import OrderIntent, Signal, Strategy, TradeCase
from app.services.signal_policy import is_signal_only_scanner_type


FAST_CONFIRMATION_SCANNERS = frozenset(
    {
        "momentum_rate_of_change",
        "vwap_reclaim",
        "opening_range_breakout",
        "moving_average",
    }
)


@dataclass(slots=True)
class EntryQualityDecision:
    allowed: bool
    score: Decimal
    reasons: list[str]
    snapshot: dict[str, Any]


def entry_preview_delay_reason(
    signal: Signal,
    strategy: Strategy,
    *,
    now: datetime | None = None,
) -> str | None:
    if not settings.entry_quality_gate_enabled:
        return None
    if not settings.entry_quality_fast_confirmation_enabled:
        return None

    scanner_type = _scanner_type(strategy)
    if scanner_type not in FAST_CONFIRMATION_SCANNERS:
        return None

    current_time = _utc_now(now)
    created_at = _aware_utc(signal.created_at)
    if created_at is None:
        return None

    timeframe_minutes = _timeframe_minutes(_feature_value(signal, "timeframe") or "5Min")
    required_age = timedelta(minutes=max(timeframe_minutes, 1))
    age = current_time - created_at
    if age >= required_age:
        return None

    return (
        "entry quality confirmation pending: "
        f"{scanner_type} requires one completed {timeframe_minutes} minute bar before preview"
    )


def evaluate_entry_quality(
    db: Session,
    *,
    order_intent: OrderIntent,
    strategy: Strategy,
    signal: Signal | None,
    now: datetime | None = None,
) -> EntryQualityDecision:
    if not settings.entry_quality_gate_enabled:
        return EntryQualityDecision(
            allowed=True,
            score=Decimal("100"),
            reasons=[],
            snapshot={"gate_enabled": False},
        )

    if signal is None:
        return EntryQualityDecision(
            allowed=True,
            score=Decimal("100"),
            reasons=[],
            snapshot={"gate_enabled": True, "entry_signal_found": False},
        )

    scanner_type = _scanner_type(strategy)
    features = _market_features(signal)
    quote = _quote(order_intent)
    selection = _selection(order_intent)
    reasons: list[str] = []
    score = _confidence_score(signal)

    disabled_scanners = _csv_set(settings.entry_quality_disabled_auto_submit_scanners)
    if is_signal_only_scanner_type(scanner_type):
        reasons.append(f"{scanner_type} is signal-only and cannot auto-submit standalone entries")
    elif scanner_type in disabled_scanners:
        reasons.append(f"{scanner_type} is disabled for auto-submit")

    if _recent_stop_loss_exists(db, signal=signal, strategy=strategy, now=now):
        reasons.append("recent same scanner/symbol stop-loss cooldown is active")

    score += _scanner_edge_score(
        scanner_type=scanner_type,
        signal=signal,
        features=features,
        reasons=reasons,
    )
    score += _option_quality_score(
        quote=quote,
        selection=selection,
        reasons=reasons,
    )

    min_score = Decimal(settings.entry_quality_min_score)
    if score < min_score:
        reasons.append(f"entry quality score {score} is below minimum {min_score}")

    snapshot = {
        "gate_enabled": True,
        "scanner_type": scanner_type,
        "score": str(score),
        "minimum_score": str(min_score),
        "signal": {
            "id": str(signal.id),
            "type": signal.signal_type,
            "direction": signal.direction,
            "confidence": str(signal.confidence) if signal.confidence is not None else None,
            "created_at": signal.created_at.isoformat() if signal.created_at else None,
        },
        "features": features,
        "quote": {
            "spread_percent": _string_or_none(_quote_spread_percent(quote)),
            "spread": _string_or_none(_decimal_from_mapping(quote, "spread")),
            "bid": _string_or_none(_decimal_from_mapping(quote, "bid")),
            "ask": _string_or_none(_decimal_from_mapping(quote, "ask")),
        },
        "selection": {
            "open_interest": _selection_open_interest(selection),
            "dte": _selection_dte(selection),
        },
        "reasons": reasons,
    }
    return EntryQualityDecision(
        allowed=not reasons,
        score=score,
        reasons=reasons,
        snapshot=snapshot,
    )


def _scanner_edge_score(
    *,
    scanner_type: str,
    signal: Signal,
    features: dict[str, Any],
    reasons: list[str],
) -> Decimal:
    direction = (signal.direction or "").lower()
    score = Decimal("0")

    if scanner_type == "relative_strength":
        edge = abs(_decimal(features.get("relative_edge_percent")) or Decimal("0"))
        symbol_return = _decimal(features.get("symbol_return_percent"))
        min_edge = Decimal(settings.entry_quality_min_relative_edge_percent)
        if edge < min_edge:
            reasons.append(
                f"relative strength edge {edge} is below auto-submit minimum {min_edge}"
            )
        if direction == "bearish" and (symbol_return is None or symbol_return >= 0):
            reasons.append("bearish relative-strength signal lacks negative absolute return")
        if direction == "bullish" and (symbol_return is None or symbol_return <= 0):
            reasons.append("bullish relative-strength signal lacks positive absolute return")
        return min(edge * Decimal("8"), Decimal("12"))

    if scanner_type == "momentum_rate_of_change":
        pct = abs(_decimal(features.get("percent_change")) or Decimal("0"))
        threshold = _momentum_threshold(features, signal)
        required = threshold * Decimal(settings.entry_quality_min_momentum_threshold_multiplier)
        if pct < required:
            reasons.append(f"momentum move {pct} is too close to threshold {threshold}")
        return min(pct * Decimal("10"), Decimal("10"))

    if scanner_type == "opening_range_breakout":
        distance = _decimal(features.get("distance_percent")) or Decimal("0")
        buffer = _decimal(features.get("breakout_buffer_percent")) or Decimal("0")
        required = buffer * Decimal(settings.entry_quality_min_breakout_buffer_multiplier)
        if distance < required:
            reasons.append(f"opening range distance {distance} is too close to buffer {buffer}")
        return min(distance * Decimal("12"), Decimal("10"))

    if scanner_type == "vwap_reclaim":
        distance = _decimal(features.get("distance_percent")) or Decimal("0")
        required = Decimal(settings.entry_quality_min_vwap_distance_percent)
        if distance < required:
            reasons.append(f"VWAP distance {distance} is below auto-submit minimum {required}")
        return min(distance * Decimal("8"), Decimal("8"))

    if scanner_type == "moving_average":
        separation = _decimal(features.get("average_separation_percent")) or Decimal("0")
        required = Decimal(settings.entry_quality_min_average_separation_percent)
        if separation < required:
            reasons.append(
                f"moving-average separation {separation} is below auto-submit minimum {required}"
            )
        return min(separation * Decimal("10"), Decimal("8"))

    return score


def _option_quality_score(
    *,
    quote: dict[str, Any],
    selection: dict[str, Any],
    reasons: list[str],
) -> Decimal:
    score = Decimal("0")
    spread_percent = _quote_spread_percent(quote)
    max_spread_percent = Decimal(settings.entry_quality_max_option_spread_percent)
    if spread_percent is None:
        return score
    if spread_percent > max_spread_percent:
        reasons.append(
            f"option spread percent {spread_percent} exceeds quality maximum {max_spread_percent}"
        )
    else:
        score += max(Decimal("0"), Decimal("6") - (spread_percent / Decimal("10")))

    open_interest = _selection_open_interest(selection)
    min_open_interest = int(settings.entry_quality_min_open_interest)
    if open_interest is not None and open_interest < min_open_interest:
        reasons.append(
            f"open interest {open_interest} is below quality minimum {min_open_interest}"
        )
    elif open_interest is not None:
        score += Decimal("4")

    return score


def _recent_stop_loss_exists(
    db: Session,
    *,
    signal: Signal,
    strategy: Strategy,
    now: datetime | None,
) -> bool:
    if _scanner_type(strategy) in {"unknown", "price_threshold"}:
        return False
    minutes = int(settings.entry_quality_stop_loss_cooldown_minutes)
    if minutes <= 0:
        return False
    current_time = _utc_now(now)
    since = current_time - timedelta(minutes=minutes)
    underlying = (signal.underlying_symbol or signal.symbol or "").strip().upper()
    if not underlying:
        return False
    if _recent_stop_loss_exit_intent_exists(
        db,
        strategy=strategy,
        underlying=underlying,
        since=since,
    ):
        return True

    statement = (
        select(func.count(TradeCase.id))
        .where(TradeCase.strategy_id == strategy.id)
        .where(func.upper(TradeCase.underlying_symbol) == underlying)
        .where(TradeCase.is_open == False)  # noqa: E712
        .where(TradeCase.exit_time.is_not(None))
        .where(TradeCase.exit_time >= since)
        .where(TradeCase.realized_pl < 0)
    )
    try:
        return int(db.scalar(statement) or 0) > 0
    except Exception:
        return False


def _recent_stop_loss_exit_intent_exists(
    db: Session,
    *,
    strategy: Strategy,
    underlying: str,
    since: datetime,
) -> bool:
    statement = (
        select(OrderIntent)
        .where(OrderIntent.strategy_id == strategy.id)
        .where(func.upper(OrderIntent.underlying_symbol) == underlying)
        .where(func.lower(OrderIntent.side) == "sell")
        .where(OrderIntent.created_at >= since)
        .where(OrderIntent.status.not_in(["canceled", "rejected", "expired", "stale"]))
    )
    try:
        intents = db.scalars(statement)
    except Exception:
        return False
    return any(_is_stop_loss_exit_intent(intent) for intent in intents)


def _is_stop_loss_exit_intent(order_intent: OrderIntent) -> bool:
    if order_intent.status in {"canceled", "rejected", "expired", "stale"}:
        return False
    preview = order_intent.preview if isinstance(order_intent.preview, dict) else {}
    if preview.get("source") != "position_exit_evaluator":
        return False
    trigger_reason = preview.get("trigger_reason")
    text = " ".join(
        str(value)
        for value in (trigger_reason, order_intent.rationale)
        if value is not None
    ).lower()
    return "stop_loss_percent" in text


def _scanner_type(strategy: Strategy) -> str:
    config = strategy.config if isinstance(strategy.config, dict) else {}
    scanner = config.get("scanner") if isinstance(config.get("scanner"), dict) else {}
    return str(scanner.get("type") or "unknown")


def _market_features(signal: Signal) -> dict[str, Any]:
    context = signal.market_context if isinstance(signal.market_context, dict) else {}
    return dict(context)


def _feature_value(signal: Signal, key: str) -> Any:
    return _market_features(signal).get(key)


def _quote(order_intent: OrderIntent) -> dict[str, Any]:
    preview = order_intent.preview if isinstance(order_intent.preview, dict) else {}
    quote = preview.get("quote")
    return quote if isinstance(quote, dict) else {}


def _selection(order_intent: OrderIntent) -> dict[str, Any]:
    preview = order_intent.preview if isinstance(order_intent.preview, dict) else {}
    selection = preview.get("selection")
    return selection if isinstance(selection, dict) else {}


def _confidence_score(signal: Signal) -> Decimal:
    if signal.confidence is None:
        return Decimal("55")
    return Decimal(signal.confidence) * Decimal("100")


def _momentum_threshold(features: dict[str, Any], signal: Signal) -> Decimal:
    if (signal.direction or "").lower() == "bearish":
        return abs(_decimal(features.get("change_below_percent")) or Decimal("0.50"))
    return abs(_decimal(features.get("change_above_percent")) or Decimal("0.50"))


def _quote_spread_percent(quote: dict[str, Any]) -> Decimal | None:
    direct = _decimal_from_mapping(
        quote,
        "spread_percent",
        "spread_pct",
        "bid_ask_spread_percent",
    )
    if direct is not None:
        return direct
    bid = _decimal_from_mapping(quote, "bid", "bid_price")
    ask = _decimal_from_mapping(quote, "ask", "ask_price")
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    midpoint = (bid + ask) / Decimal("2")
    if midpoint <= 0:
        return None
    return ((ask - bid) / midpoint) * Decimal("100")


def _selection_open_interest(selection: dict[str, Any]) -> int | None:
    value = _nested_value(
        selection,
        ("selected_contract", "open_interest"),
        ("contract", "open_interest"),
        ("open_interest",),
    )
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _selection_dte(selection: dict[str, Any]) -> int | None:
    value = _nested_value(
        selection,
        ("selected_contract", "dte"),
        ("contract", "dte"),
        ("dte",),
    )
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decimal_from_mapping(mapping: dict[str, Any], *keys: str) -> Decimal | None:
    for key in keys:
        value = mapping.get(key)
        parsed = _decimal(value)
        if parsed is not None:
            return parsed
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _nested_value(mapping: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current: Any = mapping
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if current is not None:
            return current
    return None


def _timeframe_minutes(value: Any) -> int:
    text = str(value or "5Min").strip().lower()
    try:
        if text.endswith("min"):
            return max(int(text[:-3]), 1)
        if text.endswith("m"):
            return max(int(text[:-1]), 1)
        if text.endswith("hour"):
            return max(int(text[:-4]) * 60, 1)
        if text.endswith("h"):
            return max(int(text[:-1]) * 60, 1)
    except ValueError:
        return 5
    return 5


def _csv_set(value: str) -> set[str]:
    return {
        item.strip().lower()
        for item in str(value or "").split(",")
        if item.strip()
    }


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_now(value: datetime | None) -> datetime:
    return _aware_utc(value) or datetime.now(timezone.utc)


def _string_or_none(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
