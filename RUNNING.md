# Scholara — Running the project

Two parts: **local development** and **production**. Every command below has been run
against this codebase; the gotchas in each section are failures that actually happened,
not hypotheticals.

Companion: `ARCHITECTURE.md`. The deployment runbook in the local `docs/` working copy covers the
longer-form deployment discussion.

---

# Part 1 — Local development

## 1.1 Prerequisites

| Need | Version | Note |
|---|---|---|
| Python | 3.11+ (built on 3.13) | |
| Node.js | 20+ | Next.js 16 |
| PostgreSQL | 15+ (built on 17) | **pgvector extension required** |
| Redis | 6+ | cache, rate limiting, Celery broker |

## 1.2 Database

```bash
# Start PostgreSQL. On Debian/Ubuntu with a packaged cluster use pg_ctlcluster —
# in a container systemctl is not running and `systemctl start postgresql` will fail.
sudo pg_ctlcluster 17 main start

# psql may not be on PATH:
export PATH=$PATH:/usr/lib/postgresql/17/bin

sudo -u postgres psql <<'SQL'
CREATE USER scholara WITH PASSWORD 'scholara';
CREATE DATABASE scholara OWNER scholara;
\c scholara
CREATE EXTENSION IF NOT EXISTS vector;
SQL
```

```bash
# Redis
redis-server --daemonize yes --port 6379 --bind 127.0.0.1 --save ''
```

## 1.3 Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

> **Do not comment anything out of `requirements.txt`.** It once shipped with the real
> stack commented, and a clean install silently fell back to SQLite — everything
> "worked" while pgvector, Redis and Celery were quietly absent.

Create `backend/.env`:

```ini
DATABASE_URL=postgresql+psycopg://scholara:scholara@127.0.0.1:5432/scholara
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_BROKER_URL=redis://127.0.0.1:6379/1
SECRET_KEY=change-me-in-production
INTERNAL_API_TOKEN=dev-internal-render-token
PUBLIC_SITE_URL=http://localhost:3000
ENVIRONMENT=development
# All third-party credentials are optional — see §1.7.
```

Migrate and seed:

```bash
alembic upgrade head              # 48 tables
python3 -m app.seed               # demo content, users, homepage slides
./../scripts/load-reference-data.sh   # countries, universities, programmes, scholarships
```

Run it:

```bash
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# http://localhost:8000/api/health
# http://localhost:8000/docs
```

## 1.4 Celery (optional locally)

```bash
cd backend
python3 -m celery -A app.services.tasks:celery_app worker --pool=prefork --concurrency=2
python3 -m celery -A app.services.tasks:celery_app beat        # scheduled jobs
```

> **Use `--pool=prefork`, not `--pool=threads`.** The worker is configured with a hard time limit
> (900 s) and a soft one (780 s), and Celery enforces those only through a prefork pool. With
> `--pool=threads` a task that hangs runs until the worker is restarted: measured, a 20 s task with
> a 5 s hard limit completed normally and was never killed. Prefork also supports
> `task_reject_on_worker_lost`, which requeues work when a child process dies.
>
> Whatever the pool, delivery is **at-least-once**: a crashed worker's task is restored after
> `visibility_timeout` (1800 s) and runs again. Every job is therefore written to be idempotent,
> and `tests/test_tasks.py` runs the whole schedule twice to keep it that way.

## 1.5 Frontend

```bash
cd frontend
npm install
echo 'INTERNAL_API_URL=http://127.0.0.1:8000' > .env.local
npm run dev        # http://localhost:3000
```

`INTERNAL_API_URL` is used by **server components only**. Browser code always calls
relative `/api/...` paths, which Next proxies — never point browser code at the API host
directly, because the user's browser is not inside your network.

## 1.6 Sign in

| Role | Email | Password | MFA |
|---|---|---|---|
| Admin | `admin@scholara.com.bd` | `Admin@12345` | yes |
| Senior counselor | `senior@scholara.com.bd` | `Senior@12345` | yes |
| Counselor | `counselor@scholara.com.bd` | `Counselor@123` | no |
| Data manager | `data@scholara.com.bd` | `Data@12345` | no |

MFA code for the seeded dev secret: `./scripts/mfa-code.sh [email]`
Student portal demo token: `demo-student`

**Change every one of these before any public deployment.** These accounts and that script do
not exist in a production environment: `seed.py` skips them when `ENVIRONMENT=production`, the
script refuses to run there, and migration `a7b8c9d0e1f2` clears any account still holding one
of the published TOTP secrets. Provision real staff with `python -m app.mgmt_admin --create-admin`.

The admin sign-in screen no longer prints these credentials; the hint is compiled out of a
production build. Enrolment now shows a QR code, and the security panel can regenerate recovery
codes or reset another staff member's second factor.

## 1.7 Running with no third-party credentials

Fully supported and the default. Each integration degrades to a defined behaviour:

| Integration | Unconfigured behaviour |
|---|---|
| WhatsApp Cloud API | `send_message` returns `{"status": "SKIPPED_NO_CREDENTIALS"}` |
| Meta Pixel / CAPI | conversions recorded as `LOGGED_LOCALLY` |
| Facebook Page | page panel shows "not connected" |
| Grok / Mistral / Gemini | assistant + extraction use the deterministic path |
| Analytics IDs | placeholder values (`G-XXXXXXXXXX`) load no scripts at all |
| Gmail SMTP | security notices stay in the staff inbox (`status=QUEUED`, reason on the row); startup logs `email disabled: ...` |

Verify from the admin UI: **Platform → Test connection** (`/config/test/{target}` for
`whatsapp`, `meta_capi`, `facebook_page`, `embeddings`, `llm`, `smtp`).

### Enabling email locally

```bash
# backend/.env (never commit it)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your.address@gmail.com     # a Google App Password, not the account password
SMTP_PASSWORD=abcd efgh ijkl mnop
SMTP_FROM_NAME=Scholara
```

Then, instead of guessing:

```bash
cd backend
python -m app.mgmt_admin --email-status                    # config + live login, sends nothing
python -m app.mgmt_admin --send-test-email you@example.org  # one real message
```

Full detail, including the Gmail App Password steps and the sending limits:
the email chapter of the local `docs/` working copy.

## 1.8 Everything at once

```bash
chmod +x scripts/*.sh      # the execute bit does not always survive a checkout
./scripts/stack.sh         # postgres + redis + api + celery + web
```

## 1.9 Tests and checks

```bash
cd backend && python3 -m pytest           # 1,037 tests on SQLite (25 skipped: the live-service tests)
./scripts/verify-new.sh                   # 63 data/API invariants
./scripts/smoke-test.sh                   # every public route
python3 scripts/check-prerender.py        # 421 prerendered pages return success
cd frontend && npx tsc --noEmit           # types
```

A SQLite run skips the tests that need a real server (`pg` and `redis_live`), because row locks,
`SKIP LOCKED`, unique-violation re-evaluation and shared counters do not exist there. To run them:

```bash
export SCHOLARA_PG_URL='postgresql+psycopg://user:pw@127.0.0.1:5432/disposable_db'
export SCHOLARA_TEST_DATABASE_URL="$SCHOLARA_PG_URL"   # run the whole suite on PostgreSQL
export SCHOLARA_REDIS_URL='redis://127.0.0.1:6379/0'   # the live cache tests
export SCHOLARA_REQUIRE_PG_TESTS=1 SCHOLARA_REQUIRE_REDIS_TESTS=1   # a skip becomes a failure
cd backend && python3 -m pytest
```

`SCHOLARA_REQUIRE_*` is what the PostgreSQL CI job uses: without it a skipped test and a passing
test look identical in a summary, so the job could "pass" while proving nothing. With it set, a
missing URL is a usage error and any required test that skips fails the run.

That four-variable profile is the strongest local check this repository has: **1,235 passed, zero
skipped**, the whole suite against a real PostgreSQL with a real Redis required. CI splits the same
coverage across two jobs so it does not run it twice — `backend-postgres` runs the suite with only
`SCHOLARA_REQUIRE_PG_TESTS=1` (1,226 passed, the 9 Redis tests skipped) plus the concurrency module
explicitly, and `backend-redis` runs it with only `SCHOLARA_REQUIRE_REDIS_TESTS=1` (1,194 passed,
the 41 PostgreSQL tests skipped) plus the live cache module explicitly. Each job fails if its own
service's tests skip, so neither can pass by skipping the thing it exists to test. The workflow is
`.github/workflows/ci.yml`, and `.github` is part of this branch — a green badge here is a run over
the tree you are reading.

#### Migrations

```bash
cd backend
export DATABASE_URL='postgresql+psycopg://user:pw@127.0.0.1:5432/disposable_db'
python3 -m alembic heads                                    # b4c5d6e7f8a9 (head) — exactly one
python3 -m alembic history                                  # the published baseline, then eight revisions
python3 -m alembic upgrade head                             # 51 tables + alembic_version
python3 -m alembic downgrade a7b8c9d0e1f2                    # back to the published baseline
python3 -m alembic upgrade head
```

The eight revisions on top of the published baseline `a7b8c9d0e1f2` are `b8c9d0e1f2a3`
(destination hierarchy, university regions, chunk provenance), `c9d0e1f2a3b4` (assistant sessions),
`d7e8f9a0b1c2` (pending object deletions), `d9e0f1a2b3c4` (explicit contact consent), `e1f2a3b4c5d6`
(assistant context slots), `f2a3b4c5d6e7` (catalogue changes), `a3b4c5d6e7f8` (unique change
version) and `b4c5d6e7f8a9` (full-length verification evidence; the downgrade refuses while evidence
longer than 400 characters exists rather than truncating it). `tests/test_pg_concurrency.py` is the suite that needs the real server; it runs against a
dedicated schema (`pgcamp`) and drops it first, so it can share a disposable database.

---

# Part 2 — Production

## 2.1 Topology

```
            Internet (HTTPS only)
                    │
          ┌─────────▼─────────┐
          │  Nginx / Caddy    │  TLS, HSTS, gzip, security headers
          └────┬─────────┬────┘
      /api/*   │         │   everything else
          ┌────▼────┐ ┌──▼──────────┐
          │ FastAPI │ │  Next.js    │
          │ gunicorn│ │  next start │
          │ +uvicorn│ │             │
          └────┬────┘ └─────────────┘
               │
   ┌───────────┼───────────────┬──────────────┐
   │           │               │              │
┌──▼───────┐ ┌─▼─────┐ ┌───────▼──────┐ ┌─────▼──────┐
│PostgreSQL│ │ Redis │ │Celery worker │ │Celery beat │
│+pgvector │ │       │ │              │ │            │
└──────────┘ └───────┘ └──────────────┘ └────────────┘
```

Postgres, Redis and Celery must **not** be reachable from the internet.

## 2.2 Production environment

```ini
ENVIRONMENT=production
SECRET_KEY=<64+ random chars — rotating this invalidates stored encrypted secrets>
DATABASE_URL=postgresql+psycopg://USER:PASS@db-host:5432/scholara
REDIS_URL=redis://redis-host:6379/0
CELERY_BROKER_URL=redis://redis-host:6379/1
PUBLIC_SITE_URL=https://scholara.com.bd
CORS_ORIGINS=https://scholara.com.bd
INTERNAL_API_TOKEN=<random, server-to-server only>

STORAGE_BACKEND=s3
S3_ENDPOINT=...  S3_BUCKET=...  S3_REGION=...
S3_ACCESS_KEY=...  S3_SECRET_KEY=...
SIGNED_URL_TTL_SECONDS=300

MFA_REQUIRED_ROLES=ADMIN,SENIOR_COUNSELOR,DATA_MANAGER
MFA_CHALLENGE_TTL_SECONDS=300
RATE_LIMIT_ENABLED=true
SCHEDULER_ENABLED=true
```

Credentials you add when the accounts exist (all optional):

```ini
WHATSAPP_PHONE_NUMBER_ID=   WHATSAPP_ACCESS_TOKEN=
WHATSAPP_VERIFY_TOKEN=      WHATSAPP_APP_SECRET=
FACEBOOK_PAGE_ID=           FACEBOOK_PAGE_ACCESS_TOKEN=
META_PIXEL_ID=              META_CAPI_ACCESS_TOKEN=
GA4_MEASUREMENT_ID=         GOOGLE_ADS_CONVERSION_ID=
GOOGLE_SITE_VERIFICATION=
XAI_API_KEY=                MISTRAL_API_KEY=       GEMINI_API_KEY=
LLM_PROVIDER_ORDER=grok,mistral,gemini
SMTP_HOST=smtp.gmail.com    SMTP_PORT=587
SMTP_USER=                  SMTP_PASSWORD=          # Google App Password
SMTP_FROM_EMAIL=            SMTP_FROM_NAME=Scholara
```

Set the SMTP variables on **both** the API and the worker service: the API sends the
security notices, and a worker that raises notifications needs the same credentials. Confirm
from the deployed shell with `python -m app.mgmt_admin --email-status`.

Most of these can also be set from **Admin → Platform settings** without a redeploy —
21 keys across 5 groups, stored encrypted and returned masked.

## 2.3 Deploy

```bash
# Backend
cd backend
pip install -r requirements.txt
alembic upgrade head
gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  -w 4 -b 0.0.0.0:8000 \
  --timeout 90 --access-logfile - --error-logfile -
```

```bash
# Frontend
cd frontend
npm ci
NODE_OPTIONS="--max-old-space-size=1024" NEXT_BUILD_WORKERS=1 npm run build
npm run start      # or a Node process manager
```

> **Build memory.** `--max-old-space-size=1250` gets OOM-killed on a small box. Use
> 1024 with `NEXT_BUILD_WORKERS=1`, and stop Celery and any running `next start` first —
> the build competes with them for RAM.

```bash
# Workers
celery -A app.services.tasks:celery_app worker --pool=prefork --concurrency=4 --loglevel=info
celery -A app.services.tasks:celery_app beat --loglevel=info
```

Run exactly **one** beat process. Reminders are de-duplicated per entity per day, so a second beat
no longer double-sends them, but it still duplicates work, competes for the same rows and makes the
schedule harder to reason about.

### systemd sketch

```ini
# /etc/systemd/system/scholara-api.service
[Unit]
After=network.target postgresql.service redis.service
[Service]
User=scholara
WorkingDirectory=/srv/scholara/backend
EnvironmentFile=/srv/scholara/backend/.env
ExecStart=/srv/scholara/backend/.venv/bin/gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker -w 4 -b 127.0.0.1:8000
Restart=always
[Install]
WantedBy=multi-user.target
```

### Nginx

```nginx
server {
  listen 443 ssl http2;
  server_name scholara.com.bd;

  add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
  add_header X-Content-Type-Options nosniff always;
  add_header X-Frame-Options SAMEORIGIN always;
  add_header Referrer-Policy strict-origin-when-cross-origin always;

  client_max_body_size 16m;          # uploads are capped at 15 MB server-side

  location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
  location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

`X-Forwarded-For` matters: the rate limiter keys on client IP, and without it every
request appears to come from the proxy and shares one bucket.

## 2.4 Go-live checklist

Use **Admin → Go-live & verification** (`/admin/readiness`), which checks these live.
Manually confirm:

- [ ] All seeded passwords changed; MFA enrolled for every staff account
- [ ] `SECRET_KEY` is new and backed up — rotating it invalidates stored encrypted secrets
- [ ] `CORS_ORIGINS` is the real domain, not `*`
- [ ] TLS valid; HTTP redirects to HTTPS
- [ ] Postgres/Redis not publicly reachable; automated backups running
- [ ] `STORAGE_BACKEND=s3` with a **private** bucket
- [ ] One beat process only
- [ ] WhatsApp webhook URL + verify token registered in Meta
- [ ] `GOOGLE_SITE_VERIFICATION` set, sitemap submitted to Search Console
- [ ] `/admin/*` is `noindex` (already set in metadata)
- [ ] A restore from backup has actually been tested

## 2.5 Operations

```bash
# Health
curl https://scholara.com.bd/api/health

# Re-check every cited source URL (link reachability)
curl -X POST .../api/v1/admin/verification/sources/check -H "Authorization: Bearer $TOKEN"

# The human verification queue
curl .../api/v1/admin/verification/queue -H "Authorization: Bearer $TOKEN"

# Citation depth audit → the citation diff in the local docs/ working copy
cd backend && python3 scripts/verify_citations.py

# Rebuild the knowledge index (refits the local LSA model)
curl -X POST .../api/v1/admin/embeddings/reindex -H "Authorization: Bearer $TOKEN"
```

Reindex after any bulk import. Retrieval scores are only comparable within one fitted
model, so stale vectors degrade search quietly rather than loudly.

Backups: nightly `pg_dump`, plus the object store. The database holds all 4,551
provenance records — losing it loses the evidence trail, not just the content.

## 2.6 Troubleshooting

| Symptom | Cause |
|---|---|
| `401 Missing bearer token` while testing repeatedly | login rate limit (10/5 min). Clear `rl:*` from Redis. |
| Everything works but pgvector/Celery seem absent | `requirements.txt` was edited; reinstall in full |
| `systemctl start postgresql` fails in a container | use `pg_ctlcluster 17 main start` |
| Frontend builds then OOMs | lower `--max-old-space-size` to 1024, `NEXT_BUILD_WORKERS=1` |
| Assistant answers "I don't have verified information" | expected with no LLM key and thin retrieval — not a bug |
| Search quality drops after an import | reindex; vectors are stale |
| Students get duplicate reminders | more than one Celery beat running |
| `scripts/*.sh: permission denied` | `chmod +x scripts/*.sh` |
