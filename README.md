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
- `/api/v1/ready` requires `Authorization: Bearer <ADMIN_API_TOKEN>` and checks database connectivity.

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

## Render

Render startup now runs `python -m alembic upgrade head` before launching the app via [start.sh](/c:/Users/Miste/OneDrive/Documents/dev/stocks-api/start.sh).

Set these environment variables in Render:
- `DATABASE_URL`
- `ADMIN_API_TOKEN`
- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`
