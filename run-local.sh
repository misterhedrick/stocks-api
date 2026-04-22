#!/usr/bin/env bash

set -e

PROJECT_DIR="/c/Users/Miste/OneDrive/Documents/dev/stocks-api"

cd "$PROJECT_DIR"

if [ ! -d ".venv" ]; then
  echo "Virtual environment not found. Create it first with:"
  echo "python -m venv .venv"
  exit 1
fi

source .venv/Scripts/activate

if [ ! -f ".env" ]; then
  echo ".env file not found. Create it first by copying env.example to .env"
  exit 1
fi

echo "Starting stocks-api locally..."
uvicorn app.main:app --reload