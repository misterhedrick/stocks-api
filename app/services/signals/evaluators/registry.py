from __future__ import annotations

from app.services.signals.evaluators.base import SignalEvaluator
from app.services.signals.evaluators.breakout import BreakoutPriceThresholdEvaluator
from app.services.signals.evaluators.macd import MacdCrossoverEvaluator
from app.services.signals.evaluators.mean_reversion import MeanReversionEvaluator
from app.services.signals.evaluators.momentum import MomentumRateOfChangeEvaluator
from app.services.signals.evaluators.moving_average import MovingAverageTrendEvaluator
from app.services.signals.evaluators.rsi import RsiReversalEvaluator

_REGISTRY: dict[str, SignalEvaluator] = {
    MomentumRateOfChangeEvaluator.strategy_type: MomentumRateOfChangeEvaluator(),
    MovingAverageTrendEvaluator.strategy_type: MovingAverageTrendEvaluator(),
    RsiReversalEvaluator.strategy_type: RsiReversalEvaluator(),
    MacdCrossoverEvaluator.strategy_type: MacdCrossoverEvaluator(),
    MeanReversionEvaluator.strategy_type: MeanReversionEvaluator(),
    BreakoutPriceThresholdEvaluator.strategy_type: BreakoutPriceThresholdEvaluator(),
}


def get_evaluator(strategy_type: str) -> SignalEvaluator | None:
    return _REGISTRY.get(strategy_type)
