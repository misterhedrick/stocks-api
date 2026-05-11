from __future__ import annotations

from decimal import Decimal

from typing import Any

from sqlalchemy import select

from sqlalchemy.orm import Session

from app.db.models import PaperReviewSnapshot, TradeCase

from app.services.ai_trade_review_stats import _empty_group_stats, _group_summary_text

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
    confidence = "medium"
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
        "confidence": confidence,
        "realized_pl": str(realized_pl),
        "realized_pl_percent": str(realized_pl_percent),
        "group_context": stats,
        "observations": observations,
        "risk_notes": risk_notes,
        "snapshot_context": snapshot_context,
        "recommendation_boundary": "Suggestions are pending human review and must not be applied automatically.",
    }

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
