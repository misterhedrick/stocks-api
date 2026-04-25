from __future__ import annotations

import unittest
import uuid
from decimal import Decimal

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes.signals import create_signal, get_signal, update_signal
from app.db.models import AuditLog, Signal
from app.schemas.signals import SignalCreate, SignalUpdate


class FakeSignalSession:
    def __init__(self, signal: Signal | None = None) -> None:
        self.signal = signal
        self.added: list[object] = []
        self.commit_count = 0
        self.flush_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)
        if isinstance(obj, Signal):
            self.signal = obj

    def flush(self) -> None:
        self.flush_count += 1
        if self.signal is not None and getattr(self.signal, "id", None) is None:
            self.signal.id = uuid.uuid4()

    def commit(self) -> None:
        self.commit_count += 1

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    def get(self, model: type[Signal], signal_id: uuid.UUID) -> Signal | None:
        if model is not Signal or self.signal is None:
            return None
        if self.signal.id != signal_id:
            return None
        return self.signal


def build_signal() -> Signal:
    return Signal(
        id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        symbol="SPY260417C00500000",
        underlying_symbol="SPY",
        signal_type="breakout",
        direction="bullish",
        confidence=Decimal("0.7500"),
        rationale="Opening range breakout",
        market_context={"price": "512.34"},
        status="new",
    )


class SignalRouteTests(unittest.TestCase):
    def test_create_signal_records_audit_log(self) -> None:
        db = FakeSignalSession()

        signal = create_signal(
            SignalCreate(
                strategy_id=uuid.uuid4(),
                symbol="SPY260417C00500000",
                underlying_symbol="SPY",
                signal_type="breakout",
                direction="bullish",
                confidence=Decimal("0.7500"),
                rationale="Opening range breakout",
                market_context={"price": "512.34"},
            ),
            db,
        )

        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(signal.status, "new")
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(audit_logs[-1].event_type, "signal.created")
        self.assertEqual(audit_logs[-1].entity_id, signal.id)
        self.assertEqual(audit_logs[-1].payload["confidence"], "0.7500")

    def test_update_signal_records_audit_log(self) -> None:
        existing_signal = build_signal()
        db = FakeSignalSession(existing_signal)

        signal = update_signal(
            existing_signal.id,
            SignalUpdate(
                status="rejected",
                rejected_reason="Spread too wide",
                confidence=Decimal("0.6500"),
            ),
            db,
        )

        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(signal.status, "rejected")
        self.assertEqual(signal.rejected_reason, "Spread too wide")
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(audit_logs[-1].event_type, "signal.updated")
        self.assertEqual(audit_logs[-1].payload["changes"]["confidence"], "0.6500")

    def test_get_signal_returns_404_when_missing(self) -> None:
        db = FakeSignalSession()

        with self.assertRaises(HTTPException) as context:
            get_signal(uuid.uuid4(), db)

        self.assertEqual(context.exception.status_code, 404)

    def test_signal_confidence_must_be_between_zero_and_one(self) -> None:
        with self.assertRaises(ValidationError):
            SignalCreate(
                symbol="SPY260417C00500000",
                underlying_symbol="SPY",
                signal_type="breakout",
                direction="bullish",
                confidence=Decimal("1.2500"),
            )


if __name__ == "__main__":
    unittest.main()
