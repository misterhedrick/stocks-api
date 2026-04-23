#!/usr/bin/env bash

set -e

# Get the directory where the script is actually located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

cd "$SCRIPT_DIR"

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
# Use python -m uvicorn to ensure the current directory is added to the PYTHONPATH
python -m uvicorn app.main:app --reload