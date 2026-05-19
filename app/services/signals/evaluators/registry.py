from __future__ import annotations

from app.services.signals.evaluators.base import SignalEvaluator
from app.services.signals.evaluators.advanced import (
    MarketRegimeFilterEvaluator,
    OpeningRangeBreakoutEvaluator,
    OptionsSpreadCandidateEvaluator,
    PairsRelativeValueEvaluator,
    RelativeStrengthEvaluator,
    TimeSeriesMomentumEvaluator,
    VwapReclaimEvaluator,
)
from app.services.signals.evaluators.breakout import BreakoutPriceThresholdEvaluator
from app.services.signals.evaluators.macd import MacdCrossoverEvaluator
from app.services.signals.evaluators.mean_reversion import MeanReversionEvaluator
from app.services.signals.evaluators.momentum import MomentumRateOfChangeEvaluator
from app.services.signals.evaluators.moving_average import MovingAverageTrendEvaluator
from app.services.signals.evaluators.rsi import RsiReversalEvaluator
from app.services.signals.evaluators.support_resistance import SupportResistanceEvaluator
from app.services.signals.evaluators.volatility_squeeze import VolatilitySqueezeEvaluator
from app.services.signals.evaluators.volume_breakout import VolumeConfirmedBreakoutEvaluator

_REGISTRY: dict[str, SignalEvaluator] = {
    MomentumRateOfChangeEvaluator.strategy_type: MomentumRateOfChangeEvaluator(),
    MovingAverageTrendEvaluator.strategy_type: MovingAverageTrendEvaluator(),
    RsiReversalEvaluator.strategy_type: RsiReversalEvaluator(),
    MacdCrossoverEvaluator.strategy_type: MacdCrossoverEvaluator(),
    MeanReversionEvaluator.strategy_type: MeanReversionEvaluator(),
    BreakoutPriceThresholdEvaluator.strategy_type: BreakoutPriceThresholdEvaluator(),
    VolumeConfirmedBreakoutEvaluator.strategy_type: VolumeConfirmedBreakoutEvaluator(),
    VolatilitySqueezeEvaluator.strategy_type: VolatilitySqueezeEvaluator(),
    SupportResistanceEvaluator.strategy_type: SupportResistanceEvaluator(),
    VwapReclaimEvaluator.strategy_type: VwapReclaimEvaluator(),
    OpeningRangeBreakoutEvaluator.strategy_type: OpeningRangeBreakoutEvaluator(),
    RelativeStrengthEvaluator.strategy_type: RelativeStrengthEvaluator(),
    TimeSeriesMomentumEvaluator.strategy_type: TimeSeriesMomentumEvaluator(),
    MarketRegimeFilterEvaluator.strategy_type: MarketRegimeFilterEvaluator(),
    PairsRelativeValueEvaluator.strategy_type: PairsRelativeValueEvaluator(),
    OptionsSpreadCandidateEvaluator.strategy_type: OptionsSpreadCandidateEvaluator(),
}


def get_evaluator(strategy_type: str) -> SignalEvaluator | None:
    return _REGISTRY.get(strategy_type)
