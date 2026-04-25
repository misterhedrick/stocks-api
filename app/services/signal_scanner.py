from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobRun, Signal, Strategy
from app.integrations.alpaca import AlpacaLatestStockQuote, AlpacaMarketDataClient
from app.services.audit_logs import record_audit_log

DEFAULT_DEDUPE_MINUTES = 240
DEDUPE_STATUSES = ("new", "previewed", "submitted")


@dataclass(slots=True)
class SignalScanResult:
    job_run: JobRun
    strategies_seen: int
    strategies_scanned: int
    signals_created: int
    signals_skipped: int
    errors: list[str]
    created_signal_ids: list[uuid.UUID]


def scan_signals(
    db: Session,
    *,
    limit: int = 100,
    market_data_client: AlpacaMarketDataClient | None = None,
) -> SignalScanResult:
    started_at = datetime.now(timezone.utc)
    job_run = JobRun(
        job_name="scan_signals",
        status="running",
        started_at=started_at,
        details={},
    )
    db.add(job_run)
    db.flush()

    try:
        strategies = list(
            db.scalars(
                select(Strategy)
                .where(Strategy.is_active == True)  # noqa: E712
                .order_by(Strategy.created_at.asc())
                .limit(limit)
            )
        )

        strategies_scanned = 0
        signals_created = 0
        signals_skipped = 0
        created_signal_ids: list[uuid.UUID] = []
        errors: list[str] = []

        for strategy in strategies:
            signal_specs = _signal_specs_from_strategy(strategy)
            try:
                signal_specs.extend(
                    _signal_specs_from_scanner(
                        strategy,
                        market_data_client=market_data_client,
                    )
                )
            except ValueError as exc:
                signals_skipped += 1
                errors.append(f"{strategy.name}.scanner: {exc}")
            except Exception as exc:
                signals_skipped += 1
                errors.append(f"{strategy.name}.scanner: {exc.__class__.__name__}: {exc}")

            if not signal_specs:
                continue

            strategies_scanned += 1
            for index, signal_spec in enumerate(signal_specs):
                try:
                    signal = _signal_from_spec(strategy, signal_spec)
                except ValueError as exc:
                    signals_skipped += 1
                    errors.append(f"{strategy.name}[{index}]: {exc}")
                    continue

                if _has_recent_duplicate_signal(db, signal, signal_spec):
                    signals_skipped += 1
                    errors.append(
                        f"{strategy.name}[{index}]: duplicate signal suppressed for "
                        f"{signal.symbol} {signal.signal_type} {signal.direction}"
                    )
                    continue

                db.add(signal)
                db.flush()
                record_audit_log(
                    db,
                    event_type="signal.created",
                    entity_type="signal",
                    entity_id=signal.id,
                    message="Signal created by scanner",
                    payload={
                        "strategy_id": str(strategy.id),
                        "strategy_name": strategy.name,
                        "symbol": signal.symbol,
                        "underlying_symbol": signal.underlying_symbol,
                        "signal_type": signal.signal_type,
                        "direction": signal.direction,
                        "confidence": str(signal.confidence)
                        if signal.confidence is not None
                        else None,
                        "source": "scan_signals",
                    },
                )
                signals_created += 1
                created_signal_ids.append(signal.id)

        details = {
            "strategies_seen": len(strategies),
            "strategies_scanned": strategies_scanned,
            "signals_created": signals_created,
            "signals_skipped": signals_skipped,
            "errors": errors,
            "created_signal_ids": [str(signal_id) for signal_id in created_signal_ids],
        }
        job_run.status = "succeeded"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = details
        job_run.error = None
        db.add(job_run)
        record_audit_log(
            db,
            event_type="signal_scan.succeeded",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Signal scan succeeded",
            payload=details,
        )
        db.commit()
        db.refresh(job_run)

        return SignalScanResult(
            job_run=job_run,
            strategies_seen=len(strategies),
            strategies_scanned=strategies_scanned,
            signals_created=signals_created,
            signals_skipped=signals_skipped,
            errors=errors,
            created_signal_ids=created_signal_ids,
        )
    except Exception as exc:
        db.rollback()
        job_run.status = "failed"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = {}
        job_run.error = f"{exc.__class__.__name__}: {exc}"
        db.add(job_run)
        record_audit_log(
            db,
            event_type="signal_scan.failed",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Signal scan failed",
            payload={"error": job_run.error},
        )
        db.commit()
        db.refresh(job_run)
        raise


def _signal_specs_from_strategy(strategy: Strategy) -> list[dict[str, Any]]:
    signal_specs = strategy.config.get("scan_signals")
    if isinstance(signal_specs, list):
        return [item for item in signal_specs if isinstance(item, dict)]
    return []


def _signal_specs_from_scanner(
    strategy: Strategy,
    *,
    market_data_client: AlpacaMarketDataClient | None,
) -> list[dict[str, Any]]:
    scanner_config = strategy.config.get("scanner")
    if scanner_config is None:
        return []
    if not isinstance(scanner_config, dict):
        raise ValueError("scanner must be an object")

    scanner_type = scanner_config.get("type")
    if scanner_type != "price_threshold":
        raise ValueError("scanner.type must be price_threshold")

    symbols = scanner_config.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("scanner.symbols must be a non-empty list")

    clean_symbols = []
    for symbol in symbols:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("scanner.symbols must contain only non-empty strings")
        clean_symbols.append(symbol.strip().upper())

    price_above = _optional_decimal(scanner_config, "price_above")
    price_below = _optional_decimal(scanner_config, "price_below")
    if price_above is None and price_below is None:
        raise ValueError("scanner requires price_above or price_below")

    feed = scanner_config.get("data_feed", "iex")
    if not isinstance(feed, str) or not feed.strip():
        raise ValueError("scanner.data_feed must be a non-empty string")

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    quotes = client.get_latest_stock_quotes(clean_symbols, feed=feed.strip())
    signal_specs: list[dict[str, Any]] = []

    for symbol in clean_symbols:
        latest_quote = quotes.get(symbol)
        if latest_quote is None:
            continue

        price = _price_from_quote(latest_quote)
        if price is None:
            continue

        triggered = (
            (price_above is not None and price >= price_above)
            or (price_below is not None and price <= price_below)
        )
        if not triggered:
            continue

        signal_specs.append(
            {
                "symbol": symbol,
                "underlying_symbol": symbol,
                "signal_type": _scanner_string(
                    scanner_config,
                    "signal_type",
                    default="price_threshold",
                ),
                "direction": _scanner_string(
                    scanner_config,
                    "direction",
                    default="bullish" if price_above is not None else "bearish",
                ),
                "confidence": scanner_config.get("confidence"),
                "rationale": _scanner_string(
                    scanner_config,
                    "rationale",
                    default="Price threshold scanner triggered",
                ),
                "market_context": {
                    "source": "scanner.price_threshold",
                    "price": str(price),
                    "price_above": str(price_above) if price_above is not None else None,
                    "price_below": str(price_below) if price_below is not None else None,
                    "data_feed": feed.strip(),
                    "quote": _stock_quote_context(latest_quote),
                },
                "dedupe_minutes": scanner_config.get(
                    "dedupe_minutes",
                    DEFAULT_DEDUPE_MINUTES,
                ),
            }
        )

    return signal_specs


def _signal_from_spec(strategy: Strategy, signal_spec: dict[str, Any]) -> Signal:
    symbol = _required_string(signal_spec, "symbol")
    signal_type = _required_string(signal_spec, "signal_type")
    direction = _required_string(signal_spec, "direction")

    return Signal(
        strategy_id=strategy.id,
        symbol=symbol,
        underlying_symbol=_optional_string(signal_spec, "underlying_symbol"),
        signal_type=signal_type,
        direction=direction,
        confidence=_optional_confidence(signal_spec),
        rationale=_optional_string(signal_spec, "rationale"),
        market_context=signal_spec.get("market_context")
        if isinstance(signal_spec.get("market_context"), dict)
        else {},
        status="new",
    )


def _required_string(signal_spec: dict[str, Any], key: str) -> str:
    value = signal_spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _optional_string(signal_spec: dict[str, Any], key: str) -> str | None:
    value = signal_spec.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_confidence(signal_spec: dict[str, Any]) -> Decimal | None:
    value = signal_spec.get("confidence")
    if value is None:
        return None
    try:
        confidence = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("confidence must be a decimal between 0 and 1") from exc
    if confidence < Decimal("0") or confidence > Decimal("1"):
        raise ValueError("confidence must be between 0 and 1")
    return confidence


def _has_recent_duplicate_signal(
    db: Session,
    signal: Signal,
    signal_spec: dict[str, Any],
) -> bool:
    dedupe_minutes = _dedupe_minutes(signal_spec)
    if dedupe_minutes <= 0:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=dedupe_minutes)
    statement = (
        select(Signal)
        .where(Signal.strategy_id == signal.strategy_id)
        .where(Signal.symbol == signal.symbol)
        .where(Signal.signal_type == signal.signal_type)
        .where(Signal.direction == signal.direction)
        .where(Signal.status.in_(DEDUPE_STATUSES))
        .where(Signal.created_at >= cutoff)
        .limit(1)
    )
    return db.scalar(statement) is not None


def _dedupe_minutes(signal_spec: dict[str, Any]) -> int:
    value = signal_spec.get("dedupe_minutes", DEFAULT_DEDUPE_MINUTES)
    try:
        dedupe_minutes = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("dedupe_minutes must be an integer") from exc
    if dedupe_minutes < 0:
        raise ValueError("dedupe_minutes must be greater than or equal to 0")
    return dedupe_minutes


def _optional_decimal(config: dict[str, Any], key: str) -> Decimal | None:
    value = config.get(key)
    if value is None:
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"scanner.{key} must be a decimal") from exc
    return decimal_value


def _scanner_string(
    scanner_config: dict[str, Any],
    key: str,
    *,
    default: str,
) -> str:
    value = scanner_config.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"scanner.{key} must be a non-empty string")
    return value.strip()


def _price_from_quote(latest_quote: AlpacaLatestStockQuote) -> Decimal | None:
    bid_price = latest_quote.quote.bid_price
    ask_price = latest_quote.quote.ask_price
    if bid_price is not None and ask_price is not None:
        return (bid_price + ask_price) / Decimal("2")
    return ask_price or bid_price


def _stock_quote_context(latest_quote: AlpacaLatestStockQuote) -> dict[str, object]:
    quote = latest_quote.quote
    return {
        "symbol": latest_quote.symbol,
        "bid_price": str(quote.bid_price) if quote.bid_price is not None else None,
        "bid_size": str(quote.bid_size) if quote.bid_size is not None else None,
        "ask_price": str(quote.ask_price) if quote.ask_price is not None else None,
        "ask_size": str(quote.ask_size) if quote.ask_size is not None else None,
        "quote_timestamp": quote.timestamp.isoformat()
        if quote.timestamp is not None
        else None,
        "raw_quote": latest_quote.raw_response,
    }
