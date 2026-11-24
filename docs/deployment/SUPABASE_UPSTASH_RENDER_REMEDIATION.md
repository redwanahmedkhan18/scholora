# Supabase + Upstash + Render remediation

Status: **code verified against the real Supabase and Upstash services and a throwaway
PostgreSQL 17 instance; live Render verification is blocked because the Render service has
no running instance.**

Two independent production blockers are now confirmed by direct measurement rather than
inference:

1. The Supabase **direct** host is IPv6-only, so Render cannot reach it
   (`Network is unreachable` -> `psycopg.OperationalError: connection is bad`). The
   IPv4-reachable **Session Pooler** answers on `aws-1-ap-south-1` and accepts the
   project's existing password (`SELECT 1` succeeds).
2. The production database has **zero tables in `public`** and no `alembic_version`.
   Even with connectivity fixed, every endpoint would fail with `UndefinedTable` until
   `alembic upgrade head` runs. This is not a connectivity problem and would not have
   been fixed by a URL change.

See `docs/deployment/ENVIRONMENT_MATRIX.md` for the per-service variable matrix and the
owner runbook. The findings from this session are in §14.

This document records the permanent fix for the production startup failure:

```
psycopg.OperationalError: connection to server at
"2600:1f18:...:5432" failed: Network is unreachable
```

The failure was not a password problem and not a SQLAlchemy problem. It was the
Supabase **direct** database host resolving to IPv6 on Render.

A second deployment failure, `ImportError: cannot import name 'connectivity' from
'app.core'`, was caused by a `.gitignore` rule that silently excluded the new module from
the commit. That is documented in §0.

---

## 0. Render import failure audit

### 0.1 Symptom

The latest Render build succeeded, but the process crashed before serving:

```text
File "/opt/render/project/src/backend/app/main.py", line 9, in <module>
    from app.core import connectivity
ImportError: cannot import name 'connectivity' from 'app.core'
```

### 0.2 Findings

| Question | Finding |
| --- | --- |
| Was `connectivity.py` never created? | No. It exists at `backend/app/core/connectivity.py` (13,020 bytes, non-empty, valid Python). |
| Was it created in the wrong directory? | No. The path matches the import `app.core.connectivity`. |
| Was it omitted from Git? | **Yes.** `git ls-files backend/app/core` did not list it. |
| Did the filename or import path differ? | No. The filename and import are consistent. |
| Is a circular import responsible? | No. The working tree imports `app.main` successfully. |
| Does the deployed commit contain it? | **No.** `git cat-file -e 9701eb7:backend/app/core/connectivity.py` fails. |

The cause was `.gitignore` line 564:

```gitignore
core
```

A bare `core` pattern matches **any file or directory named `core` at any depth**. It
therefore ignored the new file inside `backend/app/core/`. The other files in that
directory are tracked because they were added before the pattern existed; a new file is
subject to it. `git check-ignore -v backend/app/core/connectivity.py` confirmed:

```text
.gitignore:564:core    backend/app/core/connectivity.py
```

Commit `9701eb7` (`fixing dependency errors`) contains every other remediation file —
`main.py`, `database.py`, `cache.py`, `config.py`, `tasks.py`, `alembic/env.py`, the
migration, tests, the diagnostic script and this document — but not
`connectivity.py`. Branch `main` and `origin/main` both pointed at `9701eb7`, so Render
deployed exactly that incomplete commit.

### 0.3 Reproduction

The commit was exported to a temporary directory and imported with a clean environment:

```bash
git archive 9701eb7 | tar -x -C "$TMP"
cd "$TMP/backend"
ENVIRONMENT=test DATABASE_URL="sqlite:///$TMP/import.db" \
  REDIS_URL="" CELERY_BROKER_URL="" CELERY_RESULT_BACKEND="" \
  python -c "import app.main"
```

Result: the exact Render error, `ImportError: cannot import name 'connectivity' from
'app.core'`. The same command against the working tree succeeds.

### 0.4 Fix applied

`.gitignore` no longer contains a bare `core` pattern. It now ignores only core-dump
shapes and a root-level `core` path:

```gitignore
*.core
core.[0-9]*
/core
```

`git check-ignore -v backend/app/core/connectivity.py` now reports that the file is not
ignored. The file itself was not modified or recreated; it was already correct.

**The file is still untracked because no commit was made.** It must be added and pushed
before Render can see it:

```bash
git add .gitignore backend/app/core/connectivity.py
git commit -m "fix: track connectivity module required by app.main"
git push origin main
```

No commit or push was performed here: the task requires explicit approval for that.

### 0.5 Import and startup audit

| Check | Result |
| --- | --- |
| `import app.main` from working tree | PASS |
| `import app.services.tasks` / Celery app build | PASS (`scholara`, scheduler mode `celery`) |
| Production-like lifespan + `/api/health/live` with no live DB/Redis | PASS (HTTP 200) |
| Production-like import/lifespan with `backend/.env` moved aside | PASS; no local-only `.env` dependency |
| AST audit of `app/main.py` module imports | PASS; every module path exists in the working tree |
| `Base.metadata.create_all()` in FastAPI startup | PASS; none (only tests and guarded seed call it) |
| `pip check` (Python 3.13.1 venv) | PASS; all 73 installed packages compatible |
| Backend test suite | 273 passed, 1 pre-existing LSA-quality failure |
| Ruff | No project configuration; default run reports style-only findings, no undefined names |
| Mypy | Not configured; no project config and the global entry point is unusable |

The import audit covered every module path referenced by `app/main.py`:
`app.core`, `app.core.config`, `app.core.connectivity`, `app.core.database`,
`app.core.ratelimit`, `app.api.v1.routers.*`, `app.models` and the dynamic
`app.services.tasks` import in the lifespan. Imported symbols were checked by actually
importing `app.main`, not only by filename.

### 0.6 Remaining blockers

1. `connectivity.py` is unignored but still untracked; it must be committed and pushed.
2. Render must redeploy the commit that contains it.
3. Live Supabase and Upstash verification remains blocked until it can be run from
   Render with the production environment variables.

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

The remaining work is to commit and push `connectivity.py` (see §0.4), set the Render
environment variables to the Supabase Session Pooler and Upstash TLS URLs, then deploy
and run the checks in §11.

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
| `.gitignore` | Removed the bare `core` rule that silently ignored `backend/app/core/connectivity.py`; core dumps are still covered by `*.core`, `core.[0-9]*` and `/core`. |

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
| `connectivity.py` present and non-empty in working tree | PASS | 13,020 bytes; import succeeds |
| `connectivity.py` tracked by Git | FAIL | Untracked; was hidden by the bare `core` ignore rule |
| Latest commit contains `connectivity.py` | FAIL | Missing from `9701eb7` |
| Import `app.main` from latest commit | FAIL | Reproduced the exact Render `ImportError` |
| Import `app.main` from working tree | PASS | `app.main import OK` |
| Celery import from working tree | PASS | App name `scholara`; scheduler mode `celery` |
| Production-like lifespan/liveness without live DB/Redis | PASS | HTTP 200; no startup schema creation |
| Production-like import/lifespan with `.env` absent | PASS | Explicit environment variables only |
| `pip check` | PASS | 73 packages, all compatible |
| Ruff / mypy | NOT CONFIGURED | No project config; default Ruff run has style-only findings, mypy unavailable |
| Git push of the fix | BLOCKED | Commit/push requires explicit owner approval |
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

1. Commit and push `.gitignore` and `backend/app/core/connectivity.py`; redeploy Render.
2. Update Render environment variables to the Session Pooler and Upstash TLS URLs.
3. Remove the stray quote from the Render `CELERY_BROKER_URL` value.
4. Run the migration job and the Render-side connectivity check.
5. Rotate any credential that was copied into a shared `.env` or chat.
6. If Supabase's pool is small, lower `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` on the worker and
   keep worker concurrency at 1–2. The defaults are sized for one web instance, one
   worker and a migration job.
7. Keep exactly one Celery beat instance. Scale workers, not beat.

Once §11.1 passes from Render, the original IPv6 startup failure is permanently closed.

---

## 14. Second remediation pass: what was actually measured

Everything below was verified from this workstation against the real Supabase project,
the real Upstash databases and a throwaway PostgreSQL 17 instance. No secret value is
reproduced here.

### 14.1 Supabase connectivity, resolved by measurement

The direct host resolves to **AAAA only** (`2406:da1a:1cb4:bf00:...`), which is
`ap-south-1`. Both pooler clusters for that region resolve to IPv4, so the cluster was
determined by connecting, not by guessing:

| Candidate | Result |
| --- | --- |
| `aws-0-ap-south-1.pooler.supabase.com:5432` | `FATAL: (ENOTFOUND) tenant/user postgres.<ref>` - project is not on this cluster |
| `aws-1-ap-south-1.pooler.supabase.com:5432` | **connect + `SELECT 1` OK**, `PostgreSQL 17.12` |

The project's configured password authenticates over the Session Pooler unchanged, so no
credential rotation is required for this fix. `current_user` is `postgres` and the
Session Pooler user is `postgres.<project-ref>`; `sslmode=require` is accepted.

The application's own code path was then exercised end to end with
`ENVIRONMENT=production` and the pooler URL: `normalize_database_url` produced
`postgresql+psycopg://`, `summarize_database_url` reported `is_supabase_pooler: true` and
`pooler_username_ok: true`, and `check_database()` returned `ok: true`.

### 14.2 The production schema is empty

`information_schema.tables` for `public` returns **0 rows**; `alembic_version` does not
exist. The `vector` extension is already enabled, which is what migration
`a1b2c3d4e5f6` needs. Until `alembic upgrade head` runs, `/api/v1/sitemap-data` cannot
succeed for any connection string.

The migration chain was therefore verified against a **real PostgreSQL 17** instance
(`pgvector/pgvector:pg17`, throwaway container, not production):

```text
alembic upgrade head      -> 3228d79ec167 (head), 48 tables in public
                           61 jsonb columns, 0 json columns, vector extension created
alembic check             -> No new upgrade operations detected
alembic downgrade base    -> clean
alembic upgrade head      -> 3228d79ec167 (head), idempotent
```

With the app served against that PostgreSQL instance and **no rows**:
`/api/health/live` 200, `/api/health/ready` 200 `ready: true`, `/api/v1/sitemap-data`
200 with every list empty (the legitimate empty state), `/api/v1/vocabularies` 200, and
`/api/v1/settings` 404 `Site settings not initialised` until seeding runs.

### 14.3 Upstash and Celery

Both configured Upstash endpoints answer over TLS (`rediss`, certificate verification
required): `PING`, `SET`/`GET`, and Redis 8.4.0. The application's `Cache` facade reported
`backend: redis`, `get_json`, `incr_with_ttl` and the `SCAN`-based
`invalidate_prefix` all worked. `build_celery_app()` registered the six `scholara.*`
tasks and completed a broker handshake.

The stored `CELERY_BROKER_URL` still carries a trailing `"`; the URL cleaner strips it,
but the Render value should be corrected.

### 14.4 Render has no running service

`https://scholora-cfch.onrender.com` answers every path with a plain-text `404` and the
header `x-render-routing: no-server`. This is not the FastAPI app (which answers
`{"detail":"Not Found"}`) and not a crashed process: there is no instance behind the
hostname. Fixing the environment variables is necessary but not sufficient - the service
must be deployed again.

### 14.5 Defects found and fixed in this pass

| File | Defect | Fix |
| --- | --- | --- |
| `frontend/src/lib/api-base.ts` | `INTERNAL_API_URL ?? "http://127.0.0.1:8000"` silently pointed a production build at the build container. Also `NEXT_PUBLIC_SITE_URL` fell back to `localhost` in canonical/OG/sitemap output. | New resolver: the variable is required in production, must be an absolute http(s) URL, must be `https` unless loopback, and loopback is refused unless `ALLOW_LOCAL_API=true`. Site URL falls back to the Vercel deployment domain instead of localhost. |
| `frontend/next.config.ts` | The `/api/:path*` rewrite used the same unvalidated fallback. | Imports the same resolver, so the rewrite and the server-side client cannot disagree. |
| `frontend/src/lib/api.ts` | A Render cold start failed the whole Vercel build. | Two bounded retries (1.5 s, 4 s) for transport errors and 502/503/504 only. Application errors are never retried, so a real outage still fails loudly. |
| `backend/app/api/v1/routers/public.py` | The new 503 handler called `log.warning` but the module had **no logger** - a database outage would have raised `NameError` and returned 500 instead of 503. | Added the module logger. |
| `backend/app/core/config.py` | `CORS_ORIGINS` was used verbatim, so a bare hostname (as configured in production: `CORS_ORIGINS=scholara.onrender.com`) can never match a browser `Origin` header. | Entries are normalised to `scheme://host`; `PUBLIC_SITE_URL` is always allowed; the effective list is logged at startup. |
| `backend/app/main.py` | `allow_origins=["*"]` was combined with `allow_credentials=True`, so any website could make credentialed requests; a hardcoded `https://.*\.e2b\.app` regex was active in production. | Credentials are disabled whenever every origin is allowed; the regex moved to the `CORS_ORIGIN_REGEX` setting (empty by default); `Retry-After` and the rate-limit headers are exposed. |
| `backend/tests/conftest.py` | The suite inherited the developer's `.env`: the seeded admin was not `admin@scholara.com.bd` (every admin test 401'd) and real Meta/LLM keys made a "no credentials configured" test exercise the wrong branch. | Pins `ADMIN_EMAIL`/`ADMIN_PASSWORD` and blanks every third-party credential, matching the existing REDIS/CELERY isolation. |
| `backend/tests/test_sitemap_and_cors.py` | No coverage for the sitemap failure contract or the CORS policy. | 13 tests: normal, empty and database-failure states, no-leak assertions, draft exclusion, slug ordering, and the CORS rules. |

### 14.6 Verification results

| Check | Result |
| --- | --- |
| Backend suite (Python 3.13.1) | **286 passed, 1 failed** - the pre-existing LSA retrieval-quality failure, unchanged from the previous pass (273 + 13 new tests) |
| `pip check` | No broken requirements found (59 packages) |
| `import app.main`, routers, `build_celery_app` | PASS |
| Alembic on PostgreSQL 17 | PASS - upgrade/downgrade/check/idempotency (§14.2) |
| `/api/health/ready` against real PostgreSQL | PASS - 200, `ready: true` |
| `/api/v1/sitemap-data` against real PostgreSQL, empty schema tables | PASS - 200, empty lists, not a 503 and not a fabricated record |
| `/api/v1/sitemap-data` with the database down | PASS - 503 + `Retry-After`, no driver message, no hostname |
| Supabase Session Pooler from this host | PASS - `SELECT 1`, pooler identity confirmed |
| Upstash cache + broker over TLS | PASS - PING, SET/GET, pipeline, SCAN |
| Celery app + broker handshake | PASS - 6 tasks registered |
| Frontend `tsc --noEmit` | PASS |
| Frontend production build | PASS - 45 routes, `/sitemap.xml` generated with 209 URLs from the API |
| Frontend build with `INTERNAL_API_URL` unset | PASS - build refused, message names the variable |
| Frontend build with a loopback API URL | PASS - build refused, message explains the fix |
| Live Render endpoints | **FAIL** - `x-render-routing: no-server`, no instance deployed |
| Production Supabase schema | **FAIL** - 0 tables, migrations never applied |

### 14.7 Remaining owner actions

1. Redeploy the Render service (it is currently not running at all).
2. Point `DATABASE_URL` at `aws-1-ap-south-1.pooler.supabase.com` with the
   `postgres.<project-ref>` username (§6 in the matrix document).
3. Run `alembic upgrade head`, then `python -m app.seed`.
4. Set `CORS_ORIGINS` to the real Vercel origin, remove the trailing quote from
   `CELERY_BROKER_URL`, and set `ENVIRONMENT=production` where it is missing.
5. Set `INTERNAL_API_URL`, `INTERNAL_API_TOKEN` and `NEXT_PUBLIC_SITE_URL` on Vercel,
   then redeploy.
6. Rerun §11.1 from a Render Shell. Do not consider the deployment production-ready until
   `/api/health/ready` returns `ready: true` **from Render**, not from here.

