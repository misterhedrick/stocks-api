# Incomplete work / pending project notes

This file captures work that is known to be incomplete or intentionally deferred so it is not lost between sessions.

## Completed: per-symbol entry cron limit increase

The five symbol-specific `market-entry-cycle` cron paths in `render.yaml` have been updated from:

```text
scan_limit=25&order_limit=25&fill_page_size=50
```

to:

```text
scan_limit=100&order_limit=100&fill_page_size=100
```

Symbols affected:

- SPY
- QQQ
- AAPL
- MSFT
- NVDA

`OPTIONS_CANDIDATE_LIMIT` is already set to `100`, and the symbol-specific cron `JOB_PATH` values now use matching scan, order, and fill page-size limits.

Do not change the old combined `stocks-api-market-cycle` fallback job unless intentionally re-enabling it at full size.

## Incomplete AI review layer

Implemented:

- `trade_cases` table and ORM model.
- `ai_trade_reviews` table and ORM model.
- `strategy_change_suggestions` table and ORM model.
- `app/services/trade_cases.py` for FIFO-matched closed round trips.
- Post-market maintenance populates trade cases in an isolated transaction.

Not implemented yet:

- AI review service that reads `trade_cases`.
- Writer that stores generated `ai_trade_reviews`.
- Writer that stores `strategy_change_suggestions`.
- Rejected-signal / rejected-preview outcome comparison.
- Any human-approval workflow for accepting or rejecting AI suggestions.

Important rule: AI may recommend strategy changes only. It must not directly modify live strategy logic or deployed trading behavior.

## Signal scanner cleanup still pending

`app/services/signal_scanner.py` still needs physical cleanup of direct legacy scanner branches and helper functions for:

- `price_threshold`
- `percent_change`
- `trend_confirmation`

The file is large, so use a safe local patch/diff workflow rather than replacing the whole file from truncated connector output.

## Paper testing and tuning still pending

After legacy scanner cleanup and cron-limit review:

- Paper-test the full evaluator-backed strategy set.
- Compare signal volume by scanner type.
- Compare no-signal reasons by scanner type.
- Review `option_selection_diagnostics` for rejected contract candidates.
- Tune scanner thresholds by strategy type.
- Tune `PAPER_PREVIEW_PROFILE_<PROFILE>_*` settings by strategy type.

## Option contract selection improvements still pending

Current option selection is first-pass and still needs better scoring/filters as broker data allows:

- Delta / Greeks-aware selection.
- Moneyness-aware selection.
- Better liquidity scoring.
- Quote-quality scoring.
- More structured comparison between rejected candidates and closed trade outcomes.

## Testing / local infrastructure still pending

Not implemented yet:

- Real DB integration test suite.
- Local Docker Compose/Postgres helper.
- Formal state enums/state machine for currently string-based statuses.

## Operational limitations still present

- News scanning is lightweight RSS/headline gating only.
- Render cron schedules are UTC-only and must be reviewed around DST changes.
- Symbol-specific entry crons may increase monthly Render cost because each cron service can count separately.
- The old combined `stocks-api-market-cycle` entry cron should be disabled in Render once symbol-specific entry crons are confirmed healthy, if entries should come only from split jobs.
