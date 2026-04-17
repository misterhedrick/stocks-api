# stocks-api

FastAPI scaffold for a single-user stock/options trading API.

## Features
- FastAPI app with health routes
- Environment-based settings
- PostgreSQL-ready database config
- Render deployment config
- Placeholder folders for strategies, services, models, and routes

## Local setup
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000/docs`

## Environment variables
See `.env.example`.

## Deploy on Render
- Create a new Web Service from this repo
- Render should detect `render.yaml`
- Set the environment variables from `.env.example`
