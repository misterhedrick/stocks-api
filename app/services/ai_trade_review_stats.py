from __future__ import annotations

from decimal import Decimal

from typing import Any

from app.db.models import TradeCase

def _trade_case_group_stats(trade_cases: list[TradeCase]) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[TradeCase]] = {}
    for trade_case in trade_cases:
        scanner_type = _scanner_type_for_trade_case(trade_case)
        symbol = str(trade_case.underlying_symbol or trade_case.symbol).upper()
        grouped.setdefault((scanner_type, symbol), []).append(trade_case)

    return {
        key: _group_stats_for_cases(key, cases)
        for key, cases in grouped.items()
    }

def _group_stats_for_cases(
    key: tuple[str, str],
    cases: list[TradeCase],
) -> dict[str, Any]:
    realized_values = [Decimal(str(case.realized_pl or "0")) for case in cases]
    losses = [value for value in realized_values if value < 0]
    wins = [value for value in realized_values if value > 0]
    total = sum(realized_values, Decimal("0"))
    return {
        "scanner_type": key[0],
        "symbol": key[1],
        "trade_cases_seen": len(cases),
        "wins": len(wins),
        "losses": len(losses),
        "flats": len(cases) - len(wins) - len(losses),
        "total_realized_pl": str(total),
        "average_realized_pl": str(total / Decimal(len(cases))) if cases else "0",
        "win_rate_percent": str((Decimal(len(wins)) / Decimal(len(cases)) * Decimal("100")) if cases else Decimal("0")),
    }

def _empty_group_stats(key: tuple[str, str]) -> dict[str, Any]:
    return {
        "scanner_type": key[0],
        "symbol": key[1],
        "trade_cases_seen": 0,
        "wins": 0,
        "losses": 0,
        "flats": 0,
        "total_realized_pl": "0",
        "average_realized_pl": "0",
        "win_rate_percent": "0",
    }

def _scanner_type_for_trade_case(trade_case: TradeCase) -> str:
    context = trade_case.context if isinstance(trade_case.context, dict) else {}
    entry_context = context.get("entry") if isinstance(context.get("entry"), dict) else {}
    signal_context = (
        entry_context.get("signal")
        if isinstance(entry_context.get("signal"), dict)
        else {}
    )
    market_context = (
        signal_context.get("market_context")
        if isinstance(signal_context.get("market_context"), dict)
        else {}
    )
    return str(market_context.get("strategy_type") or "unknown")

def _group_summary_text(group_context: dict[str, Any]) -> str:
    if not group_context:
        return "No grouped paper-trade context is available yet."
    return (
        "Grouped context: "
        f"{group_context.get('trade_cases_seen', 0)} recent cases, "
        f"{group_context.get('wins', 0)} wins, "
        f"{group_context.get('losses', 0)} losses, "
        f"total P/L {group_context.get('total_realized_pl', '0')}."
    )
