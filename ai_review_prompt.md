# AI Trade Review - IDE Task

When told to run a trade review, execute these steps directly using the project tools.
Do not ask the user to copy/paste anything.

---

## Steps

1. **Load the auth token** from `.env` (`ADMIN_API_TOKEN`).

2. **Pull trade review data** from the running API. Use whichever fits the request:
   - Recent trade reviews: `GET /api/v1/automation/ai-trade-reviews`
   - Strategy refinement candidates: `GET /api/v1/automation/strategy-refinement`
   - Full day: `GET /api/v1/automation/daily-review`

   If no base URL is specified, use `http://127.0.0.1:8000`.
   If API data is unavailable, report what failed and use local evidence only when it is safe and relevant.

3. **Analyze the data** using these criteria:

   **Signal quality** - Was the entry setup valid? Look at the indicator values in `entry_signal.indicators`
   (RSI level, MA crossover distance, MACD state, etc.) relative to the scanner type and direction.

   **Exit quality** - Did the exit fire at the right time? Check `exit_trigger.trigger_reason`
   (stop loss, profit target, DTE limit, hold time) and whether the holding period was too short or too long.

   **Outcome vs. setup** - Did the result match what the signal suggested? A loss on a clean signal
   is different from a loss on a weak signal.

   **Group patterns** - Check `group_context` for the same scanner + symbol. A single bad trade in
   a strong group is noise. Repeated losses in the same group are a pattern.

   **Option selection** - If `snapshot_context.diagnostic_reasons` is populated, flag whether
   spread, liquidity, or moneyness filters are rejecting too many candidates.

   **Runtime/data health** - Flag missing snapshots, stale maintenance, failed jobs, or reconciliation
   gaps that could distort the review.

4. **Summarize findings** for each trade or group reviewed:
   - Outcome assessment (what happened and why)
   - Signal quality: good / questionable / poor / unclear
   - Exit quality: good / questionable / poor / unclear
   - Key observations (2-5 specific points)
   - Recommendations (what to review, no specific config values)
   - Overall: positive / negative / mixed / neutral

5. **Record anything actionable** as a tuning decision:

   ```http
   POST /api/v1/automation/strategy-tuning-decisions
   Authorization: Bearer <token>
   Content-Type: application/json
   ```

   ```json
   {
     "scanner_type": "<scanner>",
     "symbol": "<symbol>",
     "decision_type": "<review_signal_thresholds | review_option_selection_filters | review_exit_rules | monitor_strategy>",
     "description": "<what was observed and what to review>",
     "expected_effect": "<what improvement would look like>",
     "evidence_snapshot_ids": ["<snapshot id if available>"],
     "evidence_summary": {
       "pattern": "<short evidence summary>",
       "trade_count": "<count if available>",
       "diagnostic_reasons": ["<reason if available>"]
     },
     "status": "approved"
   }
   ```

   Only record decisions where there is a clear pattern or specific concern.
   Do not record a decision for every trade. Tuning decisions do not automatically change any config.

---

## Rules

- Never suggest auto-applying config changes.
- Never include specific threshold values in recommendations.
- All recommendations are for human review only.
- Keep the review paper-only unless the user explicitly asks for a different scope.
- If evidence is thin or the data pipeline is unhealthy, say so and recommend monitoring or fixing data health before tuning.
