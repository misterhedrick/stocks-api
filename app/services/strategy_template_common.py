from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from typing import Any

from app.core.config import settings

LIQUID_OPTIONS_UNIVERSE = (
    "SPY",
    "QQQ",
    "NVDA",
    "TSLA",
    "IWM",
    "AMZN",
    "MSFT",
    "GOOGL",
    "META",
    "AAPL",
)

def required_template_symbols() -> list[str]:
    return ["SPY", "QQQ"]

def _preview_config(
    *,
    symbol: str,
    option_type: str,
    target_strike: Decimal,
    rationale: str,
    max_estimated_notional: str | None = None,
    max_spread: str | None = None,
    max_spread_percent: str | None = None,
    min_open_interest: int | None = None,
    min_quote_size: int = 1,
    min_days_to_expiration: int = 2,
    max_days_to_expiration: int = 30,
) -> dict[str, Any]:
    return {
        "enabled": True,
        "underlying_symbol": symbol,
        "option_type": option_type,
        "min_days_to_expiration": min_days_to_expiration,
        "max_days_to_expiration": max_days_to_expiration,
        "target_strike": _decimal_string(target_strike),
        "side": "buy",
        "quantity": 1,
        "order_type": "limit",
        "time_in_force": "day",
        "data_feed": "indicative",
        "max_estimated_notional": max_estimated_notional
        or _decimal_string(settings.paper_strategy_max_estimated_notional),
        "max_spread": max_spread or _decimal_string(settings.paper_strategy_max_spread),
        "max_spread_percent": max_spread_percent
        or _decimal_string(settings.paper_strategy_max_spread_percent),
        "min_open_interest": min_open_interest
        if min_open_interest is not None
        else settings.paper_strategy_min_open_interest,
        "min_quote_size": min_quote_size,
        "limit": 20,
        "rationale": rationale,
    }

def _submit_config(*, max_notional_per_order: str | None = None) -> dict[str, Any]:
    return {
        "enabled": True,
        "max_orders_per_cycle": 1,
        "max_contracts_per_order": 1,
        "max_contracts_per_cycle": 1,
        "max_notional_per_order": max_notional_per_order
        or _decimal_string(settings.paper_strategy_max_estimated_notional),
        "max_open_contracts_per_symbol": 1,
        "max_open_contracts_per_strategy": 2,
        "max_orders_per_trading_day": 1,
        "trading_day_timezone": "America/New_York",
        "trade_windows": [
            {
                "timezone": "America/New_York",
                "start": "10:00",
                "end": "16:00",
            }
        ],
        "allowed_sides": ["buy"],
    }

def _exit_config(
    *,
    profit_target_percent: str | None = None,
    stop_loss_percent: str | None = None,
    max_spread: str = "0.25",
) -> dict[str, Any]:
    return {
        "enabled": True,
        "profit_target_percent": profit_target_percent
        or _decimal_string(settings.paper_strategy_profit_target_percent),
        "stop_loss_percent": stop_loss_percent
        or _decimal_string(settings.paper_strategy_stop_loss_percent),
        "max_days_to_expiration": 1,
        "max_contracts_per_exit": 1,
        "order_type": "limit",
        "limit_price_source": "bid",
        "time_in_force": "day",
        "data_feed": "indicative",
        "max_spread": max_spread,
        "submit": {
            "enabled": True,
            "max_orders_per_cycle": 1,
            "max_contracts_per_order": 1,
            "max_contracts_per_cycle": 1,
            "max_notional_per_order": "100000.00",
            "max_orders_per_trading_day": 3,
            "trading_day_timezone": "America/New_York",
            "trade_windows": [
                {
                    "timezone": "America/New_York",
                    "start": "10:00",
                    "end": "16:00",
                }
            ],
            "allowed_sides": ["sell"],
        },
    }

def _market_regime_config(direction: str) -> dict[str, Any]:
    return {
        "enabled": True,
        "symbols": ["SPY", "QQQ"],
        "bullish_min_change_percent": "0.025",
        "bearish_max_change_percent": "-0.025",
        "direction": direction,
    }

def _whole_dollar(price: Decimal) -> Decimal:
    return price.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

def _decimal_string(value: Decimal) -> str:
    return str(value)
