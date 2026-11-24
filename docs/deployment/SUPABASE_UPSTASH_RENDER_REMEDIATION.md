# Supabase + Upstash + Render remediation

Status: **code and local verification complete; live Render verification blocked.**

This document records the permanent fix for the production startup failure:

```
psycopg.OperationalError: connection to server at
"2600:1f18:...:5432" failed: Network is unreachable
```

The failure was not a password problem and not a SQLAlchemy problem. It was the
Supabase **direct** database host resolving to IPv6 on Render.

---

## 1. Executive summary

| Area | Before | After |
| --- | --- | --- |
| Database host | `db.<ref>.supabase.co` (IPv6-only from Render) | Supabase **Session Pooler** over IPv4 |
| Database driver | `postgresql://` (SQLAlchemy defaults to psycopg2, not installed) | `postgresql+psycopg://` enforced at runtime |
| TLS | Not specified on the database URL | `sslmode=require` added for Supabase hosts, never weakened |
| Schema creation | `Base.metadata.create_all()` ran on every FastAPI startup | Removed from the API lifespan; Alembic owns production schema |
| Redis | `redis://` Upstash URL | `rediss://` enforced for Upstash; certificate verification on |
| Celery | Broker URL could contain a stray quote; results could point at a missing DB | URL cleaned, TLS normalised, results disabled unless explicitly configured |
| Health checks | One `/api/health` that did not test dependencies | `/api/health/live` (liveness) and `/api/health/ready` (503 on dependency failure) |
| Scheduler | In-process scheduler could run in production | Disabled in production unless explicitly overridden; Celery beat owns the schedule |
| Diagnostics | Driver error leaked into logs | Redacted summaries and a Render-safe connectivity script |

The only remaining work is to set the Render environment variables to the Supabase
Session Pooler and Upstash TLS URLs, then deploy and run the checks in §11.

---

## 2. Scope and non-goals

In scope:

- Database URL parsing, driver selection, TLS and pooling.
- Alembic configuration and migration safety.
- Redis TLS, cache fallback and Celery broker wiring.
- Startup, liveness and readiness behaviour.
- Render service commands and environment variables.
- Tests and a repeatable connectivity diagnostic.

Out of scope:

- Rotating credentials (recommended separately if the old `.env` was shared).
- Changing the application's data model or adding a migration.
- Replacing Supabase or Upstash with another provider.
- Frontend deployment changes.

---

## 3. Failure evidence

The local reproduction used the real production `.env` and `scripts/check_connectivity.py`:

```json
"database": {
  "dns": {"ok": true, "addresses": 1, "families": ["IPv6"]},
  "tcp": {"ok": false, "error": "gaierror"},
  "check": {
    "ok": false,
    "error": "OperationalError",
    "host": "db.….supabase.co",
    "is_supabase_direct": true,
    "sslmode": "require",
    "latency_ms": 2918.9
  }
}
```

The same run showed the Upstash cache and Celery broker reachable over TLS:

```json
"cache": {"summary": {"scheme": "rediss", "tls": true}, "tcp": {"ok": true}}
"celery_broker": {"summary": {"scheme": "rediss", "tls": true}, "tcp": {"ok": true}}
```

This isolates the fault to the Supabase direct host, not to Redis, Celery, credentials
or the application.

---

## 4. Root cause

Supabase exposes two database connection paths:

1. **Direct connection** — `db.<project-ref>.supabase.co:5432`. On IPv4-only egress
   (Render's default), this can resolve to an IPv6 address that Render cannot route.
2. **Session Pooler** — `<region>.pooler.supabase.com:5432`, username
   `postgres.<project-ref>`. It is reachable over IPv4 and is the correct target for a
   long-lived SQLAlchemy/psycopg pool.

The application was also calling `Base.metadata.create_all(engine)` from the FastAPI
lifespan. That made the API process responsible for schema creation, so any database
connectivity problem became a startup crash rather than a readiness failure. Production
schema must be owned by Alembic.

Finally, `postgresql://` and `postgres://` URLs make SQLAlchemy select psycopg2, which is
not installed. The URL must say `postgresql+psycopg://`.

---

## 5. Target architecture

```
Render Web (uvicorn)
  └── Supabase Session Pooler (IPv4, sslmode=require)
  └── Upstash Redis over TLS (cache + rate limits)

Render Worker (celery --pool=threads)
  └── Supabase Session Pooler
  └── Upstash Redis over TLS (broker)

Render Beat (celery beat, exactly one instance)
  └── Upstash Redis over TLS (broker; no database pool needed)

Render Pre-deploy / one-off job
  └── alembic upgrade head
      └── PostgreSQL advisory lock (serialises concurrent migration attempts)
```

Use the **Session Pooler** (port `5432`), not the transaction pooler (port `6543`).
The transaction pooler is designed for short-lived serverless connections and is a poor
fit for SQLAlchemy's prepared statements and connection pool.

---

## 6. Render environment variables

Set these in each Render service that needs them. Never commit the values.

### 6.1 Web service

```ini
ENVIRONMENT=production
DATABASE_URL=postgresql+psycopg://postgres.<project-ref>:<url-encoded-password>@<region>.pooler.supabase.com:5432/postgres?sslmode=require
REDIS_URL=rediss://default:<password>@<host>.upstash.io:6379/0
CELERY_BROKER_URL=rediss://default:<password>@<host>.upstash.io:6379/0
CELERY_RESULT_BACKEND=
SCHEDULER_ENABLED=false
ALLOW_IN_PROCESS_SCHEDULER_IN_PRODUCTION=false
CACHE_KEY_PREFIX=scholara:
```

Optional pool tuning (defaults shown):

```ini
DB_CONNECT_TIMEOUT=10
DB_POOL_SIZE=3
DB_MAX_OVERFLOW=2
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=180
DB_SSLMODE=require
```

### 6.2 Worker service

Same as the web service, except it does not need `PORT` or a public URL. Keep
`SCHEDULER_ENABLED=false`; the worker only consumes tasks.

### 6.3 Beat service

```ini
ENVIRONMENT=production
CELERY_BROKER_URL=rediss://default:<password>@<host>.upstash.io:6379/0
CELERY_RESULT_BACKEND=
SCHEDULER_ENABLED=false
```

Beat does not need `DATABASE_URL` for scheduling, but keeping it set is harmless if the
same environment group is used. Run **exactly one** beat instance.

### 6.4 Migration job

```ini
ENVIRONMENT=production
DATABASE_URL=postgresql+psycopg://postgres.<project-ref>:<url-encoded-password>@<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

### 6.5 URL rules

- Percent-encode special characters in passwords (`@` → `%40`, `:` → `%3A`, `/` → `%2F`).
- The Session Pooler username is `postgres.<project-ref>`, not bare `postgres`.
- Do not wrap environment values in quotes. A stray trailing `"` was present in the
  local `.env` for `CELERY_BROKER_URL`; the code strips it defensively, but Render should
  store the clean value.
- Upstash TCP URLs must use `rediss://`. The code upgrades Upstash `redis://` URLs to
  `rediss://`, but the explicit form is clearer and avoids surprises.
- Upstash REST URLs (`https://...`) are not Redis TCP URLs and are rejected.
- Leave `CELERY_RESULT_BACKEND` empty unless the Upstash plan supports a separate logical
  database. A missing `/1` database must not break task execution.

---

## 7. Code changes

| File | Change |
| --- | --- |
| `backend/app/core/connectivity.py` | New module: URL cleaning/normalisation, password-safe summaries, production SQLite refusal, psycopg connect args, Upstash TLS normalisation, Redis TLS kwargs, cached `SELECT 1`/`PING` probes, readiness report. |
| `backend/app/core/config.py` | Added database pool/timeout/SSL settings, Redis timeouts, cache key prefix, Celery result backend, scheduler production guard flag, and `is_production`. |
| `backend/app/core/database.py` | Normalises the URL, refuses production SQLite, creates one bounded psycopg pool with pre-ping/recycle/LIFO, hides SQL parameters, keeps SQLite foreign-key enforcement. |
| `backend/app/core/cache.py` | Redis client uses TLS-aware connect kwargs; records redacted URL summary and last error; adds `incr_with_ttl`; keeps memory fallback for development. |
| `backend/app/services/tasks.py` | `build_celery_app()` normalises broker/result URLs, disables results unless configured, retries on startup, registers all jobs; `scheduler_mode()` reports the real owner; production guard prevents an accidental in-process scheduler. |
| `backend/app/main.py` | Removed `create_all()` from the lifespan; logs redacted connection summaries; `/api/health` and `/api/health/live` are dependency-free liveness; `/api/health/ready` returns 503 when required dependencies fail; disposes the engine on shutdown. |
| `backend/app/seed.py` | `create_all()` only outside production. |
| `backend/app/migrate.py` | Refuses to run the SQLite-only helper against PostgreSQL or production. |
| `backend/alembic/env.py` | Normalises the URL, refuses production SQLite, uses `NullPool`, takes a PostgreSQL advisory lock, and enables batch mode on SQLite. |
| `backend/alembic/versions/20261004_55b14f98dd2d_promote_faculty_school_to_a_real_entity.py` | SQLite-only portability branches for adding/dropping the foreign key and for the backfill SQL. PostgreSQL behaviour is unchanged. |
| `backend/scripts/check_connectivity.py` | New Render-safe diagnostic: DNS, TCP, auth, `SELECT 1`, `PING`, redacted JSON. |
| `backend/tests/conftest.py` | Clears `REDIS_URL`, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` so tests never touch real infrastructure. |
| `backend/tests/test_connectivity.py` | 21 focused tests for URL handling, TLS, redaction, readiness, lifespan and Celery. |
| `backend/.env.example` | Documents the Session Pooler and Upstash TLS forms. |

No new database revision was added. The Alembic head remains `3228d79ec167`.

---

## 8. Database and Alembic behaviour

- Production startup no longer creates or alters schema. It only creates a connection
  pool and starts serving.
- `alembic upgrade head` is the only supported production schema operation.
- A PostgreSQL advisory lock (`0x53434F4C`, "SCOL") serialises concurrent migration
  attempts from a Render pre-deploy command, a one-off job and a Render Shell run.
- The pgvector `knowledge_chunks.embedding_vec` column and its HNSW index remain
  excluded from autogenerate, because they are created in raw SQL and are not modelled.
- SQLite is still supported for development and tests. `render_as_batch` is enabled for
  SQLite only; PostgreSQL receives normal `ALTER` statements.
- The production guard raises a clear error if `ENVIRONMENT=production` is combined with
  a `sqlite://` URL.

Verified locally on Python 3.13.1:

```text
alembic upgrade head        -> 3228d79ec167 (head)
alembic check               -> No new upgrade operations detected.
alembic downgrade base      -> clean
alembic upgrade head        -> 3228d79ec167 (head)
```

---

## 9. Redis and Celery behaviour

- Upstash TCP endpoints require TLS. `redis://` Upstash URLs are upgraded to
  `rediss://`; certificate verification stays on (`ssl_cert_reqs=required`).
- Upstash REST URLs (`https://`) are rejected with a clear error instead of failing deep
  inside the client.
- The cache client pings once at startup. If Redis is unreachable in development, it
  falls back to process memory and logs the exception class only. In production the
  readiness endpoint reports the failure instead of pretending shared state is healthy.
- Celery results are disabled unless `CELERY_RESULT_BACKEND` is set. This prevents a
  missing Upstash logical database from breaking task execution.
- `broker_connection_retry_on_startup=True` lets the worker survive a broker that is not
  ready yet.
- Use `--pool=threads` for the worker. The jobs are synchronous and database-bound;
  threads avoid forking SQLAlchemy connections and keep memory predictable on a small
  Render instance.
- Celery beat must run as a single instance. Two beat processes would enqueue every
  scheduled job twice.
- In production the in-process scheduler is disabled by default. If Celery is not
  configured, readiness fails and the web process logs a warning rather than silently
  running a scheduler that does not scale.

---

## 10. Health and observability

| Endpoint | Purpose | Dependencies | Render use |
| --- | --- | --- | --- |
| `/api/health` | Backwards-compatible liveness | None | Optional |
| `/api/health/live` | Liveness | None | **Health check path** |
| `/api/health/ready` | Readiness | Database + required Redis/broker | Monitoring / smoke test |

Use `/api/health/live` for Render's health check. It must not depend on Supabase or
Upstash, otherwise a transient dependency outage causes Render to restart a healthy web
process in a loop. Use `/api/health/ready` after deploy and from external monitoring; it
returns HTTP 503 when a required dependency is down.

Startup logs contain only redacted summaries:

```text
database configured driver=postgresql+psycopg host=….pooler.supabase.com port=5432 sslmode=require pooler=True
cache configured scheme=rediss host=belo…stash.io port=6379 tls=True
```

They never contain passwords, full hosts or project references.

---

## 11. Verification matrix

| Check | Status | Evidence |
| --- | --- | --- |
| Dependencies install on Python 3.13.1 | PASS | Clean `uv` venv from `requirements-dev.txt` |
| Full backend test suite | PASS | 273 passed; 1 pre-existing LSA-quality failure unrelated to dependencies |
| New connectivity tests | PASS | 21 passed |
| Database URL normalisation (`postgres` → `postgresql+psycopg`) | PASS | Unit tests |
| Supabase SSL default (`sslmode=require`) | PASS | Unit tests |
| Production refuses SQLite | PASS | Unit test |
| Password/project ref never logged | PASS | Unit tests inspect serialised output |
| Redis Upstash `redis://` → `rediss://` | PASS | Unit test |
| Redis REST URL rejected | PASS | Unit test |
| Redis TLS certificate verification on | PASS | Unit test |
| Celery app builds and registers jobs | PASS | Unit test |
| Celery results disabled without result backend | PASS | Unit test |
| Production in-process scheduler disabled | PASS | Unit test |
| Lifespan does not call `create_all` | PASS | Unit test |
| Liveness returns 200 without dependencies | PASS | Test client |
| Readiness returns 503 when database fails | PASS | Test client + monkeypatch |
| Alembic upgrade head on SQLite | PASS | Temp SQLite database |
| Alembic downgrade base and re-upgrade on SQLite | PASS | Temp SQLite database |
| `alembic check` clean | PASS | Temp SQLite database |
| Alembic single head | PASS | `3228d79ec167` |
| Local DNS/TCP diagnostic | PASS | Direct host resolves IPv6 only; Upstash TCP reachable |
| Live Supabase from Render's network | BLOCKED | Requires Render Shell/one-off job; cannot be run from this environment |
| Live Upstash from Render's network | BLOCKED | Requires Render Shell/one-off job |
| Supabase Session Pooler authentication | BLOCKED | Requires the owner's pooler host, region and password |
| `alembic upgrade head` against production Supabase | BLOCKED | Requires live production credentials |
| Render web/worker/beat deployment | BLOCKED | No Render account access in this environment |
| Postgres-specific migration path | NOT APPLICABLE locally | SQLite covers the portable path; PostgreSQL SQL is unchanged |

### 11.1 Run these from Render

After setting the environment variables, run this from a Render Shell or a one-off job
using the same environment group as the web service:

```bash
cd backend
python scripts/check_connectivity.py --json
```

Expected:

```json
{
  "ready": true,
  "database": {"dns": {"families": ["IPv4"]}, "tcp": {"ok": true}, "check": {"ok": true}},
  "cache": {"summary": {"scheme": "rediss"}, "tcp": {"ok": true}, "check": {"ok": true}},
  "celery_broker": {"summary": {"scheme": "rediss"}, "tcp": {"ok": true}, "check": {"ok": true}}
}
```

If the database still reports `is_supabase_direct: true`, the Render variable was not
updated. If it reports `is_supabase_pooler: true` but `pooler_username_ok: false`, the
username is missing `.<project-ref>`.

Then:

```bash
cd backend
alembic upgrade head
alembic current
curl -fsS "$RENDER_EXTERNAL_URL/api/health/live"
curl -fsS "$RENDER_EXTERNAL_URL/api/health/ready"
```

The readiness response must contain `"ready": true`. A 503 is a real dependency failure;
do not route production traffic until it is fixed.

---

## 12. Render service configuration and deploy runbook

### 12.1 Service commands

Root directory for all backend services: `backend`.

| Service | Build command | Start command |
| --- | --- | --- |
| Web | `pip install -r requirements.txt` | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Worker | `pip install -r requirements.txt` | `celery -A app.services.tasks:celery_app worker --pool=threads --concurrency=2 --loglevel=info` |
| Beat | `pip install -r requirements.txt` | `celery -A app.services.tasks:celery_app beat --loglevel=info` |
| Migration | `pip install -r requirements.txt` | `alembic upgrade head` |

Render reads `.python-version` from the service root; the repository pins `3.13.1`.

Do **not** put `alembic upgrade head` in the web start command. Use a pre-deploy command
or a one-off job so a migration failure is visible and does not leave a half-started web
process. If a pre-deploy command is used, the advisory lock makes concurrent attempts
safe.

### 12.2 Deploy sequence

1. **Supabase**: open Dashboard → Connect → Session Pooler, copy the connection string,
   and confirm the username is `postgres.<project-ref>`. Keep `sslmode=require`.
2. **Upstash**: copy the TLS TCP URL (`rediss://`). Confirm the password is
   percent-encoded if it contains reserved characters.
3. **Render environment**: update `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL` and
   `CELERY_RESULT_BACKEND` on web, worker and beat. Remove quotes. Set
   `ENVIRONMENT=production` and `SCHEDULER_ENABLED=false`.
4. **Migration job**: run `alembic upgrade head` once against the Session Pooler.
5. **Deploy web**: build, deploy, and confirm `/api/health/live` returns 200.
6. **Deploy worker and beat**: one worker service and exactly one beat service.
7. **Smoke test**: run `scripts/check_connectivity.py` and confirm `/api/health/ready`
   returns `"ready": true`.
8. **Check logs**: confirm the redacted database summary says `pooler=True` and that no
   IPv6 address or driver password appears.

### 12.3 Rollback

No schema revision was added, so rolling back the code does not require a database
downgrade.

1. Re-deploy the previous image/commit.
2. Restore the previous Render environment variables only if they were also changed.
3. If a migration was applied manually and must be undone, use
   `alembic downgrade -1` deliberately. Do not automate this in a deploy hook.
4. If the previous deployment used the Supabase direct host, it will hit the same IPv6
   failure. Prefer rolling forward with the pooler URL.

The SQLite-only branches in the existing faculty migration are inert on PostgreSQL; a
rollback to the previous code does not change the production schema.

---

## 13. Security and remaining owner actions

Security properties:

- No password, project reference or full host is logged; summaries are redacted.
- SQLAlchemy `hide_parameters=True` prevents bound values from appearing in exceptions.
- Supabase TLS is required by default and is never downgraded.
- Upstash TLS certificate verification is required.
- Production refuses SQLite, so a misconfigured deploy cannot silently write to a local
  file.
- Readiness distinguishes "configured but unreachable" from "not configured".

Owner actions still required:

1. Update Render environment variables to the Session Pooler and Upstash TLS URLs.
2. Remove the stray quote from the Render `CELERY_BROKER_URL` value.
3. Run the migration job and the Render-side connectivity check.
4. Rotate any credential that was copied into a shared `.env` or chat.
5. If Supabase's pool is small, lower `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` on the worker and
   keep worker concurrency at 1–2. The defaults are sized for one web instance, one
   worker and a migration job.
6. Keep exactly one Celery beat instance. Scale workers, not beat.

Once §11.1 passes from Render, the original IPv6 startup failure is permanently closed.
