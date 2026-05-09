from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from app.db.models import AuditLog, JobRun, Signal, Strategy
from app.integrations.alpaca import (
    AlpacaStockBar,
    AlpacaStockBars,
)
from app.services.signal_scanner import scan_signals


class FakeScalarResult:
    def __init__(self, values: list[Strategy]) -> None:
        self.values = values

    def __iter__(self):
        return iter(self.values)


class FakeScannerSession:
    def __init__(
        self,
        strategies: list[Strategy] | None = None,
        scalar_results: list[object | None] | None = None,
    ) -> None:
        self.strategies = strategies or []
        self.scalar_results = scalar_results or []
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

    def scalar(self, _: object) -> object | None:
        if self.scalar_results:
            return self.scalar_results.pop(0)
        return None

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class FakeMarketDataClient:
    def __init__(
        self,
        bars: dict[str, list[str]] | None = None,
    ) -> None:
        self.bars = bars or {}

    def get_stock_bars(
        self,
        symbols: list[str],
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        feed: str,
        limit: int,
    ) -> dict[str, AlpacaStockBars]:
        results = {}
        for symbol in symbols:
            closes = self.bars.get(symbol)
            if closes is None:
                continue
            raw_bars = [
                {
                    "o": close,
                    "h": close,
                    "l": close,
                    "c": close,
                    "v": "1000",
                    "t": f"2026-04-23T16:{index:02d}:00Z",
                }
                for index, close in enumerate(closes)
            ]
            results[symbol] = AlpacaStockBars(
                symbol=symbol,
                bars=[AlpacaStockBar.model_validate(item) for item in raw_bars],
                raw_response=raw_bars,
            )
        return results


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


def build_existing_signal(strategy: Strategy) -> Signal:
    now = datetime.now(timezone.utc)
    return Signal(
        id=uuid.uuid4(),
        strategy_id=strategy.id,
        symbol="SPY",
        underlying_symbol="SPY",
        signal_type="price_breakout",
        direction="bullish",
        confidence=None,
        rationale="Existing signal",
        market_context={},
        status="new",
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

    def test_scan_signals_filters_static_specs_to_requested_symbol(self) -> None:
        strategy = build_strategy(
            config={
                "scan_signals": [
                    {
                        "symbol": "SPY",
                        "underlying_symbol": "SPY",
                        "signal_type": "manual_scan",
                        "direction": "bullish",
                        "rationale": "Scanner test",
                    },
                    {
                        "symbol": "QQQ",
                        "underlying_symbol": "QQQ",
                        "signal_type": "manual_scan",
                        "direction": "bullish",
                        "rationale": "Scanner test",
                    },
                ]
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(db, symbol="QQQ")

        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(signals[-1].underlying_symbol, "QQQ")
        self.assertEqual(result.job_run.details["symbol"], "QQQ")

    def test_scan_signals_filters_scanner_symbols_to_requested_symbol(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "moving_average",
                    "symbols": ["SPY", "QQQ"],
                    "short_window": 2,
                    "long_window": 3,
                    "trigger": "bullish_cross",
                }
            }
        )
        db = FakeScannerSession([strategy])
        market_data = FakeMarketDataClient(
            bars={
                "SPY": ["10.00", "10.00", "10.00", "10.00", "12.00"],
                "QQQ": ["20.00", "20.00", "20.00", "20.00", "24.00"],
            }
        )

        result = scan_signals(db, symbol="SPY", market_data_client=market_data)

        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(signals[-1].underlying_symbol, "SPY")

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
        self.assertEqual(result.no_signal_reasons, [])
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

    def test_scan_signals_records_legacy_direct_scanner_types_as_unsupported(self) -> None:
        for scanner_type in ("price_threshold", "percent_change", "trend_confirmation"):
            with self.subTest(scanner_type=scanner_type):
                strategy = build_strategy(
                    config={"scanner": {"type": scanner_type, "symbols": ["SPY"]}}
                )
                db = FakeScannerSession([strategy])

                result = scan_signals(db, market_data_client=FakeMarketDataClient())

                self.assertEqual(result.signals_created, 0)
                self.assertEqual(result.signals_skipped, 1)
                self.assertIn("scanner.type must be moving_average", result.errors[0])
                self.assertNotIn("price_threshold, percent_change", result.errors[0])
                self.assertFalse([item for item in db.added if isinstance(item, Signal)])

    def test_scan_signals_creates_signal_when_moving_average_crosses_up(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "moving_average",
                    "symbols": ["SPY"],
                    "short_window": 2,
                    "long_window": 3,
                    "trigger": "bullish_cross",
                    "signal_type": "ma_breakout",
                    "confidence": "0.70",
                    "data_feed": "iex",
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient(
                bars={"SPY": ["10.00", "10.00", "10.00", "10.00", "12.00"]}
            ),
        )

        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(signals[-1].signal_type, "ma_breakout")
        self.assertEqual(signals[-1].direction, "bullish")
        self.assertEqual(signals[-1].market_context["source"], "evaluator.moving_average")
        self.assertEqual(signals[-1].market_context["trigger"], "bullish_cross")

    def test_scan_signals_does_not_create_signal_when_moving_average_trigger_not_met(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "moving_average",
                    "symbols": ["SPY"],
                    "short_window": 2,
                    "long_window": 3,
                    "trigger": "bullish_cross",
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient(
                bars={"SPY": ["10.00", "11.00", "12.00", "13.00", "14.00"]}
            ),
        )

        self.assertEqual(result.signals_created, 0)
        self.assertIn("moving average evaluator produced no signal", result.no_signal_reasons[0])

    def test_scan_signals_moving_average_evaluator_ignores_market_regime_config(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "moving_average",
                    "symbols": ["AAPL"],
                    "short_window": 2,
                    "long_window": 3,
                    "market_regime": {
                        "enabled": True,
                        "symbols": ["SPY", "QQQ"],
                        "bullish_min_change_percent": "0.05",
                    },
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient(
                bars={
                    "AAPL": ["100.00", "101.00", "102.00", "103.00"],
                }
            ),
        )

        # The evaluator-backed path does not apply market regime filtering;
        # a signal is produced based on price/average conditions alone.
        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(signals[-1].symbol, "AAPL")
        self.assertEqual(signals[-1].direction, "bullish")
        self.assertEqual(signals[-1].market_context["source"], "evaluator.moving_average")

    def test_scan_signals_records_malformed_moving_average_config_as_skipped(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "moving_average",
                    "symbols": ["SPY"],
                    "short_window": 5,
                    "long_window": 3,
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient(
                bars={"SPY": ["10.00", "10.00", "10.00", "10.00", "12.00"]}
            ),
        )

        self.assertEqual(result.signals_created, 0)
        self.assertEqual(result.signals_skipped, 1)
        self.assertIn("short_window must be less than", result.errors[0])

    def test_scan_signals_suppresses_recent_duplicate_signal(self) -> None:
        strategy = build_strategy(
            config={
                "scan_signals": [
                    {
                        "symbol": "SPY",
                        "underlying_symbol": "SPY",
                        "signal_type": "price_breakout",
                        "direction": "bullish",
                        "dedupe_minutes": 240,
                    }
                ]
            }
        )
        db = FakeScannerSession(
            [strategy],
            scalar_results=[build_existing_signal(strategy)],
        )

        result = scan_signals(db)

        self.assertEqual(result.signals_created, 0)
        self.assertEqual(result.signals_skipped, 1)
        self.assertIn("duplicate signal suppressed", result.errors[0])
        self.assertFalse([item for item in db.added if isinstance(item, Signal)])

    def test_scan_signals_can_disable_duplicate_suppression(self) -> None:
        strategy = build_strategy(
            config={
                "scan_signals": [
                    {
                        "symbol": "SPY",
                        "underlying_symbol": "SPY",
                        "signal_type": "price_breakout",
                        "direction": "bullish",
                        "dedupe_minutes": 0,
                    }
                ]
            }
        )
        db = FakeScannerSession(
            [strategy],
            scalar_results=[build_existing_signal(strategy)],
        )

        result = scan_signals(db)

        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(result.signals_skipped, 0)
        self.assertEqual(signals[-1].symbol, "SPY")

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
