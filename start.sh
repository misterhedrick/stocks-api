#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

cd "$SCRIPT_DIR"

echo "Checking database connection..."
python -m app.db.check_database

echo "Starting stocks-api..."
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" "$@"
