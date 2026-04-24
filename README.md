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

## Render

Render startup now relies on the FastAPI startup hook to auto-run migrations when `AUTO_MIGRATE_ON_STARTUP=true` or the app environment is `production`/`staging`.

Set these environment variables in Render:
- `DATABASE_URL`
- `ADMIN_API_TOKEN`
- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`
- `AUTO_MIGRATE_ON_STARTUP=true`
