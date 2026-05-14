from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PaperReviewSnapshot, TradeCase
from app.services.ai_trade_review_stats import _empty_group_stats, _group_summary_text

# Matches the 6-digit YYMMDD expiration in OCC option symbols e.g. "SPY271219C00500000".
_OCC_EXP_RE = re.compile(r"(\d{2})(\d{2})(\d{2})[CP](\d{8})$")


def _latest_snapshot(db: Session) -> PaperReviewSnapshot | None:
    return db.scalar(
        select(PaperReviewSnapshot)
        .order_by(PaperReviewSnapshot.generated_at.desc())
        .limit(1)
    )


def _assessment_for_trade_case(
    trade_case: TradeCase,
    *,
    latest_snapshot: PaperReviewSnapshot | None,
    review_model: str,
    group_stats: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    realized_pl = Decimal(str(trade_case.realized_pl or "0"))
    realized_pl_percent = Decimal(str(trade_case.realized_pl_percent or "0"))

    context = trade_case.context if isinstance(trade_case.context, dict) else {}
    entry_context = context.get("entry") if isinstance(context.get("entry"), dict) else {}
    exit_context = context.get("exit") if isinstance(context.get("exit"), dict) else {}
    holding_seconds = context.get("holding_seconds")
    entry_notional = context.get("entry_notional")
    exit_notional = context.get("exit_notional")

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
    entry_order_intent = (
        entry_context.get("order_intent")
        if isinstance(entry_context.get("order_intent"), dict)
        else {}
    )
    exit_order_intent = (
        exit_context.get("order_intent")
        if isinstance(exit_context.get("order_intent"), dict)
        else {}
    )
    exit_preview = (
        exit_order_intent.get("preview")
        if isinstance(exit_order_intent.get("preview"), dict)
        else {}
    )

    scanner_type = market_context.get("strategy_type") or "unknown"
    symbol = trade_case.underlying_symbol or trade_case.symbol
    group_key = (str(scanner_type), str(symbol).upper())
    stats = (group_stats or {}).get(group_key, _empty_group_stats(group_key))
    snapshot_context = _snapshot_context_for_trade(
        latest_snapshot,
        scanner_type=str(scanner_type),
        symbol=str(symbol),
    )

    outcome = "win" if realized_pl > 0 else "loss" if realized_pl < 0 else "flat"
    observations = [
        f"Closed trade outcome is {outcome}.",
        f"Realized P/L: {realized_pl}; return: {realized_pl_percent}%.",
    ]
    if snapshot_context["diagnostic_reasons"]:
        observations.append(
            "Recent option-selection diagnostics exist for this scanner/symbol."
        )
    if snapshot_context["rejected_shadow_outcomes"]:
        observations.append(
            "Rejected-signal shadow outcomes are available for comparison."
        )

    risk_notes: list[str] = []
    if realized_pl < 0:
        risk_notes.append("Loss should be compared with entry signal quality and option selection filters.")
    if abs(realized_pl_percent) >= Decimal("25"):
        risk_notes.append("Large percentage move; review sizing, spread, and exit timing.")
    if snapshot_context["diagnostic_reasons"]:
        risk_notes.append("Rejected candidates may indicate liquidity or spread pressure.")

    return {
        "review_model": review_model,
        "review_status": "generated",
        "trade_case_id": str(trade_case.id),
        "strategy_id": str(trade_case.strategy_id) if trade_case.strategy_id else None,
        "symbol": trade_case.symbol,
        "underlying_symbol": trade_case.underlying_symbol,
        "scanner_type": scanner_type,
        "outcome": outcome,
        "confidence": "medium",
        "realized_pl": str(realized_pl),
        "realized_pl_percent": str(realized_pl_percent),
        "entry_notional": str(entry_notional) if entry_notional is not None else None,
        "exit_notional": str(exit_notional) if exit_notional is not None else None,
        "holding_period": _holding_period(holding_seconds),
        "entry_signal": _entry_signal_summary(signal_context, market_context),
        "entry_option": _entry_option_summary(entry_order_intent, trade_case),
        "exit_trigger": _exit_trigger_summary(exit_order_intent, exit_preview),
        "group_context": stats,
        "observations": observations,
        "risk_notes": risk_notes,
        "snapshot_context": snapshot_context,
        "recommendation_boundary": "Suggestions are pending human review and must not be applied automatically.",
    }

def _holding_period(holding_seconds: Any) -> dict[str, Any]:
    try:
        secs = int(holding_seconds)
    except (TypeError, ValueError):
        return {"holding_seconds": None, "holding_hours": None, "holding_minutes": None}
    return {
        "holding_seconds": secs,
        "holding_hours": round(secs / 3600, 2),
        "holding_minutes": round(secs / 60, 1),
    }


def _entry_signal_summary(
    signal_context: dict[str, Any],
    market_context: dict[str, Any],
) -> dict[str, Any]:
    indicator_keys = (
        "rsi", "macd", "macd_signal", "macd_hist",
        "sma_short", "sma_long", "ema_short", "ema_long",
        "upper_band", "lower_band", "atr",
        "percent_change", "volume_ratio",
        "strategy_type", "direction",
    )
    indicators = {
        k: market_context[k]
        for k in indicator_keys
        if k in market_context and market_context[k] is not None
    }
    # Capture any other numeric/string scanner-specific keys not in the fixed list.
    for k, v in market_context.items():
        if k not in indicators and isinstance(v, (int, float, str, bool)):
            indicators[k] = v

    return {
        "signal_type": signal_context.get("signal_type"),
        "direction": signal_context.get("direction"),
        "confidence": signal_context.get("confidence"),
        "rationale": signal_context.get("rationale"),
        "indicators": indicators,
    }


def _entry_option_summary(
    entry_order_intent: dict[str, Any],
    trade_case: TradeCase,
) -> dict[str, Any]:
    preview = (
        entry_order_intent.get("preview")
        if isinstance(entry_order_intent.get("preview"), dict)
        else {}
    )
    option_symbol = trade_case.symbol
    entry_price = str(trade_case.entry_price) if trade_case.entry_price is not None else None
    dte_at_entry = _dte_from_symbol(option_symbol, trade_case.entry_time)

    contract_type = None
    strike = None
    occ_match = _OCC_EXP_RE.search(option_symbol)
    if occ_match:
        raw_strike = occ_match.group(4)
        try:
            strike = str(Decimal(raw_strike) / Decimal("1000"))
        except Exception:
            pass
        cp_idx = occ_match.start() - 1
        if 0 <= cp_idx < len(option_symbol):
            contract_type = "call" if option_symbol[cp_idx].upper() == "C" else "put"

    return {
        "option_symbol": option_symbol,
        "contract_type": contract_type,
        "strike": strike,
        "entry_price": entry_price,
        "dte_at_entry": dte_at_entry,
        "bid": _str_or_none(preview.get("bid")),
        "ask": _str_or_none(preview.get("ask")),
        "spread": _str_or_none(preview.get("spread")),
        "iv": _str_or_none(preview.get("iv") or preview.get("implied_volatility")),
        "delta": _str_or_none(preview.get("delta")),
        "open_interest": preview.get("open_interest"),
        "rationale": entry_order_intent.get("rationale"),
    }


def _exit_trigger_summary(
    exit_order_intent: dict[str, Any],
    exit_preview: dict[str, Any],
) -> dict[str, Any]:
    trigger_reason = (
        exit_preview.get("trigger_reason")
        or exit_order_intent.get("rationale")
    )
    return {
        "trigger_reason": trigger_reason,
        "rationale": exit_order_intent.get("rationale"),
        "exit_bid": _str_or_none(exit_preview.get("bid")),
        "exit_ask": _str_or_none(exit_preview.get("ask")),
    }


def _dte_from_symbol(option_symbol: str, entry_time: Any) -> int | None:
    match = _OCC_EXP_RE.search(option_symbol)
    if match is None:
        return None
    try:
        yy, mm, dd = int(match.group(1)), int(match.group(2)), int(match.group(3))
        exp_date = datetime(2000 + yy, mm, dd, tzinfo=timezone.utc).date()
        if isinstance(entry_time, datetime):
            entry_date = entry_time.astimezone(timezone.utc).date()
        else:
            return None
        return (exp_date - entry_date).days
    except Exception:
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return str(Decimal(str(value)))
    except Exception:
        return str(value) if value != "" else None


def _snapshot_context_for_trade(
    snapshot: PaperReviewSnapshot | None,
    *,
    scanner_type: str,
    symbol: str,
) -> dict[str, Any]:
    if snapshot is None:
        return {
            "paper_review_snapshot_id": None,
            "diagnostic_reasons": {},
            "rejected_trade_comparisons": [],
            "rejected_shadow_outcomes": [],
        }

    diagnostics = snapshot.diagnostics if isinstance(snapshot.diagnostics, dict) else {}
    diagnostic_summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
    rejected = snapshot.rejected_outcomes if isinstance(snapshot.rejected_outcomes, dict) else {}

    return {
        "paper_review_snapshot_id": str(snapshot.id),
        "diagnostic_reasons": diagnostic_summary.get("reason_counts", {}),
        "rejected_trade_comparisons": [
            item
            for item in rejected.get("trade_comparison", [])
            if _matches_scanner_symbol(item, scanner_type=scanner_type, symbol=symbol)
        ][:10],
        "rejected_shadow_outcomes": [
            item
            for item in rejected.get("shadow_market_movement", [])
            if _matches_scanner_symbol(item, scanner_type=scanner_type, symbol=symbol)
        ][:10],
    }

def _matches_scanner_symbol(
    item: Any,
    *,
    scanner_type: str,
    symbol: str,
) -> bool:
    if not isinstance(item, dict):
        return False
    return (
        str(item.get("scanner_type") or "unknown") == scanner_type
        and str(item.get("symbol") or "").upper() == symbol.upper()
    )

def _suggestions_for_assessment(
    trade_case: TradeCase,
    assessment: dict[str, Any],
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    outcome = assessment.get("outcome")
    scanner_type = assessment.get("scanner_type")
    symbol = assessment.get("underlying_symbol") or assessment.get("symbol")
    group_context = assessment.get("group_context") if isinstance(assessment.get("group_context"), dict) else {}
    group_summary = _group_summary_text(group_context)

    if outcome == "loss":
        suggestions.append(
            {
                "suggestion_type": "review_strategy_risk_controls",
                "description": (
                    f"Review {scanner_type} risk controls for {symbol}: this closed "
                    "trade lost money. Compare signal features, exit timing, spread, "
                    f"and notional sizing before changing config. {group_summary}"
                ),
                "proposed_config_patch": {},
            }
        )

    snapshot_context = assessment.get("snapshot_context", {})
    if isinstance(snapshot_context, dict) and snapshot_context.get("diagnostic_reasons"):
        suggestions.append(
            {
                "suggestion_type": "review_option_selection_filters",
                "description": (
                    f"Review option-selection filters for {scanner_type} {symbol}; "
                    "recent diagnostics show rejected candidates. Consider liquidity, "
                    f"spread, moneyness, and notional limits with human approval. {group_summary}"
                ),
                "proposed_config_patch": {},
            }
        )

    if (
        isinstance(snapshot_context, dict)
        and snapshot_context.get("rejected_shadow_outcomes")
    ):
        suggestions.append(
            {
                "suggestion_type": "review_rejected_signal_outcomes",
                "description": (
                    f"Review rejected-signal shadow outcomes for {scanner_type} {symbol}; "
                    f"some rejected signals have later market movement evidence. {group_summary}"
                ),
                "proposed_config_patch": {},
            }
        )

    if not suggestions:
        suggestions.append(
            {
                "suggestion_type": "monitor_strategy",
                "description": (
                    f"Monitor {scanner_type} {symbol}; no immediate config patch is "
                    "recommended from this single trade case."
                ),
                "proposed_config_patch": {},
            }
        )

    # Deduplicate suggestion types while preserving order.
    seen: set[str] = set()
    unique = []
    for suggestion in suggestions:
        suggestion_type = suggestion["suggestion_type"]
        if suggestion_type in seen:
            continue
        seen.add(suggestion_type)
        unique.append(suggestion)
    return unique
