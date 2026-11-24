#!/usr/bin/env bash
# Start the Scholara backend and frontend together for local development.
# Usage: ./scripts/dev.sh        (Ctrl-C stops both)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ ! -f backend/data/scholara.db ]; then
  echo "→ No database found. Seeding…"
  (cd backend && python3 -m app.seed)
fi

echo "→ Backend  http://localhost:8000/api/v1  (OpenAPI: /api/openapi.json)"
(cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload) &
BACKEND_PID=$!

echo "→ Frontend http://localhost:3000  (admin: /admin)"
(cd frontend && npm run dev) &
FRONTEND_PID=$!

trap 'echo; echo "→ Stopping…"; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true' INT TERM
wait
