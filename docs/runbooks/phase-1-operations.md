# Phase 1 operations runbook

This runbook covers the paper-trading Phase 1 operating flow for the deployed `stocks-api` service.

Phase 1 means the system is running paper options trading only. The goal is safe scheduled execution, observable behavior, and enough daily evidence to tune scanner thresholds and option-selection settings.

## Key URLs

```text
Production app: https://stocks-api-z11i.onrender.com/
Health: https://stocks-api-z11i.onrender.com/health
API health: https://stocks-api-z11i.onrender.com/api/v1/health
Readiness: https://stocks-api-z11i.onrender.com/api/v1/ready
```

Protected endpoints require:

```text
Authorization: Bearer <ADMIN_API_TOKEN>
```

## Required safety posture

Before scheduled paper-auto trading is considered safe, verify these settings:

```text
ALPACA_PAPER=true
AUTO_SUBMIT_REQUIRES_PAPER=true
TRADING_AUTOMATION_ENABLED=true
MARKET_CYCLE_SUBMIT_ENABLED=true
SCHEDULED_JOBS_ENABLED=true
```

Emergency switches:

| Purpose | Setting |
|---|---|
| Stop all auto-submit | `TRADING_AUTOMATION_ENABLED=false` |
| Stop entry submits only | `MARKET_CYCLE_SUBMIT_ENABLED=false` |
| Stop cron runner execution | `SCHEDULED_JOBS_ENABLED=false` |
| Pause exit automation | `MARKET_CYCLE_EXIT_ENABLED=false` |

Paper safety settings should stay enabled:

```text
ALPACA_PAPER=true
AUTO_SUBMIT_REQUIRES_PAPER=true
```

## After every deploy

Run these checks:

```bash
curl.exe --ssl-no-revoke -L --max-time 90 https://stocks-api-z11i.onrender.com/health
```

```bash
curl.exe --ssl-no-revoke -L --max-time 90 https://stocks-api-z11i.onrender.com/api/v1/health
```

```bash
curl.exe --ssl-no-revoke -L --max-time 90 \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  https://stocks-api-z11i.onrender.com/api/v1/ready
```

Then check automation status:

```bash
curl.exe --ssl-no-revoke -L --max-time 90 \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  https://stocks-api-z11i.onrender.com/api/v1/automation/status
```

Review blockers before allowing market crons to run.

## Daily morning pre-market check

Before market open:

1. Confirm Render service is healthy.
2. Confirm readiness passes.
3. Confirm automation status shows paper mode and expected safety caps.
4. Confirm no unexpected failed job runs from the prior session.
5. Confirm emergency switches are set intentionally.

Useful calls:

```bash
curl.exe --ssl-no-revoke -L --max-time 90 \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  https://stocks-api-z11i.onrender.com/api/v1/automation/status
```

```bash
curl.exe --ssl-no-revoke -L --max-time 90 \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  "https://stocks-api-z11i.onrender.com/api/v1/automation/daily-paper-review?date=YYYY-MM-DD"
```

## Manual job calls

Manual SPY entry-cycle test:

```bash
curl.exe --ssl-no-revoke -L --max-time 120 -X POST \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  "https://stocks-api-z11i.onrender.com/api/v1/jobs/market-entry-cycle?symbol=SPY&scan_limit=100&order_limit=100&fill_page_size=100"
```

Manual exits test:

```bash
curl.exe --ssl-no-revoke -L --max-time 120 -X POST \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  "https://stocks-api-z11i.onrender.com/api/v1/jobs/market-cycle-exits?limit=100&order_limit=100&fill_page_size=100&phase_timeout_seconds=45"
```

Manual maintenance run:

```bash
curl.exe --ssl-no-revoke -L --max-time 180 -X POST \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  "https://stocks-api-z11i.onrender.com/api/v1/jobs/market-maintenance?phase=auto&fill_page_size=100&news_enabled=false"
```

Manual AI review writer:

```bash
curl.exe --ssl-no-revoke -L --max-time 120 -X POST \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  "https://stocks-api-z11i.onrender.com/api/v1/jobs/write-ai-trade-reviews?limit=100"
```

## Scheduled cron expectations

Current symbol entry jobs:

```text
SPY, QQQ, AAPL, MSFT, NVDA
```

Entry jobs run symbol-specific `market-entry-cycle` calls. Exit and maintenance jobs remain global.

Expected current EDT behavior:

- Entry cycles: about 10:00am through 3:55pm Eastern, staggered by symbol.
- Exit cycle: about 9:00am through 4:59pm Eastern.
- Maintenance: pre-market and post-market.

Render cron schedules are UTC-only and need review around DST changes.

## Post-market review

After market close and post-market maintenance:

1. Check daily paper review.
2. Check option-selection diagnostics summary.
3. Check paper review snapshots.
4. Check trade cases.
5. Check AI reviews and pending strategy suggestions.

Daily review:

```bash
curl.exe --ssl-no-revoke -L --max-time 120 \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  "https://stocks-api-z11i.onrender.com/api/v1/automation/daily-paper-review?date=YYYY-MM-DD&limit=5000"
```

Option-selection diagnostics summary:

```bash
curl.exe --ssl-no-revoke -L --max-time 120 \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  "https://stocks-api-z11i.onrender.com/api/v1/automation/option-selection-diagnostics/summary?date=YYYY-MM-DD&limit=5000"
```

Paper review snapshots:

```bash
curl.exe --ssl-no-revoke -L --max-time 120 \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  "https://stocks-api-z11i.onrender.com/api/v1/automation/paper-review-snapshots?limit=5"
```

AI reviews:

```bash
curl.exe --ssl-no-revoke -L --max-time 120 \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  "https://stocks-api-z11i.onrender.com/api/v1/automation/ai-trade-reviews?limit=100"
```

Pending suggestions:

```bash
curl.exe --ssl-no-revoke -L --max-time 120 \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  "https://stocks-api-z11i.onrender.com/api/v1/automation/strategy-change-suggestions?status=pending&limit=100"
```

## What to inspect in the daily review

Check these sections first:

```text
summary
jobs.failed
signals.by_scanner_type_status
signals.preview_error_codes
previews.by_status
orders.by_status
fills.by_symbol
option_selection_diagnostics.reason_counts
trade_cases.closed
ai_reviews.suggestions_by_status
paper_review_snapshot
```

Common interpretations:

| Symptom | Likely next check |
|---|---|
| Signals but no previews | `signals.preview_error_codes` and option diagnostics |
| Many `preview_rejected` signals | option-selection diagnostic reason counts |
| Orders submitted but no fills | broker orders status and Alpaca paper account |
| Fills but no trade cases | post-market maintenance / trade-case population |
| Trade cases but no AI reviews | AI review writer job status |
| No snapshot | post-market maintenance status |

## Option-selection tuning review

Use the diagnostics summary to answer:

- Which symbol is failing most often?
- Which scanner type is producing unusable contracts?
- Are failures mostly open interest, spread, notional, quote availability, or strike/expiration matching?
- Are preview profiles too strict for a scanner type?

Do not tune randomly. Tune based on grouped rejection counts and paper-trade outcomes.

## Resetting paper data

Use reset only when intentionally clearing paper-trading state. Prefer dry-run first.

Local script for a new Alpaca paper account:

```bash
python scripts/reset_paper_account_data.py
python scripts/reset_paper_account_data.py --apply --confirm RESET_TRADING_DATA
```

The reset preserves `strategies`. By default it clears runtime trading tables plus
old `job_runs` and `audit_logs`, then writes a fresh reset job/audit record. Pass
`--keep-history` to retain job and audit history.

Dry run:

```bash
curl.exe --ssl-no-revoke -L --max-time 120 -X POST \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  "https://stocks-api-z11i.onrender.com/api/v1/jobs/reset-trading-data?dry_run=true"
```

Actual reset:

```bash
curl.exe --ssl-no-revoke -L --max-time 120 -X POST \
  -H "Authorization: Bearer %ADMIN_API_TOKEN%" \
  "https://stocks-api-z11i.onrender.com/api/v1/jobs/reset-trading-data?dry_run=false&confirm=RESET_TRADING_DATA"
```

## Rollback guidance

If a deploy breaks health/readiness:

1. Disable scheduled jobs or auto-submit using Render env vars if needed.
2. Identify the last known good commit on `master`.
3. Revert or deploy the known good commit.
4. Re-run health, readiness, and automation status checks.
5. Do not re-enable automation until readiness and safety checks pass.

## Phase 1 release gate

Phase 1 is operationally complete when:

- Health and readiness pass after deploys.
- Paper-only safety settings are verified.
- Entry, exit, and maintenance crons run on schedule.
- Daily paper review explains what happened each market day.
- Option diagnostics explain skipped/rejected trades.
- Trade cases and paper review snapshots are created post-market.
- Emergency stops are documented and tested.
- Basic route/service tests pass locally.
