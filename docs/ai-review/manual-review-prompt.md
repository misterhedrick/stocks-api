# Manual AI Trade Review Prompt

Works with Claude (claude.ai) and ChatGPT (chat.openai.com / GPT-4o).

**Claude:** paste the system prompt into the System Prompt box, then paste the data in the chat.
**ChatGPT:** no separate system prompt box in the web UI — just paste the system prompt at the top of your message, followed by the data.

---

## System Prompt

```
You are a trading strategy analyst reviewing closed paper options trades for a
single-user automated trading system. Your goal is to identify patterns, assess
signal and exit quality, and suggest areas for improvement.

Rules:
- All recommendations require human review before any strategy changes are made.
- Never suggest auto-applying changes.
- Never include specific config values or thresholds in recommendations.
- human_review_required is always true.

For each trade or group reviewed, respond in plain English with these sections:

OUTCOME ASSESSMENT
1-2 sentences on what happened and why.

SIGNAL QUALITY: good / questionable / poor / unclear
Notes on the entry signal indicators — was the setup valid?

EXIT QUALITY: good / questionable / poor / unclear
Notes on the exit trigger — did it fire at the right time?

KEY OBSERVATIONS
2-5 specific, actionable observations about this trade or group.

RECOMMENDATIONS
What to review and why. No specific config values. Note if human review is required.

OVERALL: positive / negative / mixed / neutral
```

---

## What data to paste

### Single trade review
Hit the API and copy a single review's `assessment` block:

```
GET /api/v1/automation/ai-trade-reviews
```

Each `assessment` includes:
- scanner type, symbol, direction (call/put)
- entry signal indicators at trade time (RSI level, MA values, MACD state, etc.)
- option contract details (strike, DTE at entry, bid/ask, IV, delta)
- exit trigger reason (which rule fired and at what P&L level)
- holding period
- group win/loss stats for the same scanner + symbol

### Strategy refinement batch review
```
GET /api/v1/automation/strategy-refinement
```
Paste one or more `candidates` from the response. Each candidate has readiness status,
priority trend across recent days, evidence totals, and focus recommendations.
Good for reviewing a scanner/symbol that has been flagging for a few days.

### End-of-day review
```
GET /api/v1/automation/daily-paper-review
```
Paste the full response or just the `trade_cases`, `signals`, and `option_selection_diagnostics`
sections for a focused session.

---

## Example message structure

```
[paste system prompt here]

Here is the trade data I want you to review:

[paste assessment JSON or refinement candidate here]

Please review this trade and give me your analysis.
```

---

## Recording the outcome

If something is worth acting on, record it as a tuning decision:

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

Tuning decisions are human-review records only — they do not automatically change any strategy config.
