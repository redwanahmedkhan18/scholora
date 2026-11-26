# Scholara — System Architecture

International Education Consultancy Intelligence & Application Platform
Single organisation (Uttara, Dhaka). Not SaaS, not multi-tenant.

> This is the consolidated architecture reference. Companion documents:
> `RUNNING.md` (how to run it). Every table and column, every endpoint, the full trust rules and
> the gap audit live in the local `docs/` working copy, which is deliberately not published; this
> file is the published description of the system and does not depend on it.
>
> **Scope of this commit.** This file describes the platform as a whole. The commit that publishes
> it contains the security work only — MFA, the password policy, transactional email and contact
> routing. Scope resolution, catalogue integrity and change review, privacy erasure and the
> outbox, the PostgreSQL concurrency suite and the Celery delivery hardening were developed later
> and are marked **(working copy)** where they appear. Counts below are for the published tree.

---

## 1. What the system is

A consultancy's students ask three kinds of question: *where can I go*, *will they take
me*, and *what will it cost*. The platform answers all three from a curated catalogue of
countries, universities, programmes, scholarships and faculty — and refuses to answer
when it does not know.

That refusal is the architectural centre of gravity. Almost every design decision below
exists to make "I don't know" cheaper to express than a plausible guess, because the
cost of a wrong tuition figure is a student's money and a counsellor's credibility.

Three channels, one backend: the website, WhatsApp, and the counsellor's office
(through the admin UI).

---

## 2. Shape

A **modular monolith**, not microservices. One FastAPI process, one database, one
frontend. The spec called for this explicitly and it is right for the scale: a single
office's catalogue does not need independently deployable services, and distributing it
would buy nothing but operational overhead and distributed-transaction bugs.

```
                     ┌──────────────────────────────────────┐
  Students  ───────▶ │  Next.js 16 (App Router, RSC)        │
  (browser)          │  SSR/ISR pages · admin SPA islands   │
                     └───────────────┬──────────────────────┘
                                     │  relative /api/*  (same origin)
                                     ▼
                     ┌──────────────────────────────────────┐
  WhatsApp  ───────▶ │  FastAPI (modular monolith)          │
  Meta webhooks      │  routers → services → models         │
                     └──┬───────────┬──────────┬────────────┘
                        │           │          │
              ┌─────────▼──┐  ┌─────▼─────┐  ┌─▼──────────────┐
              │ PostgreSQL │  │   Redis   │  │ Celery workers │
              │ + pgvector │  │ cache /   │  │ + beat         │
              │ 47 tables  │  │ ratelimit │  │ 6 scheduled    │
              └────────────┘  │ / broker  │  │ jobs           │
                              └───────────┘  └────────────────┘
                                     │
                        ┌────────────▼─────────────┐
                        │ Outbound (all optional)  │
                        │ WhatsApp Cloud · Meta    │
                        │ CAPI · Grok/Mistral/     │
                        │ Gemini · university sites│
                        └──────────────────────────┘
```

Every outbound integration degrades to a defined local behaviour when unconfigured. The
platform is fully usable with zero third-party credentials — that is a hard requirement,
not a convenience, because the consultancy must be able to evaluate it before opening
accounts.

---

## 3. Backend layout

```
backend/app/
├── main.py                 app assembly, CORS, /api/health, router mounting
├── core/          (16)     cross-cutting concerns, no business logic
│   ├── config.py           Pydantic settings + meta_graph_base_url
│   ├── runtime.py          DB overlay over env (_DynamicSettings, 21 keys/5 groups)
│   ├── database.py         engine, SessionLocal, Base
│   ├── security.py         password hashing, JWT
│   ├── passwords.py        the staff-password policy, applied at every write path
│   ├── deps.py             current_user, require_role, require_staff
│   ├── mfa.py              TOTP for staff roles, single-use step and recovery codes
│   ├── ratelimit.py        fixed-window limiter, first-match-wins rules
│   ├── cache.py            Redis helpers, graceful no-Redis fallback
│   ├── storage.py          private docs, short-lived signed URLs
│   ├── webhooks.py         signature validation
│   ├── validation.py       phone / email / URL normalisation shared by API and CLI
│   ├── connectivity.py     deployment URL normalisation (rediss://, TLS)
│   ├── llm.py              provider failover: grok → mistral → gemini
│   ├── embeddings.py       hosted → local LSA → hashing
│   └── local_semantic.py   TF-IDF + TruncatedSVD, no API key
├── models/        (4)      SQLAlchemy; 47 application tables (+ alembic_version)
│   ├── base.py             JSONType, VerificationMixin, SeoMixin, ALL vocabularies
│   ├── education.py        Country, University, Faculty, Department, Program,
│   │                       ProgramRequirement, ProgramIntake, Scholarship,
│   │                       UniversityRanking, Professor, Publication …
│   ├── content.py          HomepageSlide, Service, SuccessStory, Event, BlogPost, FAQ …
│   └── crm.py              User, Lead, Application, Document, Appointment,
│                           DataVerification, SourceCheck, KnowledgeChunk, AuditLog …
├── services/      (19)     the actual business logic
│   ├── eligibility.py      5 verdicts, never a probability
│   ├── matching.py         programme scoring + faculty research matching
│   ├── costing.py / fx.py  personalised cost of attendance
│   ├── brochure.py         degree-specific PDF generation
│   ├── rotation.py         homepage slide scheduling/priority
│   ├── retrieval.py        BM25 + dense fusion, confidence-weighted
│   ├── assistant*.py       RAG assistant with tool-calling + guardrails
│   ├── email.py / email_templates.py  SMTP transport, retry, delivery log
│   ├── importer.py         8 entity types, staging + CONFLICT detection
│   ├── extraction.py       live reading of university websites
│   ├── source_verification.py  link classification + verification queue
│   ├── reference_data.py   replay the reference CSVs idempotently
│   ├── readiness.py        go-live checklist
│   ├── whatsapp.py / notifications.py
│   └── tasks.py            Celery jobs
└── api/
    ├── serializers.py      shaping + field-level visibility rules
    └── v1/routers/ (7)     public · actions · student · integrations ·
                            admin · admin_extra · admin_platform
```

**Dependency direction is one-way:** `routers → services → models → core`. Routers hold
no business logic; services never import FastAPI. This is what keeps the Celery workers
and the test suite able to call the same logic as HTTP, with no duplication.

### Vocabularies live in exactly one place

All enumerations (`DEGREE_LEVELS`, `CONFIDENCE_STATES`, `SOURCE_TIERS`, verdicts,
`COUNTRY_STATUSES`, …) are declared in `app/models/base.py` and imported everywhere
else. Re-declaring a list of valid values near its point of use is how two parts of a
system quietly start disagreeing about what "VERIFIED" means.

---

## 4. Frontend layout

Next.js 16 (App Router, Turbopack), TypeScript, React, Tailwind, shadcn/ui, TanStack
Query, React Hook Form, Zod 4.

```
frontend/src/
├── app/            43 pages, SEO routes mirroring the spec:
│   ├── study-destinations/[country]      universities/[country]/[slug]
│   ├── programs/…  scholarships/…  phd/…  faculty/…  success-stories/…
│   ├── search · compare · shortlist · brochure · profile-assessment
│   ├── my/…                              student portal
│   └── admin/      15 screens (CMS, leads, data workbench, marketing,
│                   platform settings, readiness, extraction)
└── components/     28, incl. home-experience.tsx (30-second rotation engine)
                    and site-chrome.tsx (mobile bottom nav + WhatsApp FAB)
```

**Server components** read through `INTERNAL_API_URL` (server-to-server, inside the
network). **Browser code** only ever calls relative `/api/...` paths. Browser code must
never address the API host directly — the user's browser is not inside the deployment
network, and hard-coding an internal host is the classic way a preview or a staging
deploy breaks.

ISR revalidation: homepage 30 s, settings 300 s, vocabularies 3600 s.

### The 30-second homepage

`home-experience.tsx` is a client-side rotation engine. Slides come from the admin CMS
with `duration_seconds` (default 30), priority, and start/end scheduling. The next slide
is preloaded; rotation causes **no page reload and no new server request per cycle**.
The active slide drives country-linked featured content below it.

---

## 5. The trust spine

This is the part that distinguishes the platform from a brochure site.

### 5.1 Provenance on every important field

`DataVerification` records, per field: `entity_type`, `entity_id`, `field_name`,
`field_value`, `source_url`, `source_type` (TIER_1…TIER_7), `retrieved_at`,
`verified_at`, `verified_by`, `review_due_at`, `status`, `conflict_with`.
The table starts almost empty: the demo seed writes **3** rows, the importer writes more, and
`verification_sweep` + `check_source_links` keep them honest. The wider working copy carries an
enriched dataset; nothing in the code depends on its size.

Confidence states: `VERIFIED` · `NEEDS_REVIEW` · `OUTDATED` · `CONFLICTING` ·
`UNVERIFIED`. Conflicting sources are **surfaced, never silently overwritten** — the
importer raises `CONFLICT` when an existing value is VERIFIED, has its own source URL,
and disagrees; the default decision is `SKIP` and a human adjudicates.

### 5.2 Bounded verdicts

Eligibility returns exactly one of `MEETS` · `DOES_NOT_MEET` · `PARTIALLY_MEETS` ·
`REQUIRES_REVIEW` · `INFORMATION_UNAVAILABLE`. There is no percentage anywhere, because
"87% admission chance" is a number the system cannot possibly justify and a student will
absolutely act on. Scholarships are phrased as *"stated eligibility criteria appear to
be met"*.

### 5.3 Faculty emails

Shown only when `email_status` is `OFFICIAL_UNIVERSITY_EMAIL` or
`PUBLIC_PROFESSIONAL_EMAIL`; otherwise `UNAVAILABLE` and no address is rendered. The
importer **refuses** a row carrying an email without a status, or with a status but no
`email_source_url` — the realistic way a fabricated address enters a dataset is a
spreadsheet column filled in by pattern, and that is exactly the shape this rejects.

### 5.4 AI guardrails

The assistant answers only from retrieved catalogue chunks. Responses are scrubbed of
emails, sentences matching 12 `PROHIBITED_CLAIMS` patterns are dropped, and a quarantine
note is appended when content is removed. The LLM never writes to the catalogue.

### 5.5 Live extraction (`services/extraction.py`)

The highest-risk feature, so the constraints are structural rather than advisory:

1. **Never guesses a domain** — the site comes from the verified catalogue record or the
   operator types it in.
2. **Every finding is a verbatim quote** from a page actually fetched, carried with its
   URL.
3. **The LLM is never trusted** — it only selects passages; everything it returns is
   checked back against the fetched text character-for-character and discarded if absent
   (`dropped_ungrounded` is reported, not hidden).

Rule 3 reduces the model from a source of facts to a search function over text already
held, which makes fabrication impossible rather than unlikely — and lets the feature run
with no LLM credentials at all via the deterministic path. `robots.txt` is honoured;
requests are spaced; output is always `UNVERIFIED` and nothing auto-writes.

---

### 5.6 The verification register (and what is not here yet)

**Implemented in this commit.** One row per field in `models.crm.DataVerification` carries the
status, the source and the reviewer, and the entity's confidence is read from that register rather
than asserted on the entity itself. `admin_platform` exposes the queue
(`GET /admin/verification/queue`), the resolve action and the source register
(`GET /admin/verification/sources`, `check`, `verify`), and the `verifications` resource is
listed in the admin CRUD. Two consequences are enforced today: a value cannot reach `VERIFIED`
without a source URL and a verification date, and `CONFLICTING` rows are surfaced for a human
instead of being overwritten — the importer's `CONFLICT` verdict defaults to `SKIP`.

**Not in this commit (working copy).** Three capabilities were built afterwards and are absent
from this tree, so do not look for them here:

| Capability | What it adds | Where it would live |
| --- | --- | --- |
| Integrity audit | 14 inconsistency categories (missing source, expired verification, thin coverage, duplicates) with an explicit cap | `services/catalogue_integrity.py` |
| Change history | One row per material change, recorded at *staging*, with approval as compare-and-swap and per-(entity, field) version numbering | `models.crm.CatalogueChange` |
| Scope resolution | One immutable `Scope` per question, failing closed on ambiguous or unsupported constraints | `services/scoping.py`, W5 context slots |

What exists in this commit instead: the verification queue plus the review endpoints above, and
the importer's staging/`CONFLICT` machinery, which is what keeps an upload from certifying
itself today.

## 6. Retrieval

```
tokenize → embed → BM25 lexical → dense top-max(limit*5, 25)
         → gate (lexical > 0 OR dense ≥ min_score)
         → fuse  w·dense + (1−w)·(bm25 / max_bm25)       w = 0.65
         → × confidence weight
           VERIFIED 1.15 · NEEDS_REVIEW 1.0 · CONFLICTING 0.85
           · OUTDATED 0.8 · UNVERIFIED 0.7
```

Weighting by confidence means a well-sourced document outranks a shakier one at equal
textual relevance — the retrieval layer enforces the trust policy rather than ignoring it.

**Narrowing happens at the edges, not in a resolver.** `retrieval.search()` takes an optional
`entity_types` filter, a limit and the fusion weights; it has no notion of a destination, a
region, a budget or an intake year. Structured narrowing is done by the calling endpoint, which
already knows what the student asked for — `/search`, `/programs`, `/scholarships` and the
eligibility endpoints filter their own SQL — and the assistant passes the entity types it decided
to consult. A dedicated scope resolver that turns a free-text question into one immutable,
fail-closed `Scope` (destination, region, university, subject, degree level, budget, intake year,
with exclusions winning over inclusions) is **not part of this commit**; it was built later in the
working copy together with the W5 context slots that carry it across turns. Until then, an
ambiguous question is answered from a broader retrieval rather than being pinned to a wrong
subset, and the assistant's guardrails still prevent a fabricated figure.

**Embeddings** fall back in three stages: hosted provider → local LSA → hashing.
The local tier (`core/local_semantic.py`) is TF-IDF + TruncatedSVD fitted on the corpus
at index time, pickled, and **never refitted at query time** (a refit between indexing
and querying puts stored and query vectors in different spaces and makes every score
meaningless). Components are chosen by **variance share (0.90)**, never a fixed count:
fitting at full rank reproduces TF-IDF exactly and yields no latent semantics at all.
Measured on the real corpus: hashing 5/8 synonym probes, LSA 7/8.

---

## 7. Background work

Celery with a Redis broker. Six scheduled jobs:

| Task | Purpose |
|---|---|
| `scholara.verification_sweep` | surface fields whose `review_due_at` has passed |
| `scholara.check_source_links` | re-resolve cited URLs; 404/410 = BROKEN, 401/403/429 = BLOCKED, 5xx/DNS = UNREACHABLE |
| `scholara.deadline_reminders` | intake and application deadline notifications |
| `scholara.data_quality_digest` | counts by confidence state for the data manager |
| `scholara.reindex_knowledge` | refit LSA + rebuild the vector index |
| `scholara.campaign_metrics` | marketing attribution roll-up |

(Working copy: a seventh job, `scholara.process_pending_deletions`, drains the erasure object
outbox every five minutes. Erasure and its outbox are not part of this commit.)

Every job returns a dict containing `{"job": "<name>"}` so results are identifiable in
the result backend.

**(Working copy) Delivery is at-least-once, and the jobs are written for it.** In the working copy
the worker acknowledges a message only after the task finishes (`task_acks_late`), requeues it when
a worker dies (`task_reject_on_worker_lost`), and the broker restores an unfinished message after
`visibility_timeout` (1,800 s) — deliberately longer than the 900 s hard time limit, so recovery
can never hand a *running* task to a second worker. Idempotency is therefore a requirement, not a
preference: the outbox drain claims rows with `SELECT … FOR UPDATE SKIP
LOCKED`, the verification sweep claims the records it flips with `UPDATE … RETURNING`, reminders
and the digest de-duplicate per entity per day, and the link checker adopts an existing row when
two sweeps race to insert it. **This commit runs the default ack-on-receipt configuration**; none
of those guarantees apply until the hardening lands.

**Time limits need a prefork pool (working copy).** The 780 s soft and 900 s hard limits are
enforced by Celery only through a prefork worker; under `--pool=threads` a hung task runs until the
process is restarted. `RUNNING.md` therefore specifies `--pool=prefork`, and the deployment
documentation previously specified `--pool=threads` — a discrepancy recorded in the audit and left
for the owner to settle, because changing a service's pool is a deployment decision. This commit
configures no time limits.

---

## 8. Security

- HTTPS everywhere; RBAC via `require_role(minimum)` against `ROLE_HIERARCHY`
  (STUDENT 10 → COUNSELOR 50 → APPLICATION_OFFICER 55 → DATA_MANAGER 60 →
  SENIOR_COUNSELOR 70 → ADMIN 100).
- **MFA (TOTP) enforced** for configured staff roles.
- Documents stored privately; access only through **300-second signed URLs**. Uploads
  capped at 15 MB with magic-byte sniffing, not extension trust.
- Webhook signature validation on all inbound Meta/WhatsApp traffic.
- Rate limiting, first match wins: login 10/5 min · leads 8/h · appointments 6/h ·
  assistant 30/h · brochure 20/h · engines 60/h · documents 40/h · track 600/h ·
  admin 600/min · public 300/min.
- Audit log on privileged mutations.
- Secrets held in runtime settings are **Fernet-encrypted** from `SECRET_KEY` and
  returned masked by the API.

---

## 9. Request input discipline

Every request model whose fields drive advice sets `extra="forbid"`.

This is a scar, not a style preference. `ProfileIn` once accepted `ielts` and
`budget_annual`, silently discarded them (the real fields are `ielts_overall` and
`budget_per_year`), and returned "English requirement: not provided" with every
programme scored identically — confidently wrong advice, produced by a validator
behaving exactly as documented. It was the third instance of the same class, after
`MatchIn.degree_level` and the importer's success tally.

**Standing review check:** *does this accept input it then discards?*

A second class, from §11 of the compliance doc: *does this check pass for a reason
unrelated to the thing being checked?* The source-link sweep reported
"93 pages, 81 OK, 0 BROKEN" — accurate, and nearly worthless, because 75 of those URLs
are homepages that return 200 forever. It measured reachability and was read as
verifiability.

---

## 10. Data at a glance

| | count |
|---|---:|
| Tables | 47 application tables (+ `alembic_version`) |
| API paths / operations | 119 / 127 |
| Routers | 7 |
| Alembic revisions | 9 |
| Backend tests | 464 (SQLite) |
| Frontend tests | 28 |
| Frontend source files | 86 |

Content depends on which dataset is loaded, so it is not a property of the code:

| Dataset | What it contributes |
| --- | --- |
| Demo seed (`app/seed_data.py`) | 14 countries, 23 universities, 55 programmes, 120 intakes, 15 scholarships, 10 faculty, 3 verification rows |
| Reference CSVs (`backend/data/reference/`) | 91 universities, 110 programmes, 149 intakes, 13 scholarships, 30 professors, 5 countries |

Provenance rows, knowledge chunks and indexed embeddings are produced by the importer and the
reindex job, so their counts change with every import and every reindex.

**Known thin spots** (tracked in the local gap audit): 16 universities have no programmes
attached; 13 countries have no faculty; the citation-depth queue from §11 is unworked.

---

## 11. Object storage and erasure

Documents are private and are never served from a public path. `core/storage.py` has one interface
with two backends: `local` (files under `STORAGE_DIR`, mode `0600`, with a key-traversal guard) and
`s3` (bucket + key). Downloads go through a short-lived signed token — HMAC over the key with an
expiry — so a link can be shared for minutes and then stops working; tampering and expiry both
raise rather than degrade.

**Deletion is an outbox, not a hope (working copy).** Erasing a profile revokes its sessions first, deletes its
dependent rows leaf-first inside one transaction, and writes the storage keys it must destroy into
`pending_object_deletions` *in that same transaction*. The objects are then deleted and each key is
removed from the outbox only after the backend confirms the object is gone. A backend that cannot
confirm raises, the row stays with its attempt count and last error, and the five-minute
`process_pending_deletions` job retries it — idempotently, because an absent object counts as
success. The result is that the system never claims a document was destroyed while its bytes are
still readable, and never claims atomicity across two systems that cannot offer it.

**Durability is the operator's decision.** `STORAGE_BACKEND` defaults to `local`, which is the
container's own ephemeral disk: a redeploy loses every uploaded document while the database keeps
its rows. The S3 backend exists for exactly that reason and is exercised only by hand — it needs
credentials, so it is excluded from the automated suite and its behaviour on a real bucket is
unverified.

---

## 12. Deployment and infrastructure boundaries

```
Local                     Staging / production (as documented)
─────────────────────     ──────────────────────────────────────────────────────
SQLite file               Supabase PostgreSQL 16 + pgvector (Session Pooler, TLS)
in-process cache          Upstash Redis over TLS: cache · rate limits · broker
in-process scheduler      Render: web (uvicorn) · worker (Celery) · beat (one instance)
STORAGE_DIR on disk       Render pre-deploy job: `alembic upgrade head`
Next.js `next dev`        Vercel: `npm run build` + ISR, proxying /api/* to the API
```

Migrations are an explicit deployment step, never something an application or worker process runs
on boot: `app.migrate` refuses to run against PostgreSQL or in production, and the documented
topology runs `alembic upgrade head` as a pre-deploy job. The readiness endpoint (`/api/health/ready`)
reports whether the database, cache and broker actually answer and returns the *names* of the failed
dependencies only; liveness (`/api/health/live`) touches nothing, so a dependency blip cannot put
Render into a restart loop.

**Continuous integration** (`.github/workflows/ci.yml`) runs five read-only jobs: the SQLite suite
with `ruff`/`mypy`, a PostgreSQL 16 + pgvector service that migrates an empty database and then
serves requests through it, a real Redis cache check, the frontend tests and TypeScript build, and
the publication-policy guard. Each job asserts the *positive* fact it exists for — the cache job
fails if the cache fell back to memory, the database job fails if the schema is not a vector
schema, the policy job fails if its own guard can be made to pass on a contaminated history.

**What this repository cannot tell you.** Service names, instance types, the deployed commit,
whether a dedicated beat service exists and is a single instance, the worker's pool flag,
Supabase backup schedule and retention, the storage bucket behind `STORAGE_BACKEND`, and which
Vercel environment variables exist are all dashboard facts. The last read-only audit could not
read them; it did observe the documented API origin answering `404` and the deployed frontend's
`/api/*` proxy returning its own 404 page, so the published site had no reachable backend at that
moment. Those remain owner actions, not repository facts — do not treat the diagram above as
proof that any hosted component is running.
