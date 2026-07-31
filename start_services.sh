#!/bin/bash
# Orchestrates FastAPI backend (8001) + CRA frontend (3000) under one supervisor program
set -m

cd /app/backend
nohup /root/.venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8001 --reload > /var/log/supervisor/backend.out.log 2>&1 &

cd /app/frontend
exec env PORT=3000 HOST=0.0.0.0 yarn start
