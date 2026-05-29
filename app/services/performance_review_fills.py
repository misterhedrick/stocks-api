from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
import re
from typing import Any
import uuid

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    BrokerOrder,
    Fill,
    JobRun,
    OptionSelectionDiagnostic,
    OrderIntent,
    PositionSnapshot,
    Signal,
    Strategy,
)

_OPTION_SYMBOL_EXPIRATION_RE = re.compile(r"(\d{6})[CP]\d{8}$")


@dataclass(slots=True)
class FillRecord:
    fill_id: uuid.UUID
    filled_at: datetime
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    strategy_id: uuid.UUID | None
    strategy_name: str | None
    order_intent_id: uuid.UUID | None
    order_intent_side: str | None
    order_intent_rationale: str | None
    order_intent_preview: dict[str, Any]
    signal_id: uuid.UUID | None
    signal_underlying_symbol: str | None
    signal_type: str | None
    signal_direction: str | None
    signal_confidence: Decimal | None
    signal_rationale: str | None
    signal_market_context: dict[str, Any]


def _fill_records(db: Session, *, limit: int) -> list[FillRecord]:
    statement = (
        select(
            Fill.id,
            Fill.filled_at,
            Fill.symbol,
            Fill.side,
            Fill.quantity,
            Fill.price,
            OrderIntent.strategy_id,
            Strategy.name,
            OrderIntent.id,
            OrderIntent.side,
            OrderIntent.rationale,
            OrderIntent.preview,
            Signal.id,
            Signal.underlying_symbol,
            Signal.signal_type,
            Signal.direction,
            Signal.confidence,
            Signal.rationale,
            Signal.market_context,
        )
        .select_from(Fill)
        .join(BrokerOrder, Fill.broker_order_id == BrokerOrder.id, isouter=True)
        .join(OrderIntent, BrokerOrder.order_intent_id == OrderIntent.id, isouter=True)
        .join(Strategy, OrderIntent.strategy_id == Strategy.id, isouter=True)
        .join(Signal, OrderIntent.signal_id == Signal.id, isouter=True)
        .order_by(Fill.filled_at.asc())
        .limit(limit)
    )
    return [
        _coerce_fill_record(row)
        for row in db.execute(statement)
    ]


def _coerce_fill_record(row: object) -> FillRecord:
    values = tuple(row)
    order_intent_preview = values[11] if len(values) > 11 else {}
    signal_market_context = values[18] if len(values) > 18 else {}
    return FillRecord(
        fill_id=values[0],
        filled_at=values[1],
        symbol=str(values[2]),
        side=str(values[3]).lower(),
        quantity=Decimal(str(values[4])),
        price=Decimal(str(values[5])),
        strategy_id=values[6],
        strategy_name=values[7],
        order_intent_id=values[8],
        order_intent_side=values[9] if len(values) > 9 else None,
        order_intent_rationale=values[10] if len(values) > 10 else None,
        order_intent_preview=(
            order_intent_preview if isinstance(order_intent_preview, dict) else {}
        ),
        signal_id=values[12] if len(values) > 12 else None,
        signal_underlying_symbol=values[13] if len(values) > 13 else None,
        signal_type=values[14] if len(values) > 14 else None,
        signal_direction=values[15] if len(values) > 15 else None,
        signal_confidence=values[16] if len(values) > 16 else None,
        signal_rationale=values[17] if len(values) > 17 else None,
        signal_market_context=(
            signal_market_context if isinstance(signal_market_context, dict) else {}
        ),
    )


def _match_round_trips(
    fills: list[FillRecord],
) -> tuple[
    list[dict[str, Any]],
    dict[tuple[str, uuid.UUID | None], list[dict[str, Any]]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    open_lots: dict[tuple[str, uuid.UUID | None], list[dict[str, Any]]] = {}
    round_trips: list[dict[str, Any]] = []
    unmatched_closing_fills: list[dict[str, Any]] = []
    ignored_fills: list[dict[str, Any]] = []

    for fill in fills:
        key = (fill.symbol, fill.strategy_id)
        if fill.side == "buy":
            open_lots.setdefault(key, []).append(
                {
                    "symbol": fill.symbol,
                    "strategy_id": fill.strategy_id,
                    "strategy_name": fill.strategy_name,
                    "quantity": fill.quantity,
                    "remaining_quantity": fill.quantity,
                    "entry_price": fill.price,
                    "entry_fill_id": fill.fill_id,
                    "entry_at": fill.filled_at,
                    "order_intent_id": fill.order_intent_id,
                    "entry_context": _fill_learning_context(fill),
                }
            )
            continue

        if fill.side == "sell_short":
            unmatched_closing_fills.append(
                {
                    **_fill_summary_for_learning(fill),
                    "reason": "sell_short fill is not matched as a long-option exit",
                }
            )
            continue

        if fill.side != "sell":
            ignored_fills.append(
                {
                    **_fill_summary_for_learning(fill),
                    "reason": "unsupported fill side",
                }
            )
            continue

        remaining_sell_quantity = fill.quantity
        lots = open_lots.get(key, [])
        matched_any = False
        while remaining_sell_quantity > 0 and lots:
            matched_any = True
            lot = lots[0]
            matched_quantity = min(remaining_sell_quantity, lot["remaining_quantity"])
            entry_price = Decimal(str(lot["entry_price"]))
            exit_price = fill.price
            multiplier = Decimal("100")
            entry_notional = entry_price * matched_quantity * multiplier
            exit_notional = exit_price * matched_quantity * multiplier
            realized_pnl = exit_notional - entry_notional
            holding_seconds = int(
                (fill.filled_at - lot["entry_at"]).total_seconds()
            )

            round_trips.append(
                {
                    "symbol": fill.symbol,
                    "underlying_symbol": fill.signal_underlying_symbol,
                    "strategy_id": str(fill.strategy_id)
                    if fill.strategy_id is not None
                    else None,
                    "strategy_name": fill.strategy_name,
                    "quantity": _decimal_string(matched_quantity),
                    "entry_price": _decimal_string(entry_price),
                    "exit_price": _decimal_string(exit_price),
                    "entry_notional": _decimal_string(entry_notional),
                    "exit_notional": _decimal_string(exit_notional),
                    "realized_pnl": _decimal_string(realized_pnl),
                    "return_percent": _decimal_string(
                        (realized_pnl / entry_notional * Decimal("100"))
                        if entry_notional != 0
                        else Decimal("0")
                    ),
                    "entry_at": lot["entry_at"].isoformat(),
                    "exit_at": fill.filled_at.isoformat(),
                    "holding_seconds": holding_seconds,
                    "entry_fill_id": str(lot["entry_fill_id"]),
                    "exit_fill_id": str(fill.fill_id),
                    "entry_order_intent_id": str(lot["order_intent_id"])
                    if lot["order_intent_id"] is not None
                    else None,
                    "exit_order_intent_id": str(fill.order_intent_id)
                    if fill.order_intent_id is not None
                    else None,
                    "entry_context": lot["entry_context"],
                    "exit_context": _fill_learning_context(fill),
                }
            )

            lot["remaining_quantity"] -= matched_quantity
            remaining_sell_quantity -= matched_quantity
            if lot["remaining_quantity"] <= 0:
                lots.pop(0)

        if remaining_sell_quantity > 0 or not matched_any:
            unmatched_closing_fills.append(
                {
                    **_fill_summary_for_learning(fill),
                    "unmatched_quantity": _decimal_string(remaining_sell_quantity),
                    "reason": "no open buy lot for symbol and strategy",
                }
            )

    return round_trips, open_lots, unmatched_closing_fills, ignored_fills


def _close_expired_missing_position_lots(
    db: Session,
    open_lots: dict[tuple[str, uuid.UUID | None], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    broker_state = _latest_broker_position_state(db)
    if broker_state is None:
        return []

    active_symbols, closed_at = broker_state
    today = closed_at.date()
    synthetic_round_trips: list[dict[str, Any]] = []

    for key, lots in list(open_lots.items()):
        symbol, strategy_id = key
        expiration_date = _option_expiration_date(symbol)
        if (
            expiration_date is None
            or expiration_date >= today
            or symbol.strip().upper() in active_symbols
        ):
            continue

        for lot in lots:
            remaining_quantity = Decimal(str(lot["remaining_quantity"]))
            if remaining_quantity <= 0:
                continue
            synthetic_round_trips.append(
                _expired_missing_position_round_trip(
                    symbol=symbol,
                    strategy_id=strategy_id,
                    lot=lot,
                    quantity=remaining_quantity,
                    closed_at=closed_at,
                    expiration_date=expiration_date.isoformat(),
                )
            )
        open_lots.pop(key, None)

    return synthetic_round_trips


def _latest_broker_position_state(
    db: Session,
) -> tuple[set[str], datetime] | None:
    if not isinstance(db, Session):
        return None

    latest_reconciliation = db.scalar(
        select(JobRun)
        .where(JobRun.job_name == "reconcile_broker")
        .where(JobRun.status == "succeeded")
        .where(JobRun.finished_at.is_not(None))
        .order_by(JobRun.finished_at.desc())
        .limit(1)
    )
    if (
        latest_reconciliation is not None
        and latest_reconciliation.started_at is not None
        and latest_reconciliation.finished_at is not None
    ):
        symbols = {
            str(symbol).strip().upper()
            for symbol in db.scalars(
                select(PositionSnapshot.symbol)
                .where(PositionSnapshot.captured_at >= latest_reconciliation.started_at)
                .where(PositionSnapshot.captured_at <= latest_reconciliation.finished_at)
                .where(PositionSnapshot.quantity > 0)
            )
        }
        return symbols, latest_reconciliation.finished_at

    latest_captured_at = (
        select(
            PositionSnapshot.symbol.label("symbol"),
            func.max(PositionSnapshot.captured_at).label("captured_at"),
        )
        .group_by(PositionSnapshot.symbol)
        .subquery()
    )
    rows = list(
        db.scalars(
            select(PositionSnapshot)
            .join(
                latest_captured_at,
                and_(
                    PositionSnapshot.symbol == latest_captured_at.c.symbol,
                    PositionSnapshot.captured_at == latest_captured_at.c.captured_at,
                ),
            )
        )
    )
    if not rows:
        return None

    symbols = {
        snapshot.symbol.strip().upper()
        for snapshot in rows
        if snapshot.quantity > 0
    }
    closed_at = max(snapshot.captured_at for snapshot in rows)
    return symbols, closed_at


def _expired_missing_position_round_trip(
    *,
    symbol: str,
    strategy_id: uuid.UUID | None,
    lot: dict[str, Any],
    quantity: Decimal,
    closed_at: datetime,
    expiration_date: str,
) -> dict[str, Any]:
    entry_price = Decimal(str(lot["entry_price"]))
    multiplier = Decimal("100")
    entry_notional = entry_price * quantity * multiplier
    exit_notional = Decimal("0")
    realized_pnl = -entry_notional
    entry_at = lot["entry_at"]
    holding_seconds = int((closed_at - entry_at).total_seconds())
    entry_context = lot.get("entry_context", {})
    signal_context = (
        entry_context.get("signal")
        if isinstance(entry_context.get("signal"), dict)
        else {}
    )
    return {
        "symbol": symbol,
        "underlying_symbol": signal_context.get("underlying_symbol")
        or _underlying_symbol(symbol),
        "strategy_id": str(strategy_id) if strategy_id is not None else None,
        "strategy_name": lot.get("strategy_name"),
        "quantity": _decimal_string(quantity),
        "entry_price": _decimal_string(entry_price),
        "exit_price": "0",
        "entry_notional": _decimal_string(entry_notional),
        "exit_notional": _decimal_string(exit_notional),
        "realized_pnl": _decimal_string(realized_pnl),
        "return_percent": "-100",
        "entry_at": entry_at.isoformat(),
        "exit_at": closed_at.isoformat(),
        "holding_seconds": holding_seconds,
        "entry_fill_id": str(lot["entry_fill_id"]),
        "exit_fill_id": None,
        "entry_order_intent_id": str(lot["order_intent_id"])
        if lot["order_intent_id"] is not None
        else None,
        "exit_order_intent_id": None,
        "entry_context": entry_context,
        "exit_context": {
            "order_intent": {
                "id": None,
                "side": "sell",
                "rationale": "synthetic expiration close at zero after broker position disappeared",
                "preview": {},
            },
            "signal": {},
            "synthetic_close": {
                "reason": "expired_missing_from_broker_positions",
                "expiration_date": expiration_date,
                "broker_position_missing": True,
            },
        },
    }


def _option_expiration_date(symbol: str) -> date | None:
    match = _OPTION_SYMBOL_EXPIRATION_RE.search(symbol)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%y%m%d").date()
    except ValueError:
        return None


def _underlying_symbol(symbol: str) -> str | None:
    match = _OPTION_SYMBOL_EXPIRATION_RE.search(symbol)
    if match is None:
        return None
    underlying = symbol[: match.start()].strip().upper()
    return underlying or None


def _fill_learning_context(fill: FillRecord) -> dict[str, Any]:
    return {
        "order_intent": {
            "id": str(fill.order_intent_id) if fill.order_intent_id is not None else None,
            "side": fill.order_intent_side,
            "rationale": fill.order_intent_rationale,
            "preview": fill.order_intent_preview,
        },
        "signal": {
            "id": str(fill.signal_id) if fill.signal_id is not None else None,
            "underlying_symbol": fill.signal_underlying_symbol,
            "signal_type": fill.signal_type,
            "direction": fill.signal_direction,
            "confidence": _optional_decimal_string(fill.signal_confidence),
            "rationale": fill.signal_rationale,
            "market_context": fill.signal_market_context,
        },
    }


def _fill_summary_for_learning(fill: FillRecord) -> dict[str, Any]:
    return {
        "fill_id": str(fill.fill_id),
        "filled_at": fill.filled_at.isoformat(),
        "symbol": fill.symbol,
        "side": fill.side,
        "quantity": _decimal_string(fill.quantity),
        "price": _decimal_string(fill.price),
        "strategy_id": str(fill.strategy_id) if fill.strategy_id is not None else None,
        "strategy_name": fill.strategy_name,
        "order_intent_id": str(fill.order_intent_id)
        if fill.order_intent_id is not None
        else None,
    }


def _decimal_string(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f")


def _optional_decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return _decimal_string(Decimal(str(value)))


def _open_position_summaries(
    open_lots: dict[tuple[str, uuid.UUID | None], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    summaries = []
    for (symbol, strategy_id), lots in open_lots.items():
        remaining_quantity = sum(
            (Decimal(str(lot["remaining_quantity"])) for lot in lots),
            Decimal("0"),
        )
        if remaining_quantity <= 0:
            continue
        cost_basis = sum(
            Decimal(str(lot["entry_price"]))
            * Decimal(str(lot["remaining_quantity"]))
            * Decimal("100")
            for lot in lots
        )
        strategy_name = next(
            (
                lot.get("strategy_name")
                for lot in lots
                if lot.get("strategy_name") is not None
            ),
            None,
        )
        summaries.append(
            {
                "symbol": symbol,
                "strategy_id": str(strategy_id) if strategy_id is not None else None,
                "strategy_name": strategy_name,
                "open_quantity": _decimal_string(remaining_quantity),
                "cost_basis": _decimal_string(cost_basis),
                "average_entry_price": _decimal_string(
                    cost_basis / remaining_quantity / Decimal("100")
                ),
                "open_lots": len(lots),
            }
        )
    return sorted(
        summaries,
        key=lambda item: (str(item["strategy_name"] or ""), str(item["symbol"])),
    )


def _totals(round_trips: list[dict[str, Any]]) -> dict[str, Any]:
    wins = [
        Decimal(str(item["realized_pnl"]))
        for item in round_trips
        if Decimal(str(item["realized_pnl"])) > 0
    ]
    losses = [
        Decimal(str(item["realized_pnl"]))
        for item in round_trips
        if Decimal(str(item["realized_pnl"])) < 0
    ]
    realized_pnl = sum(
        (Decimal(str(item["realized_pnl"])) for item in round_trips),
        Decimal("0"),
    )
    total_entry_notional = sum(
        (Decimal(str(item["entry_notional"])) for item in round_trips),
        Decimal("0"),
    )
    return {
        "realized_pnl": _decimal_string(realized_pnl),
        "total_entry_notional": _decimal_string(total_entry_notional),
        "return_percent": _decimal_string(
            realized_pnl / total_entry_notional * Decimal("100")
            if total_entry_notional != 0
            else Decimal("0")
        ),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "flat_trades": len(round_trips) - len(wins) - len(losses),
        "win_rate_percent": _decimal_string(
            Decimal(len(wins)) / Decimal(len(round_trips)) * Decimal("100")
            if round_trips
            else Decimal("0")
        ),
        "average_win": _decimal_string(sum(wins, Decimal("0")) / Decimal(len(wins)))
        if wins
        else "0",
        "average_loss": _decimal_string(
            sum(losses, Decimal("0")) / Decimal(len(losses))
        )
        if losses
        else "0",
    }


def _strategy_summaries(round_trips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
    for item in round_trips:
        key = (item.get("strategy_id"), item.get("strategy_name"))
        grouped.setdefault(key, []).append(item)

    summaries = []
    for (strategy_id, strategy_name), items in grouped.items():
        totals = _totals(items)
        summaries.append(
            {
                "strategy_id": strategy_id,
                "strategy_name": strategy_name,
                "matched_round_trips": len(items),
                **totals,
            }
        )
    return sorted(
        summaries,
        key=lambda item: Decimal(str(item["realized_pnl"])),
        reverse=True,
    )


def _symbol_summaries(round_trips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in round_trips:
        grouped.setdefault(str(item.get("symbol")), []).append(item)

    summaries = []
    for symbol, items in grouped.items():
        totals = _totals(items)
        strategy_names = sorted(
            {
                str(item["strategy_name"])
                for item in items
                if item.get("strategy_name") is not None
            }
        )
        summaries.append(
            {
                "symbol": symbol,
                "matched_round_trips": len(items),
                "strategy_names": strategy_names,
                **totals,
            }
        )
    return sorted(
        summaries,
        key=lambda item: Decimal(str(item["realized_pnl"])),
        reverse=True,
    )


