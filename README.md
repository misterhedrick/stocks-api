# stocks-api (Python 3.13)

Fresh zip of the FastAPI starter updated for **Python 3.13.13**.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Health check:

- `http://127.0.0.1:8000/health`

## Files updated for Python 3.13

- `.python-version` -> `3.13.13`
- `runtime.txt` -> `python-3.13.13`
- `render.yaml` -> `PYTHON_VERSION=3.13.13`
