#!/bin/bash
# Startet FastAPI-Backend (8001) + CRA-Frontend (3000, proxied /api -> 8001)
set -m
cd /app/backend
/root/.venv/bin/python3 -m uvicorn server:app --host 0.0.0.0 --port 8001 >> /var/log/supervisor/backend.out.log 2>&1 &
BACKEND_PID=$!
trap "kill $BACKEND_PID 2>/dev/null" EXIT TERM INT
cd /app/frontend
export HOST=0.0.0.0
export PORT=3000
exec yarn start
