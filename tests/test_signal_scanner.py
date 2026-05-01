from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from app.db.models import AuditLog, JobRun, Signal, Strategy
from app.integrations.alpaca import (
    AlpacaLatestStockQuote,
    AlpacaStockBar,
    AlpacaStockBars,
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
        quotes: dict[str, tuple[str | None, str | None]] | None = None,
        bars: dict[str, list[str]] | None = None,
    ) -> None:
        self.bars = bars or {}
        self.quotes = quotes

    def get_latest_stock_quotes(
        self,
        symbols: list[str],
        *,
        feed: str,
    ) -> dict[str, AlpacaLatestStockQuote]:
        latest_quotes = {}
        for symbol in symbols:
            bid_price, ask_price = (self.quotes or {}).get(symbol, (None, None))
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

    def test_scan_signals_ignores_zero_quote_side_when_pricing_threshold(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "price_threshold",
                    "symbols": ["SPY"],
                    "price_above": "680",
                    "data_feed": "iex",
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient({"SPY": ("689.60", "0")}),
        )

        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(signals[-1].market_context["price"], "689.60")

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
        self.assertEqual(len(result.no_signal_reasons), 1)
        self.assertIn(
            "SPY: price 500.50 did not cross configured threshold",
            result.no_signal_reasons[0],
        )
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

    def test_scan_signals_creates_signal_when_percent_change_threshold_is_met(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "percent_change",
                    "symbols": ["SPY", "QQQ"],
                    "lookback_minutes": 30,
                    "change_above_percent": "0.50",
                    "signal_type": "momentum_breakout",
                    "direction": "bullish",
                    "confidence": "0.65",
                    "data_feed": "iex",
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient(
                bars={
                    "SPY": ["500.00", "503.00"],
                    "QQQ": ["420.00", "421.00"],
                }
            ),
        )

        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(signals[-1].symbol, "SPY")
        self.assertEqual(signals[-1].signal_type, "momentum_breakout")
        self.assertEqual(signals[-1].market_context["source"], "scanner.percent_change")
        self.assertEqual(signals[-1].market_context["first_close"], "500.00")
        self.assertEqual(signals[-1].market_context["last_close"], "503.00")

    def test_scan_signals_does_not_create_signal_when_percent_change_threshold_is_not_met(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "percent_change",
                    "symbols": ["SPY"],
                    "lookback_minutes": 30,
                    "change_above_percent": "1.00",
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient(
                bars={"SPY": ["500.00", "502.00"]}
            ),
        )

        self.assertEqual(result.signals_created, 0)
        self.assertEqual(len(result.no_signal_reasons), 1)
        self.assertIn(
            "SPY: percent change 0.400 did not cross configured threshold",
            result.no_signal_reasons[0],
        )
        self.assertFalse([item for item in db.added if isinstance(item, Signal)])

    def test_scan_signals_creates_signal_when_percent_change_drops_below_threshold(
        self,
    ) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "percent_change",
                    "symbols": ["SPY"],
                    "lookback_minutes": 30,
                    "change_below_percent": "-0.50",
                    "signal_type": "momentum_breakdown",
                    "direction": "bearish",
                    "confidence": "0.65",
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient(
                bars={"SPY": ["500.00", "495.00"]}
            ),
        )

        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(signals[-1].symbol, "SPY")
        self.assertEqual(signals[-1].signal_type, "momentum_breakdown")
        self.assertEqual(signals[-1].direction, "bearish")
        self.assertEqual(signals[-1].market_context["change_below_percent"], "-0.50")

    def test_scan_signals_records_malformed_percent_change_config_as_skipped(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "percent_change",
                    "symbols": ["SPY"],
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient(
                bars={"SPY": ["500.00", "503.00"]}
            ),
        )

        self.assertEqual(result.signals_created, 0)
        self.assertEqual(result.signals_skipped, 1)
        self.assertIn("change_above_percent or change_below_percent", result.errors[0])

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
        self.assertEqual(signals[-1].market_context["source"], "scanner.moving_average")
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
        self.assertIn("moving average trigger bullish_cross was not met", result.no_signal_reasons[0])

    def test_scan_signals_blocks_bullish_moving_average_when_market_regime_is_weak(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "moving_average",
                    "symbols": ["AAPL"],
                    "short_window": 2,
                    "long_window": 3,
                    "trigger": "bullish_trend",
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
                    "SPY": ["500.00", "499.00", "498.00", "497.00"],
                    "QQQ": ["400.00", "399.00", "398.00", "397.00"],
                }
            ),
        )

        self.assertEqual(result.signals_created, 0)
        self.assertIn("market regime did not confirm", result.no_signal_reasons[0])

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

    def test_scan_signals_creates_signal_when_trend_confirmation_is_met(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "trend_confirmation",
                    "symbols": ["SPY"],
                    "direction": "bullish",
                    "short_window": 2,
                    "long_window": 4,
                    "min_change_percent": "1.00",
                    "signal_type": "confirmed_trend",
                    "confidence": "0.68",
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient(
                bars={"SPY": ["100.00", "100.50", "101.00", "102.00", "103.00"]}
            ),
        )

        signals = [item for item in db.added if isinstance(item, Signal)]
        self.assertEqual(result.signals_created, 1)
        self.assertEqual(signals[-1].signal_type, "confirmed_trend")
        self.assertEqual(signals[-1].direction, "bullish")
        self.assertEqual(signals[-1].market_context["source"], "scanner.trend_confirmation")
        self.assertEqual(signals[-1].market_context["min_change_percent"], "1.00")

    def test_scan_signals_requires_momentum_for_trend_confirmation(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "trend_confirmation",
                    "symbols": ["SPY"],
                    "direction": "bullish",
                    "short_window": 2,
                    "long_window": 4,
                    "min_change_percent": "2.00",
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient(
                bars={"SPY": ["100.00", "100.10", "100.20", "100.30", "100.40"]}
            ),
        )

        self.assertEqual(result.signals_created, 0)
        self.assertIn("did not confirm bullish trend", result.no_signal_reasons[0])

    def test_scan_signals_requires_alignment_for_trend_confirmation(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "trend_confirmation",
                    "symbols": ["SPY"],
                    "direction": "bullish",
                    "short_window": 2,
                    "long_window": 4,
                    "min_change_percent": "0.10",
                }
            }
        )
        db = FakeScannerSession([strategy])

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient(
                bars={"SPY": ["100.00", "105.00", "104.00", "103.00", "102.00"]}
            ),
        )

        self.assertEqual(result.signals_created, 0)
        self.assertIn("moving averages were not aligned", result.no_signal_reasons[0])

    def test_scan_signals_suppresses_recent_duplicate_signal(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "price_threshold",
                    "symbols": ["SPY"],
                    "signal_type": "price_breakout",
                    "direction": "bullish",
                    "price_above": "500",
                    "dedupe_minutes": 240,
                }
            }
        )
        db = FakeScannerSession(
            [strategy],
            scalar_results=[build_existing_signal(strategy)],
        )

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient({"SPY": ("500.00", "501.00")}),
        )

        self.assertEqual(result.signals_created, 0)
        self.assertEqual(result.signals_skipped, 1)
        self.assertIn("duplicate signal suppressed", result.errors[0])
        self.assertFalse([item for item in db.added if isinstance(item, Signal)])

    def test_scan_signals_can_disable_duplicate_suppression(self) -> None:
        strategy = build_strategy(
            config={
                "scanner": {
                    "type": "price_threshold",
                    "symbols": ["SPY"],
                    "signal_type": "price_breakout",
                    "direction": "bullish",
                    "price_above": "500",
                    "dedupe_minutes": 0,
                }
            }
        )
        db = FakeScannerSession(
            [strategy],
            scalar_results=[build_existing_signal(strategy)],
        )

        result = scan_signals(
            db,
            market_data_client=FakeMarketDataClient({"SPY": ("500.00", "501.00")}),
        )

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
