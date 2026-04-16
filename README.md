# stocks-api

A starter FastAPI project for a single-user stock options trading API hosted on Render with Alpaca.

## What is included

- FastAPI app with health endpoints
- Bearer-token protected admin routes
- SQLAlchemy database setup placeholder
- Render web service + cron job starter config
- Project structure ready for strategies, signals, order intents, broker orders, fills, snapshots, and audit logs

## Python version

Use **Python 3.11**.

## Local setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Open:

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/api/v1/secure/ping`

For the secure endpoint, send this header:

```text
Authorization: Bearer change-me
```

## Next build steps

1. Add Alembic migrations
2. Create core DB models
3. Add Alpaca client wrapper
4. Add signal scanner service
5. Add order intent preview flow
6. Add cron-job reconciliation logic
7. Add AI trade review tables and offline review process
