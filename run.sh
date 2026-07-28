#!/usr/bin/env bash
# Локальный запуск без Docker.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

[ -f .env ] || cp .env.example .env

echo "Сайт: http://localhost:8000"
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
