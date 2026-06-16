# stocks-api

Single-user FastAPI backend for stock/options paper trading with Alpaca, Postgres, and Render cron jobs.

## Git workflow

- All work is done on a feature branch. Feature branches are merged to `develop` when ready.
- Never commit directly to `develop` or `master`.
- All merges from `develop` → `master` require explicit human approval.
- Claude Code must prompt for confirmation before any push or merge targeting `develop` or `master`.

## Current status

- **Primary deploy branch:** `master`
- **Active development branch:** `develop`
- **Render URL:** `https://stocks-api-z11i.onrender.com/`
- **Current mode:** paper-trading first; live trading is a later target.
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

When option contract selection fails before an `order_intent` can be previewed, the app does not create a fake order intent. It emits structured selection-failure logs and stores an `option_selection_diagnostics` row grouped by signal, strategy, underlying symbol, scanner type, and preview profile. These diagnostics include candidate rejection reason counts such as missing/low open interest, notional cap, wide spread, missing quote, no usable two-sided quote, unavailable quote, no expiration/strike match, and not-tradable contracts. Detailed rejected-candidate samples are capped by `OPTIONS_DIAGNOSTIC_CANDIDATE_LIMIT` so Render logs stay readable.

Market-cycle may create signals but no orders when no option contract passes quote, liquidity, spread, or notional filters. Failed auto-preview attempts are tracked on the signal. After `OPTIONS_PREVIEW_MAX_ATTEMPTS` failures, the signal is marked `preview_rejected` and skipped by future preview cycles; this is expected behavior and preserves duplicate signal suppression without retrying the same impossible signal forever.

`OPTIONS_CANDIDATE_LIMIT` controls how many option contracts are requested and quote-checked before giving up. Increasing it can improve contract discovery but increases preview runtime, so this is the first tuning step before loosening open-interest, spread, or notional safety filters. `OPTIONS_CANDIDATE_LIMIT` is already `100`. `OPTIONS_DIAGNOSTIC_CANDIDATE_LIMIT` only caps the number of rejected candidate samples stored/logged for debugging; the current recommended value is `10`.

The `/api/v1/automation/performance` and learning report outputs include paper-trade summaries plus signal and rejection context for tuning. They report signal volume by status, scanner type, and symbol; aggregate no-signal reasons and option-selection diagnostic rejection reasons; compare `preview_rejected` signals with later same-symbol/same-scanner paper round trips; and surface `refinement_candidates` grouped by scanner and symbol. The post-market maintenance run also persists a daily `review_snapshots` row with signals, previews, broker orders, fills, diagnostics, rejected-preview trade comparisons, rejected-signal shadow market movement comparisons, and the generated learning report at `raw_payload.learning_report`. Old review snapshots are pruned during post-market maintenance using `REVIEW_SNAPSHOT_RETENTION_DAYS` (default `45`). Recent snapshots are available at `/api/v1/automation/review-snapshots`. This is for review and tuning only; it does not change strategy logic automatically.

## Current Render cron topology

Render cron schedules are UTC and are not DST-aware.

| Service | Purpose | Endpoint | Schedule |
|---|---|---|---|
| `stocks-api-market-entry-spy` | SPY-only entry cycle: scan -> preview -> submit | `POST /api/v1/jobs/market-entry-cycle?symbol=SPY&scan_limit=100&order_limit=100&fill_page_size=100` | `0-55/5 14-19 * * 1-5` |
| `stocks-api-market-entry-qqq` | QQQ-only entry cycle: scan -> preview -> submit | `POST /api/v1/jobs/market-entry-cycle?symbol=QQQ&scan_limit=100&order_limit=100&fill_page_size=100` | `1-55/5 14-19 * * 1-5` |
| `stocks-api-market-entry-aapl` | AAPL-only entry cycle: scan -> preview -> submit | `POST /api/v1/jobs/market-entry-cycle?symbol=AAPL&scan_limit=100&order_limit=100&fill_page_size=100` | `2-55/5 14-19 * * 1-5` |
| `stocks-api-market-entry-msft` | MSFT-only entry cycle: scan -> preview -> submit | `POST /api/v1/jobs/market-entry-cycle?symbol=MSFT&scan_limit=100&order_limit=100&fill_page_size=100` | `3-55/5 14-19 * * 1-5` |
| `stocks-api-market-entry-nvda` | NVDA-only entry cycle: scan -> preview -> submit | `POST /api/v1/jobs/market-entry-cycle?symbol=NVDA&scan_limit=100&order_limit=100&fill_page_size=100` | `4-55/5 14-19 * * 1-5` |
| `stocks-api-market-exits` | Exit protection: reconcile -> exit-eval -> exit-submit | `POST /api/v1/jobs/market-cycle-exits?limit=100&order_limit=100&fill_page_size=100&phase_timeout_seconds=45` | `*/1 13-19 * * 1-5` |
| `stocks-api-market-maintenance` | Pre/post-market maintenance and trade-case population | `POST /api/v1/jobs/market-maintenance?phase=auto&fill_page_size=100&news_enabled=false` | `30 12,21 * * 1-5` |

Current EDT behavior:

- Symbol entry cycles (`market-entry-cycle`) run from 10:00am through 3:55pm Eastern, staggered by minute, while reusing the same FastAPI app, duplicate-signal suppression, option filters, and global automation guards.
- Exit cycle (`market-exits`) runs every minute from about **9:00am through 3:59pm Eastern** during EDT, so it stops before the 4:00pm market close. When Eastern switches to EST, change the exit hour window from `13-19` UTC to `14-20` UTC.
- Maintenance runs pre-market (8:30am ET) and post-market (5:30pm EDT / 4:30pm EST).

Entry splitting is meant to keep expanded option candidate searches smaller per invocation. The five symbol-specific entry cron jobs should use `scan_limit=100`, `order_limit=100`, and `fill_page_size=100` to match the current option candidate budget. The `market-entry-cycle` endpoint does not run exits or post-market maintenance; exits and maintenance stay global. Each Render cron job may have its own minimum monthly cost, so keep the symbol list intentional.

## Emergency stops

Any one of these is enough to halt the relevant automation:

| Action | Switch |
|---|---|
| Stop all auto-submit | `TRADING_AUTOMATION_ENABLED=false` |
| Stop entry submits only | `MARKET_CYCLE_SUBMIT_ENABLED=false` |
| Stop cron runner execution | `SCHEDULED_JOBS_ENABLED=false` |
| Pause exit automation | `MARKET_CYCLE_EXIT_ENABLED=false` |

Paper-trading safety settings that should remain enabled:

```text
ALPACA_PAPER=true
AUTO_SUBMIT_REQUIRES_PAPER=true
```

Automation risk caps:

```text
MAX_AUTO_ORDERS_PER_CYCLE
MAX_AUTO_ORDERS_PER_DAY
MAX_AUTO_ORDERS_PER_SYMBOL_PER_DAY
MAX_OPEN_POSITIONS
MAX_OPEN_POSITIONS_PER_SYMBOL
MAX_CONTRACTS_PER_ORDER
MAX_ESTIMATED_PREMIUM_PER_ORDER
```

## Preview profiles by signal strategy type

Contract selection can now use env-backed preview profiles by strategy type instead of relying only on hard-coded strategy DB values.

Profile names currently used:

```text
moving_average
momentum_rate_of_change
rsi_reversal
macd_crossover
mean_reversion
breakout_price_threshold
volume_confirmed_breakout
volatility_squeeze
support_resistance
vwap_reclaim
opening_range_breakout
relative_strength
time_series_momentum
market_regime_filter
pairs_relative_value
options_spread_candidate
```

Env format:

```text
PREVIEW_PROFILE_<PROFILE>_<SETTING>
```

Examples:

```text
PREVIEW_PROFILE_MOVING_AVERAGE_MIN_OPEN_INTEREST=50
PREVIEW_PROFILE_MOVING_AVERAGE_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_MOVING_AVERAGE_MAX_SPREAD_PERCENT=35
PREVIEW_PROFILE_MOMENTUM_RATE_OF_CHANGE_MIN_OPEN_INTEREST=50
PREVIEW_PROFILE_MOMENTUM_RATE_OF_CHANGE_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_RSI_REVERSAL_MAX_ESTIMATED_NOTIONAL=5000
PREVIEW_PROFILE_VOLUME_CONFIRMED_BREAKOUT_MAX_ESTIMATED_NOTIONAL=5000
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

`seed_trade_universe.py` seeds the paper trading strategy universe. It creates one active strategy per scanner type, stores the ticker list in `scanner.symbols`, and sets shared `scanner.preview` config (DTE range, spread, notional, and OI limits). Bullish signals preview calls and bearish signals preview puts. It defaults to five core liquid symbols:

```text
SPY, QQQ, NVDA, AAPL, MSFT
```

The GitHub Actions workflow also defaults to the same five-symbol set, and both the script and workflow accept explicit symbol overrides.

The seed script creates one global strategy for each scanner type:

```text
moving_average
momentum_rate_of_change
rsi_reversal
macd_crossover
mean_reversion
breakout_price_threshold
volume_confirmed_breakout
volatility_squeeze
support_resistance
vwap_reclaim
opening_range_breakout
relative_strength
time_series_momentum
market_regime_filter
pairs_relative_value
options_spread_candidate
```

Each seeded strategy tags `scanner.preview.preview_profile` from the scanner type.

Default seeded set:

```text
16 scanner-type strategies
```

When reseeded, legacy symbol-specific preview strategies such as `MSFT momentum rate-of-change put preview` are deactivated and replaced by global scanner-type strategies such as `momentum_rate_of_change`.

Current paper data-gathering profile:

```text
strictness_level=0.70
strictness_profile=selective_winner_bias
max_estimated_notional=5000
max_notional_per_order=5000
min_open_interest=50
max_spread=0.35
max_spread_percent=35
seeded submit caps remain high enough to allow good signals through:
max_orders_per_cycle=100
max_orders_per_trading_day=500
max_open_contracts_per_strategy=100
```

This profile is intentionally semi-picky at the signal level: it favors stronger setups and slower duplicate cadence, but it does not throttle good signals merely to reduce count. Runtime env caps still provide global paper-safety limits.

Current Render risk caps reduce runaway paper exposure while keeping each symbol cron enabled:

```text
MAX_AUTO_ORDERS_PER_DAY=60
MAX_OPEN_POSITIONS=30
```

Current Render preview profile notional caps are tuned by strategy type:

```text
moving_average=5000
momentum_rate_of_change=5000
rsi_reversal=5000
macd_crossover=5000
mean_reversion=5000
breakout_price_threshold=5000
volume_confirmed_breakout=5000
volatility_squeeze=5000
support_resistance=5000
vwap_reclaim=5000
opening_range_breakout=5000
relative_strength=5000
time_series_momentum=5000
market_regime_filter=5000
pairs_relative_value=5000
options_spread_candidate=5000
```

The market-regime filter, pairs relative-value, and options-spread candidate scanners are signal-only. They can create review/context signals, but the market-cycle preview gate marks them `signal_only` and does not create option previews or order intents. Paired, spread, and other multi-leg orders need separate execution plumbing before these signals can submit true strategy-specific trades.

## Entry quality gate

Auto-submit now has a quality gate between preview and broker submission. Scanners still create signals and previews for review, but weak entries can be rejected before they become Alpaca orders. The gate is controlled by:

```text
ENTRY_QUALITY_GATE_ENABLED=true
ENTRY_QUALITY_MIN_SCORE=60
ENTRY_QUALITY_FAST_CONFIRMATION_ENABLED=true
ENTRY_QUALITY_DISABLED_AUTO_SUBMIT_SCANNERS=market_regime_filter,pairs_relative_value,options_spread_candidate
ENTRY_QUALITY_MIN_RELATIVE_EDGE_PERCENT=1.0
ENTRY_QUALITY_MIN_MOMENTUM_THRESHOLD_MULTIPLIER=1.8
ENTRY_QUALITY_MIN_BREAKOUT_BUFFER_MULTIPLIER=1.5
ENTRY_QUALITY_MIN_VWAP_DISTANCE_PERCENT=0.25
ENTRY_QUALITY_MIN_AVERAGE_SEPARATION_PERCENT=0.20
ENTRY_QUALITY_MAX_OPTION_SPREAD_PERCENT=25
ENTRY_QUALITY_MIN_OPEN_INTEREST=50
ENTRY_QUALITY_STOP_LOSS_COOLDOWN_MINUTES=120
```

Fast scanners (`momentum_rate_of_change`, `vwap_reclaim`, `opening_range_breakout`, and `moving_average`) wait one completed signal timeframe before previewing so the next cycle can confirm the setup. `market_regime_filter`, `pairs_relative_value`, and `options_spread_candidate` stay signal-only until their execution plumbing exists; all three are blocked before option contract selection so they do not create preview diagnostics, stale previews, or rejected order intents.

The strongest loss sources are tuned at the strategy/profile level rather than pausing a single symbol cron. SPY remains in the paper universe unless a future review explicitly removes it.

Apply the 2026-05-18 strategy-type tuning batch with:

```powershell
.\.venv\Scripts\python.exe scripts\tune_strategies.py apply-2026-05-18-strategy-type-batch --dry-run
.\.venv\Scripts\python.exe scripts\tune_strategies.py apply-2026-05-18-strategy-type-batch
```

The batch patches scanner config for `support_resistance`, `momentum_rate_of_change`, `moving_average`, `mean_reversion`, `rsi_reversal`, and `volume_confirmed_breakout`. It leaves `macd_crossover`, `breakout_price_threshold`, and `volatility_squeeze` in watch mode because the current evidence is not strong enough to tighten them.

Apply the 2026-06-11 fresh-paper tuning batch with:

```powershell
.\.venv\Scripts\python.exe scripts\tune_strategies.py apply-2026-06-11-fresh-paper-tuning-batch --dry-run
.\.venv\Scripts\python.exe scripts\tune_strategies.py apply-2026-06-11-fresh-paper-tuning-batch
```

That batch uses the 2026-06-09 through 2026-06-10 paper evidence to tighten only the highest-confidence scanner knobs before a clean paper-account restart:

- `mean_reversion`: stricter Bollinger setup and tighter distance-to-middle.
- `momentum_rate_of_change`: stronger ROC threshold and lower max extension.
- `support_resistance`: closer entries to the active level.
- `time_series_momentum`: stronger minimum trend threshold.

The 2026-06-16 support-resistance winner-bias decision keeps the scanner active but restricts seeded `support_resistance` to breakout/breakdown entries:

```text
scanner.mode=breakout
scanner.breakout_buffer_percent=0.20
scanner.max_distance_percent=0.35
```

The decision was based on the 2026-06-15 paper snapshot where `support_resistance` closed 0 wins and 5 losses. Judge the outcome after 3 post-market snapshots before restoring bounce/rejection mode.

Momentum rate-of-change and mean reversion use a controlled wider-stop test in the 2026-05-18 batch:

```text
PREVIEW_PROFILE_MOMENTUM_RATE_OF_CHANGE_MIN_OPEN_INTEREST=50
PREVIEW_PROFILE_MOMENTUM_RATE_OF_CHANGE_MAX_ESTIMATED_NOTIONAL=5000
scanner.exit.stop_loss_percent=15
```

Current paper exit defaults:

```text
profit_target_percent=25
stop_loss_percent=10
stop_loss_min_dollars=10
```

The percent stop only triggers when the unrealized percent loss and the dollar loss floor are both met. The $10 floor still avoids noise exits on tiny positions while letting weak small-premium trades stop sooner.

## Reset paper account data

When switching to a new Alpaca paper trading account, clear the generated trading data before starting new runs. The reset preserves `strategies` so the application keeps its configured strategy universe.

Dry run first:

```bash
python scripts/reset_account_data.py
```

Confirmed reset:

```bash
python scripts/reset_account_data.py --apply --confirm RESET_TRADING_DATA
```

The script clears runtime trading tables such as `signals`, `order_intents`, `broker_orders`, `fills`, `position_snapshots`, diagnostics, trade cases, review snapshots, AI review rows, and, by default, old `job_runs` and `audit_logs`. It then records the reset itself. Pass `--keep-history` to preserve existing job and audit history.

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
app/services/signals/evaluators/advanced.py
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
tests/services/signals/test_advanced_evaluators.py
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
VwapReclaimEvaluator
OpeningRangeBreakoutEvaluator
RelativeStrengthEvaluator
TimeSeriesMomentumEvaluator
MarketRegimeFilterEvaluator
PairsRelativeValueEvaluator
OptionsSpreadCandidateEvaluator
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
vwap_reclaim
opening_range_breakout
relative_strength
time_series_momentum
market_regime_filter
pairs_relative_value
options_spread_candidate
```

The live scanner routes all of those `scanner.type` values through evaluator-backed scan paths.

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
VWAP_RECLAIM_EVALUATOR_ENABLED
OPENING_RANGE_BREAKOUT_EVALUATOR_ENABLED
RELATIVE_STRENGTH_EVALUATOR_ENABLED
TIME_SERIES_MOMENTUM_EVALUATOR_ENABLED
MARKET_REGIME_FILTER_EVALUATOR_ENABLED
PAIRS_RELATIVE_VALUE_EVALUATOR_ENABLED
OPTIONS_SPREAD_CANDIDATE_EVALUATOR_ENABLED
```

Review note: `app/services/signal_scanner.py` is a large file. Some GitHub connector reads may truncate it before the evaluator helper implementations. When reviewing or editing scanner routing, use a local checkout or otherwise verify the complete file before making full-file replacements.

## AI review layer

Implemented:

- `trade_cases` table and ORM model.
- `ai_trade_reviews`, `strategy_change_suggestions`, and `strategy_tuning_decisions` tables and ORM models.
- `option_selection_diagnostics` table and ORM model for rejected preview/contract-selection context.
- `app/services/trade_cases.py` to idempotently populate closed FIFO round trips.
- Post-market maintenance automatically populates trade cases in an isolated transaction.
- Post-market maintenance persists `review_snapshots` with signal, preview, fill, diagnostic, rejected-outcome context, and the generated learning report.
- Post-market maintenance prunes old review snapshots using `REVIEW_SNAPSHOT_RETENTION_DAYS`.
- `app/services/ai_trade_review.py` writes local, deterministic paper-trade reviews from `trade_cases` plus the latest `review_snapshots` row.
- `POST /api/v1/jobs/write-ai-trade-reviews?limit=100` stores generated `ai_trade_reviews` and pending `strategy_change_suggestions`.
- Post-market maintenance runs the AI review writer after trade cases and review snapshots are created; failures are isolated from the maintenance job.
- `GET /api/v1/automation/ai-trade-reviews` and `GET /api/v1/automation/strategy-change-suggestions?status=pending` expose the review queue.
- `PATCH /api/v1/automation/strategy-change-suggestions/{id}` records approval/rejection notes and review metadata without applying any strategy change.
- `GET /api/v1/automation/strategy-refinement` summarizes recent snapshots into a tuning queue with minimum evidence gates, readiness statuses, priority trends, and before/after windows around recorded decisions.
- `POST /api/v1/automation/strategy-tuning-decisions` records human-approved tuning decisions and evidence without applying changes automatically.
- `GET /api/v1/automation/strategy-tuning-decisions` lists recorded tuning decisions for later before/after review.
- `docs/maintenance/strategy-refinement-playbook.md` describes the full strategy tuning workflow and per-scanner refinement guidance.
- `strategy_tuning_prompt.md` is the top-level prompt to ask the AI for a broad tuning research pass and proposed batch of changes.
- `scripts/print_review_snapshot.py` prints the latest review snapshot as a readable CLI report.

Not implemented yet:

- External LLM-backed review generation.
- Automatic application of approved suggestions to strategy config.
- Automatic strategy changes from AI suggestions. AI recommendations are recommendation-only; strategy logic changes remain human-approved and must not be applied automatically.

## Important limitations / next work

Current high-priority next steps:

1. Paper-test the full evaluator-backed strategy set and tune scanner thresholds / preview-profile limits by strategy type.
2. Continue improving option contract selection with Greeks/delta-style scoring as broker data allows.
3. Review generated `ai_trade_reviews` and pending `strategy_change_suggestions` after post-market maintenance.
4. Add real DB integration tests that run against the local Postgres helper.
5. Add an explicit implementation step for approved suggestions, still gated by human review.

Known limitations:

- Option contract selection can reject many candidates due open interest, notional, spread, or quote quality. Repeated failed previews retire to `preview_rejected` after `OPTIONS_PREVIEW_MAX_ATTEMPTS`.
- Render only needs explicit evaluator env vars when overriding defaults; `render.yaml` lists them so deployed behavior is visible.
- News scanning is lightweight RSS/headline gating only.
- Statuses are plain strings, not a formal enum/state machine.

## Local Postgres helper

A lightweight Postgres helper is available for real-DB integration tests:

```bash
docker compose -f docker-compose.postgres.yml up -d
```

```bash
DATABASE_URL=postgresql+psycopg://stocks_api:stocks_api@127.0.0.1:5433/stocks_api_test alembic upgrade head
```

```bash
STOCKS_API_RUN_DB_INTEGRATION_TESTS=1 \
STOCKS_API_INTEGRATION_DATABASE_URL=postgresql+psycopg://stocks_api:stocks_api@127.0.0.1:5433/stocks_api_test \
python -m pytest tests/integration
```

On PowerShell:

```powershell
$env:STOCKS_API_RUN_DB_INTEGRATION_TESTS="1"
$env:STOCKS_API_INTEGRATION_DATABASE_URL="postgresql+psycopg://stocks_api:stocks_api@127.0.0.1:5433/stocks_api_test"
python -m pytest tests/integration
```

## Useful manual job calls

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/jobs/market-entry-cycle?symbol=SPY&scan_limit=100&order_limit=100&fill_page_size=100" \
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
python scripts/print_review_snapshot.py --limit 8
```

```bash
python scripts/update_strategy_stop_loss.py --dry-run
```

```bash
python scripts/update_strategy_stop_loss.py
```

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/jobs/write-ai-trade-reviews?limit=100" \
  -H "Authorization: Bearer change-me"
```

```bash
curl -H "Authorization: Bearer change-me" \
  "http://127.0.0.1:8000/api/v1/automation/status"
```
