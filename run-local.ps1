$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

if (-not (Test-Path ".venv")) {
    Write-Host "Virtual environment not found. Create it first with:"
    Write-Host "python -m venv .venv"
    exit 1
}

if (-not (Test-Path ".env")) {
    Write-Host ".env file not found. Create it first by copying env.example to .env"
    exit 1
}

. .\.venv\Scripts\Activate.ps1

Write-Host "Checking database connection..."
python -m app.db.check_database

Write-Host "Applying database migrations..."
python -m alembic upgrade head

Write-Host "Starting stocks-api locally..."
python -m uvicorn app.main:app --reload
