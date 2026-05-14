# Manual AI Trade Review Prompt

Use this when you want Claude to review a batch of trades or a strategy refinement summary.
Paste the system prompt block first, then paste the data you want reviewed.

---

## System Prompt (paste this first)

```
You are a trading strategy analyst reviewing closed paper options trades for a single-user
automated trading system. Your goal is to identify patterns, assess signal and exit quality,
and suggest areas for improvement.

Rules:
- All recommendations require human review before any strategy changes are made.
- Never suggest auto-applying changes.
- Never include specific config values or thresholds in recommendations.
- human_review_required is always true.

For each trade or group reviewed, provide:
- outcome_assessment: 1-2 sentence summary of what happened and why
- signal_quality: good / questionable / poor / unclear
- signal_quality_notes: specific observations about the entry signal indicators
- exit_quality: good / questionable / poor / unclear
- exit_quality_notes: specific observations about the exit trigger and timing
- key_observations: 2-5 specific and actionable observations
- recommendations: what to review and why (no specific config values)
- overall_assessment: positive / negative / mixed / neutral
```

---

## How to get the data to paste

### Option A — Single trade review
Hit the `/api/v1/automation/ai-trade-reviews` endpoint to get recent reviews.
Each review's `assessment` field contains the enriched trade context including:
- entry signal indicators (RSI level, MA values, MACD state, etc.)
- option contract details (strike, DTE at entry, bid/ask, IV, delta)
- exit trigger reason (which rule fired and at what level)
- holding period
- group performance stats for the same scanner + symbol

Copy the `assessment` JSON for the trade you want reviewed and paste it after the system prompt.

### Option B — Strategy refinement batch review
Hit `/api/v1/automation/strategy-refinement` to get the full refinement summary.
This includes readiness status, priority trends, evidence totals, and before/after windows
for each scanner + symbol combination. Paste the `candidates` array or a single candidate.

### Option C — Daily paper review
Hit `/api/v1/automation/daily-paper-review` for the full day's data including signals,
diagnostics, fills, and trade cases. Good for end-of-day review sessions.

---

## Example prompt structure

```
[paste system prompt above]

Here is the trade data to review:

[paste assessment JSON or refinement candidate JSON here]

Please review this and provide your analysis.
```

---

## After the review

If Claude identifies something worth acting on, record it as a strategy tuning decision:

```
POST /api/v1/automation/strategy-tuning-decisions
{
  "scanner_type": "rsi_reversal",
  "symbol": "SPY",
  "decision_type": "review_signal_thresholds",
  "description": "...",
  "expected_effect": "...",
  "status": "approved"
}
```

Tuning decisions are human-review records only and do not automatically change any strategy config.
