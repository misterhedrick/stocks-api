from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from app.db.models import JobRun, TradeCase
from app.services.trade_cases import (
    TradeCasePopulationResult,
    _build_context,
    _optional_uuid,
    _underlying_symbol,
    _upsert_round_trips,
    populate_trade_cases_from_closed_round_trips,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_round_trip(
    *,
    symbol: str = "SPY271219C00500000",
    strategy_id: str | None = None,
    entry_fill_id: str | None = None,
    exit_fill_id: str | None = None,
    entry_order_intent_id: str | None = None,
    entry_at: str = "2026-01-10T10:00:00+00:00",
    exit_at: str = "2026-01-10T14:00:00+00:00",
    entry_price: str = "1.50",
    exit_price: str = "2.10",
    quantity: str = "1",
    realized_pnl: str = "60.00",
    return_percent: str = "40.00",
    holding_seconds: int = 14400,
) -> dict:
    return {
        "symbol": symbol,
        "strategy_id": strategy_id or str(uuid.uuid4()),
        "strategy_name": "test-strategy",
        "quantity": quantity,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_notional": str(Decimal(entry_price) * Decimal(quantity) * 100),
        "exit_notional": str(Decimal(exit_price) * Decimal(quantity) * 100),
        "realized_pnl": realized_pnl,
        "return_percent": return_percent,
        "entry_at": entry_at,
        "exit_at": exit_at,
        "holding_seconds": holding_seconds,
        "entry_fill_id": entry_fill_id or str(uuid.uuid4()),
        "exit_fill_id": exit_fill_id or str(uuid.uuid4()),
        "entry_order_intent_id": entry_order_intent_id or str(uuid.uuid4()),
        "exit_order_intent_id": str(uuid.uuid4()),
        "entry_context": {"order_intent": {"id": str(uuid.uuid4()), "side": "buy"}, "signal": {}},
        "exit_context": {"order_intent": {"id": str(uuid.uuid4()), "side": "sell"}, "signal": {}},
    }


class FakeTradeCaseSession:
    """Minimal DB session stub for trade case population tests."""

    def __init__(self, scalar_results: list[object | None] | None = None) -> None:
        # scalar_results consumed in order: first call is job_run flush id seed,
        # subsequent calls answer "does this trade case already exist?"
        self._scalar_results: list[object | None] = list(scalar_results or [])
        self.added: list[object] = []
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)
        if isinstance(obj, JobRun) and obj.id is None:
            obj.id = uuid.uuid4()

    def flush(self) -> None:
        self.flush_count += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    def scalar(self, _: object) -> object | None:
        if self._scalar_results:
            return self._scalar_results.pop(0)
        return None

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


# ---------------------------------------------------------------------------
# Unit tests: pure helpers
# ---------------------------------------------------------------------------


class UnderlyingSymbolTests(unittest.TestCase):
    def test_extracts_underlying_from_option_symbol(self) -> None:
        self.assertEqual(_underlying_symbol("SPY271219C00500000"), "SPY")

    def test_extracts_multi_char_underlying(self) -> None:
        self.assertEqual(_underlying_symbol("AAPL271219P00150000"), "AAPL")

    def test_returns_none_for_plain_stock_symbol(self) -> None:
        self.assertIsNone(_underlying_symbol("AAPL"))

    def test_returns_none_for_spy_stock(self) -> None:
        self.assertIsNone(_underlying_symbol("SPY"))

    def test_returns_none_for_empty_prefix_option(self) -> None:
        # Pathological: OCC string with no underlying prefix
        self.assertIsNone(_underlying_symbol("271219C00500000"))


class BuildContextTests(unittest.TestCase):
    def test_context_keys_present(self) -> None:
        rt = _make_round_trip()
        ctx = _build_context(rt)
        self.assertIn("entry", ctx)
        self.assertIn("exit", ctx)
        self.assertIn("holding_seconds", ctx)
        self.assertIn("entry_notional", ctx)
        self.assertIn("exit_notional", ctx)

    def test_holding_seconds_value(self) -> None:
        rt = _make_round_trip(holding_seconds=3600)
        ctx = _build_context(rt)
        self.assertEqual(ctx["holding_seconds"], 3600)


class OptionalUuidTests(unittest.TestCase):
    def test_parses_valid_uuid_string(self) -> None:
        uid = uuid.uuid4()
        self.assertEqual(_optional_uuid(str(uid)), uid)

    def test_returns_none_for_none(self) -> None:
        self.assertIsNone(_optional_uuid(None))

    def test_returns_none_for_invalid_string(self) -> None:
        self.assertIsNone(_optional_uuid("not-a-uuid"))


# ---------------------------------------------------------------------------
# Unit tests: _upsert_round_trips
# ---------------------------------------------------------------------------


class UpsertRoundTripsTests(unittest.TestCase):
    def test_inserts_new_trade_case(self) -> None:
        rt = _make_round_trip()
        db = FakeTradeCaseSession(scalar_results=[None])  # no existing case
        inserted, updated, skipped, errors = _upsert_round_trips(db, [rt])

        self.assertEqual(inserted, 1)
        self.assertEqual(updated, 0)
        self.assertEqual(skipped, 0)
        self.assertEqual(errors, [])

        trade_cases = [obj for obj in db.added if isinstance(obj, TradeCase)]
        self.assertEqual(len(trade_cases), 1)
        tc = trade_cases[0]
        self.assertEqual(tc.symbol, rt["symbol"])
        self.assertEqual(tc.underlying_symbol, "SPY")
        self.assertFalse(tc.is_open)
        self.assertEqual(tc.realized_pl, Decimal(rt["realized_pnl"]))

    def test_skips_unchanged_trade_case(self) -> None:
        rt = _make_round_trip()
        existing = TradeCase(
            id=uuid.uuid4(),
            symbol=rt["symbol"],
            is_open=False,
            context=_build_context(rt),
            quantity=Decimal("1"),
            entry_price=Decimal("1.50"),
            entry_time=datetime.now(timezone.utc),
        )
        db = FakeTradeCaseSession(scalar_results=[existing])
        inserted, updated, skipped, errors = _upsert_round_trips(db, [rt])

        self.assertEqual(inserted, 0)
        self.assertEqual(updated, 0)
        self.assertEqual(skipped, 1)
        self.assertEqual(errors, [])

    def test_updates_trade_case_when_context_changed(self) -> None:
        rt = _make_round_trip()
        existing = TradeCase(
            id=uuid.uuid4(),
            symbol=rt["symbol"],
            is_open=False,
            context={"entry": {}, "exit": {}, "holding_seconds": 999},  # stale
            quantity=Decimal("1"),
            entry_price=Decimal("1.50"),
            entry_time=datetime.now(timezone.utc),
        )
        db = FakeTradeCaseSession(scalar_results=[existing])
        inserted, updated, skipped, errors = _upsert_round_trips(db, [rt])

        self.assertEqual(inserted, 0)
        self.assertEqual(updated, 1)
        self.assertEqual(skipped, 0)
        self.assertEqual(errors, [])
        self.assertEqual(existing.context, _build_context(rt))

    def test_accumulates_errors_for_bad_round_trip(self) -> None:
        bad_rt = {"entry_fill_id": "not-a-uuid", "exit_fill_id": "also-bad"}
        db = FakeTradeCaseSession()
        inserted, updated, skipped, errors = _upsert_round_trips(db, [bad_rt])

        self.assertEqual(inserted, 0)
        self.assertEqual(updated, 0)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("not-a-uuid", errors[0])

    def test_processes_multiple_round_trips(self) -> None:
        rt1 = _make_round_trip(symbol="SPY271219C00500000")
        rt2 = _make_round_trip(symbol="AAPL271219C00150000")
        db = FakeTradeCaseSession(scalar_results=[None, None])
        inserted, updated, skipped, errors = _upsert_round_trips(db, [rt1, rt2])

        self.assertEqual(inserted, 2)
        self.assertEqual(errors, [])

    def test_stock_symbol_has_no_underlying(self) -> None:
        rt = _make_round_trip(symbol="AAPL")
        db = FakeTradeCaseSession(scalar_results=[None])
        inserted, _u, _s, errors = _upsert_round_trips(db, [rt])
        self.assertEqual(inserted, 1)
        self.assertEqual(errors, [])
        tc = next(obj for obj in db.added if isinstance(obj, TradeCase))
        self.assertIsNone(tc.underlying_symbol)


# ---------------------------------------------------------------------------
# Integration test: populate_trade_cases_from_closed_round_trips
# ---------------------------------------------------------------------------


class PopulateTradeCasesTests(unittest.TestCase):
    def _make_db_with_fill_rows(
        self,
        fill_rows: list[tuple],
        scalar_results: list[object | None] | None = None,
    ) -> FakeTradeCaseSession:
        db = FakeTradeCaseSession(scalar_results=scalar_results or [])
        db._fill_rows = fill_rows  # type: ignore[attr-defined]
        return db

    def test_no_fills_returns_zero_counts(self) -> None:
        with patch(
            "app.services.trade_cases._fill_records", return_value=[]
        ), patch(
            "app.services.trade_cases._match_round_trips",
            return_value=([], {}, [], []),
        ):
            db = FakeTradeCaseSession()
            result = populate_trade_cases_from_closed_round_trips(db, limit=100)

        self.assertIsInstance(result, TradeCasePopulationResult)
        self.assertEqual(result.round_trips_seen, 0)
        self.assertEqual(result.inserted, 0)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.job_run.status, "succeeded")
        self.assertEqual(db.commit_count, 1)

    def test_inserts_new_cases_from_round_trips(self) -> None:
        rt = _make_round_trip()
        with patch(
            "app.services.trade_cases._fill_records", return_value=[]
        ), patch(
            "app.services.trade_cases._match_round_trips",
            return_value=([rt], {}, [], []),
        ):
            db = FakeTradeCaseSession(scalar_results=[None])
            result = populate_trade_cases_from_closed_round_trips(db, limit=100)

        self.assertEqual(result.round_trips_seen, 1)
        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(result.job_run.status, "succeeded")

    def test_skips_existing_unchanged_cases(self) -> None:
        rt = _make_round_trip()
        existing = TradeCase(
            id=uuid.uuid4(),
            symbol=rt["symbol"],
            is_open=False,
            context=_build_context(rt),
            quantity=Decimal("1"),
            entry_price=Decimal("1.50"),
            entry_time=datetime.now(timezone.utc),
        )
        with patch(
            "app.services.trade_cases._fill_records", return_value=[]
        ), patch(
            "app.services.trade_cases._match_round_trips",
            return_value=([rt], {}, [], []),
        ):
            db = FakeTradeCaseSession(scalar_results=[existing])
            result = populate_trade_cases_from_closed_round_trips(db, limit=100)

        self.assertEqual(result.inserted, 0)
        self.assertEqual(result.skipped, 1)

    def test_failed_service_rolls_back_and_reraises(self) -> None:
        with patch(
            "app.services.trade_cases._fill_records",
            side_effect=RuntimeError("db exploded"),
        ):
            db = FakeTradeCaseSession()
            with self.assertRaises(RuntimeError):
                populate_trade_cases_from_closed_round_trips(db, limit=100)

        self.assertEqual(db.rollback_count, 1)
        self.assertEqual(db.commit_count, 1)  # final commit for failed job_run row

        job_runs = [obj for obj in db.added if isinstance(obj, JobRun)]
        failed = next((j for j in job_runs if j.status == "failed"), None)
        self.assertIsNotNone(failed)
        self.assertIn("db exploded", failed.error or "")

    def test_job_run_details_populated(self) -> None:
        rt = _make_round_trip()
        with patch(
            "app.services.trade_cases._fill_records", return_value=[]
        ), patch(
            "app.services.trade_cases._match_round_trips",
            return_value=([rt], {}, [], []),
        ):
            db = FakeTradeCaseSession(scalar_results=[None])
            result = populate_trade_cases_from_closed_round_trips(db, limit=100)

        self.assertIn("round_trips_seen", result.job_run.details)
        self.assertIn("inserted", result.job_run.details)
        self.assertEqual(result.job_run.details["inserted"], 1)
