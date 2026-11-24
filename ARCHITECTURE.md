# Scholara — System Architecture

International Education Consultancy Intelligence & Application Platform
Single organisation (Uttara, Dhaka). Not SaaS, not multi-tenant.

> This is the consolidated architecture reference. Companion documents:
> `RUNNING.md` (how to run it), `docs/03-data-model.md` (every table and column),
> `docs/04-api-reference.md` (every endpoint), `docs/05-data-integrity-and-trust-policy.md`
> (the trust rules), `docs/09-specification-compliance.md` (gap audit and post-mortems).

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
              │ 48 tables  │  │ ratelimit │  │ 6 scheduled    │
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
├── core/          (14)     cross-cutting concerns, no business logic
│   ├── config.py           Pydantic settings + meta_graph_base_url
│   ├── runtime.py          DB overlay over env (_DynamicSettings, 21 keys/5 groups)
│   ├── database.py         engine, SessionLocal, Base
│   ├── security.py         password hashing, JWT
│   ├── deps.py             current_user, require_role, require_staff
│   ├── mfa.py              TOTP for staff roles
│   ├── ratelimit.py        fixed-window limiter, first-match-wins rules
│   ├── cache.py            Redis helpers, graceful no-Redis fallback
│   ├── storage.py          private docs, short-lived signed URLs
│   ├── webhooks.py         signature validation
│   ├── llm.py              provider failover: grok → mistral → gemini
│   ├── embeddings.py       hosted → local LSA → hashing
│   └── local_semantic.py   TF-IDF + TruncatedSVD, no API key
├── models/        (5)      SQLAlchemy; 48 tables
│   ├── base.py             JSONType, VerificationMixin, SeoMixin, ALL vocabularies
│   ├── education.py        Country, University, Faculty, Department, Program,
│   │                       ProgramRequirement, ProgramIntake, Scholarship,
│   │                       UniversityRanking, Professor, Publication …
│   ├── content.py          HomepageSlide, Service, SuccessStory, Event, BlogPost, FAQ …
│   └── crm.py              User, Lead, Application, Document, Appointment,
│                           DataVerification, SourceCheck, KnowledgeChunk, AuditLog …
├── services/      (18)     the actual business logic
│   ├── eligibility.py      5 verdicts, never a probability
│   ├── matching.py         programme scoring + faculty research matching
│   ├── costing.py / fx.py  personalised cost of attendance
│   ├── brochure.py         degree-specific PDF generation
│   ├── rotation.py         homepage slide scheduling/priority
│   ├── retrieval.py        BM25 + dense fusion, confidence-weighted
│   ├── assistant*.py       RAG assistant with tool-calling + guardrails
│   ├── importer.py         8 entity types, staging + CONFLICT detection
│   ├── extraction.py       live reading of university websites
│   ├── source_verification.py  link classification + verification queue
│   ├── readiness.py        go-live checklist
│   ├── whatsapp.py / notifications.py
│   └── tasks.py            Celery jobs
└── api/
    ├── serializers.py      shaping + field-level visibility rules
    └── v1/routers/ (6)     public · actions · student · integrations ·
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
│   └── admin/      16 screens (CMS, leads, data workbench, marketing,
│                   platform settings, readiness, extraction)
└── components/     27, incl. home-experience.tsx (30-second rotation engine)
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
Currently **4,551 rows over 104 cited URLs**.

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

Every job returns a dict containing `{"job": "<name>"}` so results are identifiable in
the result backend.

---

## 8. Security

- HTTPS everywhere; RBAC via `require_role(minimum)` against `ROLE_HIERARCHY`
  (STUDENT → COUNSELOR → SENIOR_COUNSELOR → DATA_MANAGER → ADMIN).
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
| Tables | 48 |
| API paths / operations | 112 / 119 |
| Countries | 19 |
| Universities | 111 |
| Programmes | 165 |
| Intakes | 269 |
| Scholarships | 28 |
| Professors | 40 |
| Provenance records | 4,551 |
| Knowledge chunks indexed | 363 |
| Backend tests | 253 |
| Frontend pages / components | 43 / 27 |

**Known thin spots** (tracked in `docs/09` §13): 16 universities have no programmes
attached; 13 countries have no faculty; the citation-depth queue from §11 is unworked.
