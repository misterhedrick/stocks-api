#!/usr/bin/env bash
if [ ! -f ".venv/Scripts/activate" ]; then
  echo "Missing virtual environment. Create it first with: python -m venv .venv"
  exit 1
fi

source .venv/Scripts/activate
python -m uvicorn app.main:app --reload
