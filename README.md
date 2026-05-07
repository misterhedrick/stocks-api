# stocks-api

Single-user FastAPI backend for stock/options paper trading with Alpaca, Postgres, and Render cron jobs.

## Current status

- **Primary deploy branch:** `master`
- **Active development branch:** `develop`
- **Render URL:** `https://stocks-api-z11i.onrender.com/`
- **Current mode:** paper trading first; live trading is a later target.
- **Broker/data:** Alpaca paper trading and Alpaca market data.
- **Database:** Postgres with SQLAlchemy/Alembic.
- **Auth:** single admin bearer token through `ADMIN_API_TOKEN`.

## Local setup

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item env.example .env
.\run-local.ps1
```

Git Bash:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp env.example .env
./run-local.sh
```

Run migrations manually:

```bash
python -m alembic upgrade head
```

Run tests:

```bash
python -m pytest
```

`pytest` is included in `requirements.txt` because the evaluator test suite contains pytest-style tests. Fallback for unittest-only coverage if pytest is unavailable:

```bash
python -m unittest discover -s tests
```

## Health checks

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/health
curl -H "Authorization: Bearer change-me" http://127.0.0.1:8000/api/v1/ready
```

On Windows, Render checks may need:

```bash
curl.exe --ssl-no-revoke -L --max-time 90 https://stocks-api-z11i.onrender.com/health
```

## Main API areas

- `/api/v1/strategies` - create/list/update strategy rows.
- `/api/v1/signals` - create/list/update signal rows.
- `/api/v1/order-intents` - preview, submit, and cancel option order intents.
- `/api/v1/options/select-contract` - select an active tradable Alpaca option contract.
- `/api/v1/jobs/*` - protected operational jobs for reconciliation, scans, cycles, exits, maintenance, resets, and trade-case population.
- `/api/v1/automation/*` - read-only operational dashboards for readiness, positions, lifecycle, performance, trade cases, and learning reports.

## Trading pipeline

Current execution model:

```text
strategy config
-> scanner creates signal
-> preview creates order_intent
-> optional auto-submit places Alpaca paper order
-> broker reconciliation imports orders/fills/positions
-> exit cycle evaluates managed positions
-> post-market maintenance populates trade_cases
```

All execution flows through previewed `order_intents` before orders are submitted.

Broker reconciliation imports Alpaca orders, FILL account activities, and positions. Alpaca's `/v2/account/activities/FILL` endpoint has a `page_size` maximum of **100** when no explicit date filter is used, so API routes cap `fill_page_size` at 100 and reconciliation paginates with Alpaca `page_token` values instead of requesting larger pages. Reconciliation continues through FILL pages until no next page is available, records pagination metadata in job summaries, and fill inserts remain idempotent through the unique Alpaca fill id.

Deterministic Alpaca validation errors such as 400/422 responses are mapped as client/configuration errors instead of transient 502s. Render job retries remain enabled for transient 429/500/502/503/504 responses, but known validation-style failure bodies such as page-size-limit errors are treated as non-retryable.

When option contract selection fails before an `order_intent` can be previewed, the app does not create a fake order intent. It emits structured selection-failure logs and stores an `option_selection_diagnostics` row grouped by signal, strategy, underlying symbol, scanner type, and preview profile. These diagnostics include candidate rejection reason counts such as missing/low open interest, notional cap, wide spread, missing quote, no usable two-sided quote, unavailable quote, no expiration/strike match, and not-tradable contracts.

## Current Render cron topology

Render cron schedules are UTC and are not DST-aware.

| Service | Purpose | Endpoint | Schedule |
|---|---|---|---|
| `stocks-api-market-cycle` | Entry cycle: scan -> reconcile/news/preview/submit | `POST /api/v1/jobs/market-cycle?scan_limit=25&order_limit=25&fill_page_size=50` | `*/10 14-19 * * 1-5` |
| `stocks-api-market-exits` | Exit protection: reconcile -> exit-eval -> exit-submit | `POST /api/v1/jobs/market-cycle-exits?limit=100&order_limit=100&fill_page_size=100&phase_timeout_seconds=45` | `*/1 13-20 * * 1-5` |
| `stocks-api-market-maintenance` | Pre/post-market maintenance and trade-case population | `POST /api/v1/jobs/market-maintenance?phase=auto&fill_page_size=100&news_enabled=false` | `30 12,21 * * 1-5` |

Current EDT behavior:

- Entry cycle (`market-cycle`) runs **every 10 minutes** from **10:00am through 3:50pm Eastern**.
- Exit cycle (`market-exits`) runs every minute from about **9:00am through 4:59pm Eastern**.
- Maintenance runs pre-market (8:30am ET) and post-market (5:30pm EDT / 4:30pm EST).

### market-cycle schedule: always 10 minutes

**Do not shorten `market-cycle` below 10 minutes.** The full scan → preview → reconcile → submit cycle can take 60–120+ seconds under normal load. Shorter intervals cause overlapping cron invocations, which compounds timeout pressure and can produce Render 502/504 errors.

The `market-cycle` endpoint is protected by a PostgreSQL advisory lock (`pg_try_advisory_xact_lock`). If one invocation is already running, the next one returns immediately with `status: skipped, reason: already_running`. However, frequent overlapping invocations still waste cron capacity and load the free-tier web service.

Only reduce the interval after timing logs (`timings` field in the response) confirm that the cycle consistently completes well inside the interval. Example: if `total_seconds` is routinely under 40s, a 5-minute schedule might be safe. If `total_seconds` is over 60s, keep it at 10 minutes.

**Temporary safety limits** (lower scan/order limits reduce per-run work while the system is being tuned):

```
scan_limit=25
order_limit=25
fill_page_size=50
```

Restore to `scan_limit=100&order_limit=100&fill_page_size=100` only after timing confirms safe headroom.

**Do not confuse `market-cycle` with `market-exits`:**

- `market-cycle`: entry scans, previews, and submits. Runs every **10 minutes**. Has the advisory lock.
- `market-exits`: exit evaluations only. Runs every **1 minute**. Time-sensitive; shorter interval is intentional.

**Runtime budget:** `MARKET_CYCLE_PHASE_TIMEOUT_SECONDS=120` (env var on the web service). The cycle aborts gracefully after this many seconds and returns `status: partial`. Completed phases are preserved. Increase this setting only if timing logs show consistently slow but necessary work.

**Runner retries:** `JOB_RETRY_DELAYS_SECONDS` is intentionally empty for `market-cycle`. Render will invoke the job again in 10 minutes anyway; retrying a timed-out cycle just piles on more overlapping load.

When Eastern time switches back to EST, the entry cron should be reviewed. The equivalent 10:00am through 3:50pm EST schedule is:

```yaml
schedule: "*/10 15-20 * * 1-5"
```

## Emergency stops

Any one of these is enough to halt the relevant automation:

| Action | Switch |
|---|---|
| Stop all auto-submit | `TRADING_AUTOMATION_ENABLED=false` |
| Stop entry submits only | `MARKET_CYCLE_SUBMIT_ENABLED=false` |
| Stop cron runner execution | `SCHEDULED_JOBS_ENABLED=false` |
| Pause exit automation | `MARKET_CYCLE_EXIT_ENABLED=false` |

Paper safety settings that should remain enabled:

```text
ALPACA_PAPER=true
AUTO_SUBMIT_REQUIRES_PAPER=true
```

Automation risk caps:

```text
MAX_AUTO_ORDERS_PER_CYCLE
MAX_AUTO_ORDERS_PER_DAY
MAX_OPEN_POSITIONS
MAX_OPEN_POSITIONS_PER_SYMBOL
MAX_CONTRACTS_PER_ORDER
MAX_ESTIMATED_PREMIUM_PER_ORDER
```

## Preview profiles by signal strategy type

Contract selection can now use env-backed preview profiles by strategy type instead of relying only on hard-coded strategy DB values.

Profile names currently used:

```text
price_threshold
percent_change
moving_average
trend_confirmation
momentum_rate_of_change
rsi_reversal
macd_crossover
mean_reversion
breakout_price_threshold
volume_confirmed_breakout
volatility_squeeze
support_resistance
```

Env format:

```text
PAPER_PREVIEW_PROFILE_<PROFILE>_<SETTING>
```

Examples:

```text
PAPER_PREVIEW_PROFILE_MOVING_AVERAGE_MIN_OPEN_INTEREST=50
PAPER_PREVIEW_PROFILE_MOVING_AVERAGE_MAX_ESTIMATED_NOTIONAL=3000
PAPER_PREVIEW_PROFILE_MOVING_AVERAGE_MAX_SPREAD_PERCENT=20
PAPER_PREVIEW_PROFILE_TREND_CONFIRMATION_MAX_ESTIMATED_NOTIONAL=3500
PAPER_PREVIEW_PROFILE_RSI_REVERSAL_MAX_ESTIMATED_NOTIONAL=2500
PAPER_PREVIEW_PROFILE_VOLUME_CONFIRMED_BREAKOUT_MAX_SPREAD_PERCENT=20
```

The existing production strategies were patched with `scanner.preview.preview_profile` using the GitHub Actions workflow and the result was:

```text
Committed. updated=43 skipped=0
```

Manual workflow:

```text
Actions -> Update Strategy Preview Profiles -> Run workflow
```

Workflow file:

```text
.github/workflows/update-strategy-preview-profiles.yml
```

Script used by the workflow:

```bash
python scripts/update_strategy_preview_profiles.py --dry-run
python scripts/update_strategy_preview_profiles.py
```

## Current seeded universe

`seed_paper_trade_universe.py` seeds the paper trading strategy universe — it creates call and put strategies for each ticker across all scanner types, stores them in the `strategies` table, and sets their `scanner.preview` config (DTE range, spread, notional, and OI limits). It defaults to five core liquid symbols:

```text
SPY, QQQ, NVDA, AAPL, MSFT
```

The GitHub Actions workflow also defaults to the same five-symbol set, and both the script and workflow accept explicit symbol overrides.

The seed script creates call/put variants for:

```text
moving_average
trend_confirmation
momentum_rate_of_change
rsi_reversal
macd_crossover
mean_reversion
breakout_price_threshold
volume_confirmed_breakout
volatility_squeeze
support_resistance
```

Each seeded strategy tags `scanner.preview.preview_profile` from the scanner type.

Current paper data-gathering profile:

```text
strictness_level=0.50
max_estimated_notional=5000
max_notional_per_order=5000
min_open_interest=25
max_spread=0.35
max_spread_percent=35
dedupe_minutes=60
```

This profile is intentionally loose enough to collect paper-trade outcomes, including losing trades, while keeping execution in the Alpaca paper sandbox.

## Signal strategy planning docs

Detailed signal strategy specs live under:

```text
docs/signal-strategies/
```

Each strategy now has its own folder with `description.md`, `deep-dive.md`, and `tuning.md`. Strategies with implementation notes also include `implementation-note.md`.

The docs describe purpose, inputs, formulas, bullish/bearish rules, rejection rules, confidence scoring, feature payloads, pseudocode, tests, and human/AI-friendly tuning guidance.

Shared references, including global option selection settings and diagnostics, live under `docs/signal-strategies/shared/` and are linked from the per-strategy tuning guides.

## Signal evaluator foundation

The app contains a reusable signal evaluation foundation:

```text
app/services/signals/candles.py
app/services/signals/indicators.py
app/services/signals/evaluators/base.py
app/services/signals/evaluators/registry.py
app/services/signals/evaluators/momentum.py
app/services/signals/evaluators/moving_average.py
app/services/signals/evaluators/rsi.py
app/services/signals/evaluators/macd.py
app/services/signals/evaluators/mean_reversion.py
app/services/signals/evaluators/breakout.py
app/services/signals/evaluators/volume_breakout.py
app/services/signals/evaluators/volatility_squeeze.py
app/services/signals/evaluators/support_resistance.py
```

Tests:

```text
tests/services/signals/test_indicators.py
tests/services/signals/test_momentum_evaluator.py
tests/services/signals/test_moving_average_evaluator.py
tests/services/signals/test_rsi_evaluator.py
tests/services/signals/test_macd_evaluator.py
tests/services/signals/test_mean_reversion_evaluator.py
tests/services/signals/test_breakout_evaluator.py
tests/services/signals/test_volume_breakout_evaluator.py
tests/services/signals/test_volatility_squeeze_evaluator.py
tests/services/signals/test_support_resistance_evaluator.py
tests/services/signals/test_signal_scanner_evaluator.py
```

Implemented shared indicator helpers:

```text
SMA
EMA
RSI
MACD
Bollinger Bands
ATR
percent_change
```

Implemented evaluators:

```text
MomentumRateOfChangeEvaluator
MovingAverageTrendEvaluator
RsiReversalEvaluator
MacdCrossoverEvaluator
MeanReversionEvaluator
BreakoutPriceThresholdEvaluator
VolumeConfirmedBreakoutEvaluator
VolatilitySqueezeEvaluator
SupportResistanceEvaluator
```

The evaluator registry currently includes:

```text
momentum_rate_of_change
moving_average
rsi_reversal
macd_crossover
mean_reversion
breakout_price_threshold
volume_confirmed_breakout
volatility_squeeze
support_resistance
```

The live scanner routes all of those `scanner.type` values through evaluator-backed scan paths. Legacy hand-built scanner paths still exist for `price_threshold`, `percent_change`, and `trend_confirmation`.

Evaluator feature flags:

```text
SIGNAL_EVALUATORS_ENABLED
MOMENTUM_EVALUATOR_ENABLED
MOVING_AVERAGE_EVALUATOR_ENABLED
RSI_EVALUATOR_ENABLED
MACD_EVALUATOR_ENABLED
MEAN_REVERSION_EVALUATOR_ENABLED
BREAKOUT_PRICE_THRESHOLD_EVALUATOR_ENABLED
VOLUME_CONFIRMED_BREAKOUT_EVALUATOR_ENABLED
VOLATILITY_SQUEEZE_EVALUATOR_ENABLED
SUPPORT_RESISTANCE_EVALUATOR_ENABLED
```

Review note: `app/services/signal_scanner.py` is a large file. Some GitHub connector reads may truncate it before the evaluator helper implementations. When reviewing or editing scanner routing, use a local checkout or otherwise verify the complete file before making full-file replacements.

## AI review layer

Implemented:

- `trade_cases` table and ORM model.
- `ai_trade_reviews` and `strategy_change_suggestions` tables and ORM models.
- `option_selection_diagnostics` table and ORM model for rejected preview/contract-selection context.
- `app/services/trade_cases.py` to idempotently populate closed FIFO round trips.
- Post-market maintenance automatically populates trade cases in an isolated transaction.

Not implemented yet:

- AI review service that reads `trade_cases` and writes `ai_trade_reviews` / `strategy_change_suggestions`.
- Automatic strategy changes from AI suggestions. AI recommendations are recommendation-only; strategy logic changes remain human-approved and must not be applied automatically.

## Important limitations / next work

Current high-priority next steps:

1. Paper-test the full evaluator-backed strategy set and tune scanner thresholds / preview-profile limits by strategy type.
2. Improve option contract selection with better liquidity/moneyness/delta-style scoring.
3. Compare `option_selection_diagnostics` with `trade_cases` so AI review can learn from rejected previews as well as closed round trips.
4. Add real DB integration tests or a local Docker Compose/Postgres helper.
5. Build AI trade review service using persisted `trade_cases`.

Known limitations:

- Option contract selection is still first-pass and can reject many candidates due open interest, notional, spread, or quote quality.
- Render only needs explicit evaluator env vars when overriding defaults; `render.yaml` lists them so deployed behavior is visible.
- News scanning is lightweight RSS/headline gating only.
- Statuses are plain strings, not a formal enum/state machine.

## Useful manual job calls

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/jobs/market-cycle?scan_limit=100&order_limit=100&fill_page_size=100" \
  -H "Authorization: Bearer change-me"
```

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/jobs/market-cycle-exits?limit=100&order_limit=100&fill_page_size=100&phase_timeout_seconds=45" \
  -H "Authorization: Bearer change-me"
```

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/jobs/market-maintenance?phase=auto&news_enabled=false" \
  -H "Authorization: Bearer change-me"
```

```bash
curl -H "Authorization: Bearer change-me" \
  "http://127.0.0.1:8000/api/v1/automation/status"
```
