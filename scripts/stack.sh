#!/usr/bin/env bash
#
# Bring up the full specified stack: PostgreSQL + pgvector, Redis, the FastAPI app, a Celery
# worker and beat, and the Next.js site.
#
#   ./scripts/stack.sh up        start infrastructure, migrate, seed, reindex
#   ./scripts/stack.sh status    show what is running and what the app would connect to
#   ./scripts/stack.sh serve     run API + worker + beat + website in the foreground
#   ./scripts/stack.sh down      stop the services this script started
#
# `dev.sh` remains the zero-infrastructure path (SQLite, in-process cache and scheduler) and is
# still fully supported - the application selects its backends from the environment, so the only
# difference between the two is which variables are set.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PG_VERSION="${PG_VERSION:-17}"
PG_USER="${PG_USER:-scholara}"
PG_PASS="${PG_PASS:-scholara}"
PG_DB="${PG_DB:-scholara}"

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://${PG_USER}:${PG_PASS}@127.0.0.1:5432/${PG_DB}}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
export CELERY_BROKER_URL="${CELERY_BROKER_URL:-redis://127.0.0.1:6379/1}"
export INTERNAL_API_URL="${INTERNAL_API_URL:-http://127.0.0.1:8000}"

say() { printf '\n\033[1m→ %s\033[0m\n' "$*"; }

start_postgres() {
  if pg_isready -h 127.0.0.1 -q 2>/dev/null; then
    echo "   PostgreSQL already accepting connections"
    return
  fi
  # NOTE: use pg_ctlcluster, not systemctl. In containers policy-rc.d denies service starts and
  # systemd is usually not PID 1, so `systemctl start postgresql` fails confusingly.
  sudo pg_ctlcluster "$PG_VERSION" main start
  for _ in $(seq 1 30); do
    pg_isready -h 127.0.0.1 -q 2>/dev/null && break
    sleep 1
  done
}

provision_postgres() {
  sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${PG_USER}'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE ROLE ${PG_USER} LOGIN PASSWORD '${PG_PASS}' CREATEDB"
  sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${PG_DB}'" | grep -q 1 || \
    sudo -u postgres createdb -O "${PG_USER}" "${PG_DB}"
  # pgvector must exist before the migration that adds the embedding column runs.
  sudo -u postgres psql -d "${PG_DB}" -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null
  echo "   role/database ${PG_USER}/${PG_DB} ready, vector extension present"
}

start_redis() {
  if redis-cli ping >/dev/null 2>&1; then
    echo "   Redis already responding"
    return
  fi
  redis-server --daemonize yes --port 6379 --bind 127.0.0.1 --save ''
  sleep 1
  redis-cli ping >/dev/null && echo "   Redis started"
}

case "${1:-up}" in
  up)
    say "PostgreSQL"
    start_postgres
    provision_postgres

    say "Redis"
    start_redis

    say "Migrations"
    (cd backend && python3 -m alembic upgrade head)

    say "Seed + knowledge index"
    (cd backend && python3 - <<'PY'
from app.core.database import SessionLocal
from app.models.education import University
from app.services import retrieval

db = SessionLocal()
if db.query(University).count() == 0:
    from app import seed
    db.close()
    print("   empty database - seeding")
    print("  ", seed.run(reset=True))
    db = SessionLocal()
else:
    print("   data already present, not reseeding")

counts = retrieval.rebuild_index(db)
print(f"   indexed {counts['total']} chunks, {counts['embedded']} embedded "
      f"via {counts['embedding_backend']} into {counts['vector_store']}")
db.close()
PY
    )

    say "Ready"
    echo "   DATABASE_URL=$DATABASE_URL"
    echo "   REDIS_URL=$REDIS_URL"
    echo "   CELERY_BROKER_URL=$CELERY_BROKER_URL"
    echo
    echo "   next: ./scripts/stack.sh serve"
    ;;

  serve)
    say "API http://localhost:8000/api/docs"
    (cd backend && python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000) & API_PID=$!

    say "Celery worker + beat"
    (cd backend && python3 -m celery -A app.services.tasks:celery_app worker \
        --loglevel=warning --concurrency=2 --pool=threads) & WORKER_PID=$!
    (cd backend && python3 -m celery -A app.services.tasks:celery_app beat \
        --loglevel=warning) & BEAT_PID=$!

    say "Website http://localhost:3000"
    (cd frontend && npm run dev) & WEB_PID=$!

    trap 'echo; echo "→ Stopping…"; kill $API_PID $WORKER_PID $BEAT_PID $WEB_PID 2>/dev/null || true' INT TERM
    wait
    ;;

  status)
    say "Infrastructure"
    pg_isready -h 127.0.0.1 2>/dev/null || echo "   PostgreSQL: not running"
    redis-cli ping >/dev/null 2>&1 && echo "   Redis: PONG" || echo "   Redis: not running"

    say "What the application sees"
    (cd backend && python3 - <<'PY'
from app.core.config import settings
from app.core.cache import cache
from app.core import embeddings
from app.services import tasks

print(f"   database        {settings.database_url.split('@')[-1] or settings.database_url}")
print(f"   is_postgres     {settings.is_postgres}")
print(f"   cache backend   {cache.backend}")
print(f"   celery          {'configured' if tasks.celery_app else 'not configured (in-process scheduler)'}")
print(f"   embeddings      {embeddings.active_backend()} @ {embeddings.dim()} dims")
PY
    )

    say "Vector index"
    (cd backend && python3 - <<'PY'
from sqlalchemy import text
from app.core.config import settings
from app.core.database import SessionLocal

db = SessionLocal()
if settings.is_postgres:
    total, vectors = db.execute(text(
        "SELECT count(*), count(embedding_vec) FROM knowledge_chunks")).one()
    print(f"   {vectors}/{total} chunks carry a pgvector embedding")
else:
    print("   SQLite: JSON embeddings + Python cosine (no pgvector)")
db.close()
PY
    )
    ;;

  down)
    say "Stopping"
    pkill -f "celery -A app.services.tasks" 2>/dev/null && echo "   celery stopped" || true
    pkill -f "uvicorn app.main:app" 2>/dev/null && echo "   api stopped" || true
    redis-cli shutdown nosave 2>/dev/null && echo "   redis stopped" || true
    sudo pg_ctlcluster "$PG_VERSION" main stop 2>/dev/null && echo "   postgresql stopped" || true
    ;;

  *)
    echo "usage: $0 {up|serve|status|down}" >&2
    exit 2
    ;;
esac
