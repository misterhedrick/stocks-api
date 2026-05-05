from __future__ import annotations

from app.services.signals.evaluators.base import SignalEvaluator
from app.services.signals.evaluators.momentum import MomentumRateOfChangeEvaluator

_REGISTRY: dict[str, SignalEvaluator] = {
    MomentumRateOfChangeEvaluator.strategy_type: MomentumRateOfChangeEvaluator(),
}


def get_evaluator(strategy_type: str) -> SignalEvaluator | None:
    return _REGISTRY.get(strategy_type)
