from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.integrations.alpaca import AlpacaStockBar, AlpacaStockBars
from app.services.signal_scanner_evaluator_advanced import _advanced_evaluator_signal_specs
from app.services.signals.candles import Candle, CandleFrame
from app.services.signals.evaluators.advanced import (
    MarketRegimeFilterEvaluator,
    OpeningRangeBreakoutEvaluator,
    OptionsSpreadCandidateEvaluator,
    PairsRelativeValueEvaluator,
    RelativeStrengthEvaluator,
    TimeSeriesMomentumEvaluator,
    VwapReclaimEvaluator,
)
from app.services.signals.indicators import IndicatorFrame


def _frame(symbol: str, closes: list[float], volumes: list[float] | None = None) -> CandleFrame:
    start = datetime(2026, 5, 18, 14, 0, tzinfo=timezone.utc)
    candles = []
    for index, close in enumerate(closes):
        volume = 1000 if volumes is None else volumes[index]
        candles.append(
            Candle(
                ts=start + timedelta(minutes=index * 5),
                open=Decimal(str(close - 0.05)),
                high=Decimal(str(close + 0.10)),
                low=Decimal(str(close - 0.10)),
                close=Decimal(str(close)),
                volume=Decimal(str(volume)),
            )
        )
    return CandleFrame(symbol=symbol, timeframe="5Min", candles=tuple(candles))


def _indicators(frame: CandleFrame) -> IndicatorFrame:
    return IndicatorFrame(
        close=frame.closes,
        high=frame.highs,
        low=frame.lows,
        volume=frame.volumes,
    )


def _stock_bars(symbol: str, closes: list[float]) -> AlpacaStockBars:
    start = datetime(2026, 5, 18, 14, 0, tzinfo=timezone.utc)
    bars = [
        AlpacaStockBar.model_validate(
            {
                "o": close - 0.05,
                "h": close + 0.10,
                "l": close - 0.10,
                "c": close,
                "v": 1000,
                "t": (start + timedelta(minutes=index * 5)).isoformat(),
            }
        )
        for index, close in enumerate(closes)
    ]
    return AlpacaStockBars(symbol=symbol, bars=bars, raw_response=[])


def test_vwap_reclaim_bullish_signal() -> None:
    frame = _frame("SPY", [100, 99.8, 99.7, 100.4], [1000, 1000, 1000, 4000])
    candidate = VwapReclaimEvaluator().evaluate(
        symbol="SPY",
        config={"max_distance_percent": "2.0"},
        candles=frame,
        indicators=_indicators(frame),
    )
    assert candidate is not None
    assert candidate.strategy_type == "vwap_reclaim"
    assert candidate.direction == "bullish"


def test_opening_range_breakout_bullish_signal() -> None:
    frame = _frame("QQQ", [100, 100.2, 100.1, 100.15, 100.55])
    candidate = OpeningRangeBreakoutEvaluator().evaluate(
        symbol="QQQ",
        config={"range_candles": 3, "breakout_buffer_percent": "0.05"},
        candles=frame,
        indicators=_indicators(frame),
    )
    assert candidate is not None
    assert candidate.signal_type == "opening_range_breakout"


def test_relative_strength_uses_peer_returns() -> None:
    frame = _frame("NVDA", [100, 101])
    candidate = RelativeStrengthEvaluator().evaluate(
        symbol="NVDA",
        config={"min_edge_percent": "0.30"},
        candles=frame,
        indicators=_indicators(frame),
        market_regime={"peer_returns": {"SPY": 0.1, "QQQ": 0.2, "NVDA": 1.1}},
    )
    assert candidate is not None
    assert candidate.signal_type == "relative_strength_leader"


def test_time_series_momentum_bullish_signal() -> None:
    closes = [100 + index * 0.12 for index in range(35)]
    frame = _frame("MSFT", closes)
    candidate = TimeSeriesMomentumEvaluator().evaluate(
        symbol="MSFT",
        config={"lookback_bars": 26, "min_trend_percent": "1.0", "trend_average_window": 10},
        candles=frame,
        indicators=_indicators(frame),
    )
    assert candidate is not None
    assert candidate.direction == "bullish"


def test_market_regime_filter_requires_benchmark_alignment() -> None:
    frame = _frame("AAPL", [100, 100.4])
    candidate = MarketRegimeFilterEvaluator().evaluate(
        symbol="AAPL",
        config={"benchmark_symbols": ["SPY", "QQQ"]},
        candles=frame,
        indicators=_indicators(frame),
        market_regime={"peer_returns": {"SPY": 0.4, "QQQ": 0.3, "AAPL": 0.2}},
    )
    assert candidate is not None
    assert candidate.signal_type == "risk_on_regime_alignment"


def test_pairs_relative_value_fades_outperformance_by_default() -> None:
    frame = _frame("NVDA", [100, 101])
    candidate = PairsRelativeValueEvaluator().evaluate(
        symbol="NVDA",
        config={"benchmark_symbol": "SPY", "min_spread_percent": "0.50"},
        candles=frame,
        indicators=_indicators(frame),
        market_regime={"peer_returns": {"SPY": 0.1, "NVDA": 1.0}},
    )
    assert candidate is not None
    assert candidate.direction == "bearish"
    assert candidate.signal_type == "pair_relative_value_fade"
    assert candidate.features["execution_note"] == "signal_only_until_pair_execution_supported"


def test_options_spread_candidate_marks_signal_only_execution_note() -> None:
    frame = _frame("SPY", [100 + index * 0.1 for index in range(25)])
    candidate = OptionsSpreadCandidateEvaluator().evaluate(
        symbol="SPY",
        config={"min_move_percent": "0.50", "min_atr_percent": "0.05"},
        candles=frame,
        indicators=_indicators(frame),
    )
    assert candidate is not None
    assert candidate.signal_type == "debit_call_spread_candidate"
    assert candidate.features["execution_note"] == "signal_only_until_multileg_orders_are_supported"


def test_advanced_relative_strength_fetches_peers_but_emits_requested_symbol() -> None:
    client = MagicMock()
    client.get_stock_bars.return_value = {
        "SPY": _stock_bars("SPY", [100, 100.1]),
        "QQQ": _stock_bars("QQQ", [100, 100.2]),
        "NVDA": _stock_bars("NVDA", [100, 101.2]),
    }
    reasons: list[str] = []
    with patch("app.services.signal_scanner_evaluator_advanced.settings") as mock_settings:
        mock_settings.signal_evaluators_enabled = True
        mock_settings.relative_strength_evaluator_enabled = True
        specs = _advanced_evaluator_signal_specs(
            "relative_strength",
            {
                "type": "relative_strength",
                "symbols": ["SPY", "QQQ", "NVDA"],
                "_emit_symbols": ["NVDA"],
                "min_edge_percent": "0.30",
            },
            ["SPY", "QQQ", "NVDA"],
            market_data_client=client,
            no_signal_reasons=reasons,
        )

    assert [spec["symbol"] for spec in specs] == ["NVDA"]
    client.get_stock_bars.assert_called_once()
    assert set(client.get_stock_bars.call_args.args[0]) == {"SPY", "QQQ", "NVDA"}
