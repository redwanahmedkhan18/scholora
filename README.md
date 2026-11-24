# Scholara

**Global Education Intelligence & Application Platform**
for an international education consultancy based in Uttara, Dhaka.

Scholara is a single-organisation platform (not a SaaS, not multi-tenant) that gives Bangladeshi
students a *verifiable* way to research studying abroad — universities, programmes, requirements,
intakes, real annual costs, scholarships and PhD supervisors — and gives the consultancy a CMS, a
CRM pipeline and a marketing/ads control centre behind it.

The product's organising principle: **every student-facing fact carries its source, its source
type, the date it was retrieved, the date it was verified and a confidence state.** The platform
never invents a university fact, never produces a fake "95% admission chance", and never guesses a
professor's email address.

---

## What is in this repository

| Path | What it is |
| --- | --- |
| `backend/` | FastAPI + SQLAlchemy modular monolith. 42 tables under Alembic, 88 API paths, eligibility / costing / matching / rotation / brochure / retrieval / assistant / WhatsApp services, 174 tests. |
| `frontend/` | Next.js 16 (App Router, React 19, TypeScript, Tailwind v4). Public site + admin control centre. 70 source files. |
| `docs/` | The written deliverables — requirements, architecture, data model, API reference, data-integrity policy, SEO/ads playbook, admin guide, deployment & roadmap. |
| `scripts/` | `stack.sh` (full PostgreSQL/Redis/Celery stack), `dev.sh` (zero-infrastructure SQLite run), `smoke-test.sh` (40 routes), `verify-new.sh` (63 live assertions). |

### Documents

| # | Document | Read it for |
| --- | --- | --- |
| 01 | [Product requirements](docs/01-product-requirements.md) | Scope, personas, every module and its acceptance criteria |
| 02 | [System architecture](docs/02-system-architecture.md) | How the pieces fit, request flow, rotation engine, caching |
| 03 | [Data model](docs/03-data-model.md) | All 42 tables, the verification mixin, enums |
| 04 | [API reference](docs/04-api-reference.md) | Every endpoint, parameters, response envelopes |
| 05 | [Data integrity & trust policy](docs/05-data-integrity-and-trust-policy.md) | **The rules that make this platform different.** Confidence states, eligibility verdicts, faculty email policy, AI guardrails |
| 06 | [SEO, marketing & ads playbook](docs/06-seo-marketing-and-ads-playbook.md) | URL architecture, structured data, Facebook page + Meta Pixel/CAPI, GA4, Google Ads, UTM attribution |
| 07 | [Admin operations guide](docs/07-admin-operations-guide.md) | Running the CMS day to day, the 30-second homepage rotation, lead pipeline |
| 08 | [Deployment, security & roadmap](docs/08-deployment-security-and-roadmap.md) | Production stack, env vars, migrations, security controls, testing, phased plan |
| 09 | [Specification compliance audit](docs/09-specification-compliance.md) | **An honest gap analysis.** 40/40 modules, all 7 named stack technologies adopted, the 5 real bugs that auditing exposed (2 PostgreSQL, 3 requirement-level), the 1 accepted deviation, and why the dataset is still demo content |

---

## Running it locally

There are two supported ways to run this, and the application picks its backends from the
environment, so the only difference is which variables are set.

### The full stack (what the specification asks for)

PostgreSQL + pgvector, Redis, Celery, FastAPI and Next.js:

```bash
./scripts/stack.sh up       # start Postgres + Redis, migrate, seed, build the vector index
./scripts/stack.sh serve    # API + Celery worker + beat + website
./scripts/stack.sh status   # one screen: which backends are actually live
./scripts/stack.sh down
```

`status` is the quickest way to confirm what you are really connected to:

```
   database        127.0.0.1:5432/scholara
   is_postgres     True
   cache backend   redis
   celery          configured
   embeddings      local @ 1024 dims
   117/117 chunks carry a pgvector embedding
```

Use `pg_ctlcluster`, not `systemctl`, if you start PostgreSQL by hand — in containers
`policy-rc.d` blocks service starts and the systemctl error is misleading. `stack.sh` already
does this.

### The zero-infrastructure run

`./scripts/dev.sh` runs the same application on SQLite with an in-process cache and scheduler, no
services required. Everything works except the four things that need real infrastructure
(shared-state rate limiting, distributed task queue, pgvector ANN search, cross-process cache) —
each of which degrades to a working in-process equivalent rather than failing.

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # edit secrets before anything real
python -m alembic upgrade head  # build the schema (42 tables)
python -m app.seed              # populate data/scholara.db with the demo dataset
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

API: `http://localhost:8000/api/v1` (91 paths / 94 operations) · OpenAPI:
`http://localhost:8000/api/openapi.json`

#### The AI assistant (optional)

The assistant runs without any API key, answering from deterministic templates built on verified
records. To switch on generated prose, set **any one** of these in `backend/.env` — they are
interchangeable and no code changes:

| Provider | Key | Get one from |
| --- | --- | --- |
| Grok (xAI) | `XAI_API_KEY` | [console.x.ai](https://console.x.ai) |
| Mistral AI | `MISTRAL_API_KEY` | [console.mistral.ai](https://console.mistral.ai) |
| Gemini | `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |

They are tried in `LLM_PROVIDER_ORDER` (default `grok,mistral,gemini`) and the first that answers
wins; if one fails the next takes over. Verify a key at **Admin → Operations → AI providers**.
Full detail in [document 08 §6](docs/08-deployment-security-and-roadmap.md).

#### Database migrations

The schema is owned by Alembic, not by `create_all`. `alembic.ini` reads the URL from your settings
at runtime, so the same commands work against SQLite locally and PostgreSQL in production.

```bash
python -m alembic upgrade head                      # apply everything
python -m alembic downgrade base                    # tear back down
python -m alembic revision --autogenerate -m "msg"  # after editing app/models/*
python -m alembic current                           # where am I
```

When autogenerating, point `DATABASE_URL` at a **throwaway** file first. Running autogenerate
against an already-migrated `data/scholara.db` diffs against the live schema and produces an empty
revision. `env.py` sets `compare_type=True` and `render_as_batch=True` so SQLite handles column
alterations correctly.

### Frontend

```bash
cd frontend
npm install
npm run dev                     # http://localhost:3000
```

`frontend/.env.local` holds `INTERNAL_API_URL=http://127.0.0.1:8000`. Server components call the API
directly on that URL; **browser code always uses relative `/api/...`**, which `next.config.ts`
rewrites to the backend. That keeps the browser from ever needing to know the backend's address.

It also holds `INTERNAL_API_TOKEN`, which must match the backend's. A production build renders
every university, programme and scholarship page by calling the API — far more requests per
minute than the public rate limit allows — and this token exempts that first-party traffic.
Without it the limiter refuses pages mid-build and they are frozen as 404s. Run
`python3 scripts/check-prerender.py frontend` after any build to confirm no page was baked as an
error.

### Tests and verification

```bash
cd backend && python -m pytest          # 174 tests
```

| File | Covers |
| --- | --- |
| `tests/test_api.py` | Public endpoints, homepage rotation contract, section ordering, filters and pagination, 404s, itemised costing and budget gap, brochure versioning, student portal lifecycle, counselor dashboard, applications board, audit log, notification inbox, every admin resource, scheduled jobs, BM25 retrieval |
| `tests/test_data_integrity.py` | Provenance on every important field, conflicting sources retained rather than overwritten, the eligibility vocabulary, a regex battery proving no admission-probability language is ever emitted, faculty email labelling, assistant refusal and escalation, partnership data kept private |
| `tests/test_security.py` | RBAC per role, TOTP and recovery codes, the MFA enrolment flow, signed-URL tampering and expiry, upload rejection, cross-token isolation, webhook signatures, security headers, `no-store` |
| `tests/test_ratelimit.py` | Rule ordering and strictness, method-scoped matching, middleware headers and 429s, per-client budgets, cache roundtrip and expiry, memory fallback |
| `tests/test_llm.py` | Grok/Mistral/Gemini resolution, priority order, failover, kill switch, key redaction — plus the generation guardrails, including a hostile model that emits probabilities, guarantees and fake emails and must not reach a student |

`conftest.py` builds a throwaway SQLite database in a temp directory and seeds it, so the suite
never touches `data/scholara.db`. Rate limiting and the scheduler are disabled for the HTTP tests;
`test_ratelimit.py` re-enables the limiter for itself.

SQLite runs with `PRAGMA foreign_keys=ON`. This matters: SQLite leaves foreign keys unenforced by
default, which let a staff-deletion bug pass every test while returning 500 on PostgreSQL. With
the pragma on, the test suite rejects the same writes the production database would.

Two shell scripts check a **running** pair of servers rather than the code:

```bash
./scripts/smoke-test.sh     # 40 routes return 200, unknown route returns 404
./scripts/load-reference-data.sh  # replay real destinations/universities (idempotent)
./scripts/verify-new.sh     # 63 behavioural assertions against the live API
```

### Signing in to the admin

`http://localhost:3000/admin`

| Role | Email | Password | Second factor |
| --- | --- | --- | --- |
| Admin | `admin@scholara.com.bd` | `Admin@12345` | **required** |
| Senior counselor | `senior@scholara.com.bd` | `Senior@12345` | **required** |
| Counselor | `counselor@scholara.com.bd` | `Counselor@123` | not required |
| Data manager | `data@scholara.com.bd` | `Data@12345` | not required |

`MFA_REQUIRED_ROLES` defaults to `ADMIN,SENIOR_COUNSELOR`, and it is enforced at sign-in rather
than merely suggested: an account in one of those roles that has not enrolled receives a token
scoped to the enrolment endpoints only — it cannot open the back office until a second factor
is active. The two privileged demo accounts are therefore seeded already enrolled, so the
password step hands back `mfa_required` and you need the current code:

```bash
./scripts/mfa-code.sh                        # admin@scholara.com.bd
./scripts/mfa-code.sh senior@scholara.com.bd
```

Those secrets live in `backend/app/seed_data.py` so the demo and the test-suite can sign in
unattended. They are committed, therefore public: **re-enrol every account before exposing the
platform**, and rotate these passwords — see
[document 08](docs/08-deployment-security-and-roadmap.md).

---

## The site map

**Public**

```
/                                   rotating homepage (30s cycle, 9 slides)
/study-destinations                 /study-destinations/{country}
/universities                       /universities/{country}/{university}
/programs                           /programs/{program}
/scholarships                       /scholarships/{scholarship}
/phd                                /faculty/{professor}
/services                           /services/{service}
/success-stories                    /success-stories/{story}
/events  /blog  /blog/{post}  /faqs
/profile-assessment                 4-step wizard → matches, eligibility, cost, brochure
/compare  /shortlist  /search
/contact  /appointments
/brochure/{token}                   private, printable, personalised brochure
/my/{token}                         student portal — noindex
/sitemap.xml  /robots.txt
```

**Admin**

```
/admin                  dashboard            /admin/counselor      counselor workbench
/admin/applications     pipeline board       /admin/documents      review queue
/admin/notifications    inbox                /admin/audit          audit log
/admin/marketing        pixels, GA4, ads     /admin/operations     scheduled jobs
/admin/security         MFA and sessions     /admin/settings       brand, contact, SEO
/admin/{resource}       a managed table for each of the 36 CMS resources
```

Five of those resources are read-only and return 405 on write: audit logs, analytics events, search
queries, assistant conversations, application history.

---

## What makes it "fully dynamic"

Nothing student-facing is hard-coded in the frontend. The homepage hero, the sections below it and
their order, the countries, services, scholarships, stories, events, FAQs, blog, brand colours,
contact channels, Facebook links, tracking IDs, SEO defaults and even the rotation interval all come
from the database and are editable in the admin. Deleting a row removes it from the site; disabling
a homepage section hides it without losing it.

The homepage rotation engine fetches the full slide set **once**, then cycles client-side on a timer
(default 30 seconds per slide, per-slide overrides allowed) with the next slide preloaded. No page
reload, no request per cycle, and it respects `prefers-reduced-motion`.
