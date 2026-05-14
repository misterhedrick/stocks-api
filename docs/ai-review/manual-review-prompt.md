# Manual AI Trade Review — How To

Works with Claude (claude.ai) and ChatGPT (chat.openai.com / GPT-4o).

---

## Steps

**1. Open a new chat in Claude or ChatGPT.**

**2. Paste the system prompt below.**
- In Claude: paste it into the System Prompt box before starting the chat.
- In ChatGPT: paste it at the very top of your first message, before the data.

**3. Pull the data you want reviewed from the API.**

Pick one depending on what you want to look at:

- **Single trade** — `GET /api/v1/automation/ai-trade-reviews`
  Copy one review's `assessment` block. It includes entry signal indicators, option details (strike, DTE, IV, delta), exit trigger reason, holding period, and group win/loss stats.

- **Strategy refinement** — `GET /api/v1/automation/strategy-refinement`
  Copy one or more items from the `candidates` array. Good when a scanner/symbol has been flagging for several days.

- **End-of-day** — `GET /api/v1/automation/daily-paper-review`
  Paste the `trade_cases`, `signals`, and `option_selection_diagnostics` sections for a full day session.

**4. Paste the data into the chat and ask for the review.**

```
[system prompt — Claude: system box / ChatGPT: top of message]

Here is the trade data I want you to review:

[paste data here]

Please review this and give me your analysis.
```

**5. If the review surfaces something worth acting on, record it as a tuning decision.**

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
