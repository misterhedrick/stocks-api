# stocks-api

Single-user FastAPI scaffold for a stock/options trading API.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
copy env.example .env
uvicorn app.main:app --reload
```

Health endpoints:
- `/health`
- `/api/v1/health`
- `/api/v1/ready` requires `Authorization: Bearer <ADMIN_API_TOKEN>` and checks database connectivity.

Readiness check example:

```bash
curl -H "Authorization: Bearer change-me" http://127.0.0.1:8000/api/v1/ready
```

## Render

Set these environment variables in Render:
- `DATABASE_URL`
- `ADMIN_API_TOKEN`
- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`
