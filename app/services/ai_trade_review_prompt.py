from __future__ import annotations

from typing import Any


_SYSTEM_PROMPT = """\
You are a trading strategy analyst reviewing closed paper options trades for a single-user automated trading system. \
Your goal is to identify patterns, assess signal and exit quality, and suggest areas for improvement. \
All recommendations require human review before any strategy changes are made — you must never suggest auto-applying changes.

Respond ONLY with a valid JSON object matching this exact structure:
{
  "outcome_assessment": "<1-2 sentence summary of what happened and why>",
  "signal_quality": "good" | "questionable" | "poor" | "unclear",
  "signal_quality_notes": "<specific observations about the entry signal indicators>",
  "exit_quality": "good" | "questionable" | "poor" | "unclear",
  "exit_quality_notes": "<specific observations about the exit trigger and timing>",
  "key_observations": ["<observation 1>", "<observation 2>", ...],
  "recommendations": [
    {
      "type": "<recommendation_type>",
      "description": "<what to review and why, no specific config values>",
      "human_review_required": true
    }
  ],
  "overall_assessment": "positive" | "negative" | "mixed" | "neutral"
}

Rules:
- key_observations should have 2-5 items, each specific and actionable
- recommendations may be empty if there is nothing to improve
- never include specific config values or thresholds in recommendations
- never suggest automatic application of any change
- human_review_required must always be true
"""


def build_trade_review_prompt(assessment: dict[str, Any]) -> str:
    lines: list[str] = []

    lines.append("## Trade to Review")
    lines.append(f"Symbol: {assessment.get('underlying_symbol') or assessment.get('symbol')}")
    lines.append(f"Option: {assessment.get('symbol')}")
    lines.append(f"Scanner type: {assessment.get('scanner_type')}")
    lines.append(f"Outcome: {assessment.get('outcome')}")
    lines.append(f"Realized P/L: {assessment.get('realized_pl')} ({assessment.get('realized_pl_percent')}%)")

    entry_notional = assessment.get("entry_notional")
    exit_notional = assessment.get("exit_notional")
    if entry_notional:
        lines.append(f"Entry notional: ${entry_notional}")
    if exit_notional:
        lines.append(f"Exit notional: ${exit_notional}")

    holding = assessment.get("holding_period") or {}
    if holding.get("holding_hours") is not None:
        lines.append(f"Holding period: {holding['holding_hours']}h ({holding['holding_minutes']}min)")

    entry_option = assessment.get("entry_option") or {}
    if entry_option:
        lines.append("\n## Option Contract at Entry")
        _append_if(lines, "Contract type", entry_option.get("contract_type"))
        _append_if(lines, "Strike", entry_option.get("strike"))
        _append_if(lines, "Entry price (premium)", entry_option.get("entry_price"))
        _append_if(lines, "DTE at entry", entry_option.get("dte_at_entry"))
        _append_if(lines, "Bid", entry_option.get("bid"))
        _append_if(lines, "Ask", entry_option.get("ask"))
        _append_if(lines, "Spread", entry_option.get("spread"))
        _append_if(lines, "IV", entry_option.get("iv"))
        _append_if(lines, "Delta", entry_option.get("delta"))
        _append_if(lines, "Open interest", entry_option.get("open_interest"))
        if entry_option.get("rationale"):
            lines.append(f"Selection rationale: {entry_option['rationale']}")

    entry_signal = assessment.get("entry_signal") or {}
    if entry_signal:
        lines.append("\n## Entry Signal")
        _append_if(lines, "Signal type", entry_signal.get("signal_type"))
        _append_if(lines, "Direction", entry_signal.get("direction"))
        _append_if(lines, "Confidence", entry_signal.get("confidence"))
        if entry_signal.get("rationale"):
            lines.append(f"Rationale: {entry_signal['rationale']}")
        indicators = entry_signal.get("indicators") or {}
        if indicators:
            lines.append("Indicators at signal time:")
            for key, val in indicators.items():
                if key not in ("strategy_type",):
                    lines.append(f"  {key}: {val}")

    exit_trigger = assessment.get("exit_trigger") or {}
    if exit_trigger:
        lines.append("\n## Exit")
        _append_if(lines, "Trigger reason", exit_trigger.get("trigger_reason"))
        _append_if(lines, "Exit bid", exit_trigger.get("exit_bid"))
        _append_if(lines, "Exit ask", exit_trigger.get("exit_ask"))

    group = assessment.get("group_context") or {}
    if group.get("trade_cases_seen", 0) > 0:
        lines.append("\n## Group Performance (same scanner + symbol, recent trades)")
        lines.append(f"Trades seen: {group['trade_cases_seen']}")
        lines.append(f"Wins: {group['wins']} | Losses: {group['losses']} | Flats: {group['flats']}")
        lines.append(f"Win rate: {group.get('win_rate_percent')}%")
        lines.append(f"Total P/L: {group.get('total_realized_pl')}")
        lines.append(f"Average P/L: {group.get('average_realized_pl')}")

    snapshot = assessment.get("snapshot_context") or {}
    diagnostic_reasons = snapshot.get("diagnostic_reasons") or {}
    if diagnostic_reasons:
        lines.append("\n## Option Selection Diagnostics (recent rejections)")
        for reason, count in diagnostic_reasons.items():
            lines.append(f"  {reason}: {count}")

    rejected_shadows = snapshot.get("rejected_shadow_outcomes") or []
    if rejected_shadows:
        lines.append("\n## Rejected Signal Shadow Outcomes (signals that were filtered but later moved)")
        for item in rejected_shadows[:5]:
            direction = item.get("direction", "?")
            move = item.get("underlying_move_percent", "?")
            outcome = item.get("directional_outcome", "?")
            lines.append(f"  Direction={direction} move={move}% outcome={outcome}")

    lines.append("\n## Instructions")
    lines.append(
        "Review this trade. Assess signal quality, exit quality, and the overall outcome. "
        "Note any patterns with the group-level data. If diagnostics or rejected shadow outcomes "
        "suggest filter problems, call that out. Respond with the JSON structure specified."
    )

    return "\n".join(lines)


def _append_if(lines: list[str], label: str, value: Any) -> None:
    if value is not None and value != "":
        lines.append(f"{label}: {value}")
