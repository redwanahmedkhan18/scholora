# 09 · Specification Compliance Audit

An honest line-by-line comparison of what the specification asked for against what is actually in
this repository, produced by inspecting the running system rather than from memory.

**Short answer: the functional surface is complete, and the named technology stack is now in
place.** Every module the spec lists exists and works end to end. The seven substituted
technologies have been replaced with the ones the spec named — PostgreSQL, Redis, Celery,
pgvector, shadcn/ui, TanStack Query, and React Hook Form with Zod — and the system was
re-verified against them.

**What remains is data, and two sets of credentials.** The dataset is demo content rather than
researched fact, and the WhatsApp Business API and Meta Conversions API need the consultancy's own
accounts. Those are the gap between "demonstrably working" and "ready to put in front of
students" — and the first one cannot be closed by writing code.

> Revised 2026-10-04 after the stack migration. Section 2 records what the migration caught:
> adopting PostgreSQL exposed two real bugs that SQLite had been hiding — a staff-deletion
> failure and a JSON type error that broke every search endpoint — and adopting pgvector briefly
> broke the system's ability to say "I do not know".

---

## 1. Functional modules — 40 / 40 present

Verified by checking each module has an API path, a backend implementation and a frontend surface.

| Module | API | Backend | Frontend |
| --- | --- | --- | --- |
| Country discovery + country pages | ✅ | ✅ | `/study-destinations` |
| University search (name, country, city, degree, subject, tuition, ranking, scholarship, intake, IELTS, CGPA) | ✅ | ✅ | `/universities` |
| University profiles | ✅ | ✅ | `/universities/{country}/{slug}` |
| Rankings with provider + year + source | ✅ | ✅ | on the university page |
| Programme hierarchy University → Faculty/School → Department → Programme | ✅ | ✅ | `/programs` |
| Academic / English / document requirements | ✅ | ✅ | on the programme page |
| Intake engine, programme-specific | ✅ | ✅ | on the programme page |
| Tuition engine + personalised cost | ✅ | ✅ | `cost_estimate`, assessment |
| Scholarship engine + personalisation | ✅ | ✅ | `/scholarships` |
| Student profile | ✅ | ✅ | `/profile-assessment` |
| Eligibility engine | ✅ | ✅ | assessment, programme page |
| University / programme matching | ✅ | ✅ | assessment results |
| Shortlist | ✅ | ✅ | `/shortlist`, portal |
| Comparison | client-side | — | `/compare` |
| Degree-specific brochure (Bachelor / Master / PhD) | ✅ | ✅ | `/brochure/{token}` |
| PhD faculty + research matching with evidence | ✅ | ✅ | `/phd`, `/faculty/{slug}` |
| Lead management pipeline (12 stages) | ✅ | ✅ | `/admin/leads` |
| Applications (16 statuses) | ✅ | ✅ | `/admin/applications` |
| Documents | ✅ | ✅ | portal + `/admin/documents` |
| Counselor dashboard | ✅ | ✅ | `/admin/counselor` |
| Admin dashboard | ✅ | ✅ | `/admin` |
| Events | ✅ | ✅ | `/events` |
| Appointments | ✅ | ✅ | `/appointments` |
| Notifications | ✅ | ✅ | `/admin/notifications` |
| Analytics | ✅ | ✅ | `/admin` analytics |
| AI assistant, tool-calling + RAG | ✅ | ✅ | assistant widget |
| Homepage rotation every 30 s | ✅ | ✅ | `/` |
| Admin CMS, 37 resources, no hard-coded arrays | ✅ | ✅ | `/admin/{resource}` |
| Facebook page linking | ✅ | ✅ | `/admin/marketing` |
| Contact options | ✅ | ✅ | `/contact` |
| Ads / campaigns | ✅ | ✅ | `/admin/marketing` |
| SEO: sitemap, canonicals, JSON-LD | ✅ | ✅ | `/sitemap.xml` |
| Mobile bottom nav + WhatsApp FAB | ✅ | ✅ | `site-chrome.tsx` |
| RBAC | ✅ | ✅ | role-gated admin |
| Staff MFA | ✅ | ✅ | `/admin/security` |
| Private documents via short-lived signed URLs | ✅ | ✅ | portal downloads |
| Audit logs | ✅ | ✅ | `/admin/audit` |
| Webhook signature validation | ✅ | ✅ | n/a (server-side) |
| Rate limiting | ✅ | ✅ | n/a (server-side) |

**Comparison** is the one entry that is deliberately client-side only: the shortlist lives in
`localStorage` and the compare view comes from already-fetched records, so no endpoint is needed.

---

## 2. Stack deviations — resolved

The spec named a stack. Seven items were substituted at the time of the first audit. **All seven
have since been replaced with the technology the specification asked for**, and the running
system was re-verified against them.

| Spec asked for | Status | Evidence |
| --- | --- | --- |
| **PostgreSQL** | ✅ adopted | PostgreSQL 17.11. `alembic upgrade head` applies all 44 tables cleanly; seed and full test sweep run against it. `DATABASE_URL=postgresql+psycopg://…` |
| **Redis** | ✅ adopted | Redis 8.0.2. `cache.backend` reports `redis`; keys survive across processes. Rate-limit counters now live in Redis (`rl:*`), so limits are shared between workers instead of per-process. |
| **Celery** | ✅ adopted | `celery_app` is a real `Celery` instance with all 5 jobs registered and a beat schedule. A worker executed `scholara.reindex_knowledge` over the Redis broker and returned `SUCCESS`. |
| **pgvector** | ✅ adopted | `vector` extension 0.8.0; `knowledge_chunks.embedding_vec vector(1024)` with an HNSW cosine index. 117/117 chunks embedded; `EXPLAIN` confirms `Index Scan using ix_knowledge_chunks_embedding_vec`. |
| **shadcn/ui** | ✅ adopted | `components.json` + `src/components/ui/primitives.tsx`: Radix primitives (dialog, select, label, tabs, tooltip, checkbox, slot) with CVA variants and the `cn` merger. The domain cards in `ui.tsx` now build on `Card`/`Badge` instead of re-declaring their own. |
| **TanStack Query** | ✅ adopted | `providers.tsx` installs `QueryClientProvider`; `useResource` is now a thin wrapper over `useQuery`, keeping its old return shape so all 10 call sites were unchanged. Gains dedupe, a 30 s stale window and focus refetch. `AuthError` is explicitly never retried. |
| **React Hook Form + Zod** | ✅ adopted | `lib/schemas.ts` holds one schema per endpoint, mirroring the Pydantic models; the three public forms use `useForm` + `zodResolver`. Errors now render under the field that caused them. |

### What adopting the real stack actually caught

Swapping the database surfaced a genuine bug that SQLite had been hiding.

**Deleting a staff user returned 500 on PostgreSQL.** `audit_logs.actor_id` is a foreign key to
`users`, and seven other tables reference staff the same way. SQLite does not enforce foreign keys
unless asked, so the delete had been silently orphaning rows; PostgreSQL rejected it outright.

The fix was not to cascade. An audit log that disappears when its author is offboarded is not an
audit log. `delete_row` now **detaches** instead: every nullable FK pointing at the user is set to
`NULL`, and because `audit_logs` also stores `actor_email`, the trail stays readable after the
account is gone. Applications, leads and appointments likewise survive their counselor leaving.
Deleting your own account is now refused outright.

Two regression tests cover it, and — more usefully — SQLite now runs with `PRAGMA foreign_keys=ON`,
so the test suite can catch this entire class of bug instead of passing while production breaks.

**`SELECT DISTINCT` broke every search endpoint.** PostgreSQL has two JSON types, and the
difference is not cosmetic: plain `json` is stored as text and has **no equality operator**, so a
single such column anywhere in a table makes `SELECT DISTINCT` over it fail with *"could not
identify an equality operator for type json"*. `/programs` and `/universities` both use DISTINCT
(they join intakes and scholarships and must not return duplicate rows), so both returned 500 —
while passing every test on SQLite, which has no such distinction.

SQLAlchemy's generic `JSON` maps to `json` on PostgreSQL, so `jsonb` has to be asked for
explicitly. `app/models/base.py` now defines `JSONType = JSON().with_variant(JSONB(),
"postgresql")`, all 57 columns across the three model files use it, and migration
`b2c3d4e5f6a7` converts the 54 live columns (round-trip tested in both directions). `jsonb` is
the right type here anyway — it is also the one that can be GIN indexed, which `subject_tags`
and `research_areas` will want as the dataset grows.

The regression test for this compiles the model DDL **against the PostgreSQL dialect without
executing it**, so it catches a reintroduced bare `JSON` column while still running on SQLite.

Both bugs share a root cause worth stating plainly: *a test suite on a different database than
production is not testing production.* Neither bug was subtle, neither was caught by 144 passing
tests, and both would have surfaced on the first real deployment.

### Retrieval changed behaviour, and had to be corrected

Switching BM25 → pgvector introduced a subtle regression worth recording. An ANN index always
returns its *k* nearest neighbours no matter how far away they are, so a nonsense query that
previously returned nothing started returning three weakly-related universities. For a platform
whose core promise is "never present information it cannot support", that is the wrong failure.

Retrieval is now a **hybrid**: dense and lexical scores are fused (`retrieval_dense_weight`, 0.65),
and a chunk survives only with real support — either a lexical term match, or a dense score above
an absolute floor, which is what still lets a purely semantic match with no shared vocabulary win.
Nonsense queries return zero results again, and ranking improved: *"scholarship for Bangladeshi
students in the UK"* now ranks the Global Excellence Scholarship first, where the pure-vector pass
had put an unrelated Dublin college at the top.

Embeddings come from Mistral or Gemini when a key is present, and otherwise from a deterministic
local hashing vectoriser (`app/core/embeddings.py`), so **the pgvector path is exercised with no
API keys at all** rather than silently falling back to lexical.

---

## 2b. Requirement-level audit — three defects found and fixed

Section 1 checks that each module *exists*. This section re-walked the 50+ numbered requirements
in [document 01](01-product-requirements.md) against live responses, which is a different and
harsher question. Three real defects surfaced, all in code that was passing its tests.

### Budget comparison ignored currency (S-4) — the serious one

`costing.estimate()` computed `programme_total - student_budget` directly. The `budget_currency`
argument was accepted and never used. **35 of the 55 programmes are priced in something other
than USD**, and Bangladeshi students state budgets in USD or BDT, so roughly two thirds of every
budget verdict was a subtraction of two different units.

It was wrong in both directions:

| Programme | Annual | vs a USD 35,000 budget | Before | After |
| --- | --- | --- | --- | --- |
| GBP-priced | 28,350 GBP | USD 35,000 = **27,650 GBP** | `WITHIN_STATED_BUDGET` | `SLIGHTLY_ABOVE_BUDGET` |
| NZD-priced | 50,250 NZD | USD 35,000 = **57,400 NZD** | `ABOVE_STATED_BUDGET` | `WITHIN_STATED_BUDGET` |

So it told students a programme was affordable when it was not, *and* ruled out programmes they
could actually afford. For a platform whose entire premise is not misleading students about
money, this was the most damaging defect in the codebase.

The fix (`app/services/fx.py`) converts the budget into the programme's currency before
comparing, and follows the same trust rules as the rest of the platform: every rate carries a
source and an as-of date, conversions are labelled indicative rather than quoted, and an unknown
currency pair returns `BUDGET_CURRENCY_MISMATCH` with both figures shown rather than a fabricated
verdict. Three regression tests cover conversion, refusal and the same-currency path.

### A verdict that contradicted its own explanation (S-2 / S-5)

The programme matcher derived its academic status from `_worst()` across the CGPA and background
checks, but took the explanation from `next()` — the *first* check. When CGPA passed and
background was unknown, the output read:

```
INFORMATION_UNAVAILABLE   Academic requirement   Meets the stated minimum.
```

The verdict described one criterion and the sentence described a different one. The explanation
now comes from the check that actually produced the verdict, and names it:

```
INFORMATION_UNAVAILABLE   Academic requirement   Relevant academic background: Not provided vs Computer Science
```

A test now asserts that no `INFORMATION_UNAVAILABLE` reason is ever explained with affirmative
language.

### Garbled faculty names (D-3)

The seed applied `f"Faculty of {dept_name}"` unconditionally, producing *"Faculty of School of
Nursing"*, *"Faculty of Business School"* and *"Faculty of School of Pharmacy"* on live
university pages. Names that already carry an organisational word are now left alone.

### D-4 resolved: the hierarchy is now four levels

Recorded earlier as an accepted deviation; it has since been built. `departments.faculty_school`
was a free-text `String(200)` retyped once per department, which meant 42 departments carried
9 division names with no dean, no website, no page and no provenance — and two spellings of the
same school would simply have become two divisions.

`faculties` is now a real table (migration `55b14f98dd2d`) with
`university_id`, `name`, `slug`, `kind`, `dean_name`, `dean_title`, `website`, `email`,
`overview`, `research_areas`, `position`, the `SeoMixin` and the full `VerificationMixin`, plus
a `UNIQUE (university_id, name)`. `departments.faculty_id` replaces the string.

The migration backfills before it drops: every distinct `(university, faculty_school)` pair
becomes a row, each department is repointed at it, and only then is the column removed —
**42 faculties, 42/42 departments linked, 0 orphans**. The downgrade refills the text column
from the relation before dropping it, and the round trip was executed in both directions.

| Surface | Added |
| --- | --- |
| API | `GET /faculties` (filter by university, country, kind, `q`) and `GET /faculties/{slug}` rolling up departments, programmes by level, and academic staff |
| University payload | `faculties[]` tree alongside the existing flat `departments[]`, each department now carrying a reference up to its division |
| Site | `/faculties/{slug}` page with breadcrumb, dean, departments, programmes by level, staff, `CollegeOrUniversity` JSON-LD; university page renders the tree instead of a flat list |
| Admin | `faculties` CRUD resource (37 total), dean and provenance editable |
| SEO | `sitemap-data.faculties` → `/faculties/*` at priority 0.65 |

**A naming trap worth recording.** The specification uses "faculty" in two senses: the academic
division, and the academic staff. `/faculty/{slug}` was already the professor route. Rather than
overload it, divisions took the plural — `/faculties/{slug}` — on both the API and the site, and
a test asserts the two routes stay distinct and return different shapes. The admin labels are
"Faculties & schools" and "Faculty (academics)" for the same reason.

## 2c. "Fully dynamic" audit — the CMS was half-wired

The spec requires *"everything data-driven through an admin CMS — no hard-coded arrays in the
frontend."* A sweep for literal option lists, content arrays and business copy found the
mechanism present but only half-connected: the admin could edit a section's **title**, while
its **content** was a literal in the bundle.

| # | What was static | Where | Now sourced from |
|---|---|---|---|
| 1 | 6 "Why Scholara" cards + 6 journey steps — rows existed in `homepage_sections` with an empty `config` | `src/app/page.tsx` | `section.config.items` / `.steps`, editable per section |
| 2 | Degree levels `BACHELOR/MASTER/PHD`, declared **four times** | `forms.tsx` ×2, `quick-search.tsx`, `assessment.tsx` | `GET /vocabularies` → `DEGREE_LEVELS` |
| 3 | 8 appointment service types, 8 time slots, 3 modes, office-hours sentence | `forms.tsx` | `site_settings.appointment_*`, admin-editable |
| 4 | Office address, email and phone in the brochure imprint | `brochure/[token]/page.tsx` | `/settings` |
| 5 | Education levels, CGPA scales, English tests, funding sources | `assessment.tsx` | `GET /vocabularies` |
| 6 | 6 budget currencies | `assessment.tsx` | derived from `fx.RATES` (15) |

### Three findings worth calling out

**The degree-level list was wrong, not just duplicated.** `DEGREE_LEVELS` has five entries;
every frontend copy listed three. `FOUNDATION` and `DIPLOMA` were unreachable in the UI —
a student could not express interest in a foundation programme, and no test caught it
because three valid options render perfectly well.

**The currency dropdown contradicted the cost engine.** It offered 6 currencies; `fx.py`
converts 15. Picking an unlisted one was impossible, so the engine's new
`BUDGET_CURRENCY_MISMATCH` path was unreachable from the UI while nine legitimate currencies
were hidden. The list is now derived from `fx.RATES`, and a test asserts the two are equal —
they cannot drift again.

**Nothing validated the booking fields.** `AppointmentIn` declared `mode: str = "OFFLINE"` and
`service_type: str = "CAREER_COUNSELING"` — free strings, no checking, and that default
service code was not even one of the eight the form offered. The API accepted
`mode: "TELEPATHY"`. Because the lists are now admin-editable they cannot be a Pydantic
`Literal` or a Zod enum, so `_validate_booking()` checks each submission against what the
office currently offers and returns a 400 naming the allowed values. The Zod schema was
relaxed to "something was chosen" with the server as the authority.

### Verified against a live CMS edit

Changing the booking options through `PUT /admin/settings` — dropping `PHONE`, replacing the
service list, moving the slots to `09:00/10:00/11:00/14:00`, raising lead time to 3 days —
changed the public form and the API's accept/reject behaviour with no deploy:

```
old 18:00 slot            -> 400 That time is not bookable. Available: 09:00, 10:00, 11:00, 14:00
withdrawn PHONE mode      -> 400 Unknown appointment mode. Allowed: OFFLINE, ONLINE
new IELTS_COACHING + 09:00 -> 200 APPT-2026-000010
earliest bookable date     -> today + 3
```

Rewriting `why_us.config` through the admin API replaced the homepage cards within the 30 s
ISR window; emptying it removed the band entirely rather than leaving a titled empty strip.

### Deliberately left static

Navigation link arrays (`site-chrome.tsx`, `search/page.tsx`, admin dashboard shortcuts,
the UTM builder's field labels) map to **routes and form fields, not content**. Moving them
into a CMS would let an editor point a link at a route that does not exist. They stay in
code; the pages they point to are fully dynamic.

---

## 2d. Mandatory MFA was advisory — fixed

`MFA_REQUIRED_ROLES` (`ADMIN,SENIOR_COUNSELOR`) was consulted in exactly three places: to set
a cosmetic `mfa_recommended` flag, to label `/auth/me`, and to refuse *disabling* MFA. Nothing
consulted it at sign-in. An administrator who had never enrolled received a full, unrestricted
session; the only thing the policy actually prevented was switching off a second factor that
had never been switched on.

Three separate faults combined to hide it:

| Fault | Effect |
|---|---|
| `users.mfa_enabled` was written once by the seed and **read nowhere** | The seeded admin looked protected (`mfa_enabled=true`) while holding no secret |
| Login branched on `mfa_secret and mfa_confirmed_at` only | No secret ⇒ straight to a full session, policy never consulted |
| Seed set the flag but never a secret | Both privileged demo accounts ran with no second factor at all |

### What now happens

A mandatory-MFA account that has not enrolled gets an **enrolment-scoped token** instead of a
session — it proves the password was right and nothing else:

```
POST /admin/auth/login     -> 200 {mfa_enrolment_required: true, scope: "mfa_enrolment"}
GET  /admin/dashboard      -> 403 MFA must be set up before this account can be used
GET  /admin/auth/me        -> 403
POST /admin/auth/mfa/enroll -> 200   (enrolment stays reachable, or the account is bricked)
POST /admin/auth/mfa/confirm -> 200  {recovery_codes: [...8...], access_token: <real session>}
GET  /admin/dashboard      -> 200
```

`current_user` rejects the scope; a second dependency, `enrolling_user`, accepts it and is
wired only to the three MFA set-up endpoints. `confirm` returns a genuine session because by
that point both factors have been demonstrated in the same exchange — otherwise a user forced
through the wall would be left holding a token that can do nothing.

`users.mfa_enabled` is dropped (migration `7f8c667c163f`). Enrolment state is
`mfa_secret + mfa_confirmed_at` and nothing else, so it cannot disagree with itself again.

### Keeping the demo usable

`ADMIN` and `SENIOR_COUNSELOR` are seeded **already enrolled** with fixed development secrets
(`seed_data.DEV_MFA_SECRET_*`), and `scripts/mfa-code.sh [email]` prints the current code.
They are committed deliberately and are therefore public — a real deployment must re-enrol.
`COUNSELOR` and `DATA_MANAGER` are not in the mandatory list and still sign in with a password
alone, so both paths stay exercised.

The admin UI gained the matching screens: a set-up step showing the key and `otpauth://` link,
then a one-time recovery-code screen that must be acknowledged before the session starts.
Without that the browser would have stored the scoped token and shown a dashboard that 403s on
every panel with no explanation.

---

## 2e. Autogenerate could destroy the vector index

`knowledge_chunks.embedding_vec` and its HNSW index are created in raw SQL by migration
`a1b2c3d4e5f6`, because pgvector has no portable SQLAlchemy column type. Alembic autogenerate
cannot see them on the models and therefore proposed `drop_column` + `drop_index` on **every**
run — the appointment-settings migration was generated with exactly that, and one unreviewed
`alembic upgrade head` would have silently wiped retrieval.

`alembic/env.py` now passes an `include_object` predicate that filters both out, so generated
migrations are safe to apply unedited. Verified: autogenerate against an up-to-date schema now
reports no drift at all, and a test asserts the predicate still covers the column and the index.

---

## 3. Integrations stubbed pending credentials — 2

| Integration | State |
| --- | --- |
| **WhatsApp Business API** | Tables, service and a signature-validated webhook all exist and are tested (a forged signature is rejected with 401). Outbound sending no-ops until `WHATSAPP_ACCESS_TOKEN` is set and templates are approved. Click-to-chat works today. |
| **Meta Conversions API** | The endpoint exists and fires on lead events; it needs a real `META_CAPI_TOKEN`. |

Neither can be finished without the consultancy's own accounts.

---

## 4. The dataset is demo content, not research

This is the largest gap between "built" and "launchable", and it deserves to be stated plainly.

| Check | Result |
| --- | --- |
| Universities with a `source_url` | 23 / 23 — but **0 are deep links**; all are bare domains like `https://www.uta.edu` |
| Programmes with a `source_url` | 55 / 55, all pointing at a generic `/courses` path rather than the specific fee or requirement page |
| Tuition figures | uniformly round numbers (28000, 29500, 12000, 26000 …) — plausible placeholders, not retrieved figures |
| Confidence state | **100 % `NEEDS_REVIEW`.** Not one record claims to be `VERIFIED` |

That last row is the system behaving correctly. The provenance machinery is fully built and it is
refusing to assert verification it does not have — exactly the design intent. The platform is not
lying about its data. But a source URL that points at a university homepage is not evidence for a
tuition figure, and the whole product thesis is that every student-facing number traces to a
specific page.

**Before launch, every figure needs to be retrieved from its actual source page, the deep link
stored, and the record moved to `VERIFIED` by a named person.** The verification queue and the
`data_quality_digest` job exist to drive precisely that work. Until then this is a working system
loaded with realistic sample content.

---

## 5. Data-integrity rules — fully enforced

These were the non-negotiables, and they are the part of the spec implemented most strictly.

| Rule | Enforced |
| --- | --- |
| Provenance on every important field | ✅ `VerificationMixin` on 9 models |
| 5 confidence states, conflicts surfaced not overwritten | ✅ conflicts live in `data_verifications` |
| Eligibility limited to the 5 permitted verdicts | ✅ `ELIGIBILITY_STATES` |
| Never a fabricated admission probability | ✅ sentence-level quarantine, 14 hostile phrasings blocked |
| Scholarships phrased as "stated criteria appear to be met" | ✅ |
| Faculty emails labelled, never inferred | ✅ 3 labels, `UNAVAILABLE` is a normal outcome |
| AI never invents university data | ✅ grounded-only, ungrounded rate tracked |
| Partnership/commercial data never public | ✅ asserted in tests |

Covered by 144 automated tests, including a hostile-model test that wires the assistant to a
provider deliberately emitting probabilities, guarantees and fake addresses and proves none of it
reaches a student.

---

## 6. Verdict

| Dimension | Status |
| --- | --- |
| Functional modules | **40 / 40** present; requirement-level audit found 4 defects, **all fixed** (the four-level hierarchy, D-4, was the last) |
| Data-integrity policy | **fully enforced**, 174 tests |
| Named stack | **7 / 7 adopted** — PostgreSQL, Redis, Celery, pgvector, shadcn/ui, TanStack Query, RHF + Zod |
| Integrations | **2 stubbed** pending the client's credentials |
| Dataset | **demo content**, correctly flagged `NEEDS_REVIEW` throughout |

### Verification on the real stack

Re-run after the migration, with the API on PostgreSQL + Redis and a Celery worker attached:

| Check | Result |
| --- | --- |
| `pytest` | **196 passed** |
| `tsc --noEmit` | clean |
| `npm run build` | exit 0 |
| `scripts/check-prerender.py` | **331 / 331** prerendered pages carry a success status |
| `scripts/smoke-test.sh` | **40 / 40** |
| `scripts/verify-new.sh` | **63 / 63** |
| OpenAPI surface | 107 paths / 114 operations |
| `alembic upgrade head` | 47 tables, `embedding_vec` + HNSW index, columns on `jsonb`, head `3d87b450f327` |
| pgvector corpus | 228 / 228 chunks embedded at 1024 dims, index scan confirmed in the query plan |
| Content | 15 destinations, 61 universities, 114 programmes, 2,323 provenance records |
| Celery | `scholara.reindex_knowledge` dispatched over Redis, executed by a separate worker, `SUCCESS` |

### What is left, in order of value

The four items that previously appeared here were all *"an engineer must edit a file and
redeploy"* problems. None of them are any more — each is now an admin screen. What remains is
genuinely the consultancy's own work.

| Was | Now |
| --- | --- |
| 1. Load real, deep-linked, verified data | **Tooling built and in use.** `Admin → Data import & verification` stages a CSV/JSON upload, diffs it field by field, flags anything contradicting a `VERIFIED` record, and writes one provenance row per changed field on commit. 19 countries, 61 universities and 114 programmes are loaded; every destination arrived through this pipeline, seven entity types deep. |
| 2. Add WhatsApp / Meta credentials | **`Admin → Platform settings`.** Paste a token, press *Test connection*, get a verbatim pass/fail from the vendor. Encrypted at rest, applied without a restart. |
| 3. Tune retrieval against real content | **`Admin → Platform settings → Retrieval tuning`.** Run a query under current and candidate weights side by side, see which results enter, drop or move, then apply. |
| 4. Decide the embedding provider | **`Admin → Platform settings → Embeddings`.** Pick a provider, see how many stored vectors the change invalidates, reindex from the same screen. |

#### What genuinely remains

1. **Deep-link and verify the figures.** The import pipeline now makes this a data-entry task
   rather than a code change, but someone still has to open each university's fee page, record
   the figure against that URL, and move the record to `VERIFIED` under their own name. The
   verification queue is the worklist; `data_quality_digest` is the weekly nudge.
2. **Supply the accounts.** WhatsApp Business, Meta Pixel/CAPI and an LLM key are the
   consultancy's to obtain. Every one of them is a paste-and-test away from working.

---

## 7. Making the last four items dynamic — what was built

### 7a. Runtime settings overlay

`settings.X` is read in roughly forty places. Rewriting those call sites to consult a database
would have been a large, error-prone change, so `app/core/config.py` now exposes a
`_DynamicSettings` proxy instead: `__getattr__` checks `runtime.override(name)` first and falls
through to the cached environment settings. Every existing read became dynamic with no call-site
edits, and `__setattr__` still writes to the base object so test monkeypatching keeps working.

The allowlist in `app/core/runtime.py` is the security boundary — 21 keys across five groups.
A row for `secret_key` or `database_url` is ignored even if someone inserts one directly into the
table. Secrets are Fernet-encrypted with a key derived from `SECRET_KEY`, returned masked
(`EAAG••••••3456`), and never appear in the audit log, which records *which keys changed* and
nothing else. Clearing a field deletes the override and hands the key back to the environment.

The config write and its audit entry share one transaction. A credential that changes without
being logged is worse than one that fails to change.

### 7b. Import and verification workbench

Nothing an operator uploads touches a live table. A file is parsed, matched on its natural key,
and diffed field by field into one of five row actions:

| Action | Meaning |
| --- | --- |
| `CREATE` | No existing record with that key |
| `UPDATE` | Existing record, values differ, nothing verified is being contradicted |
| `CONFLICT` | The existing value is `VERIFIED` **and** cites its own source **and** the incoming value differs |
| `UNCHANGED` | Identical — committing it would write a pointless provenance row |
| `ERROR` | Missing a required field, a bad vocabulary value, or no source URL anywhere |

`CONFLICT` rows default to `SKIP` and are never auto-applied; resolving one stores the superseded
value and its source in `conflict_with`, so the disagreement is preserved rather than erased.
Committing writes one `DataVerification` row per changed field.

The importer covers **seven entity types**: `country`, `university`, `program`, `scholarship`,
`program_requirement`, `program_intake` and `university_ranking`. The last three have no natural
key of their own — a requirement is identified by the programme it belongs to, nothing else — so
their schemas declare `child_of` plus a discriminator (`label` for an intake, `provider` +
`edition_year` for a ranking) and match on the resolved foreign key. Files load in dependency
order, parents before children.

Two bugs found while building this are worth recording, because both were silent:

- An **unknown column was treated as a blocking error**, which rejected whole rows over a
  harmless extra field. Warnings and errors are now separate: advisory notes travel in
  `message`, only real problems set `ERROR`.
- A **string written into a JSON list column** survived the database and then crashed the
  country page on `.map()`. The importer now derives list-valued fields from the model's own
  column types rather than a hand-maintained list, so the schemas cannot drift again.

### 7c. Reference data is replayable, not a one-off

`seed.run(reset=True)` wipes everything. Real data loaded by hand would vanish on the next
reseed, so the destinations and institutions live as versioned CSVs in
`backend/data/reference/` and are replayed through the ordinary import pipeline:

```bash
./scripts/load-reference-data.sh              # load or re-load
DRY_RUN=1 ./scripts/load-reference-data.sh    # preview the diff, commit nothing
```

Running it twice is a no-op — the second pass diffs every row to `UNCHANGED`. This was verified
the hard way: the database was deliberately reseeded down to 14 countries, the loader was run,
and every record came back with its provenance intact.

The manifest is now seven files, and the most recent load wrote **220 records with 0 conflicts,
0 errors and 1,995 provenance rows**, then wrote nothing at all on a second run.

One rule enforced in the generator deserves stating. `build_programmes.py` originally emitted a
placeholder source URL of the form `https://{university}.example-source/`. Every one of those
rows would have passed validation and entered the database wearing a citation that leads
nowhere — the precise failure this platform exists to prevent. The generator now reads the real
`website` column out of the university CSVs and raises `SystemExit` if an institution has no
site on record. A programme cannot be generated with an invented source.

### 7d. Embedding provenance

Vectors from two different models are not comparable, so changing provider silently degrades
every search until the corpus is rebuilt. Each chunk now stores the `embedding_model` and
`embedding_dim` it was produced with, `retrieval.embedding_drift()` reports how many are stale,
and the Embeddings panel refuses to be reassuring about it. Any setting that invalidates stored
vectors is flagged `requires_reindex` in the config screen.

### 7e. Retrieval tuning

The defaults (dense 0.65, floor 0.10) were calibrated against the local hashing vectoriser on
demo content, and that provenance is stated in the UI. The tuner runs the same query under
current and candidate weights and shows which results entered, dropped or merely moved. The
sweep reports coverage and mean top score across a set of probes but deliberately **does not
declare a winning weight** — without labelled relevance judgements that would be a fabricated
metric, which is precisely what this platform refuses to do everywhere else.

### Where each destination's data comes from

| Destination | Source |
| --- | --- |
| Singapore | Ministry of Education — `moe.gov.sg` |
| Saudi Arabia | Ministry of Education scholarship portal — `studyinsaudi.moe.gov.sa` |
| Spain | Ministry of Universities — `universidades.gob.es` |
| Türkiye | Study in Türkiye (YTB) — `studyinturkiye.gov.tr` |
| Austria | OeAD — `studyinaustria.at` |

| Japan | MEXT standard fee schedule, plus each university's own fee page |
| Finland | Study in Finland / Ministry of Education, plus published university fee pages |

Every university cites its own official domain. Everything is loaded as `NEEDS_REVIEW`, because
compiling from an official portal is not the same as a named person opening the page and
confirming the figure. The system is not permitted to flatter itself on that distinction.

Two findings from the Japan and Finland pass are recorded because they contradict the obvious
assumption:

- **"National university" no longer implies ¥535,800.** That figure has been the MEXT standard
  since 2005, but ministerial rules permit up to 120% of it, and the University of Tokyo adopted
  ¥642,960 for undergraduates entering from 2025 while leaving its master's programmes at the
  standard rate. Fees are therefore recorded per programme and per entry year, never inferred
  from institution type.
- **A Finnish PhD is free to every nationality**, while the same university charges a non-EU
  master's student €15,000–20,000. Tuition had to be modelled per degree level rather than per
  university, or the PhD pages would have quoted a fee that does not exist.

### Destination coverage

Every active destination has at least one university **and** at least one programme. An empty
country page is worse than an absent one: it advertises a destination and then dead-ends the
student who clicks it.

| Destination | Universities | Programmes | Scholarships |
| --- | ---: | ---: | ---: |
| Japan | 10 | 13 | 1 |
| Finland | 8 | 12 | 3 |
| United Kingdom | 4 | 11 | 2 |
| United States | 4 | 11 | 2 |
| Türkiye | 4 | 8 | 1 |
| Austria | 5 | 7 | 1 |
| Canada | 3 | 7 | 2 |
| Singapore | 3 | 7 | 1 |
| Saudi Arabia | 4 | 6 | 2 |
| Spain | 4 | 6 | 0 |
| Australia | 2 | 5 | 1 |
| Malaysia | 2 | 5 | 1 |
| Ireland | 2 | 4 | 1 |
| Germany | 1 | 2 | 1 |
| South Korea | 1 | 2 | 1 |
| **Total** | **61** | **114** | **20** |

Spain carries no scholarship record on purpose. Spanish public tuition is set per credit by each
autonomous community rather than by a national scholarship scheme, and no suitable award was
confirmed from an official source. An empty list is the honest answer; inventing a plausible
"Spain Excellence Scholarship" to balance the table is exactly the behaviour the data-integrity
rules forbid.

### A green build that served 404s

The last defect found in this pass was not in the data at all. `next build` renders every
university, programme and scholarship page by calling our own API — several hundred requests in
well under a minute, past the 300/min public rate limit. The limiter refused three university
pages. The frontend helper `apiOrNull` returned `null` on any non-OK response, callers turn
`null` into `notFound()`, and so three real universities were prerendered as permanent 404 pages.
The build log was entirely green.

The failure is worth naming precisely: a transient infrastructure response was converted into a
durable factual claim — "this university does not exist" — and then cached. Three fixes:

1. `apiOrNull` treats **only a clean 404** as "no such record". A 429, a 5xx or a transport
   failure now raises, so a build fails loudly instead of publishing something false.
2. First-party rendering sends `X-Internal-Token`; the rate limiter compares it with
   `secrets.compare_digest` and skips throttling. Browser traffic reaches the API through the
   Next.js proxy, which does not forward the header, so the exemption is unreachable from
   outside. Three tests cover the exemption, a wrong token, and an unset token.
3. `scripts/check-prerender.py` reads the status Next recorded for all 331 prerendered pages and
   fails the gate on any unexpected error page.

---

## 8. Two defects found by building the verification tooling

Both were found by using a new tool on real data rather than by reading code, and both
belong to the same family: **software that reports success it has not earned.**

### 8.1 The importer reported rows it had silently discarded

A reference load announced `2 new, 0 updated, 11 unchanged, 0 errors`. Two of those rows
were not in the database afterwards.

Three separate faults combined:

1. **`commit_batch` rolled back the whole session on any single row's failure.**
   The per-row `except` called `db.rollback()`, which discarded every row already flushed
   in that batch. One bad row at line 2 destroyed the two good rows before it.
2. **Counters were incremented before the write was known to have survived.**
   `created += 1` ran at `db.add()` time, so rows that were later rolled back still
   counted.
3. **The loader summed what staging *intended*, not what commit *achieved*,** and never
   looked at `commit_batch`'s `failures` list — hence `0 errors`.

The trigger was mundane: a `stipend` string of 183 characters in a `VARCHAR(160)` column.

Fixed by giving each row its own `SAVEPOINT` (`db.begin_nested()`) so a failure costs
exactly itself; moving the counters after the savepoint survives; marking failed rows
`ERROR`; reporting `result["created"]` rather than `batch.created_rows`; and adding a
**length check at staging time** so an over-long value is a readable error before any
write is attempted. Guarded by `test_one_bad_row_does_not_destroy_its_neighbours` and
`test_an_over_long_value_is_caught_before_commit`.

### 8.2 A naive link checker condemns correct data

The first version of the source checker classified any HTTP status ≥ 400 as `BROKEN`. Run
against real citations it reported the la Caixa Foundation fellowship page — a current,
hand-verified source — as a dead link, because the site answers `403` to an unrecognised
user-agent.

Acting on that would have meant deleting correct data. The classifier now separates:

| State | Meaning |
|---|---|
| `OK` | the page answered normally |
| `BROKEN` | **404/410 only** — genuinely gone, the citation needs replacing |
| `BLOCKED` | 401/403/406/429 — *we* were refused; says nothing about the page |
| `UNREACHABLE` | 5xx, DNS, TLS, timeout — the check could not run |
| `UNCHECKED` | never looked |

`UNREACHABLE` must never render as `BROKEN`: on a host without outbound network, the
naive version would have condemned every citation on the platform at once.

**What the first honest sweep of all 58 citations found:** 52 OK, 3 blocked by bot
protection, 3 genuinely dead (404) — `oead.at/en/to-austria/scholarships`,
`aalto.fi/.../tuition-fees-and-scholarships` and the Helsinki equivalent, together
supporting 39 field claims. All three were replaced with verified live URLs and the
sweep now reports **0 broken**.

### 8.3 Verification is grouped by page, not by field

2,385 unverified claims resolve to **58 distinct URLs**. Field-by-field that backlog
never gets worked; one NUS page alone carries 98 claims. `/admin/verification/sources`
groups the queue by the page a verifier actually opens, so confirming a page clears
everything citing it in one action.

The division of labour is explicit and enforced: the machine checks **reachability** and
may never change a confidence state; only `verify_source`, which **requires a named
human** and refuses an empty verifier, can write `VERIFIED`. It is unreachable from any
scheduled job.

### 8.4 An accepted-then-ignored API field

`MatchIn.degree_level` was accepted by `/match/scholarships` and silently dropped — the
engine reads `profile.target_degree_level`. A PhD-only fellowship therefore came back
`MEETS` for a master's applicant. The endpoint now honours it as a fallback, with an
explicit profile value still winning.

---

## 9. Closing the gaps: what was built, and what genuinely cannot be

Nine items were outstanding. Four of them require accounts only the consultancy can open,
so the work here was to make them provable rather than merely plausible.

### 9.1 A full rebuild from a bare machine (and the defect it exposed)

The environment was reset to nothing — no PostgreSQL, no Redis, no Python packages — and
rebuilt from the workspace alone. It surfaced a real defect: **`requirements.txt` had the
entire production stack commented out** as "production extras":

```
# psycopg[binary]>=3.2   # alembic>=1.13   # redis>=5.0
# celery>=5.4            # pgvector>=0.3
```

A clean `pip install -r requirements.txt` therefore produced an environment that could not
run the migrations, the worker or pgvector — and because `app.core.database` silently falls
back to SQLite, the failure appeared as a baffling `no such table: import_batches` rather
than an honest "psycopg is not installed". Those five lines are now uncommented. **The
specified stack is the stack.**

The rebuild also re-validated the importer fixes from §8 end to end: a from-scratch
reference load produced **249 created, 0 errors**, and the nine bad `"All"` nationality
claims did not come back — because the fix had been made in the *builder*, not patched into
the database.

### 9.2 Data depth — 61 → 95 universities, 114 → 165 programmes

| | Before | After |
|---|---|---|
| Universities | 61 | **95** |
| Programmes | 114 | **165** |
| Intakes | 196 | **269** |
| Prerendered pages | 336 | **421** |

Germany went from 1 university to 11, and the research turned up something that matters
more than the count. **"Germany is free" is false for a Bangladeshi student at TUM.** From
winter semester 2024/25 TUM charges non-EU students €4,000–6,000 per semester for a
master's, per [its own fee page](https://www.tum.de/en/studies/fees/tuition). Third-party
sources contradicted each other on this, which is exactly the case the provenance model
exists for. German tuition is set by *state*, not university, and the data now reflects
that honestly:

| Institution | State | Annual cost to a non-EU student |
|---|---|---|
| TU Dresden | Saxony | ~€600 (semester contribution only) |
| RWTH Aachen | NRW | ~€750 (semester contribution only) |
| Heidelberg / KIT / Stuttgart / Freiburg | Baden-Württemberg | €3,000 |
| TU Munich | Bavaria | **€8,000–12,000** |

A 20× spread inside one country — precisely the kind of thing a student is otherwise
advised wrongly about.

Also added: Canada (3→8), Australia (2→7), Ireland (2→7), Malaysia (2→6), South Korea
(1→6), with the Canadian thesis-vs-course-based distinction recorded explicitly because a
UBC research MSc (CAD 9,500) and a Toronto MEng (CAD 62,000) are not comparable products.

Two latent bugs fell out of the expansion: the builder's month map covered only seven
months and raised a bare `KeyError` on the first southern-hemisphere July intake (now all
twelve), and `university_sites()` **correctly refused to invent a source URL** for a
university it had not been told about — the guard working as designed.

### 9.3 Integration conformance — proving the code before the credentials

Four integrations could not be exercised without vendor accounts, so the only evidence they
worked was that they compiled. `tests/conformance_server.py` now implements the vendors'
documented wire formats, and the **real** client code runs against it — same request
builder, same auth header, same parsing, same error handling.

Eight tests now cover what was previously unverifiable:

- the WhatsApp send body carries `messaging_product` (its absence is Meta's error 100, and
  the single most common client mistake)
- a rejected WhatsApp token surfaces as a handled 401, never a 500 for the student
- a real LLM completion round-trip, asserting the key, model and prompt that went out
- **failover**: provider 1 rejected → provider 2 answers; all three failing returns `None`
  rather than raising, so the assistant degrades to retrieval-only
- a 429 is treated as a failure to fail over from, not as a reply
- **the Conversions API hashes PII before it leaves the server** — the test asserts the raw
  email and phone appear nowhere in the bytes sent, only their SHA-256 digests

Enabling this exposed a design problem worth fixing on its own: the Graph API URL was
hard-coded in **five** places. Meta deprecates API versions on a rolling schedule, so that
was a guaranteed future outage requiring a code change. It is now one setting,
`meta_graph_base_url`.

### 9.4 Link rot across a larger corpus

Citations grew from 58 to **93 distinct pages**. The sweep: **81 OK, 0 broken**, 9 blocked
by bot protection, 3 transiently unreachable.

### What still cannot be done from here

1. **The four credentials.** WhatsApp, Meta Pixel/CAPI, Facebook Page and an LLM key need
   accounts the consultancy owns. The code is now tested against their wire formats, so
   pasting a key is the whole remaining step.
2. **Embeddings remain the local hashing vectoriser** (317 chunks). Switching is a provider
   key plus a reindex, both already in the admin.
3. **Verification remains a human act, by design.** 93 pages are queued and link-checked;
   only a named person can write `VERIFIED`, and that is deliberate.
4. **Faculty data was not expanded.** It stayed at 10 professors rather than growing,
   because inventing plausible academics and their research interests would breach the one
   rule the whole platform is built on. Real faculty must be imported from university
   directories.

---

## 10. Functional completeness audit

A direct check of whether the code behind every feature is actually written and working,
rather than present and hollow.

**Static scan.** No stub functions anywhere in `backend/app` — no `pass`-only bodies, no
`NotImplementedError`, no docstring-only functions. The two frontend `return null` hits are
a `switch` default and a conditional render guard, both legitimate.

**Live probe — 40 / 40 modules return real data**, not empty shells: country discovery and
detail, university search (with filters), university profiles, programme search and detail,
the intake engine, scholarships, faculty and research areas, success stories, services,
events, blog, FAQs, homepage slides, settings, vocabularies, global search, sitemap data,
stats, campaigns, the eligibility engine, programme matching, scholarship matching, faculty
matching, the AI assistant, lead capture, appointments, analytics tracking, the student
portal, shortlists, and the admin, counsellor, lead, application, document and notification
surfaces. 111 API paths, 118 operations.

### 10.1 The defect the audit found: silently discarded profile fields

`ProfileIn` used Pydantic's default behaviour of **dropping unknown fields**. The real
field names are `ielts_overall` and `budget_per_year`; a caller sending the
natural-looking `ielts` and `budget_annual` had them silently thrown away and still got a
confident answer — one computed as though the student had never sat IELTS and had stated
no budget.

The symptoms were quietly wrong rather than obviously broken:

| | With dropped fields | With correct fields |
|---|---|---|
| English requirement | `INFORMATION_UNAVAILABLE` — "Not provided" | `MEETS` — "IELTS 6.5 vs required 6.5" |
| Budget status | `NOT_PROVIDED`, gap `null` | `ABOVE_STATED_BUDGET`, gap computed |
| Score, €330 vs €18,000 programme | **58.50 and 58.50 — identical** | 65.50 vs 49.20 |

A student would have been ranked against programmes with cost and English score simply
ignored. `ProfileIn` now sets `extra="forbid"`, so a misspelled field is a 422 that names
it. This is the third instance of the same defect class in this project — a field accepted
and then ignored — after `MatchIn.degree_level` (§8.4) and the importer's success tally
(§8.1). **Accepting input you then discard is the failure mode this codebase is most prone
to**, and it is worth treating as a review checklist item rather than a series of
coincidences.

Guarded by `test_a_misspelled_profile_field_is_rejected_not_ignored` and
`test_the_correctly_named_fields_still_drive_the_result`.

---

## 11. Citation depth audit — the integrity check that was measuring the wrong thing

Added `backend/scripts/verify_citations.py`, which re-reads every cited page and asks
whether it still contains the figure we stored. Full output: `docs/citation-diff.md`
(regenerate with `python3 scripts/verify_citations.py` from `backend/`).

### 11.1 What it found

| Verdict | Count |
|---|---:|
| CONFIRMED — value present on the cited page | 23 |
| NOT_FOUND — page read fine, value absent | 55 |
| **CITATION_TOO_COARSE — cited page is a site homepage** | **1,123** |
| UNCHECKABLE — blocked/unreachable from here | 20 |
| NOT_APPLICABLE — internal bookkeeping or prose | 2,257 |

**75 of the 104 distinct cited URLs are bare homepages** (`https://en.snu.ac.kr/`,
`https://tu-dresden.de/`, `https://uwaterloo.ca/`, …), and 1,123 world-claims hang off
them — tuition amounts, IELTS minimums, intake months.

### 11.2 Why the earlier green result was misleading

§9 reported the source sweep as **93 cited pages, 81 OK, 0 BROKEN** and that was read as
reassurance. It was accurate and nearly worthless, because a university homepage returns
200 essentially forever. The link checker measured **reachability**; it was being read as
**verifiability**. Those differ exactly where it matters: a homepage citation for a
tuition figure is permanently green and permanently unverifiable.

The spec requires a per-field `source_url` so a human can re-check the claim. A URL that
cannot show the claim does not satisfy that requirement, however well it resolves.

### 11.3 Defect class

This is a second standing pattern, distinct from the "accepts input then discards it"
class in §10:

> **A check that passes for a reason unrelated to the thing being checked.**

Both are failures of the same kind — a signal that looks like evidence and is not. The
review question to carry forward: *if this check passed while the underlying thing was
broken, would I be able to tell?* For the link sweep the answer was no.

### 11.4 Status and remedy

Not auto-fixable, and deliberately not auto-fixed: deepening a citation means finding the
fees or admissions page that actually states the figure, which is a per-figure judgement.
Deleting the data would be worse — the figures are right, their citations are shallow.

The report groups the work by entity and field so it can be cleared in batches (all
`tuition_annual` for one country at a time). The 55 NOT_FOUND rows are the higher-urgency
queue: there the page *was* readable and the figure still was not on it.

### 11.5 Scoring note

Checks are limited to an **allowlist** of fields that assert something about the world —
money, test scores, durations, dates, city, country. Internal vocabulary (`study_mode`
`FULL_TIME`, `degree_level` `BACHELOR`, `term` `MARCH`, `currency` `KRW`) is excluded: the
page writes "full-time", "undergraduate", "Spring", "₩", so matching our enum against
their prose measures nothing. An early draft checked everything and produced mostly false
alarms — a report whose entries are mostly noise is worse than no report, because it
looks like diligence.

---

## 12. Live site extraction — reading a university's own website

New capability: an operator gives a university name, a country and a question in plain
words; the system visits that institution's site, follows the pages likely to answer,
and returns quotes. `backend/app/services/extraction.py`,
`POST /api/v1/admin/extraction/run` (DATA_MANAGER), admin screen at `/admin/extraction`.

### 12.1 Why this is the most dangerous feature in the platform

Everything else in Scholara records where a figure came from. An extractor that
summarises a web page in its own words is precisely how an invented tuition fee acquires
a source URL and starts looking trustworthy. Three rules therefore constrain it:

1. **It never guesses a domain.** The site comes from our verified catalogue record, or
   the operator types it in. There is no third path. Constructing `www.<name>.edu` and
   reading whatever answers is how the tool would end up confidently reporting the wrong
   institution's fees.
2. **Every finding is a verbatim quote from a page actually fetched**, carried together
   with the URL it came from.
3. **The LLM is optional and never trusted.** It only selects passages. Everything it
   returns is checked back against the fetched text character for character; what is not
   found there is discarded and counted in `dropped_ungrounded`.

Rule 3 is the load-bearing one. It turns the model from a source of facts into a search
function over text we already hold, which makes fabrication *structurally impossible*
rather than merely discouraged. It also means the feature works with **no LLM credentials
at all** — the deterministic path does the same job less elegantly. Tested directly:
`test_grounding_discards_a_quote_that_is_not_on_the_page`.

A quote attributed to the wrong page is repointed rather than dropped — that is a
citation bug, not a fabrication.

### 12.2 Three bugs worth recording

**Single-hop discovery cannot work.** The first version read the homepage and followed
its best links. Measured on `tcd.ie`: of 29 homepage links, exactly **one** scored above
zero for a tuition query, and the fees page was not among them. University information
architecture puts fees two or three clicks behind a generic hub ("Study", "Courses"). No
amount of scoring tuning fixes that; discovery had to become a budgeted two-level crawl
that follows hubs at level 1 and intent matches at level 2. It then reached
`/study/undergraduate/fee-finances/` and `/study/apply/admission-requirements/`.

**Substring matching produced nonsense.** A course listing yielded the tuition finding
*"The Lir Academy Costume Level 1"* — because `cost` is a substring of `costume`. Now
whole-word matching, with a trailing `s`/`es` allowed so `scholarship` still matches
`scholarships` but `cost` does not reach `costume`.

**Grounding proves a quote is real, not that it is worth reading.** Galway's pages
yielded *"research, postgraduate, programme, course, nui galway, archaeology, phd, per
annum, 2014/15, inclusive of levy,"* — a tag cloud that matches the tuition vocabulary
perfectly and informs nobody. Worth being clear that this passed grounding entirely
correctly; usefulness is a separate judgement, made by a comma-density prose filter. The
first version of that filter was too strict and threw away *"A cumulative GPA of 3.2 or
better (B+ average) unweighted."*, which is exactly the kind of thing the tool exists to
surface.

### 12.3 Operational manners

`robots.txt` is honoured (unreachable = permissive, the conventional reading), requests
are spaced, pages are capped, and the user agent identifies the crawler. A consultancy
running an automated reader against university sites all day is a guest on someone
else's infrastructure, and getting the office IP banned from the sites the business
depends on would be a self-inflicted outage.

Output is always `confidence: UNVERIFIED` and nothing is written to the catalogue.
`to_import_rows()` shapes findings for the existing importer so extracted material goes
through the same human review and conflict detection as everything else.

---

## 13. Catalogue expansion and what is still thin

| | before | after |
|---|---:|---:|
| Universities | 95 | **111** |
| United Kingdom | 4 | **11** |
| United States | 4 | **13** |
| Professors | 10 | **40** |

The UK/US gap was the most embarrassing finding of the previous audit: the two
destinations most Bangladeshi students ask about first were among the thinnest entries,
below Germany and Japan. The additions deliberately favour institutions that actually
take large numbers of Bangladeshi students — Greenwich, Portsmouth, Coventry,
Northumbria, Teesside, Bedfordshire; Texas Tech, Houston, Wayne State, Memphis, Toledo —
rather than only famous names nobody in the Uttara office gets applications for.

### 13.1 Recorded as NEEDS_REVIEW, deliberately

Every new university is `NEEDS_REVIEW`, not `VERIFIED`. Their websites were checked live
(20/22 reachable; Hertfordshire and QMUL return 403 to bots and were left out), but the
detail fields are cited to homepages — and §11 has just finished explaining why a
homepage citation cannot support a specific claim. Marking 19 fresh records `VERIFIED`
on homepage citations would have reproduced that exact defect while the write-up of it
was still on screen.

### 13.2 Still outstanding

**16 universities have no programmes.** They are published and findable but a student
clicking through finds no courses. The programme builder uses hand-researched figures
per programme, so this cannot be mass-generated — doing so would mean inventing tuition,
which is the one thing the platform must never do. This is the next real piece of work,
and the extraction tool in §12 is the intended way to gather it: read each institution's
fees page, review the quotes, stage them through the importer.

Also still open: faculty for the 13 countries at zero, and the citation-depth queue
from §11.
