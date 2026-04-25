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

## Render

Render startup now relies on the FastAPI startup hook to auto-run migrations when `AUTO_MIGRATE_ON_STARTUP=true` or the app environment is `production`/`staging`.

Set these environment variables in Render:
- `DATABASE_URL`
- `ADMIN_API_TOKEN`
- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`
- `AUTO_MIGRATE_ON_STARTUP=true`
