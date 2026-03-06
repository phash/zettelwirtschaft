#!/bin/bash
set -e

cd /app
export PYTHONPATH=/app

python migrate.py

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
