#!/bin/bash
set -e

if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
  exit 0
fi

LOCAL_DB_URL="postgresql+psycopg://postgres:postgres@localhost:5432/stocks_api"

echo "[cloud-db] Starting PostgreSQL..."
service postgresql start

echo "[cloud-db] Configuring local database..."
su -c "psql -c \"ALTER USER postgres PASSWORD 'postgres';\"" postgres
su -c "psql -c \"CREATE DATABASE stocks_api;\"" postgres 2>/dev/null || true

echo "[cloud-db] Overriding DATABASE_URL for this session..."
echo "DATABASE_URL=${LOCAL_DB_URL}" >> "$CLAUDE_ENV_FILE"

echo "[cloud-db] Running migrations..."
cd "$CLAUDE_PROJECT_DIR"
DATABASE_URL="$LOCAL_DB_URL" python -m alembic upgrade head

echo "[cloud-db] Local DB ready: $LOCAL_DB_URL"
