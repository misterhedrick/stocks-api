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

Fallback if pytest is unavailable:

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

- `/api/v1/strategies` — create/list/update strategy rows.
- `/api/v1/signals` — create/list/update signal rows.
- `/api/v1/order-intents` — preview, submit, and cancel option order intents.
- `/api/v1/options/select-contract` — select an active tradable Alpaca option contract.
- `/api/v1/jobs/*` — protected operational jobs for reconciliation, scans, cycles, exits, maintenance, resets, and trade-case population.
- `/api/v1/automation/*` — read-only operational dashboards for readiness, positions, lifecycle, performance, trade cases, and learning reports.

## Trading pipeline

Current execution model:

```text
strategy config
→ scanner creates signal
→ preview creates order_intent
→ optional auto-submit places Alpaca paper order
→ broker reconciliation imports orders/fills/positions
→ exit cycle evaluates managed positions
→ post-market maintenance populates trade_cases
```

All execution flows through previewed `order_intents` before orders are submitted.

## Current Render cron topology

Render cron schedules are UTC and are not DST-aware.

| Service | Purpose | Endpoint | Current schedule |
|---|---|---|---|
| `stocks-api-market-cycle` | Entry cycle: scan → reconcile/news/preview/submit | `POST /api/v1/jobs/market-cycle?scan_limit=100&order_limit=100&fill_page_size=100` | `*/5 14-19 * * 1-5` |
| `stocks-api-market-exits` | Exit protection: reconcile → exit-eval → exit-submit | `POST /api/v1/jobs/market-cycle-exits?limit=100&order_limit=100&fill_page_size=100&phase_timeout_seconds=45` | `*/1 13-20 * * 1-5` |
| `stocks-api-market-maintenance` | Pre/post-market maintenance and trade-case population | `POST /api/v1/jobs/market-maintenance?phase=auto&news_enabled=false` | `30 12,21 * * 1-5` |

Current EDT behavior:

- Entry cycle runs every 5 minutes from **10:00am through 3:55pm Eastern**.
- Exit cycle runs every minute from about **9:00am through 4:59pm Eastern**.
- Maintenance runs pre-market and post-market.

When Eastern time switches back to EST, the entry cron should be reviewed. The equivalent 10:00am through 3:55pm EST schedule is:

```yaml
schedule: "*/5 15-20 * * 1-5"
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

## Preview profiles by signal strategy type

Contract selection can now use env-backed preview profiles by strategy type instead of relying only on hard-coded strategy DB values.

Profile names currently used:

```text
price_threshold
percent_change
moving_average
trend_confirmation
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
```

The existing production strategies were patched with `scanner.preview.preview_profile` using the GitHub Actions workflow and the result was:

```text
Committed. updated=43 skipped=0
```

Manual workflow:

```text
Actions → Update Strategy Preview Profiles → Run workflow
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

`seed_paper_trade_universe.py` now targets a broader liquid universe:

```text
SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, AMD, NFLX, AVGO, JPM, XOM
```

It seeds moving-average, confirmed-trend, and momentum rate-of-change call/put variants and tags `scanner.preview.preview_profile` from the scanner type.

## Signal strategy planning docs

Detailed signal strategy specs live under:

```text
docs/signal-strategies/
docs/signal-strategies/deep-dives/
```

Covered signal families:

```text
moving_average_trend_following
momentum_rate_of_change
breakout_price_threshold
mean_reversion
rsi_overbought_oversold
macd_crossover
support_resistance
volume_confirmed_breakout
volatility_squeeze
```

The docs describe purpose, inputs, formulas, bullish/bearish rules, rejection rules, confidence scoring, feature payloads, pseudocode, and tests.

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
```

Tests:

```text
tests/services/signals/test_indicators.py
tests/services/signals/test_momentum_evaluator.py
tests/services/signals/test_moving_average_evaluator.py
tests/services/signals/test_rsi_evaluator.py
tests/services/signals/test_macd_evaluator.py
tests/services/signals/test_mean_reversion_evaluator.py
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
```

The evaluator registry currently includes `momentum_rate_of_change`, `moving_average`, `rsi_reversal`, `macd_crossover`, and `mean_reversion`. The live scanner routes `scanner.type == "momentum_rate_of_change"` through the evaluator-backed scan path, and the `feature/add-signal-strategies` branch also routes `scanner.type == "moving_average"` through the evaluator-backed path.

RSI, MACD, and mean-reversion evaluators are implemented and registered, but their live scanner routing still needs to be added locally using the same evaluator-backed scanner pattern. Add feature-flag guards consistently with the existing evaluator settings: `SIGNAL_EVALUATORS_ENABLED`, `RSI_EVALUATOR_ENABLED`, `MACD_EVALUATOR_ENABLED`, and `MEAN_REVERSION_EVALUATOR_ENABLED`.

Review note: `app/services/signal_scanner.py` is a large file. Some GitHub connector reads may truncate it before the evaluator helper implementations. When reviewing or editing scanner routing, use a local checkout or otherwise verify the complete file before making full-file replacements.

## AI review layer

Implemented:

- `trade_cases` table and ORM model.
- `ai_trade_reviews` and `strategy_change_suggestions` tables and ORM models.
- `app/services/trade_cases.py` to idempotently populate closed FIFO round trips.
- Post-market maintenance automatically populates trade cases in an isolated transaction.

Not implemented yet:

- AI review service that reads `trade_cases` and writes `ai_trade_reviews` / `strategy_change_suggestions`.
- Automatic strategy changes from AI suggestions. AI should recommend only; strategy logic changes remain human-approved.

## Important limitations / next work

Current high-priority next steps:

1. Wire `rsi_reversal`, `macd_crossover`, and `mean_reversion` into `app/services/signal_scanner.py` using the evaluator-backed scanner pattern.
2. Add more evaluator-backed signal strategies from `docs/signal-strategies/`, starting with breakout, volume-confirmed breakout, volatility squeeze, and support/resistance.
3. Improve option contract selection with better liquidity/moneyness/delta-style scoring.
4. Add broker reconciliation pagination.
5. Add real DB integration tests or a local Docker Compose/Postgres helper.
6. Build AI trade review service using persisted `trade_cases`.

Known limitations:

- Option contract selection is still first-pass and can reject many candidates due open interest, notional, spread, or quote quality.
- News scanning is lightweight RSS/headline gating only.
- Statuses are plain strings, not a formal enum/state machine.
- RSI, MACD, and mean-reversion evaluators are not live scanner-routed yet.

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
