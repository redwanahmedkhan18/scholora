# 08 · Deployment, Security & Roadmap

---

## 1. Production topology

```
                    Cloudflare (DNS, TLS, WAF, CDN)
                               │
                    ┌──────────▼──────────┐
                    │  Nginx / Caddy      │  TLS termination, security headers,
                    │  reverse proxy      │  rate limiting, /api → backend
                    └─────┬─────────┬─────┘
                          │         │
            ┌─────────────▼──┐   ┌──▼──────────────┐
            │ Next.js (Node) │   │ FastAPI         │
            │ :3000          │   │ uvicorn/gunicorn│
            │                │   │ :8000 (×N)      │
            └────────────────┘   └──┬───────┬──────┘
                                    │       │
                      ┌─────────────▼─┐  ┌──▼────────┐  ┌──────────────┐
                      │ PostgreSQL 16 │  │ Redis     │  │ Celery       │
                      │ + pgvector    │  │ cache,    │  │ worker+beat  │
                      └───────────────┘  │ rate lim. │  └──────────────┘
                                         └───────────┘
                      ┌──────────────────────────────┐
                      │ Object storage (private)      │  student documents,
                      │ short-lived signed URLs       │  media
                      └──────────────────────────────┘
```

Both apps containerise cleanly. A single VPS with Docker Compose is enough to start; the backend is
stateless behind the database, so it scales horizontally when needed.

### Migration from the demo build

This build ships with SQLite (`backend/data/scholara.db`) so it runs with no services. For
production: point `DATABASE_URL` at PostgreSQL, enable the `vector` extension, run
`python -m alembic upgrade head`, and either run `python -m app.seed` for a fresh install or import
real data.

The initial Alembic revision (`20261003_dc186cc99406_initial_schema`) creates all 42 tables and has
been verified to `upgrade head` and `downgrade base` cleanly. `alembic/env.py` imports `app.models`,
reads the URL from settings at runtime, and sets `compare_type=True` plus `render_as_batch=True` so
SQLite can alter columns. When autogenerating a new revision, point `DATABASE_URL` at a throwaway
file — autogenerating against an already-migrated database yields an empty revision.

---

## 2. Environment

### Backend (`backend/.env`)

```ini
APP_ENV=production
SECRET_KEY=                      # 64+ random chars. Rotating it invalidates all sessions
DATABASE_URL=postgresql+psycopg://user:pass@host:5432/scholara
REDIS_URL=redis://host:6379/0
ALLOWED_ORIGINS=https://yourdomain,https://www.yourdomain
ACCESS_TOKEN_EXPIRE_MINUTES=480

# Storage
S3_ENDPOINT=
S3_BUCKET=
S3_ACCESS_KEY=
S3_SECRET_KEY=
SIGNED_URL_TTL_SECONDS=300

# Integrations
META_CAPI_TOKEN=                 # Meta Conversions API
META_PIXEL_ID=
WHATSAPP_API_TOKEN=              # WhatsApp Business API, when enabled
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_WEBHOOK_SECRET=
SMTP_HOST=
SMTP_USER=
SMTP_PASSWORD=
# ---- Assistant LLM: Grok, Mistral or Gemini (no OpenAI dependency) ----
LLM_ENABLED=true
LLM_PROVIDER_ORDER=grok,mistral,gemini   # tried in order; first to answer wins
XAI_API_KEY=                     # Grok    - console.x.ai
MISTRAL_API_KEY=                 # Mistral - console.mistral.ai
GEMINI_API_KEY=                  # Gemini  - aistudio.google.com/apikey
# Optional overrides: XAI_MODEL, MISTRAL_MODEL, GEMINI_MODEL, *_BASE_URL,
# LLM_TIMEOUT_SECONDS, LLM_MAX_TOKENS, LLM_TEMPERATURE.
# Set none of the keys and the assistant runs its deterministic fallback.
```

### Frontend (`frontend/.env.local`)

```ini
INTERNAL_API_URL=http://backend:8000       # server-side only, never exposed
NEXT_PUBLIC_SITE_URL=https://yourdomain
```

Note the asymmetry on purpose: the browser never learns the backend's address. Client code calls
relative `/api/...`, which `next.config.ts` rewrites.

---

## 3. Security controls

| Control | Implementation | Status |
| --- | --- | --- |
| HTTPS everywhere | TLS at the edge, HSTS | configure at deploy |
| Security headers | `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` in `next.config.ts` | ✅ |
| RBAC | `ROLE_HIERARCHY` + `require_role()` on every admin route | ✅ |
| Password storage | pbkdf2-hmac-sha256 with per-user salt | ✅ |
| Session tokens | JWT, expiry configurable | ✅ |
| **Staff MFA** | TOTP for every staff account | `mfa_enabled` column present; **enforcement to be enabled before launch** |
| Private documents | Object storage, no public bucket, short-lived signed URLs | design in place |
| Audit logging | Every staff mutation writes `audit_logs` | ✅ |
| Webhook validation | Signature check on inbound webhooks | required for WhatsApp/Meta |
| Rate limiting | Redis-backed; stricter on `/leads`, `/appointments`, `/assistant/ask`, `/admin/auth/login` | configure at deploy |
| Field-level exposure control | Hand-written serializers as an allow-list | ✅ |
| PII hashing | SHA-256 before any third-party conversion event | ✅ |
| Consent | Explicit contact consent on every form; marketing consent separate | ✅ |
| `noindex` on private surfaces | `/admin`, `/brochure/*`, `/compare`, `/shortlist`, `/search` | ✅ |
| Input validation | Pydantic request models per route | ✅ |
| SQL injection | SQLAlchemy parameterised queries throughout | ✅ |
| Backups | Nightly database dump + weekly restore test | operational |

### Before the platform is exposed to the internet

1. Rotate every seeded credential (`Admin@12345` and friends are demo values).
2. Enable and enforce MFA on all staff accounts.
3. Generate a fresh `SECRET_KEY`.
4. Set `ALLOWED_ORIGINS` to the real domain — the dev build permits `https://*.e2b.app` for the preview sandbox.
5. Turn on rate limiting and a WAF.
6. Confirm the documents bucket is private and signed URLs expire in minutes.
7. Schedule backups and test a restore.

---

## 4. Build and run

```bash
# Backend
pip install -r backend/requirements.txt
python -m alembic upgrade head
python -m pytest
gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000

# Frontend
cd frontend && npm ci && npm run build
python3 ../scripts/check-prerender.py .   # refuse a build that baked error pages
npm run start

# Background work
celery -A app.worker worker -l info
celery -A app.worker beat   -l info
```

### Why the build must never be rate-limited

`next build` renders every university, programme, scholarship and story page by calling our
own API - several hundred requests in under a minute, comfortably past the 300/min public
limit. A refused request there is uniquely dangerous: the page still builds, it just builds
as "not found", and that frozen 404 is served to every visitor until the next revalidation.
A green build log hides it completely.

Two mechanisms close this off, and both must be in place before a production build:

1. **`INTERNAL_API_TOKEN`** - identical in the backend environment and `frontend/.env.local`.
   Requests carrying it in `X-Internal-Token` skip rate limiting entirely. The browser never
   sends it: it reaches the API through the Next.js proxy, which does not forward the header.
   Generate a real value with `openssl rand -hex 32`; the committed default is for dev only.
2. **`apiOrNull` fails loudly.** Only a clean `404` means "this record does not exist". A 429,
   a 5xx or a dropped connection raises, so the build stops instead of publishing a wrong page.

`scripts/check-prerender.py` is the backstop. It reads the status Next recorded for every
prerendered page and exits non-zero if any page that is not `_not-found` or `_global-error`
was baked as an error. Run it in CI straight after `npm run build`.

Scheduled jobs: nightly verification sweep (flip records past `review_due_at` to `NEEDS_REVIEW`),
deadline reminders (intakes and scholarships inside 30/14/7 days), a weekly data-quality digest to
the data manager, campaign metric sync, and sitemap warming.

### Health and monitoring

`GET /api/v1/health` for liveness. Track: API p95 latency, 5xx rate, lead submission success rate,
verification backlog size, Celery queue depth, and Core Web Vitals on the public site.

---

## 5. What is built, and what is designed but not yet wired

**Built and working**

Full education data model with provenance (42 tables under Alembic) · eligibility, costing,
matching, rotation, brochure, retrieval and assistant services · 86 API paths / 92 operations · the
complete public site (destinations, universities, programmes, scholarships, PhD and faculty,
services, success stories, events, blog, FAQs, contact, appointments, assessment, compare,
shortlist, search, brochure) · the 30-second rotation engine · the student portal at `/my/{token}`
with applications, document upload and review state, shortlist sync and notifications · admin
control centre over 37 resources plus dashboard, counselor workbench, applications board, document
queue, notification inbox, audit log, operations console, security centre, marketing hub and
settings editor · staff MFA (TOTP, recovery codes, login challenge, lockout) · private document
storage behind short-lived signed URLs · rate limiting · webhook signature validation · audit
logging · SEO (metadata, canonicals, JSON-LD, sitemap, robots) · first-party analytics with
GA4/Pixel bridging · UTM attribution · WhatsApp click-to-chat · scheduled jobs · seeded demo
dataset · 88 automated tests.

**Designed, pending integration**

| Item | What is needed |
| --- | --- |
| WhatsApp Business API | Tables, service and signature-validated webhook exist; needs real credentials and template approval |
| Meta Conversions API | Endpoint exists and is called on lead events; needs a real `META_CAPI_TOKEN` |
| LLM assistant | Tool-calling and grounded answering work end to end (`mode: llm`). Set **any one** of `XAI_API_KEY` (Grok), `MISTRAL_API_KEY` or `GEMINI_API_KEY` to enable; with none set it falls back to `mode: template`. See §9 |
| pgvector retrieval | Retrieval is BM25 over a 117-chunk index today; swap in embeddings on the PostgreSQL move |
| Celery workers | Jobs run in-process on APScheduler; move to Celery + Redis when traffic justifies it |
| Object storage | Documents are on local disk behind signed URLs; point `STORAGE_BACKEND` at S3/R2 in production |

---

## 6. Assistant LLM providers

The assistant is **not tied to OpenAI**. It can be driven by any of three vendors, all of which
expose the same OpenAI-style `POST /chat/completions` contract, so one small `httpx` client in
`app/core/llm.py` covers all three and the project carries **no vendor SDK**.

| Provider | Base URL | Key | Default model |
| --- | --- | --- | --- |
| **Grok** (xAI) | `https://api.x.ai/v1` | `XAI_API_KEY` | `grok-4-fast-non-reasoning` |
| **Mistral AI** | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` | `mistral-large-latest` |
| **Gemini** (Google AI Studio) | `https://generativelanguage.googleapis.com/v1beta/openai` | `GEMINI_API_KEY` | `gemini-2.5-flash` |

Gemini's URL must end in `/openai` — that is Google's compatibility endpoint. The native
`generateContent` endpoint speaks a different shape and will not work here.

### How selection works

`LLM_PROVIDER_ORDER` (default `grok,mistral,gemini`) is a preference list. On each question the
providers are tried **in order**:

* no key → skipped silently;
* error, timeout or non-2xx → recorded, move to the next;
* first success → used, and nothing further is called.

So exactly one completion is ever paid for. If every provider is unconfigured or failing,
`complete()` returns `None` and the assistant answers from its deterministic templates instead —
the feature degrades, it never disappears. `LLM_ENABLED=false` forces template mode outright.

### Changing provider or model

No code change is needed. Set the key, optionally override `*_MODEL`, restart. Model names move
quickly; if a call fails with "model not found", set the `*_MODEL` variable to a current name from
the provider's catalogue. `*_BASE_URL` is also overridable, which is how you point at a proxy,
an Azure/Vertex-style gateway or a self-hosted OpenAI-compatible server.

### Verifying a key

**Admin → Operations → AI providers** lists all three with their priority, whether a key is
present, the model in use and the last error. Each has a **Test** button that performs a live
round trip and reports the latency, or the exact upstream error. Equivalent API:

```bash
GET  /api/v1/admin/assistant/providers          # status (data manager+)
POST /api/v1/admin/assistant/providers/{name}/test   # live ping (admin only)
```

Neither endpoint ever returns key material, and any key that appears in an upstream error string
is redacted before it is stored or displayed.

### Guardrails are provider-independent

Whichever model answers, the text passes through the same scrub in
`services/assistant_engine.py` before a student sees it. Any **sentence** that states an admission
chance, a guarantee, a visa prediction or unapproved eligibility phrasing is removed whole — not
reworded, because rewording "the visa success rate is 98%" word-by-word leaves the number behind.
Unsourced email addresses are replaced. If generation is quarantined down to nothing, the
deterministic answer is served and the event is logged as `llm_output_rejected`.

This is covered by 56 tests in `tests/test_llm.py`, including a hostile-model test that wires the
engine to a provider which deliberately emits probabilities, guarantees and fake emails and asserts
none of it reaches the student.

---

## 7. Testing

```bash
cd backend && python -m pytest        # 88 tests
./scripts/smoke-test.sh               # 38 live routes
./scripts/verify-new.sh               # 57 live behavioural assertions
```

`tests/conftest.py` builds a throwaway SQLite database in a temp directory and seeds it, so the
suite never touches `data/scholara.db`. Four files cover the API surface and rotation contract
(`test_api.py`), the trust policy — provenance, conflicting sources, eligibility vocabulary and a
regex battery proving no admission-probability language escapes (`test_data_integrity.py`), the
security controls — RBAC, MFA, signed URLs, uploads, webhooks, headers (`test_security.py`), and
the limiter and cache (`test_ratelimit.py`).

Run the suite in CI before deploy. The data-integrity file is the one that matters most: it is the
executable form of [document 05](05-data-integrity-and-trust-policy.md), and a failure there means
the platform is making claims it is not allowed to make.

---

## 8. Roadmap

### Phase 1 — Launch (weeks 1–4)
Move to PostgreSQL (`alembic upgrade head`), import real university data for the three priority
destinations with full provenance, rotate credentials and enforce MFA, set the real tracking IDs,
verify the domain in Search Console and Meta, submit the sitemap, publish eight to ten articles from
the top-searches list, and run the first attributed campaign end to end.

### Phase 2 — Operations depth (months 2–3)
WhatsApp Business API with templated intake-deadline reminders · document upload with signed URLs ·
application tracking screens for counselors · the student portal · automated verification sweeps and
the weekly data-quality digest · counselor performance reporting.

### Phase 3 — Intelligence (months 4–6)
LLM assistant with tool calling and pgvector retrieval, bound by document 05 · richer PhD
supervisor matching with publication-level evidence · a scholarship deadline watcher · cohort
analytics (which profiles actually convert, by destination and level) · an admin data-quality
scorecard per destination.

### Phase 4 — Scale (months 6–12)
More destinations and institutions · multilingual content (Bangla) · partner-facing portal for
university representatives · a public API for partner institutions · automated change-detection on
official university pages feeding straight into the verification queue.

---

## 9. Operating cost sketch (monthly, USD)

| Item | Estimate |
| --- | --- |
| VPS (4 vCPU / 8 GB) running both apps + Redis | 40–60 |
| Managed PostgreSQL with backups | 25–50 |
| Object storage + egress | 5–15 |
| Cloudflare (Pro, optional) | 0–20 |
| Email (transactional) | 0–15 |
| WhatsApp Business API conversations | usage-based |
| LLM API (assistant) | 20–100 depending on volume |
| **Total** | **≈ 90–260 before ad spend** |

The architecture is deliberately modest: one database, one cache, two app processes. Nothing here
needs Kubernetes, and nothing here charges per seat.
