# Scholara deployment environment matrix and owner runbook

Every variable below is read by the code named in the "consumed by" column. Nothing here
is invented: the list was generated from `backend/app/core/config.py` (the `Settings`
model, 74 fields), `frontend/src/lib/api-base.ts` and `frontend/next.config.ts`.
No secret value appears in this document.

Legend for the status column:

- **REQUIRED** - the service misbehaves without it.
- **RECOMMENDED** - works without it, but degraded or unsafe in production.
- **OPTIONAL** - feature switch for an integration that is not in use yet.

---

## 1. Frontend (Vercel)

Next.js is a static/ISR build on Vercel. Server components and the `/api/:path*` rewrite
call the Render backend directly; the visitor's browser only ever talks to the Vercel
origin, so no backend credential is ever needed in the browser.

| Variable | Status | Consumed by | Notes |
| --- | --- | --- | --- |
| `INTERNAL_API_URL` | **REQUIRED** | `src/lib/api-base.ts` -> `src/lib/api.ts` and `next.config.ts` | Absolute origin of the Render API, e.g. `https://scholora-cfch.onrender.com`. **The build fails** if it is missing, is not a valid absolute URL, uses `http://` for a non-loopback host, or points at a loopback address. |
| `INTERNAL_API_TOKEN` | **RECOMMENDED** | `src/lib/api.ts` (`X-Internal-Token` header) | Shared secret that exempts server-side rendering from the API's public rate limit. Must equal the Render value exactly. **Server-side only** - never prefix it with `NEXT_PUBLIC_`. |
| `NEXT_PUBLIC_SITE_URL` | **RECOMMENDED** | `src/lib/api-base.ts` -> sitemap, robots, canonical/OG tags | The production site origin. If unset, Vercel's own domain (`VERCEL_PROJECT_PRODUCTION_URL` / `VERCEL_URL`) is used. A localhost value is refused in a production build. |
| `ALLOW_LOCAL_API` | OPTIONAL | `src/lib/api-base.ts` | `true` lets a *deliberate* production-mode build (`npm run build` on a laptop) point at `http://127.0.0.1:8000`. Leave unset on Vercel. |
| `NEXT_BUILD_WORKERS` | OPTIONAL | `next.config.ts` | Caps static-generation workers (default `1`) so a small build container is not OOM-killed. |

There is **no** `NEXT_PUBLIC_*` variable carrying a secret, a database URL or a token. Do
not add one: everything prefixed `NEXT_PUBLIC_` is inlined into the JavaScript sent to
every visitor.

Because pages use Incremental Static Regeneration, a Vercel build fetches live data from
Render. **The Render service must be up and the database must be migrated before
deploying the frontend.** A cold start is retried automatically (two retries, 1.5 s and
4 s); a real outage fails the build on purpose rather than publishing empty pages.

---

## 2. Backend web service (Render)

| Variable | Status | Consumed by | Notes |
| --- | --- | --- | --- |
| `ENVIRONMENT` | **REQUIRED** | `config.py`, `connectivity.py`, `database.py`, `main.py` | Must be `production`. Among other things it makes the app refuse SQLite, require Redis, and disable the in-process scheduler. |
| `DATABASE_URL` | **REQUIRED** | `core/database.py`, `core/connectivity.py`, `alembic/env.py` | Supabase **Session Pooler**, `postgresql+psycopg://postgres.<project-ref>:<percent-encoded-password>@aws-1-ap-south-1.pooler.supabase.com:5432/postgres?sslmode=require`. See §5. |
| `REDIS_URL` | **REQUIRED in production** | `core/cache.py`, `core/ratelimit.py`, `connectivity.py` | Upstash **TCP** URL with TLS: `rediss://default:<password>@<host>.upstash.io:6379/0`. An `https://` REST URL is rejected with a clear error. |
| `CELERY_BROKER_URL` | **REQUIRED in production** | `services/tasks.py`, `connectivity.py` | Same form as `REDIS_URL`. |
| `CELERY_RESULT_BACKEND` | OPTIONAL | `services/tasks.py` | Leave **empty**. Results are ignored unless set, so a missing logical database on Upstash cannot break task execution. |
| `SCHEDULER_ENABLED` | RECOMMENDED | `services/tasks.py` | `false` in production; Celery beat owns the schedule. |
| `ALLOW_IN_PROCESS_SCHEDULER_IN_PRODUCTION` | OPTIONAL | `services/tasks.py` | `true` only for a deliberately single-instance deployment with no Celery. |
| `SECRET_KEY` | **REQUIRED** | `core/security.py` | Signs JWTs. A weak or default value lets anyone mint an admin token. |
| `INTERNAL_API_TOKEN` | **RECOMMENDED** | `core/ratelimit.py` | Must equal the Vercel value. Generated with `openssl rand -hex 32`; not a password. |
| `CORS_ORIGINS` | **REQUIRED in production** | `config.py` -> `main.py` | Comma-separated browser origins, e.g. `https://scholara.vercel.app`. A bare hostname is upgraded to `https://`. `*` is accepted but logs a warning: any website could then call the API. |
| `CORS_ORIGIN_REGEX` | OPTIONAL | `config.py` -> `main.py` | Only for trusted preview domains. Empty by default; the previous hardcoded `https://.*\.e2b\.app` allowed every sandbox on the internet. |
| `PUBLIC_SITE_URL` | RECOMMENDED | `config.py`, sitemap metadata | The production frontend origin. It is always added to the allowed CORS origins. |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | RECOMMENDED | `app/seed.py` | Only used by the seeding command. Change the default password. |
| `DB_CONNECT_TIMEOUT`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, `DB_POOL_RECYCLE`, `DB_SSLMODE` | OPTIONAL | `core/database.py` | Defaults (10 s, 3, 2, 30 s, 180 s, `require`) already fit one web instance plus one worker inside a small Supabase pool. |
| `CACHE_KEY_PREFIX` | OPTIONAL | `core/cache.py` | Default `scholara:`; namespaces keys if the Redis database is shared. |
| `STORAGE_BACKEND`, `STORAGE_DIR`, `S3_*` | OPTIONAL | `core/storage.py` | Private document storage. `local` writes to ephemeral disk - use `s3` in production if uploaded documents must survive a redeploy. |
| `WHATSAPP_*`, `META_*`, `FACEBOOK_*`, `GA4_*`, `GOOGLE_*` | OPTIONAL | `services/notifications.py`, `core/webhooks.py`, routers | External integrations. Most can also be set at runtime from the admin UI, which stores them encrypted in `runtime_settings`. |
| `XAI_API_KEY`, `MISTRAL_API_KEY`, `GEMINI_API_KEY`, `LLM_*`, `EMBEDDING_*` | OPTIONAL | `core/llm.py`, `core/embeddings.py` | The grounded assistant falls back to deterministic templates and a local vectoriser when no key is set. |

### Render service commands

Root directory for all backend services: `backend`.

| Service | Build | Start |
| --- | --- | --- |
| Web | `pip install -r requirements.txt` | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Worker | `pip install -r requirements.txt` | `celery -A app.services.tasks:celery_app worker --pool=threads --concurrency=2 --loglevel=info` |
| Beat (exactly one) | `pip install -r requirements.txt` | `celery -A app.services.tasks:celery_app beat --loglevel=info` |
| Migrations | `pip install -r requirements.txt` | `alembic upgrade head` (pre-deploy or one-off job) |

Render health check path: **`/api/health/live`**. It deliberately touches neither Supabase
nor Upstash, so a dependency blip cannot make Render restart a healthy process in a loop.
Use `/api/health/ready` for monitoring and smoke tests; it returns 503 when a required
dependency is down.

---

## 3. Worker service (Render)

Same environment group as the web service, because the scheduled jobs read and write
PostgreSQL. Keep `SCHEDULER_ENABLED=false`; the worker only consumes tasks. If the
Supabase pool is small, reduce `DB_POOL_SIZE` to 2 and keep worker concurrency at 1-2.

## 4. Beat service (Render)

Only needs `ENVIRONMENT` and `CELERY_BROKER_URL` (plus `CELERY_RESULT_BACKEND` if used).
Run **exactly one** beat instance: two would enqueue every scheduled job twice.

---

## 5. The Supabase connection string

The direct host `db.<project-ref>.supabase.co` has an **IPv6-only** DNS record:

```
db.<project-ref>.supabase.co  ->  2406:da1a:...  (AAAA only, no A record)
```

Render's egress is IPv4, so it reports `Network is unreachable` and psycopg surfaces
`connection is bad`. Supabase's IPv4-compatible path is the **Session Pooler**:

```
postgresql+psycopg://postgres.<project-ref>:<url-encoded-password>@aws-1-ap-south-1.pooler.supabase.com:5432/postgres?sslmode=require
```

- Get the host and the `postgres.<project-ref>` username from
  **Supabase Dashboard -> Connect -> Session Pooler**. Do not retype them.
- Use the **Session Pooler (port 5432)**, not the transaction pooler (port 6543). The
  transaction pooler is built for short-lived serverless connections and is a poor fit
  for SQLAlchemy's prepared statements and connection pool.
- Percent-encode reserved characters in the password: `@` -> `%40`, `:` -> `%3A`,
  `/` -> `%2F`, `#` -> `%23`, `?` -> `%3F`.
- Keep `sslmode=require`. TLS is never disabled by the application.
- The driver must be `postgresql+psycopg://`. A plain `postgresql://` makes SQLAlchemy
  select psycopg2, which is not installed.
- Confirm at startup: the log line `database configured ... pooler=True` with
  `host=….pooler.supabase.com`. If it says `is_supabase_direct`, the variable was not
  updated. If authentication fails with "tenant/user ... not found", the pooler host or
  the `<project-ref>` suffix in the username belongs to a different Supabase cluster.

A dedicated IPv4 address (`Supabase -> Project Settings -> Addons -> IPv4`) also works and
removes the pooler from the path, but it is a **paid add-on**, gives no connection pooling,
and still exposes port 5432 to the internet. The Session Pooler is free, is the supported
answer for long-lived application pools, and is what this deployment uses.

---

## 6. Owner checklist (no secrets in this document)

Do these in order. Steps 1-4 are what currently block production.

1. **Supabase** - Dashboard -> Connect -> Session Pooler. Copy the host, port, database,
   username (`postgres.<project-ref>`) and password. Nothing to change in the database.
2. **Render -> scholara API -> Environment** - set `DATABASE_URL` to the Session Pooler URL
   from step 1, `ENVIRONMENT=production`, `REDIS_URL` and `CELERY_BROKER_URL` to the
   Upstash **TCP/TLS** URLs (`rediss://`), `CELERY_RESULT_BACKEND` empty,
   `SCHEDULER_ENABLED=false`, and `CORS_ORIGINS` to the real Vercel origin. Remove
   surrounding quotes from every value.
3. **Render -> run the migration** - one-off job or pre-deploy: `cd backend && alembic
   upgrade head`, then `alembic current` must print `3228d79ec167 (head)`.
4. **Seed the site settings** - `cd backend && python -m app.seed` on an empty database.
   Until this runs, `GET /api/v1/settings` answers 404 and the frontend falls back to
   built-in defaults.
5. **Render -> smoke test** - `cd backend && python scripts/check_connectivity.py --json`,
   then `curl -fsS https://scholara-cfch.onrender.com/api/health/ready` must show
   `"ready": true`.
6. **Vercel -> Settings -> Environment Variables (Production and Preview)** - set
   `INTERNAL_API_URL=https://scholara-cfch.onrender.com`, `INTERNAL_API_TOKEN` (identical
   to Render), and `NEXT_PUBLIC_SITE_URL` to the Vercel production domain.
7. **Vercel -> Redeploy.** The build now fails loudly if any of the above is wrong.
8. **Rotate credentials** that were ever shared in plain text, and change the seeded admin
   password (`ADMIN_PASSWORD`) before the site is public.

---

## 7. Post-deploy verification

```bash
BASE=https://scholara-cfch.onrender.com
curl -fsS $BASE/api/health/live            # {"status":"ok",...}
curl -fsS $BASE/api/health/ready           # {"ready":true,...,"status":"ready"}
curl -fsS $BASE/api/v1/sitemap-data | head # real slugs, not []
curl -fsS $BASE/api/v1/settings            # 200 after seeding
curl -fsS $BASE/api/v1/vocabularies        # 200

# CORS from the production origin (must not be 400)
curl -s -o /dev/null -w '%{http_code}\n' -X OPTIONS $BASE/api/v1/settings \
  -H "Origin: https://<your-vercel-domain>" \
  -H "Access-Control-Request-Method: GET"

# Sitemap from the deployed frontend
curl -fsS https://<your-vercel-domain>/sitemap.xml | grep -c '<url>'
```

A readiness response of 503 is a real dependency failure. Do not route traffic until it
is fixed, and do not read an empty `/sitemap-data` as success - it means the database is
migrated but not seeded.
