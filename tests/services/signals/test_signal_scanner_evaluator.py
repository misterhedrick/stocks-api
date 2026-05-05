from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.integrations.alpaca import AlpacaStockBar, AlpacaStockBars
from app.services.signal_scanner import (
    _candle_frame_from_stock_bars,
    _momentum_rate_of_change_signal_specs,
    _signal_spec_from_candidate,
)
from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.base import SignalCandidate
from app.services.signals.evaluators.registry import get_evaluator


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_returns_momentum_evaluator() -> None:
    evaluator = get_evaluator("momentum_rate_of_change")
    assert evaluator is not None
    assert evaluator.strategy_type == "momentum_rate_of_change"


def test_registry_returns_none_for_unknown_type() -> None:
    assert get_evaluator("does_not_exist") is None


# ---------------------------------------------------------------------------
# Candle frame converter
# ---------------------------------------------------------------------------


def _make_stock_bars(symbol: str, closes: list[float]) -> AlpacaStockBars:
    start = datetime(2026, 5, 4, 14, 0, tzinfo=timezone.utc)
    bars = []
    for index, close in enumerate(closes):
        bars.append(
            AlpacaStockBar.model_validate(
                {
                    "o": close - 0.05,
                    "h": close + 0.10,
                    "l": close - 0.10,
                    "c": close,
                    "v": 1000,
                    "t": (start + timedelta(minutes=index)).isoformat(),
                }
            )
        )
    return AlpacaStockBars(symbol=symbol, bars=bars, raw_response=[])


def test_candle_frame_from_stock_bars_basic() -> None:
    stock_bars = _make_stock_bars("SPY", [100.0, 100.5, 101.0])
    frame = _candle_frame_from_stock_bars(stock_bars, "1Min")
    assert frame.symbol == "SPY"
    assert frame.timeframe == "1Min"
    assert len(frame.candles) == 3
    assert frame.candles[-1].close == Decimal("101.0")


def test_candle_frame_preserves_order() -> None:
    stock_bars = _make_stock_bars("AAPL", [200.0, 201.0, 202.0, 203.0])
    frame = _candle_frame_from_stock_bars(stock_bars, "5Min")
    closes = [c.close for c in frame.candles]
    assert closes == sorted(closes)


# ---------------------------------------------------------------------------
# Signal spec from candidate
# ---------------------------------------------------------------------------


def _make_candidate(direction: str = "bullish") -> SignalCandidate:
    return SignalCandidate(
        symbol="SPY",
        strategy_type="momentum_rate_of_change",
        signal_type="momentum_breakout",
        direction=direction,  # type: ignore[arg-type]
        confidence=Decimal("0.65"),
        rationale="SPY moved 0.50% over 30 minutes with bullish candle confirmation",
        features={
            "timeframe": "1Min",
            "lookback_minutes": 30,
            "percent_change": "0.5000",
            "dedupe_minutes": 120,
        },
        dedupe_key="SPY:momentum_rate_of_change:momentum_breakout:bullish",
    )


def test_signal_spec_from_candidate_fields() -> None:
    spec = _signal_spec_from_candidate(_make_candidate(), {})
    assert spec["symbol"] == "SPY"
    assert spec["underlying_symbol"] == "SPY"
    assert spec["signal_type"] == "momentum_breakout"
    assert spec["direction"] == "bullish"
    assert spec["confidence"] == Decimal("0.65")
    assert spec["dedupe_minutes"] == 120


def test_signal_spec_source_is_evaluator() -> None:
    spec = _signal_spec_from_candidate(_make_candidate(), {})
    assert spec["market_context"]["source"] == "evaluator.momentum_rate_of_change"


def test_signal_spec_dedupe_falls_back_to_scanner_config() -> None:
    candidate = SignalCandidate(
        symbol="SPY",
        strategy_type="momentum_rate_of_change",
        signal_type="momentum_breakout",
        direction="bullish",
        confidence=Decimal("0.60"),
        rationale="test",
        features={},
    )
    spec = _signal_spec_from_candidate(candidate, {"dedupe_minutes": 60})
    assert spec["dedupe_minutes"] == 60


def test_signal_spec_dedupe_falls_back_to_default() -> None:
    candidate = SignalCandidate(
        symbol="SPY",
        strategy_type="momentum_rate_of_change",
        signal_type="momentum_breakout",
        direction="bullish",
        confidence=Decimal("0.60"),
        rationale="test",
        features={},
    )
    spec = _signal_spec_from_candidate(candidate, {})
    assert spec["dedupe_minutes"] == 240


# ---------------------------------------------------------------------------
# _momentum_rate_of_change_signal_specs — feature flag guards
# ---------------------------------------------------------------------------


def test_evaluators_disabled_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = False
        mock_settings.momentum_evaluator_enabled = True
        result = _momentum_rate_of_change_signal_specs(
            "test-strategy",
            {"type": "momentum_rate_of_change", "symbols": ["SPY"]},
            ["SPY"],
            market_data_client=None,
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("SIGNAL_EVALUATORS_ENABLED=false" in r for r in reasons)


def test_momentum_evaluator_disabled_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.momentum_evaluator_enabled = False
        result = _momentum_rate_of_change_signal_specs(
            "test-strategy",
            {"type": "momentum_rate_of_change", "symbols": ["SPY"]},
            ["SPY"],
            market_data_client=None,
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("MOMENTUM_EVALUATOR_ENABLED=false" in r for r in reasons)


# ---------------------------------------------------------------------------
# _momentum_rate_of_change_signal_specs — data + evaluator paths
# ---------------------------------------------------------------------------


def _mock_client(bars_by_symbol: dict[str, AlpacaStockBars]) -> MagicMock:
    client = MagicMock()
    client.get_stock_bars.return_value = bars_by_symbol
    return client


def test_no_bars_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.momentum_evaluator_enabled = True
        result = _momentum_rate_of_change_signal_specs(
            "test-strategy",
            {"type": "momentum_rate_of_change", "symbols": ["SPY"]},
            ["SPY"],
            market_data_client=_mock_client({}),
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("no usable bars" in r for r in reasons)


def test_evaluator_no_signal_returns_empty() -> None:
    # Price series with no momentum move (flat)
    stock_bars = _make_stock_bars("SPY", [100.0] * 35)
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.momentum_evaluator_enabled = True
        result = _momentum_rate_of_change_signal_specs(
            "test-strategy",
            {
                "type": "momentum_rate_of_change",
                "timeframe": "1Min",
                "lookback_minutes": 30,
                "change_above_percent": "0.35",
                "change_below_percent": "-0.35",
            },
            ["SPY"],
            market_data_client=_mock_client({"SPY": stock_bars}),
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("produced no signal" in r for r in reasons)


def test_evaluator_bullish_signal_returned() -> None:
    # Prices that rise >0.35% over 30 bars with bullish final candle
    closes = [100.0 + i * 0.015 for i in range(35)]
    stock_bars = _make_stock_bars("SPY", closes)
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.momentum_evaluator_enabled = True
        result = _momentum_rate_of_change_signal_specs(
            "test-strategy",
            {
                "type": "momentum_rate_of_change",
                "timeframe": "1Min",
                "lookback_minutes": 30,
                "change_above_percent": "0.35",
                "change_below_percent": "-0.35",
            },
            ["SPY"],
            market_data_client=_mock_client({"SPY": stock_bars}),
            no_signal_reasons=reasons,
        )
    assert len(result) == 1
    spec = result[0]
    assert spec["symbol"] == "SPY"
    assert spec["direction"] == "bullish"
    assert spec["signal_type"] == "momentum_breakout"
    assert spec["market_context"]["source"] == "evaluator.momentum_rate_of_change"
