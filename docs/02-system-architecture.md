# 02 · System Architecture

## 1. Shape

A **modular monolith** on the backend and a **Next.js App Router** frontend. Not microservices —
one consultancy, one team, one database. Modules are separated by package, not by network boundary,
so they can be split later if they ever need to be.

```
┌──────────────────────────────────────────────────────────────────────┐
│ Browser (mobile-first)                                               │
│  public site · admin control centre · floating WhatsApp · bottom nav │
└───────────────┬──────────────────────────────────────────────────────┘
                │ HTTPS. Browser only ever calls relative /api/...
┌───────────────▼──────────────────────────────────────────────────────┐
│ Next.js 16 · App Router · React 19 · TypeScript · Tailwind v4        │
│                                                                      │
│  Server Components ──► fetch(INTERNAL_API_URL) with revalidate       │
│  Client Components ──► fetch('/api/v1/...') ──► rewrite ──► backend  │
│  sitemap.ts · robots.ts · generateStaticParams · generateMetadata    │
└───────────────┬──────────────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────────────┐
│ FastAPI (ASGI, uvicorn)                                              │
│  api/v1/routers:  public · actions · admin · integrations            │
│  api/serializers.py  ← the only thing that may reach the public      │
│  services: eligibility · costing · matching · rotation ·             │
│            brochure · assistant · whatsapp                           │
│  core: config · database · security (JWT, pbkdf2) · deps (RBAC)      │
└───────────────┬──────────────────────────────────────────────────────┘
                │ SQLAlchemy 2.x
┌───────────────▼──────────────────────────────────────────────────────┐
│ PostgreSQL + pgvector (production)  ·  SQLite (this build)           │
│ Redis (cache, rate limit)  ·  Celery (verification sweeps, digests)  │
│ Object storage (private documents, signed URLs)                      │
└──────────────────────────────────────────────────────────────────────┘
```

## 2. Why these choices

| Decision | Reason |
| --- | --- |
| **FastAPI** | Typed request models, automatic OpenAPI (the frontend is written against `/api/openapi.json`, never against guesses), async-ready. |
| **Modular monolith** | One deployable. The data is heavily relational — a programme's requirement, intake, cost and scholarship are one transaction, not five service calls. |
| **PostgreSQL + pgvector** | Relational integrity for the education graph, and vector search for the assistant's retrieval in the same database. |
| **Next.js App Router** | Server Components let detail pages be statically generated per entity with ISR — the SEO surface is pre-rendered, while the interactive parts (rotation, wizard, admin) stay client-side. |
| **Hand-written serializers** | Pydantic read-models were rejected. A serializer function is an explicit allow-list, which is how partnership terms, commission rates, campaign spend and unverified faculty emails are kept off public endpoints. |
| **Tailwind v4** | Design tokens live in `@theme` in `globals.css`; brand colours come from settings and can be themed without touching components. |

## 3. Backend layout

```
backend/app/
├── main.py                     app factory, CORS, OpenAPI at /api/openapi.json
├── core/
│   ├── config.py               pydantic-settings, .env
│   ├── database.py             Base, SessionLocal, get_db
│   ├── security.py             pbkdf2 password hashing, JWT issue/verify
│   ├── deps.py                 current_user, require_role, ROLE_HIERARCHY
│   ├── mfa.py                  TOTP, recovery codes
│   ├── storage.py              private uploads, magic-byte sniff, HMAC signed URLs
│   ├── ratelimit.py            fixed-window rules + middleware
│   ├── cache.py                Redis with an in-process memory fallback
│   ├── llm.py                  Grok / Mistral / Gemini failover (one httpx client, no SDK)
│   └── webhooks.py             HMAC signature validation
├── models/
│   ├── base.py                 TimestampMixin, SeoMixin, VerificationMixin
│   ├── education.py            countries … publications
│   ├── content.py              settings, slides, sections, services, stories, events, blog, faqs, campaigns
│   └── crm.py                  users, profiles, leads, appointments, applications, documents,
│                               brochures, whatsapp, verifications, search logs, analytics, audit, notifications
├── services/
│   ├── eligibility.py          per-criterion verdicts
│   ├── costing.py              full annual cost + budget position
│   ├── matching.py             programme ranking, soft subject filter
│   ├── rotation.py             which slides are active now, ordering, durations
│   ├── brochure.py             degree-specific payload + token
│   ├── assistant.py            intent routing, tool calls, grounded answers
│   ├── assistant_engine.py     LLM tool-calling loop + sentence-level guardrails
│   │                           (falls back to templates when no provider answers)
│   ├── retrieval.py            BM25 over knowledge_chunks (pgvector-ready)
│   ├── notifications.py        staff and student notifications
│   ├── tasks.py                scheduled jobs
│   └── whatsapp.py             click-to-chat links, conversation records
├── api/
│   ├── serializers.py          public shapes (the allow-list)
│   └── v1/routers/
│       ├── public.py           GET discovery endpoints
│       ├── actions.py          POST student actions
│       ├── student.py          token-based portal: applications, documents, shortlist
│       ├── admin_extra.py      MFA, counselor desk, applications board, documents queue,
│       │                       audit log, notifications, jobs  (registered BEFORE admin.py
│       │                       so its literal paths win over /admin/{resource})
│       ├── admin.py            auth, dashboard, analytics, settings, generic CRUD
│       └── integrations.py     tracking, marketing conversions, webhooks
├── seed_data.py / seed.py      realistic demo dataset
├── migrate.py                  one-shot helper for the pre-Alembic schema
└── templates/                  print/export templates

backend/alembic/                env.py + versions/  (42 tables at head)
backend/tests/                  conftest + test_api · test_data_integrity · test_security ·
                                test_ratelimit · test_llm        (174 tests)
```

> Router order is load-bearing. `admin_extra.py` is included **before** `admin.py`; if it were not,
> the catch-all `/admin/{resource}` would swallow `/admin/counselor/dashboard` and friends.

### Role hierarchy

`ADMIN 100 > SENIOR_COUNSELOR 70 > DATA_MANAGER 60 > APPLICATION_OFFICER 55 > COUNSELOR 50 > STUDENT 10`

`require_role("X")` admits any role whose weight is ≥ X's weight. Deletes require senior rights;
settings and user management require admin.

### Generic admin CRUD

`admin.py` exposes `GET/POST /admin/{resource}` and `GET/PUT/DELETE /admin/{resource}/{id}` over a
`RESOURCES` dictionary of 36 whitelisted model keys. An unknown key 404s — the route cannot be used
to reach a model that was not deliberately exposed. Every mutation writes an `AuditLog` row.

Five of those keys are in `READ_ONLY_RESOURCES` and reject POST/PUT/DELETE with a **405**:
`audit-logs`, `analytics-events`, `search-queries`, `assistant-conversations` and
`application-history`. They are the platform's evidence trail; being editable would defeat them.

## 4. Frontend layout

```
frontend/src/
├── app/
│   ├── layout.tsx              settings-driven metadata, JSON-LD, header, chrome, analytics
│   ├── page.tsx                homepage (rotation engine host)
│   ├── study-destinations/     [slug]
│   ├── universities/           [country]/[slug]
│   ├── programs/               [slug]
│   ├── scholarships/           [slug]
│   ├── phd/  faculty/[slug]
│   ├── services/               [slug]
│   ├── success-stories/        [slug]
│   ├── events/  blog/[slug]  faqs/
│   ├── profile-assessment/  compare/  shortlist/  search/
│   ├── contact/  appointments/
│   ├── brochure/[token]/       private, print-optimised
│   ├── my/[token]/             student portal — noindex, no-store
│   ├── admin/                  layout · dashboard · [resource] · counselor · applications ·
│   │                           documents · notifications · audit · operations · security ·
│   │                           marketing · settings
│   ├── sitemap.ts  robots.ts  not-found.tsx
├── components/
│   ├── ui.tsx                  server-side presentational kit (cards, badges, source notes)
│   ├── home-experience.tsx     rotation engine + country rail
│   ├── site-header.tsx  site-chrome.tsx  quick-search.tsx  filter-bar.tsx
│   ├── assessment.tsx  faculty-match.tsx  forms.tsx  compare-view.tsx  shortlist-view.tsx
│   ├── assistant.tsx           the AI widget
│   ├── portal-view.tsx         student portal client
│   ├── analytics.tsx  print-bar.tsx
│   └── admin/                  shell · dashboard · resource-manager · workflow · marketing ·
│                               settings-editor
└── lib/
    ├── api.ts                  server fetch + settings flattening + qs
    ├── format.ts               money, ranges, labels, confidence/verdict styling
    ├── track.ts                session id, UTM capture, first-party + gtag + fbq tracking
    ├── portal.ts               portal token handling + shortlist sync
    └── admin.ts / admin-meta.ts  admin auth + resource metadata (37 resources)
```

### The two fetch paths

| Caller | URL | Why |
| --- | --- | --- |
| Server Component | `${INTERNAL_API_URL}/api/v1/...` | Direct, inside the trusted network, with Next's `revalidate` cache. |
| Client Component | `/api/v1/...` (relative) | The user's browser is not the server. A rewrite in `next.config.ts` proxies it. The browser never needs the backend's hostname, and there is no CORS surface in production. |

`src/lib/api.ts` also **flattens** the public `/settings` response. The API groups fields
(`whatsapp.*`, `facebook.*`, `social.*`, `marketing.*`, `seo.*`, `rotation.*`); components read a flat
shape. The flattener additionally treats placeholder IDs (`G-XXXXXXXXXX`, `AW-XXXXXXXXX`,
`000000000000000`) as *absent*, so a fresh install loads **zero** third-party scripts.

## 5. The rotation engine

`src/components/home-experience.tsx`, a client component fed by the server-rendered `/homepage`
payload.

1. The server fetches `/homepage` once and passes `hero.slides` (9 seeded), `hero.default_duration_seconds` (30) and the country-linked collections to the client component.
2. All slides are mounted as absolutely-positioned layers; only the active one is opaque and in the accessibility tree.
3. A timer per slide uses `slide.duration_seconds ?? default_duration_seconds`.
4. On each tick the index advances modulo the slide count, and the **next** slide's media is already in the DOM, so there is no fetch and no flash.
5. The active slide's `country_id` filters the rail beneath — featured universities, scholarships and stories follow the hero.
6. Hovering or focusing the hero pauses the timer. Previous/next/dot controls set the index directly and restart the timer.
7. `prefers-reduced-motion: reduce` removes the transitions; the content still changes.
8. Which slides are *eligible* is decided server-side by `services/rotation.py` from status, priority, start/end dates and optional hour windows — scheduling never depends on the visitor's clock.

Net effect: one request produces a nine-stage, 270-second experience.

## 6. Caching and revalidation

| Surface | Strategy |
| --- | --- |
| Detail pages (country, university, programme, scholarship, faculty, service, story, post) | `generateStaticParams()` from `/sitemap-data` + `revalidate = 300`. Pre-rendered at build, refreshed every 5 minutes. |
| List pages | `revalidate` 120–300; filters are server-rendered from `searchParams`. |
| `/settings` | `revalidate = 300` — one fetch serves every page in a window. |
| `/events` | `revalidate = 120` (seats change). |
| `/brochure/[token]` | `force-dynamic`, `revalidate = 0`, `noindex`. Personal data is never cached. |
| `/search`, `/compare`, `/shortlist`, `/profile-assessment` | Dynamic or client-side; `noindex`. |

A production build pre-renders all 23 universities, 55 programmes, 15 scholarships, 14 destinations,
10 faculty, 16 services, 14 stories and 6 posts.

## 7. Request flows

**Student checks eligibility**

```
Wizard (client) → POST /api/v1/match/programs      → matching.py → costing.py + eligibility.py
                → POST /api/v1/match/scholarships  → scholarship personalisation
                → POST /api/v1/eligibility/check   → per-criterion verdicts
                → POST /api/v1/brochures/generate  → brochure.py → token → /brochure/{token}
                → POST /api/v1/leads               → Lead row with UTM + consent
```

**Ad click to attributed lead**

```
Facebook ad (UTM link from the admin builder)
  → landing page → track.ts captures UTM into sessionStorage
  → any form submit attaches { utm } to the POST body
  → backend maps utm_campaign → AdCampaign
  → POST /marketing/conversion → Meta CAPI with SHA-256 hashed email/phone
  → the lead appears in the pipeline already attributed
```

## 8. Observability

First-party analytics first: `/track` records named events with path, entity, UTM and metadata, and
`search_queries` records what students actually searched for. GA4 and the Meta Pixel are additive
and optional. The admin dashboard reads from the platform's own tables, so it keeps working even if
every third-party tag is blocked by the browser.
