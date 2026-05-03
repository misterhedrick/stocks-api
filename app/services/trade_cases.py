from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobRun, TradeCase
from app.services.audit_logs import record_audit_log
from app.services.performance_review import _fill_records, _match_round_trips

logger = logging.getLogger(__name__)

# Matches the 6-digit expiration date in OCC option symbols like "SPY271219C00500000".
# Everything before the match is the underlying ticker.
_OPTION_SYMBOL_RE = re.compile(r"(\d{6})[CP]\d{8}$")


@dataclass(slots=True)
class TradeCasePopulationResult:
    job_run: JobRun
    round_trips_seen: int
    inserted: int
    updated: int
    skipped: int
    errors: list[str] = field(default_factory=list)


def populate_trade_cases_from_closed_round_trips(
    db: Session,
    *,
    limit: int = 100,
) -> TradeCasePopulationResult:
    started_at = datetime.now(timezone.utc)
    job_run = JobRun(
        job_name="populate_trade_cases",
        status="running",
        started_at=started_at,
        details={},
    )
    db.add(job_run)
    db.flush()

    try:
        fill_records = _fill_records(db, limit=limit)
        round_trips, _open_lots, _unmatched, _ignored = _match_round_trips(fill_records)

        inserted, updated, skipped, errors = _upsert_round_trips(db, round_trips)

        details: dict[str, Any] = {
            "round_trips_seen": len(round_trips),
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
        }
        job_run.status = "succeeded"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = details
        logger.info(
            "Trade case population succeeded: seen=%d inserted=%d updated=%d skipped=%d",
            len(round_trips),
            inserted,
            updated,
            skipped,
        )
        record_audit_log(
            db,
            event_type="trade_cases.population_succeeded",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Trade case population succeeded",
            payload=details,
        )
        db.commit()
        db.refresh(job_run)

        return TradeCasePopulationResult(
            job_run=job_run,
            round_trips_seen=len(round_trips),
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            errors=errors,
        )

    except Exception as exc:
        logger.error(
            "Trade case population failed: %s: %s", exc.__class__.__name__, exc
        )
        db.rollback()
        job_run.status = "failed"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = {}
        job_run.error = f"{exc.__class__.__name__}: {exc}"
        db.add(job_run)
        record_audit_log(
            db,
            event_type="trade_cases.population_failed",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Trade case population failed",
            payload={"error": job_run.error},
        )
        db.commit()
        db.refresh(job_run)
        raise


def _upsert_round_trips(
    db: Session,
    round_trips: list[dict[str, Any]],
) -> tuple[int, int, int, list[str]]:
    inserted = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    for rt in round_trips:
        try:
            entry_fill_id = uuid.UUID(rt["entry_fill_id"])
            exit_fill_id = uuid.UUID(rt["exit_fill_id"])

            existing = db.scalar(
                select(TradeCase).where(
                    TradeCase.entry_fill_id == entry_fill_id,
                    TradeCase.exit_fill_id == exit_fill_id,
                )
            )

            new_context = _build_context(rt)

            if existing is None:
                db.add(
                    TradeCase(
                        strategy_id=_optional_uuid(rt.get("strategy_id")),
                        entry_order_intent_id=_optional_uuid(
                            rt.get("entry_order_intent_id")
                        ),
                        entry_fill_id=entry_fill_id,
                        exit_fill_id=exit_fill_id,
                        symbol=rt["symbol"],
                        underlying_symbol=_underlying_symbol(rt["symbol"]),
                        quantity=Decimal(rt["quantity"]),
                        entry_price=Decimal(rt["entry_price"]),
                        entry_time=datetime.fromisoformat(rt["entry_at"]),
                        exit_price=Decimal(rt["exit_price"]),
                        exit_time=datetime.fromisoformat(rt["exit_at"]),
                        realized_pl=Decimal(rt["realized_pnl"]),
                        realized_pl_percent=Decimal(rt["return_percent"]),
                        is_open=False,
                        context=new_context,
                    )
                )
                db.flush()
                inserted += 1
            elif existing.context != new_context:
                existing.context = new_context
                existing.realized_pl = Decimal(rt["realized_pnl"])
                existing.realized_pl_percent = Decimal(rt["return_percent"])
                db.flush()
                updated += 1
            else:
                skipped += 1

        except Exception as exc:
            errors.append(
                f"round_trip {rt.get('entry_fill_id')} → {rt.get('exit_fill_id')}: "
                f"{exc.__class__.__name__}: {exc}"
            )
            logger.error(
                "Failed to upsert trade case entry_fill=%s exit_fill=%s: %s: %s",
                rt.get("entry_fill_id"),
                rt.get("exit_fill_id"),
                exc.__class__.__name__,
                exc,
            )

    return inserted, updated, skipped, errors


def _build_context(rt: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry": rt.get("entry_context", {}),
        "exit": rt.get("exit_context", {}),
        "holding_seconds": rt.get("holding_seconds"),
        "entry_notional": rt.get("entry_notional"),
        "exit_notional": rt.get("exit_notional"),
    }


def _underlying_symbol(symbol: str) -> str | None:
    match = _OPTION_SYMBOL_RE.search(symbol)
    if match is not None:
        prefix = symbol[: match.start()].strip().upper()
        return prefix or None
    return None


def _optional_uuid(value: str | None) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None
