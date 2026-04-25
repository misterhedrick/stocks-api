from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from app.db.models import AuditLog, JobRun, Signal, Strategy
from app.integrations.alpaca import (
    AlpacaLatestStockQuote,
    AlpacaStockQuote,
    AlpacaTradingError,
)
from app.services.signal_scanner import scan_signals


class FakeScalarResult:
    def __init__(self, values: list[Strategy]) -> None:
        self.values = values

    def __iter__(self):
        return iter(self.values)


class FakeScannerSession:
    def __init__(self, strategies: list[Strategy] | None = None) -> None:
        self.strategies = strategies or []
        self.added: list[object] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.flush_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flush_count += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    def scalars(self, _: object) -> FakeScalarResult:
        return FakeScalarResult(
            [strategy for strategy in self.strategies if strategy.is_active]
        )

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class FakeMarketDataClient:
    def __init__(self, quotes: dict[str, tuple[str | None, str | None]]) -> None:
        self.quotes = quotes

    def get_latest_stock_quotes(
        self,
        symbols: list[str],
        *,
        feed: str,
    ) -> dict[str, AlpacaLatestStockQuote]:
        latest_quotes = {}
        for symbol in symbols:
            bid_price, ask_price = self.quotes.get(symbol, (None, None))
            if bid_price is None and ask_price is None:
                continue
            raw_quote = {
                "bp": bid_price,
                "bs": "10",
                "ap": ask_price,
                "as": "12",
                "t": "2026-04-23T16:00:00Z",
            }
            latest_quotes[symbol] = AlpacaLatestStockQuote(
                symbol=symbol,
                quote=AlpacaStockQuote.model_validate(raw_quote),
                raw_response=raw_quote,
            )
        return latest_quotes


class FailingMarketDataClient:
    def get_latest_stock_quotes(
        self,
        symbols: list[str],
        *,
        feed: str,
    ) -> dict[str, AlpacaLatestStockQuote]:
        raise AlpacaTradingError("market data unavailable")


def build_strategy(
    *,
    is_active: bool = True,
    config: dict | None = None,
) -> Strategy:
    now = datetime.now(timezone.utc)
    return Strategy(
        id=uuid.uuid4(),
        name=f"Strategy {uuid.uuid4()}",
        description="Test strategy",
        is_active=is_active,
        config=config or {},
        created_at=now,
        updated_at=now,
    )


class SignalScannerTests(unittest.TestCase):
    def test_scan_signals_creates_signals_from_active_strategy_config(self) -> None:
        strategy = build_strategy(
            config={
                "scan_signals": [
                    {
                        "symbol": "SPY",
                        "underlying_symbol": "SPY",
                        "signal_type": "manual_scan",
                        "direction": "bullish",
                        "confidence": "0.75",
                        "rationale": "Scanner test",
                        "market_context": {"price": "500"},
                    }
                ]
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(db)

        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.strategies_seen, 1)
        self.assertEqual(result.strategies_scanned, 1)
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(result.signals_skipped, 0)
        self.assertEqual(signals[-1].strategy_id, strategy.id)
        self.assertEqual(signals[-1].symbol, "SPY")
        self.assertEqual(signals[-1].status, "new")
        self.assertEqual(db.commit_count, 1)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "signal_scan.succeeded")

    def test_scan_signals_ignores_inactive_strategies(self) -> None:
        db = FakeScannerSession(
            [
                build_strategy(
                    is_active=False,
                    config={
                        "scan_signals": [
                            {
                                "symbol": "SPY",
                                "signal_type": "manual_scan",
                                "direction": "bullish",
                            }
                        ]
                    },
                )
            ]
        )

        result = scan_signals(db)

        self.assertEqual(result.strategies_seen, 0)
        self.assertEqual(result.signals_created, 0)
        self.assertFalse([item for item in db.added if isinstance(item, Signal)])

    def test_scan_signals_skips_malformed_signal_specs(self) -> None:
        strategy = build_strategy(
            config={
                "scan_signals": [
                    {
                        "symbol": "SPY",
                        "signal_type": "manual_scan",
                    },
                    {
                        "symbol": "QQQ",
                        "signal_type": "manual_scan",
                        "direction": "bearish",
                    },
                ]
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(db)

        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(result.signals_skipped, 1)
        self.assertEqual(signals[-1].symbol, "QQQ")
        self.assertIn("direction is required", result.errors[0])

    def test_scan_signals_creates_signal_when_price_threshold_is_met(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "price_threshold",
                    "symbols": ["SPY", "QQQ"],
                    "signal_type": "price_breakout",
                    "direction": "bullish",
                    "price_above": "500",
                    "confidence": "0.65",
                    "data_feed": "iex",
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient(
                {
                    "SPY": ("500.00", "501.00"),
                    "QQQ": ("420.00", "421.00"),
                }
            ),
        )

        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(signals[-1].symbol, "SPY")
        self.assertEqual(signals[-1].signal_type, "price_breakout")
        self.assertEqual(signals[-1].market_context["price"], "500.50")
        self.assertEqual(
            signals[-1].market_context["quote"]["quote_timestamp"],
            "2026-04-23T16:00:00+00:00",
        )

    def test_scan_signals_does_not_create_signal_when_threshold_is_not_met(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "price_threshold",
                    "symbols": ["SPY"],
                    "price_below": "490",
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient({"SPY": ("500.00", "501.00")}),
        )

        self.assertEqual(result.signals_created, 0)
        self.assertFalse([item for item in db.added if isinstance(item, Signal)])

    def test_scan_signals_records_malformed_scanner_config_as_skipped(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "price_threshold",
                    "symbols": ["SPY"],
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient({"SPY": ("500.00", "501.00")}),
        )

        self.assertEqual(result.signals_created, 0)
        self.assertEqual(result.signals_skipped, 1)
        self.assertIn("price_above or price_below", result.errors[0])

    def test_scan_signals_records_market_data_error_as_skipped(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "price_threshold",
                    "symbols": ["SPY"],
                    "price_above": "500",
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(db, market_data_client=FailingMarketDataClient())

        self.assertEqual(result.signals_created, 0)
        self.assertEqual(result.signals_skipped, 1)
        self.assertIn("market data unavailable", result.errors[0])

    def test_scan_signals_records_failed_job_run(self) -> None:
        class FailingScannerSession(FakeScannerSession):
            def scalars(self, _: object) -> FakeScalarResult:
                raise RuntimeError("database unavailable")

        db = FailingScannerSession()

        with self.assertRaises(RuntimeError):
            scan_signals(db)

        self.assertEqual(db.rollback_count, 1)
        self.assertEqual(db.commit_count, 1)
        job_runs = [item for item in db.added if isinstance(item, JobRun)]
        self.assertEqual(job_runs[-1].status, "failed")
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "signal_scan.failed")


if __name__ == "__main__":
    unittest.main()
