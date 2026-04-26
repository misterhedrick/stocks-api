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
      "target_strike": "500"
    },
    "side": "buy",
    "quantity": 1,
    "order_type": "limit",
    "time_in_force": "day",
    "data_feed": "indicative"
  }'
```

Preview requests must provide exactly one of `option_symbol` or `contract_selection`.

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

The automation status endpoint summarizes market-cycle switches, active strategy scanner/submit settings, and the latest `market_cycle`, `scan_signals`, and `reconcile_broker` job runs.

The scanner reads active strategy configs with a `scan_signals` list, validates each signal spec, inserts valid `signals`, skips malformed specs, and records the run in `job_runs`. The market-cycle job can then optionally turn scanner-created signals into previewed or submitted paper orders when the feature switches and strategy config allow it.

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

## Scheduled jobs

`render.yaml` includes a Render cron service named `stocks-api-market-cycle` that runs every 30 minutes and calls:

```text
POST /api/v1/jobs/market-cycle?scan_limit=100&order_limit=100&fill_page_size=100
```

It is disabled by default with:

```text
SCHEDULED_JOBS_ENABLED=false
```

To activate it in Render, set `SCHEDULED_JOBS_ENABLED=true` on the cron service. You can turn the cron service off in Render as a second safety switch.

Market-cycle behavior is controlled by these web-service env vars:

```text
MARKET_CYCLE_SCAN_ENABLED=true
MARKET_CYCLE_RECONCILE_ENABLED=true
MARKET_CYCLE_PREVIEW_ENABLED=false
MARKET_CYCLE_SUBMIT_ENABLED=false
```

Current market-cycle automation can scan for signals, reconcile broker state, auto-preview scanner-created signals, and auto-submit same-cycle previewed paper orders when the matching environment and strategy-level switches are enabled.

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
- `MARKET_CYCLE_SUBMIT_ENABLED=false`
- `SCHEDULED_JOBS_ENABLED=false` until you are ready for cron runs
