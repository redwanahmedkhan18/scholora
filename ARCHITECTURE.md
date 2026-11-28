# Scholara — System Architecture

International Education Consultancy Intelligence & Application Platform
Single organisation (Uttara, Dhaka). Not SaaS, not multi-tenant.

> This is the consolidated architecture reference. Companion documents:
> `RUNNING.md` (how to run it). Every table and column, every endpoint, the full trust rules and
> the gap audit live in the local `docs/` working copy, which is deliberately not published; this
> file is the published description of the system and does not depend on it.

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
              │ 50 tables  │  │ ratelimit │  │ 7 scheduled    │
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
├── models/        (5)      SQLAlchemy; 50 application tables
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

### Stored Markdown is rendered as elements, never as markup

The blog body is staff-authored Markdown and is rendered by `src/lib/markdown.ts`: it parses the
text into a tree of blocks and inline spans, and React turns that tree into elements. There is no
`dangerouslySetInnerHTML` for user content anywhere in the application — the only sinks left are
`jsonLdScript()` (§5.2) — so there is no attribute context for authored text to escape into and no
`href` that React did not build.

This replaced a renderer that escaped `& < >` and then rewrote `[text](url)` into an `<a href="…">`
string. Quotes were not escaped and the destination was not checked, so a `javascript:` link
produced a working one and a `"` in a destination closed the attribute and opened an event handler.
Any COUNSELOR can author a post, the blog is public, and the back office shares the origin with its
JWT in `localStorage` — one hover was a path from a content role to a staff session.

Two rules make a destination a URL or nothing (`safeHref`): `http:`, `https:`, `mailto:`, `tel:` and
same-site relative paths are allowed; everything else — `javascript:`, `data:`, `vbscript:`, `file:`,
protocol-relative, an HTML character reference, a percent-encoded colon — is refused, and a refused
destination degrades to its own link text rather than being rewritten. Scheme detection normalises
the way a browser does (control characters and whitespace removed, case folded) and allowed absolute
URLs are re-serialised by the platform parser, so the stored value is the browser's own canonical
form. Nothing is truncated. The parser is single-pass and linear: an earlier version was quadratic,
and 180 KB of emphasis spans cost ~12 s of CPU per render.

**The CSP is defence in depth, and its limits are stated rather than implied** (`config/csp.js`,
applied in `next.config.ts`). `script-src` must include `'unsafe-inline'`: Next's App Router ships
its hydration payload in inline `__next_f.push(...)` scripts, and the optional GA4/GTM/Meta bootstrap
is inline too, while the pages are static/ISR and so cannot carry a per-request nonce. This policy
is therefore **not** claimed to stop an injected inline script. What it does enforce:
`script-src-attr 'none'` (every inline event-handler attribute is blocked, which is the attribute
vector a future rendering mistake would reintroduce), `object-src 'none'`, `base-uri 'self'`,
`form-action 'self'`, `frame-ancestors 'none'`, no `'unsafe-eval'` in production, and allowlisted
analytics origins rather than `*`.

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

### 5.6 Catalogue integrity, review and change history

Three layers, each answering a different question, and none of them able to promote a figure by
itself:

| Layer | Question it answers | Where |
| --- | --- | --- |
| Integrity audit | *What is inconsistent right now?* 14 categories — missing source, expired verification, conflict, thin coverage, duplicate — reported with an explicit cap, never silently truncated | `services/catalogue_integrity.py` |
| Verification register | *Who says this value is right, and when?* One row per field with status, source and reviewer; the entity's confidence is **derived** from that register rather than asserted | `models.crm.DataVerification` |
| Change history | *What changed, and was it approved?* One row per material change, recorded when an import is **staged** so a conflict is visible before it is applied | `models.crm.CatalogueChange` |

An upload can never certify itself: a commit forces `NEEDS_REVIEW` and clears the verification
dates, trust columns are settable only through the review path (the generic admin CRUD strips
them), a refusal requires a note, and a reviewer cannot approve their own submission.

**A direct edit invalidates the verification it no longer supports.** The generic admin CRUD writes
any content column a form posts, so the same rule the importer follows applies there
(`services/catalogue_writes.py`): when a *factual* field of a governed record changes, the record's
confidence becomes `NEEDS_REVIEW`, `verified_at` / `verified_by` / `review_due_at` are cleared, the
edited value is appended to the field register as a claim awaiting review, and a `CatalogueChange`
keeps the previous value beside the new one with the actor's address. The three writes share one
transaction, so a value cannot land without its trust state and its history. Presentation, routing
and exposure columns (slug, SEO text, images, feature flags, ordering, counters) are explicitly not
claims and never trigger it. The list of governed models is derived from the `VerificationMixin`
rather than maintained by hand, so a model cannot be governed by accident or forgotten. Decisions
are compare-and-swap on the state that was reviewed, so two reviewers cannot both "win" one field
and one change cannot be recorded as both approved and rejected. Version numbers per
(entity, field) are assigned under a PostgreSQL advisory lock and are unique by index. Staleness
(`CURRENT` / `REVIEW_OVERDUE` / `STALE` / `UNKNOWN`) is reported, and `UNKNOWN` is a state — a
missing freshness window is never treated as fresh.

**The register is evidence, so it is append-only and the derivation fails closed.** A register row
is what an entity's state is derived *from*, which means the row may not be removed: deleting the
one row still saying `NEEDS_REVIEW` used to leave a register in which every remaining row said
`VERIFIED`, and the derivation duly promoted a fee nobody had reviewed while naming a reviewer who
had never seen it. Four rules close that:

1. **No row can be deleted through the application.** `DELETE` and `PUT` on `verifications` return
   405 and name the supported transitions; `review.delete_field` refuses as well, so a future
   caller that reaches for a delete cannot succeed quietly.
2. **Withdrawal is a transition.** `POST /admin/verification/{id}/withdraw` requires a reason,
   keeps the row and its recorded evidence, sets `WITHDRAWN`, clears the verification stamp, and
   can only ever lower trust. The actor and the reason land in `audit_logs`.
3. **The derivation reads the newest row per field and withholds on three conditions:** an
   undecided `CatalogueChange` for the entity, any field whose current row is not `VERIFIED`, and a
   `VERIFIED` row whose recorded evidence no longer matches the stored value (an approval certifies
   the value it saw, not whatever is stored now).
4. **Every register mutation refreshes the entity** in the same transaction
   (`review.refresh_entity_state`), including a page-level source confirmation and a claim created
   through `POST /admin/verifications`: that path re-derives the entity after flushing the new row,
   so the record can never describe the register as it was before the insert. A created row is
   always `NEEDS_REVIEW` and creation can therefore only lower trust.

**Evidence is stored in full, and withholding says why.** The evidence column held 400 characters
and every writer clipped to fit it, so a claim about a longer value — a programme `overview`, a
serialised `other_fees` map — recorded a prefix. The derivation compares the recorded value with the
value actually stored, so that prefix never matched, and the record could never be promoted: the
reviewer approved the field, was told it was `VERIFIED`, and the record stayed withheld for ever
with nothing explaining it and no evidence that could agree because the column could not hold it
(C2-10). Two changes fix it:

* **The column is `Text`** (`alembic` revision `b4c5d6e7f8a9`, a pure widening) and no writer clips,
  so the register holds the value it attests to and the comparison is made against the full stored
  value. A fixed larger limit was rejected: the columns being attested are themselves unbounded, so
  any cap moves the same defect to a different threshold.
* **A withholding is explained** (`review.explain_entity_state`, returned as `derivation` by the
  entity detail, the field decision and the withdrawal): a reason code (`no_register`,
  `fields_not_verified`, `conflicting`, `outdated`, `pending_change`, `evidence_mismatch`,
  `evidence_truncated`, `record_missing`), a sentence, the fields involved, and the supported
  recovery where one exists. None of it describes the schema or discloses a value.

Claims written before the widening hold a clipped value whose remainder exists nowhere. They are
reported as `evidence_truncated` — *unanswerable*, not disagreeing — and the recovery is to
register the field again from the current source and decide that claim; the new row supersedes the clipped
one for its field. A recorded value that is not a prefix of the stored value is a genuine
`evidence_mismatch`.

That detection is a **heuristic, not proof**: a recorded value of exactly 400 characters whose
normalised form is a prefix of a longer stored value is treated as a clip, and a genuine
400-character value that happens to be a prefix of the stored value is indistinguishable from one.
There is therefore no "confirmed truncation" state — a pair that is legitimately 400 characters
long and equal to the stored value is compared normally and agrees, and anything the heuristic
flags is withheld with wording that says the evidence *looks like* a truncated copy rather than
asserting what happened to it. Withholding is the same in both readings, and the same recovery
resolves both.

**A value the database refuses is a 422, in layers that are not equally portable.** Three things
happen, and only the first is backend-independent:

1. **Shape validation before the write** (`admin._reject_unrepresentable`) runs on every backend:
   non-finite floats (including inside a JSON column), a container aimed at a scalar column, a
   fractional number aimed at an integer column, and a boolean aimed at a numeric column are all
   refused with 422 and nothing written.
2. **Database refusals are mapped** (`admin._refuse_client_values`, `_client_value_error`) around the
   whole governed write, including the `flush()` calls inside
   `catalogue_writes.apply_direct_edit` that used to escape as a 500. The classification is narrow:
   a `DataError` (SQLSTATE class `22`), an out-of-range number, a driver refusal to *adapt* the
   Python value (client-side, no SQLSTATE) or a type mismatch (`42804`). It is **PostgreSQL
   behaviour**: those error classes come from the server rejecting a value against a declared
   column type.
3. **SQLite does not enforce column types.** Its driver stores whatever it is given, so
   `{"duration_months": "twelve"}` is a 422 on PostgreSQL and a 200 that stores the text `twelve`
   on SQLite. The value-mapping tests are therefore marked PostgreSQL-only, and a green SQLite run
   says nothing about them. Production runs PostgreSQL; SQLite is the zero-setup engine the rest of
   the suite uses.

An operational error, an integrity error and any other programming error returned *by the server*
keep their own behaviour on both backends — a database outage is a 503, a duplicate key is a
conflict, and neither is mislabelled as the caller's typo.

Three shapes are refused before the write because accepting them would corrupt the record rather
than the answer: `NaN`/`Infinity` (JSON has no such literal, but Python's reader accepts it and
`double precision` stores it — after which every response for that record fails), a container aimed
at a scalar column (the driver stored `["USD"]` as the text `{USD}`), and a fractional number aimed
at an integer column (PostgreSQL rounds `1.5` to `2`). A value that is merely *formatted* differently
— a numeric string, an integral float, a JSON column's mapping — is still accepted.

Attribution follows the account, not a text field: `verified_by` is written from the authenticated
reviewer, a caller cannot name a colleague as the decider, and the uploader of a record cannot
confirm it on any path — including `POST /admin/verification/sources/verify`.

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

**Scope before scoring.** A question is first resolved into one immutable `Scope`
(`services/scoping.py`): destination, region, university, subject, degree level, budget, intake
year. `RESOLVED` narrows the query; `NEEDS_CLARIFICATION`, `UNSUPPORTED` and `CONTRADICTORY` fail
closed — the assistant asks a targeted question or returns an honest empty answer, never an
unfiltered result. The scope is applied in SQL by per-model adapters (one per schema, because the
five catalogues do not share a shape), and it is carried across turns by the W5 context slots,
where the server owns the value and a client cannot set it. Exclusions always win over inclusions,
and an unresolved mandatory constraint is never dropped to make a query succeed.

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
| `scholara.process_pending_deletions` | drain the erasure object-deletion outbox (every 5 minutes) |

Every job returns a dict containing `{"job": "<name>"}` so results are identifiable in
the result backend.

**Delivery is at-least-once, and the jobs are written for it.** The worker acknowledges a message
only after the task finishes (`task_acks_late`), requeues it when a worker dies
(`task_reject_on_worker_lost`), and the broker restores an unfinished message after
`visibility_timeout` (1,800 s) — which is deliberately longer than the 900 s hard time limit, so
recovery can never hand a *running* task to a second worker. Idempotency is therefore a
requirement, not a preference: the outbox drain claims rows with `SELECT … FOR UPDATE SKIP
LOCKED`, the verification sweep claims the records it flips with `UPDATE … RETURNING`, reminders
and the digest de-duplicate per entity per day, and the link checker adopts an existing row when
two sweeps race to insert it.

**Time limits need a prefork pool.** The 780 s soft and 900 s hard limits are enforced by Celery
only through a prefork worker; under `--pool=threads` a hung task runs until the process is
restarted. `RUNNING.md` therefore specifies `--pool=prefork`, and the deployment documentation
previously specified `--pool=threads` — a discrepancy recorded in the audit and left for the owner
to settle, because changing a service's pool is a deployment decision.

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
- The internal token exempts **reads only** (`GET`/`HEAD`/`OPTIONS`). It used to exempt every
  method, which made one secret mean both "do not throttle the build" and "unthrottle the
  conversion relay, lead capture and the assistant": 65 token-bearing conversion calls produced no
  429 against a rule allowing 60 an hour. Writes are now throttled like any other write.
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
| Tables | 51 |
| API paths / operations | 131 / 140 |
| Routers | 7 |
| Alembic revisions | 17 |
| Backend tests | 1,185 (SQLite, 50 skipped) / 1,235 (PostgreSQL with Redis required, none skipped) |
| Frontend tests | 99 |
| Frontend source files | 93 |

Content depends on which dataset is loaded, so it is not a property of the code:

| Dataset | What it contributes |
| --- | --- |
| Demo seed (`app/seed_data.py`) | 14 countries, 23 universities, 23 programmes, 120 intakes, 15 scholarships, 10 faculty |
| Reference CSVs (`backend/data/reference/`) | 91 universities, 110 programmes, 149 intakes, 13 scholarships, 30 professors, 5 countries |

Provenance rows, knowledge chunks and indexed embeddings are produced by the importer and the
reindex job, so their counts change with every import and with `POLICY_MAX_AGE_DAYS`.

**Known thin spots** (tracked in the local gap audit): 16 universities have no programmes
attached; 13 countries have no faculty; the citation-depth queue from §11 is unworked.

---

## 11. Object storage and erasure

Documents are private and are never served from a public path. `core/storage.py` has one interface
with two backends: `local` (files under `STORAGE_DIR`, mode `0600`, with a key-traversal guard) and
`s3` (bucket + key). Downloads go through a short-lived signed token — HMAC over the key with an
expiry — so a link can be shared for minutes and then stops working; tampering and expiry both
raise rather than degrade.

**Deletion is an outbox, not a hope.** Erasing a profile revokes its sessions first, deletes its
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

**What this repository cannot tell you.** Service names, instance types, the deployed commit,
whether a dedicated beat service exists and is a single instance, the worker's pool flag,
Supabase backup schedule and retention, the storage bucket behind `STORAGE_BACKEND`, and which
Vercel environment variables exist are all dashboard facts. The last read-only audit could not
read them; it did observe the documented API origin answering `404` and the deployed frontend's
`/api/*` proxy returning its own 404 page, so the published site had no reachable backend at that
moment. Those remain owner actions, not repository facts — do not treat the diagram above as
proof that any hosted component is running.
