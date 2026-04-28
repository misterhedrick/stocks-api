# stocks-api

Single-user FastAPI scaffold for a stock/options trading API.

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

Health endpoints:
- `/health`
- `/api/v1/health`
- `/api/v1/ready` requires `Authorization: Bearer <ADMIN_API_TOKEN>` and checks database connectivity plus required tables.
- `/api/v1/jobs/reconcile-broker` requires `Authorization: Bearer <ADMIN_API_TOKEN>` and syncs recent Alpaca orders, fill activities, and current positions into local durable tables.

Both local run scripts apply `alembic` migrations before starting `uvicorn`.

Readiness check example:

```bash
curl -H "Authorization: Bearer change-me" http://127.0.0.1:8000/api/v1/ready
```

Create a strategy:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/strategies \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Opening range options",
    "description": "Paper strategy scaffold",
    "is_active": true,
    "config": {
      "underlying": "SPY"
    }
  }'
```

List active strategies:

```bash
curl -H "Authorization: Bearer change-me" \
  "http://127.0.0.1:8000/api/v1/strategies?is_active=true"
```

Update a strategy:

```bash
curl -X PATCH http://127.0.0.1:8000/api/v1/strategies/<strategy_id> \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "is_active": false
  }'
```

Create a signal:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/signals \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "strategy_id": "<strategy_id>",
    "symbol": "SPY260417C00500000",
    "underlying_symbol": "SPY",
    "signal_type": "breakout",
    "direction": "bullish",
    "confidence": "0.7500",
    "rationale": "Opening range breakout",
    "market_context": {
      "price": "512.34"
    }
  }'
```

List signals:

```bash
curl -H "Authorization: Bearer change-me" \
  "http://127.0.0.1:8000/api/v1/signals?status=new&limit=50"
```

Mark a signal rejected:

```bash
curl -X PATCH http://127.0.0.1:8000/api/v1/signals/<signal_id> \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "rejected",
    "rejected_reason": "Spread too wide"
  }'
```

Generate a previewed order intent from a signal:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/order-intents/preview \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "<signal_id>",
    "option_symbol": "SPY260417C00500000",
    "side": "buy",
    "quantity": 1,
    "order_type": "limit",
    "time_in_force": "day",
    "data_feed": "indicative"
  }'
```

The preview endpoint fetches the latest Alpaca option quote, derives a limit price when one is not supplied, stores quote/risk context in `preview`, and does not place an Alpaca order.

Generate a previewed order intent and let the API select the option contract:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/order-intents/preview \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "<signal_id>",
    "contract_selection": {
      "underlying_symbol": "SPY",
      "option_type": "call",
      "expiration_date_gte": "2026-04-17",
      "expiration_date_lte": "2026-05-01",
      "target_strike": "500",
      "max_estimated_notional": "250",
      "max_spread": "0.20"
    },
    "side": "buy",
    "quantity": 1,
    "order_type": "limit",
    "time_in_force": "day",
    "data_feed": "indicative",
    "max_estimated_notional": "250",
    "max_spread": "0.20"
  }'
```

Preview requests must provide exactly one of `option_symbol` or `contract_selection`.
Generated previews default to `MAX_ESTIMATED_PREMIUM_PER_ORDER` as a max notional guard unless `max_estimated_notional` is supplied. Optional `max_spread` can also reject wide quotes. Contract selection applies these quote constraints while choosing a contract, which helps avoid accidentally selecting deep ITM contracts for small paper tests.

Select an option contract:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/options/select-contract \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "underlying_symbol": "SPY",
    "option_type": "call",
    "side": "buy",
    "expiration_date_gte": "2026-04-17",
    "expiration_date_lte": "2026-05-01",
    "target_strike": "500",
    "max_estimated_notional": "250",
    "max_spread": "0.20",
    "data_feed": "indicative"
  }'
```

The contract selector fetches active Alpaca contracts, chooses the earliest expiration with the strike closest to `target_strike` or `underlying_price`, and returns the selected contract plus latest quote context. It does not create or submit an order.

Run database migrations:

```bash
python -m alembic upgrade head
```

Smoke test the scaffold after startup:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/health
curl -H "Authorization: Bearer change-me" http://127.0.0.1:8000/api/v1/ready
```

Create a previewed order intent:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/order-intents \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "underlying_symbol": "SPY",
    "option_symbol": "SPY260417C00500000",
    "side": "buy",
    "quantity": 1,
    "order_type": "limit",
    "limit_price": "1.25",
    "time_in_force": "day",
    "rationale": "Manual preview only; does not place an Alpaca order."
  }'
```

Option order intents currently support Alpaca's `day` time-in-force only.
When `strategy_id` or `signal_id` is supplied, the API validates those records exist and rejects mismatched strategy/signal links.

Submit a previewed order intent to Alpaca paper trading:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/order-intents/<order_intent_id>/submit \
  -H "Authorization: Bearer change-me"
```

Request cancellation for a submitted broker order:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/order-intents/<order_intent_id>/cancel \
  -H "Authorization: Bearer change-me"
```

The cancel endpoint sends a cancel request to Alpaca for the linked broker order and marks the local order intent and broker order as `cancel_requested`. Run broker reconciliation afterward to pull the final broker status such as `canceled` or `filled`.

Manually reconcile broker state from Alpaca:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/jobs/reconcile-broker?order_limit=100&fill_page_size=100" \
  -H "Authorization: Bearer change-me"
```

The reconciliation job:
- upserts recent Alpaca orders into `broker_orders`
- inserts new Alpaca fill activities into `fills`
- snapshots current Alpaca positions into `position_snapshots`
- records success or failure in `job_runs`

Manually scan configured strategies for signals:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/jobs/scan-signals?limit=100" \
  -H "Authorization: Bearer change-me"
```

Check automation status:

```bash
curl -H "Authorization: Bearer change-me" \
  "http://127.0.0.1:8000/api/v1/automation/status"
```

The automation status endpoint summarizes market-cycle switches, global automation safety settings, active strategy scanner/submit settings, and the latest `market_cycle`, `scan_signals`, and `reconcile_broker` job runs.

Check position management status:

```bash
curl -H "Authorization: Bearer change-me" \
  "http://127.0.0.1:8000/api/v1/automation/positions?limit=100"
```

This read-only endpoint summarizes current reconciled positions with ownership, active exit order status, exit config availability, P/L fields, and a recommended action such as `hold`, `exit_rule_triggered`, `exit_pending`, `add_exit_config`, or `preview_unmanaged_exit`.

Evaluate current positions for exits:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/jobs/evaluate-exits?limit=100" \
  -H "Authorization: Bearer change-me"
```

The exit evaluator reads the latest reconciled position snapshots, links each position back to its most recent entry order intent and strategy when possible, checks `scanner.exit` rules, and creates previewed sell order intents when a rule triggers. It supports profit target, stop loss, and days-to-expiration rules. The response includes `position_ownership` so unmanaged positions are visible with reasons such as no linked entry order, inactive strategy, or missing exit config. Market-cycle exit evaluation is controlled by `MARKET_CYCLE_EXIT_ENABLED`; auto-submit of exit intents still requires `MARKET_CYCLE_SUBMIT_ENABLED=true`, `TRADING_AUTOMATION_ENABLED=true`, and a strategy `scanner.exit.submit.enabled=true`.

Preview exits for unmanaged positions:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/jobs/preview-unmanaged-exits?symbol=SPY" \
  -H "Authorization: Bearer change-me"
```

This creates previewed sell order intents only for positions that are not already linked to an active managed strategy. It does not submit the orders. Omit `symbol` to preview exits for all currently unmanaged long positions.

Check market and owned-ticker news:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/jobs/check-news?market_limit=10&ticker_limit=5" \
  -H "Authorization: Bearer change-me"
```

The news scan checks configured market RSS feeds plus a deeper feed search for each currently owned ticker. By default, the broad scan covers US indexes, Fed/rates/inflation/economic data, US economy/yields/dollar/recession/GDP, world markets/geopolitical risk/oil/war/tariffs, and earnings/volatility/credit/banking headlines. Option contract symbols are reduced to their underlying ticker for news searches. Results are stored in the `news_scan` job run details with headline, URL, source, publish time, simple impact keywords, and a first-pass `risk_assessment`. When market-cycle news checks are enabled, a high market-level news risk blocks new entry previews for that cycle and flags affected owned tickers for manual review; it does not auto-sell positions from headlines.

Seed preview-first paper strategies:

```powershell
.\.venv\Scripts\python.exe .\scripts\seed_paper_strategies.py --dry-run --sample-prices
.\.venv\Scripts\python.exe .\scripts\seed_paper_strategies.py --dry-run
.\.venv\Scripts\python.exe .\scripts\seed_paper_strategies.py
```

The seed script builds active SPY/QQQ paper strategy configs from the latest IEX quote midpoints, enables scanner-driven order previews, and keeps `scanner.submit.enabled=false`. Use `--sample-prices` when you want to inspect the config shape without Alpaca credentials. It is meant for getting observable paper signals and previewed order intents before any auto-submit settings are enabled.

Smoke test the configured local environment:

```powershell
.\.venv\Scripts\python.exe .\scripts\smoke_preflight.py
.\.venv\Scripts\python.exe .\scripts\run_market_cycle_smoke.py
.\.venv\Scripts\python.exe .\scripts\run_paper_submit_smoke.py
.\.venv\Scripts\python.exe .\scripts\run_full_smoke_suite.py
```

`smoke_preflight.py` checks sanitized settings, external Postgres connectivity/schema, Alpaca market-data reads, Alpaca trading reads, and broker reconciliation. `run_market_cycle_smoke.py` runs one market-cycle pass. `run_paper_submit_smoke.py` creates a one-contract paper order intent, submits it to Alpaca paper, requests cancellation by default, and reconciles. `run_full_smoke_suite.py` runs preflight, strategy seeding, market-cycle smoke, paper submit/cancel, and deterministic unit tests; use `--skip-paper-submit` for a non-ordering smoke run.

The scanner reads active strategy configs with a `scan_signals` list, validates each signal spec, inserts valid `signals`, skips malformed specs, and records the run in `job_runs`. The market-cycle job can then optionally turn scanner-created signals into previewed or submitted paper orders when the feature switches and strategy config allow it.
When a scan succeeds but does not create signals, the scan response and `job_runs.details` include `no_signal_reasons` to explain harmless no-op cases such as missing recent bars, missing quotes, or thresholds that were not crossed.

Example strategy config:

```json
{
  "scan_signals": [
    {
      "symbol": "SPY",
      "underlying_symbol": "SPY",
      "signal_type": "manual_scan",
      "direction": "bullish",
      "confidence": "0.7500",
      "rationale": "Manual scanner seed",
      "market_context": {
        "source": "strategy_config"
      }
    }
  ]
}
```

It also supports live stock scanner rules. A quote threshold rule uses the latest Alpaca stock quote midpoint:

```json
{
  "scanner": {
    "type": "price_threshold",
    "symbols": ["SPY", "QQQ"],
    "signal_type": "price_breakout",
    "direction": "bullish",
    "price_above": "500",
    "confidence": "0.6500",
    "data_feed": "iex",
    "dedupe_minutes": 240,
    "preview": {
      "enabled": true,
      "option_type": "call",
      "target_strike": "500",
      "side": "buy",
      "quantity": 1,
      "order_type": "limit",
      "time_in_force": "day",
      "data_feed": "indicative"
    },
    "submit": {
      "enabled": true,
      "max_orders_per_cycle": 1,
      "max_contracts_per_order": 1,
      "max_contracts_per_cycle": 1,
      "max_notional_per_order": "250.00",
      "max_open_contracts_per_symbol": 1,
      "max_open_contracts_per_strategy": 2,
      "max_orders_per_trading_day": 3,
      "trading_day_timezone": "America/New_York",
      "trade_windows": [
        {
          "timezone": "America/New_York",
          "start": "09:45",
          "end": "15:45"
        }
      ],
      "allowed_sides": ["buy"]
    }
  }
}
```

For bearish threshold checks, use `price_below` instead of `price_above`. The scanner uses the latest Alpaca stock quote midpoint as the observed price and stores quote context in `signals.market_context`.

A percent-change rule uses recent Alpaca stock bars:

```json
{
  "scanner": {
    "type": "percent_change",
    "symbols": ["SPY", "QQQ"],
    "lookback_minutes": 30,
    "timeframe": "1Min",
    "change_above_percent": "0.50",
    "signal_type": "momentum_breakout",
    "direction": "bullish",
    "confidence": "0.6500",
    "data_feed": "iex",
    "dedupe_minutes": 240
  }
}
```

For bearish momentum checks, use `change_below_percent` instead of `change_above_percent`, for example `"-0.50"`.
`dedupe_minutes` suppresses repeated signals with the same strategy, symbol, signal type, and direction while a prior signal is still recent. Set it to `0` to allow repeated signals.
When `MARKET_CYCLE_PREVIEW_ENABLED=true`, scanner-created signals with `scanner.preview.enabled=true` can automatically create previewed order intents. This still does not submit orders while `MARKET_CYCLE_SUBMIT_ENABLED=false`.
When `MARKET_CYCLE_SUBMIT_ENABLED=true`, only order intents created by that same market-cycle run are eligible for auto-submit, and the strategy must also set `scanner.submit.enabled=true`.
Submit config supports `max_orders_per_cycle`, `max_contracts_per_order`, optional `max_contracts_per_cycle`, optional `max_notional_per_order`, optional `max_open_contracts_per_symbol`, optional `max_open_contracts_per_strategy`, optional `max_orders_per_trading_day`, optional `trading_day_timezone`, optional `trade_windows`, and `allowed_sides`. Option notional is treated as `contract_price * quantity * 100`. Existing open-contract checks use broker orders linked back to the strategy's order intents.

Global automation safety gates apply in addition to strategy-level `scanner.submit` config:

```text
TRADING_AUTOMATION_ENABLED=false
AUTO_SUBMIT_REQUIRES_PAPER=true
MAX_AUTO_ORDERS_PER_CYCLE=1
MAX_AUTO_ORDERS_PER_DAY=3
MAX_OPEN_POSITIONS=3
MAX_OPEN_POSITIONS_PER_SYMBOL=1
MAX_CONTRACTS_PER_ORDER=1
MAX_ESTIMATED_PREMIUM_PER_ORDER=250
```

Automated submit is intended for paper trading right now. To intentionally enable fully automated paper trading, set `ALPACA_PAPER=true`, `MARKET_CYCLE_PREVIEW_ENABLED=true`, `MARKET_CYCLE_SUBMIT_ENABLED=true`, `TRADING_AUTOMATION_ENABLED=true`, keep `AUTO_SUBMIT_REQUIRES_PAPER=true`, and enable both `scanner.preview.enabled` and `scanner.submit.enabled` on the strategy. Manual order intent submit is unchanged.

## Scheduled jobs

`render.yaml` includes a Render cron service named `stocks-api-market-cycle` that runs every 30 minutes and calls:

```text
POST /api/v1/jobs/market-cycle?scan_limit=100&order_limit=100&fill_page_size=100
```

It is disabled by default with:

```text
SCHEDULED_JOBS_ENABLED=false
```

The cron runner retries temporary HTTP failures by default:

```text
JOB_RETRY_DELAYS_SECONDS=10,30
```

Retryable responses are `429`, `500`, `502`, `503`, and `504`, giving the job up to three total attempts.

To activate it in Render, set `SCHEDULED_JOBS_ENABLED=true` on the cron service. You can turn the cron service off in Render as a second safety switch.

Market-cycle behavior is controlled by these web-service env vars:

```text
MARKET_CYCLE_SCAN_ENABLED=true
MARKET_CYCLE_RECONCILE_ENABLED=true
MARKET_CYCLE_PREVIEW_ENABLED=false
MARKET_CYCLE_EXIT_ENABLED=false
MARKET_CYCLE_NEWS_ENABLED=false
MARKET_CYCLE_SUBMIT_ENABLED=false
NEWS_REQUEST_TIMEOUT_SECONDS=10
NEWS_MARKET_RSS_FEEDS=https://news.google.com/rss/search?q=stock%20market%20OR%20S%26P%20500%20OR%20Nasdaq%20OR%20Dow%20Jones&hl=en-US&gl=US&ceid=US:en,https://news.google.com/rss/search?q=Federal%20Reserve%20OR%20interest%20rates%20OR%20inflation%20OR%20CPI%20OR%20PPI%20OR%20jobs%20report&hl=en-US&gl=US&ceid=US:en,https://news.google.com/rss/search?q=US%20economy%20OR%20Treasury%20yields%20OR%20dollar%20OR%20recession%20OR%20GDP&hl=en-US&gl=US&ceid=US:en,https://news.google.com/rss/search?q=world%20markets%20OR%20global%20stocks%20OR%20geopolitical%20risk%20OR%20oil%20prices%20OR%20war%20OR%20tariffs&hl=en-US&gl=US&ceid=US:en,https://news.google.com/rss/search?q=earnings%20guidance%20OR%20market%20volatility%20OR%20VIX%20OR%20credit%20markets%20OR%20banking%20sector&hl=en-US&gl=US&ceid=US:en
NEWS_TICKER_RSS_TEMPLATE=https://news.google.com/rss/search?q={symbol}%20stock%20OR%20{symbol}%20options&hl=en-US&gl=US&ceid=US:en
```

Current market-cycle automation can scan for signals, reconcile broker state, auto-preview scanner-created signals, evaluate current positions for exit previews, check market/owned-ticker news, and auto-submit same-cycle previewed paper orders when the matching environment and strategy-level switches are enabled.
Before any automated submit, the market-cycle job checks the global automation guard. Blocked intents are skipped, recorded in the market-cycle submit errors, and written to `audit_logs` as `order_intent.auto_submit_skipped`.
Market-cycle scan details include `no_signal_reasons` from the scanner, which is useful when Render cron runs succeed but create no previews.

Audit logging currently records:
- strategy creation
- strategy updates
- signal creation
- signal updates
- order intent creation
- generated order intent previews
- order intent submission
- Alpaca order intent rejection
- broker reconciliation success or failure
- signal scan success or failure
- market cycle success or failure
- auto-submit skips blocked by automation safety gates

Postman coverage includes automation status safety-field assertions, scanner no-op visibility, market-cycle submit skip visibility when observable, and the existing preview/manual submit flow.

Run tests:

```bash
python -m pytest
```

If `pytest` is not installed in the environment, the current repo tests also run with:

```bash
python -m unittest discover -s tests
```

## Render

Render startup now relies on the FastAPI startup hook to auto-run migrations when `AUTO_MIGRATE_ON_STARTUP=true` or the app environment is `production`/`staging`.

Set these environment variables in Render:
- `DATABASE_URL`
- `ADMIN_API_TOKEN`
- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`
- `AUTO_MIGRATE_ON_STARTUP=true`
- `MARKET_CYCLE_SCAN_ENABLED=true`
- `MARKET_CYCLE_RECONCILE_ENABLED=true`
- `MARKET_CYCLE_PREVIEW_ENABLED=false`
- `MARKET_CYCLE_EXIT_ENABLED=false`
- `MARKET_CYCLE_NEWS_ENABLED=false`
- `MARKET_CYCLE_SUBMIT_ENABLED=false`
- `TRADING_AUTOMATION_ENABLED=false` until you are intentionally ready for automated paper submit
- `AUTO_SUBMIT_REQUIRES_PAPER=true`
- `MAX_AUTO_ORDERS_PER_CYCLE=1`
- `MAX_AUTO_ORDERS_PER_DAY=3`
- `MAX_OPEN_POSITIONS=3`
- `MAX_OPEN_POSITIONS_PER_SYMBOL=1`
- `MAX_CONTRACTS_PER_ORDER=1`
- `MAX_ESTIMATED_PREMIUM_PER_ORDER=250`
- `SCHEDULED_JOBS_ENABLED=false` until you are ready for cron runs
- `JOB_RETRY_DELAYS_SECONDS=10,30` on the cron service unless you want different retry timing
