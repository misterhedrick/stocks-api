from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.integrations.alpaca import AlpacaStockBar, AlpacaStockBars
from app.services.signal_scanner import (
    _candle_frame_from_stock_bars,
    _macd_crossover_signal_specs,
    _mean_reversion_signal_specs,
    _momentum_rate_of_change_signal_specs,
    _moving_average_evaluator_signal_specs,
    _rsi_reversal_signal_specs,
    _signal_spec_from_candidate,
    _signal_specs_from_scanner,
)
from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.base import SignalCandidate
from app.services.signals.evaluators.macd import MacdCrossoverEvaluator
from app.services.signals.evaluators.mean_reversion import MeanReversionEvaluator
from app.services.signals.evaluators.registry import get_evaluator
from app.services.signals.evaluators.rsi import RsiReversalEvaluator


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_returns_momentum_evaluator() -> None:
    evaluator = get_evaluator("momentum_rate_of_change")
    assert evaluator is not None
    assert evaluator.strategy_type == "momentum_rate_of_change"


def test_registry_returns_moving_average_evaluator() -> None:
    evaluator = get_evaluator("moving_average")
    assert evaluator is not None
    assert evaluator.strategy_type == "moving_average"


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


# ---------------------------------------------------------------------------
# _moving_average_evaluator_signal_specs — feature flag guards
# ---------------------------------------------------------------------------

_MA_BASE_CONFIG = {
    "type": "moving_average",
    "short_window": 2,
    "long_window": 3,
}


def test_moving_average_evaluators_disabled_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = False
        mock_settings.moving_average_evaluator_enabled = True
        result = _moving_average_evaluator_signal_specs(
            "test-strategy",
            _MA_BASE_CONFIG,
            ["SPY"],
            market_data_client=None,
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("SIGNAL_EVALUATORS_ENABLED=false" in r for r in reasons)


def test_moving_average_evaluator_disabled_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.moving_average_evaluator_enabled = False
        result = _moving_average_evaluator_signal_specs(
            "test-strategy",
            _MA_BASE_CONFIG,
            ["SPY"],
            market_data_client=None,
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("MOVING_AVERAGE_EVALUATOR_ENABLED=false" in r for r in reasons)


# ---------------------------------------------------------------------------
# _moving_average_evaluator_signal_specs — data + evaluator paths
# ---------------------------------------------------------------------------


def test_moving_average_no_bars_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.moving_average_evaluator_enabled = True
        result = _moving_average_evaluator_signal_specs(
            "test-strategy",
            _MA_BASE_CONFIG,
            ["SPY"],
            market_data_client=_mock_client({}),
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("no usable bars" in r for r in reasons)


def test_moving_average_evaluator_no_signal_flat_prices() -> None:
    stock_bars = _make_stock_bars("SPY", [100.0] * 10)
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.moving_average_evaluator_enabled = True
        result = _moving_average_evaluator_signal_specs(
            "test-strategy",
            {**_MA_BASE_CONFIG, "trigger": "bullish_cross"},
            ["SPY"],
            market_data_client=_mock_client({"SPY": stock_bars}),
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("produced no signal" in r for r in reasons)


def test_moving_average_evaluator_bullish_signal_returned() -> None:
    # Prices flat then jump: creates bullish crossover (short EMA rises above long EMA)
    closes = [10.0, 10.0, 10.0, 10.0, 12.0]
    stock_bars = _make_stock_bars("SPY", closes)
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.moving_average_evaluator_enabled = True
        result = _moving_average_evaluator_signal_specs(
            "test-strategy",
            {**_MA_BASE_CONFIG, "trigger": "bullish_cross"},
            ["SPY"],
            market_data_client=_mock_client({"SPY": stock_bars}),
            no_signal_reasons=reasons,
        )
    assert len(result) == 1
    spec = result[0]
    assert spec["symbol"] == "SPY"
    assert spec["direction"] == "bullish"
    assert spec["market_context"]["source"] == "evaluator.moving_average"
    assert spec["market_context"]["trigger"] == "bullish_cross"


# ---------------------------------------------------------------------------
# Registry — new evaluators
# ---------------------------------------------------------------------------


def test_registry_returns_rsi_evaluator() -> None:
    evaluator = get_evaluator("rsi_reversal")
    assert evaluator is not None
    assert evaluator.strategy_type == "rsi_reversal"


def test_registry_returns_macd_evaluator() -> None:
    evaluator = get_evaluator("macd_crossover")
    assert evaluator is not None
    assert evaluator.strategy_type == "macd_crossover"


def test_registry_returns_mean_reversion_evaluator() -> None:
    evaluator = get_evaluator("mean_reversion")
    assert evaluator is not None
    assert evaluator.strategy_type == "mean_reversion"


# ---------------------------------------------------------------------------
# Unknown scanner type
# ---------------------------------------------------------------------------


def test_unknown_scanner_type_raises_value_error() -> None:
    strategy = MagicMock()
    strategy.name = "test-strategy"
    strategy.config = {"scanner": {"type": "does_not_exist", "symbols": ["SPY"]}}
    with pytest.raises(ValueError, match="scanner.type must be"):
        _signal_specs_from_scanner(
            strategy,
            market_data_client=None,
            no_signal_reasons=[],
        )


# ---------------------------------------------------------------------------
# _rsi_reversal_signal_specs — feature flag guards
# ---------------------------------------------------------------------------

_RSI_BASE_CONFIG = {
    "type": "rsi_reversal",
    "timeframe": "5Min",
    "lookback_minutes": 240,
    "rsi_period": 5,
}


def test_rsi_reversal_evaluators_disabled_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = False
        mock_settings.rsi_evaluator_enabled = True
        result = _rsi_reversal_signal_specs(
            "test-strategy",
            _RSI_BASE_CONFIG,
            ["SPY"],
            market_data_client=None,
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("SIGNAL_EVALUATORS_ENABLED=false" in r for r in reasons)


def test_rsi_reversal_evaluator_disabled_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.rsi_evaluator_enabled = False
        result = _rsi_reversal_signal_specs(
            "test-strategy",
            _RSI_BASE_CONFIG,
            ["SPY"],
            market_data_client=None,
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("RSI_EVALUATOR_ENABLED=false" in r for r in reasons)


# ---------------------------------------------------------------------------
# _rsi_reversal_signal_specs — data + evaluator paths
# ---------------------------------------------------------------------------


def test_rsi_reversal_no_bars_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.rsi_evaluator_enabled = True
        result = _rsi_reversal_signal_specs(
            "test-strategy",
            _RSI_BASE_CONFIG,
            ["SPY"],
            market_data_client=_mock_client({}),
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("no usable bars" in r for r in reasons)


def test_rsi_reversal_evaluator_no_signal_flat_prices() -> None:
    stock_bars = _make_stock_bars("SPY", [100.0] * 20)
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.rsi_evaluator_enabled = True
        result = _rsi_reversal_signal_specs(
            "test-strategy",
            _RSI_BASE_CONFIG,
            ["SPY"],
            market_data_client=_mock_client({"SPY": stock_bars}),
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("produced no signal" in r for r in reasons)


def test_rsi_reversal_creates_signal_through_routing() -> None:
    stock_bars = _make_stock_bars("SPY", [100.0] * 20)
    reasons: list[str] = []
    candidate = SignalCandidate(
        symbol="SPY",
        strategy_type="rsi_reversal",
        signal_type="rsi_oversold_recovery",
        direction="bullish",
        confidence=Decimal("0.60"),
        rationale="SPY RSI crossed back above 30 with bullish price confirmation",
        features={"dedupe_minutes": 240},
        dedupe_key="SPY:rsi_reversal:rsi_oversold_recovery:bullish",
    )
    with patch("app.services.signal_scanner.settings") as mock_settings, patch.object(
        RsiReversalEvaluator, "evaluate", return_value=candidate
    ):
        mock_settings.signal_evaluators_enabled = True
        mock_settings.rsi_evaluator_enabled = True
        result = _rsi_reversal_signal_specs(
            "test-strategy",
            _RSI_BASE_CONFIG,
            ["SPY"],
            market_data_client=_mock_client({"SPY": stock_bars}),
            no_signal_reasons=reasons,
        )
    assert len(result) == 1
    spec = result[0]
    assert spec["symbol"] == "SPY"
    assert spec["direction"] == "bullish"
    assert spec["signal_type"] == "rsi_oversold_recovery"
    assert spec["market_context"]["source"] == "evaluator.rsi_reversal"


# ---------------------------------------------------------------------------
# _macd_crossover_signal_specs — feature flag guards
# ---------------------------------------------------------------------------

_MACD_BASE_CONFIG = {
    "type": "macd_crossover",
    "timeframe": "5Min",
    "lookback_minutes": 480,
    "fast_period": 12,
    "slow_period": 26,
    "signal_period": 9,
}


def test_macd_crossover_evaluators_disabled_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = False
        mock_settings.macd_evaluator_enabled = True
        result = _macd_crossover_signal_specs(
            "test-strategy",
            _MACD_BASE_CONFIG,
            ["SPY"],
            market_data_client=None,
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("SIGNAL_EVALUATORS_ENABLED=false" in r for r in reasons)


def test_macd_crossover_evaluator_disabled_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.macd_evaluator_enabled = False
        result = _macd_crossover_signal_specs(
            "test-strategy",
            _MACD_BASE_CONFIG,
            ["SPY"],
            market_data_client=None,
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("MACD_EVALUATOR_ENABLED=false" in r for r in reasons)


# ---------------------------------------------------------------------------
# _macd_crossover_signal_specs — data + evaluator paths
# ---------------------------------------------------------------------------


def test_macd_crossover_no_bars_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.macd_evaluator_enabled = True
        result = _macd_crossover_signal_specs(
            "test-strategy",
            _MACD_BASE_CONFIG,
            ["SPY"],
            market_data_client=_mock_client({}),
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("no usable bars" in r for r in reasons)


def test_macd_crossover_evaluator_no_signal_flat_prices() -> None:
    stock_bars = _make_stock_bars("SPY", [100.0] * 50)
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.macd_evaluator_enabled = True
        result = _macd_crossover_signal_specs(
            "test-strategy",
            _MACD_BASE_CONFIG,
            ["SPY"],
            market_data_client=_mock_client({"SPY": stock_bars}),
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("produced no signal" in r for r in reasons)


def test_macd_crossover_creates_signal_through_routing() -> None:
    stock_bars = _make_stock_bars("SPY", [100.0] * 50)
    reasons: list[str] = []
    candidate = SignalCandidate(
        symbol="SPY",
        strategy_type="macd_crossover",
        signal_type="macd_bullish_crossover",
        direction="bullish",
        confidence=Decimal("0.65"),
        rationale="SPY MACD crossed above the signal line with bullish price confirmation",
        features={"dedupe_minutes": 240},
        dedupe_key="SPY:macd_crossover:macd_bullish_crossover:bullish",
    )
    with patch("app.services.signal_scanner.settings") as mock_settings, patch.object(
        MacdCrossoverEvaluator, "evaluate", return_value=candidate
    ):
        mock_settings.signal_evaluators_enabled = True
        mock_settings.macd_evaluator_enabled = True
        result = _macd_crossover_signal_specs(
            "test-strategy",
            _MACD_BASE_CONFIG,
            ["SPY"],
            market_data_client=_mock_client({"SPY": stock_bars}),
            no_signal_reasons=reasons,
        )
    assert len(result) == 1
    spec = result[0]
    assert spec["symbol"] == "SPY"
    assert spec["direction"] == "bullish"
    assert spec["signal_type"] == "macd_bullish_crossover"
    assert spec["market_context"]["source"] == "evaluator.macd_crossover"


# ---------------------------------------------------------------------------
# _mean_reversion_signal_specs — feature flag guards
# ---------------------------------------------------------------------------

_MR_BASE_CONFIG = {
    "type": "mean_reversion",
    "timeframe": "5Min",
    "lookback_minutes": 480,
    "bollinger_period": 5,
}


def test_mean_reversion_evaluators_disabled_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = False
        mock_settings.mean_reversion_evaluator_enabled = True
        result = _mean_reversion_signal_specs(
            "test-strategy",
            _MR_BASE_CONFIG,
            ["SPY"],
            market_data_client=None,
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("SIGNAL_EVALUATORS_ENABLED=false" in r for r in reasons)


def test_mean_reversion_evaluator_disabled_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.mean_reversion_evaluator_enabled = False
        result = _mean_reversion_signal_specs(
            "test-strategy",
            _MR_BASE_CONFIG,
            ["SPY"],
            market_data_client=None,
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("MEAN_REVERSION_EVALUATOR_ENABLED=false" in r for r in reasons)


# ---------------------------------------------------------------------------
# _mean_reversion_signal_specs — data + evaluator paths
# ---------------------------------------------------------------------------


def test_mean_reversion_no_bars_returns_empty() -> None:
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.mean_reversion_evaluator_enabled = True
        result = _mean_reversion_signal_specs(
            "test-strategy",
            _MR_BASE_CONFIG,
            ["SPY"],
            market_data_client=_mock_client({}),
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("no usable bars" in r for r in reasons)


def test_mean_reversion_evaluator_no_signal_flat_prices() -> None:
    stock_bars = _make_stock_bars("SPY", [100.0] * 25)
    reasons: list[str] = []
    with patch("app.services.signal_scanner.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.mean_reversion_evaluator_enabled = True
        result = _mean_reversion_signal_specs(
            "test-strategy",
            _MR_BASE_CONFIG,
            ["SPY"],
            market_data_client=_mock_client({"SPY": stock_bars}),
            no_signal_reasons=reasons,
        )
    assert result == []
    assert any("produced no signal" in r for r in reasons)


def test_mean_reversion_creates_signal_through_routing() -> None:
    stock_bars = _make_stock_bars("SPY", [100.0] * 25)
    reasons: list[str] = []
    candidate = SignalCandidate(
        symbol="SPY",
        strategy_type="mean_reversion",
        signal_type="mean_reversion_lower_band_recovery",
        direction="bullish",
        confidence=Decimal("0.62"),
        rationale="SPY touched the lower Bollinger Band and closed back inside with bullish confirmation",
        features={"dedupe_minutes": 240},
        dedupe_key="SPY:mean_reversion:mean_reversion_lower_band_recovery:bullish",
    )
    with patch("app.services.signal_scanner.settings") as mock_settings, patch.object(
        MeanReversionEvaluator, "evaluate", return_value=candidate
    ):
        mock_settings.signal_evaluators_enabled = True
        mock_settings.mean_reversion_evaluator_enabled = True
        result = _mean_reversion_signal_specs(
            "test-strategy",
            _MR_BASE_CONFIG,
            ["SPY"],
            market_data_client=_mock_client({"SPY": stock_bars}),
            no_signal_reasons=reasons,
        )
    assert len(result) == 1
    spec = result[0]
    assert spec["symbol"] == "SPY"
    assert spec["direction"] == "bullish"
    assert spec["signal_type"] == "mean_reversion_lower_band_recovery"
    assert spec["market_context"]["source"] == "evaluator.mean_reversion"
