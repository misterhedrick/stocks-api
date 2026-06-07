from __future__ import annotations

from typing import Any

SIGNAL_ONLY_SCANNER_TYPES = frozenset(
    {
        "market_regime_filter",
        "pairs_relative_value",
        "options_spread_candidate",
    }
)


def scanner_config_for_strategy(strategy: Any) -> dict[str, Any]:
    config = getattr(strategy, "config", {})
    if not isinstance(config, dict):
        return {}
    scanner = config.get("scanner")
    return scanner if isinstance(scanner, dict) else {}


def scanner_type_for_strategy(strategy: Any) -> str:
    scanner = scanner_config_for_strategy(strategy)
    scanner_type = scanner.get("type")
    if isinstance(scanner_type, str) and scanner_type.strip():
        return scanner_type.strip().lower()
    return "unknown"


def is_signal_only_scanner_type(scanner_type: object) -> bool:
    return str(scanner_type or "").strip().lower() in SIGNAL_ONLY_SCANNER_TYPES


def is_signal_only_strategy(strategy: Any) -> bool:
    return is_signal_only_scanner_type(scanner_type_for_strategy(strategy))
