from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.strategy_template_advanced import (
    build_market_regime_filter_strategy_payload,
    build_opening_range_breakout_strategy_payload,
    build_options_spread_candidate_strategy_payload,
    build_pairs_relative_value_strategy_payload,
    build_relative_strength_strategy_payload,
    build_time_series_momentum_strategy_payload,
    build_vwap_reclaim_strategy_payload,
)
from app.services.strategy_template_breakout import (
    build_breakout_price_threshold_strategy_payload,
    build_support_resistance_strategy_payload,
    build_volatility_squeeze_strategy_payload,
    build_volume_confirmed_breakout_strategy_payload,
)
from app.services.strategy_template_common import (
    LIQUID_OPTIONS_UNIVERSE,
    _decimal_string,
    _exit_config,
    _market_regime_config,
    _preview_config,
    _submit_config,
    _whole_dollar,
    required_template_symbols,
)
from app.services.strategy_template_trend import (
    build_macd_crossover_strategy_payload,
    build_mean_reversion_strategy_payload,
    build_momentum_rate_of_change_strategy_payload,
    build_moving_average_strategy_payload,
    build_rsi_reversal_strategy_payload,
)


def build_preview_first_strategy_payloads(
    *,
    prices: dict[str, Decimal],
) -> list[dict[str, Any]]:
    return [
        build_moving_average_strategy_payload(
            symbol="SPY",
            target_strike=_whole_dollar(prices["SPY"]),
        ),
        build_momentum_rate_of_change_strategy_payload(
            symbol="SPY",
            target_strike=_whole_dollar(prices["SPY"]),
        ),
        build_breakout_price_threshold_strategy_payload(
            symbol="SPY",
            target_strike=_whole_dollar(prices["SPY"]),
        ),
        build_rsi_reversal_strategy_payload(
            symbol="QQQ",
            target_strike=_whole_dollar(prices["QQQ"]),
        ),
        build_macd_crossover_strategy_payload(
            symbol="QQQ",
            target_strike=_whole_dollar(prices["QQQ"]),
        ),
    ]
