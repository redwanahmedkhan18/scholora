# 04 · API Reference

Base URL `**/api/v1**` · admin under `**/api/v1/admin**` · OpenAPI document at `**/api/openapi.json**`
(note the `/api` prefix — `/openapi.json` 404s). 91 paths / 94 operations in total.
Liveness is at `**/api/health**`, outside the versioned prefix.

The OpenAPI document is authoritative. Never guess a request body; read it from there.

---

## 1. Response envelopes

| Shape | Endpoints |
| --- | --- |
| `{ total, page, page_size, items[] }` | `/universities`, `/programs`, `/scholarships`, `/faculty`, `/blog` |
| `{ total, page, page_size, items[], facets{} }` | `/success-stories` |
| **bare array** | `/services`, `/events`, `/faqs`, `/campaigns/active`, `/research-areas` |
| object | every detail endpoint, `/homepage`, `/search`, `/settings`, `/stats`, `/sitemap-data` |

---

## 2. Public reads (GET)

### Discovery

| Endpoint | Query parameters |
| --- | --- |
| `/countries` · `/countries/{slug}` | — |
| `/universities` · `/universities/{slug}` | `q`, `country`, `city`, `degree_level`, `subject`, `min_tuition`, `max_tuition`, `intake`, `max_ielts`, `max_cgpa`, `scholarship`, `ranking_provider`, `featured`, `sort`, `page`, `page_size` |
| `/programs` · `/programs/{slug}` | `q`, `country`, `degree_level`, `subject`, `university`, `max_tuition`, `intake`, `scholarship`, `sort`, `page`, `page_size` |
| `/scholarships` · `/scholarships/{slug}` | `q`, `country`, `university`, `degree_level`, `page`, `page_size` |
| `/faculty` · `/faculty/{slug}` | `q`, `country`, `university`, `field`, `interests`, `accepting`, `page`, `page_size` |
| `/faculties` · `/faculties/{slug}` | `university`, `country`, `kind`, `q`, `page`, `page_size` — **academic divisions**, not people |
| `/research-areas` | — |
| `/search` | `q`, `limit` → `{ query, universities, programs, countries, scholarships, faculty }` |

### Content

| Endpoint | Query parameters |
| --- | --- |
| `/services` · `/services/{slug}` | `featured` |
| `/success-stories` · `/success-stories/{slug}` | `country`, `university`, `degree_level`, `year`, `scholarship`, `q`, `page`, `page_size` |
| `/events` · `/events/{slug}` | `upcoming` |
| `/blog` · `/blog/{slug}` | `category`, `tag`, `q`, `page`, `page_size` |
| `/faqs` | `scope`, `category` |

### Platform

| Endpoint | Returns |
| --- | --- |
| `/homepage` | `hero{rotation_enabled, default_duration_seconds, total_cycle_seconds, slides[]}`, `sections[]`, `countries[]`, `featured_universities[]`, `popular_programs[]`, `scholarships[]`, `success_stories[]`, `services[]`, `events[]`, `posts[]`, `phd{programs,professors}`, `stats{}` |
| `/homepage/hero` | the hero block only |
| `/settings` | grouped public settings — `whatsapp{}`, `facebook{}`, `social{}`, `marketing{}`, `seo{}`, `rotation{}` plus flat brand/office fields |
| `/stats` | public counters |
| `/sitemap-data` | `{ countries[], universities[], programs[], scholarships[], faculty[], services[], success_stories[], posts[] }` of `{slug, updated}` (universities also carry `country`) |
| `/campaigns/active` | campaigns safe to expose (no budget or spend) |
| `/brochures/{token}` | `{token, version, student_name, degree_level, target_intake, created_at, payload{}}` |
| `/profiles/{token}` | a student's own saved profile |
| `/whatsapp/link` | a prefilled click-to-chat URL |

### Homepage section order

`10` hero · `20` search · `30` countries · `40` featured universities · `50` popular programmes ·
`60` scholarships · `70` why us · `80` services · `90` success stories · `100` events · `110` PhD ·
`120` journey · `130` articles · `140` CTA.

---

## 3. Student actions (POST)

| Endpoint | Body (required fields in bold) |
| --- | --- |
| `/leads` | **`full_name`**, **`phone`**, `email`, `message`, `interest_country`, `interest_degree`, `interest_subject`, `intake`, `source`, `channel_detail`, `utm{}`, `page_path`, `consent_contact`, `profile{}` |
| `/appointments` | **`full_name`**, **`phone`**, **`scheduled_date`**, **`scheduled_time`**, `email`, `service_type`, `counselor_name`, `mode`, `notes`, `utm{}` |
| `/profiles` | `ProfileIn` — no required fields. Keys: `full_name`, `email`, `phone`, `nationality`, `city`, `highest_qualification`, `major`, `institution`, `cgpa`, `cgpa_scale`, `graduation_year`, `study_gap_years`, `backlogs`, `completed_subjects[]`, `english_test`, `ielts_overall`, `ielts_lowest_band`, `toefl_ibt`, `pte_academic`, `duolingo`, `moi_available`, `gre_score`, `gmat_score`, `work_experience_years`, `job_title`, `research_experience`, `publications_count`, `research_interests[]`, `budget_per_year`, `budget_currency`, `funding_source`, `preferred_countries[]`, `preferred_universities[]`, `target_degree_level`, `target_discipline`, `target_intake`, `career_goal`, `consent_marketing`, `source` |
| `/events/register` | **`event_id`**, **`full_name`**, **`phone`**, `email`, `preferred_country`, `degree_level` |
| `/eligibility/check` | **`profile`**, `program_id`, `program_slug` |
| `/match/programs` | **`profile`**, `degree_level`, `country_slugs[]`, `subject`, `intake`, `limit` |
| `/match/faculty` | **`interests[]`**, `country_slug`, `university_slug`, `field`, `limit` |
| `/match/scholarships` | **`profile`**, `limit` |
| `/brochures/generate` | **`profile`**, `program_id`, `program_slug`, `target_intake`, `channel` |
| `/assistant/ask` | **`question`**, `context{}`. Response adds `mode` (`llm`/`template`), plus `provider` and `model` naming which of Grok/Mistral/Gemini served it (both `null` in template mode) |
| `/track` | **`name`**, `path`, `entity_type`, `entity_id`, `utm{}`, `meta{}` |
| `/marketing/conversion` | server-side conversion relay (Meta CAPI) |

> Two traps worth remembering: the match response key is **`results`**, not `matches`; and
> `/match/programs` returns 400 if a null `program_slug` is forwarded to `/eligibility/check`.

### Match result item

```jsonc
{
  "stats": { "total": 12, "within_budget": 7, "academic_ok": 9,
             "with_scholarship": 5, "intake_available": 10, "research_aligned": 3 },
  "results": [{
    "program": { … },
    "score": 86,
    "eligibility": { "overall": "PARTIALLY_MEETS",
                     "checks": [{ "criterion", "status", "required", "provided", "note" }],
                     "summary": "…" },
    "cost": { "currency", "breakdown[]", "estimated_annual_total",
              "budget_status": "WITHIN_STATED_BUDGET" },
    "reasons": [{ "factor", "status", "detail" }],
    "scholarships": [ … ],
    "intake": { … },
    "research_hits": [ … ]
  }]
}
```

### Brochure payload

`/brochures/generate` → `{ token, version, degree_level, url: "/brochure/{token}", payload }`, where
`payload` contains `meta`, `cover`, `university`, `program`, `eligibility`, `requirements`, `costs`,
`scholarships`, `intakes`, `student_life`, `sources` — plus `phd` and
`requirements.doctoral_specific` for PhD, and `requirements.masters_specific` for Master's. Every
block carries its own `source_url` / `source_type` / `verified_at` / `confidence`, and `sources[]`
is the consolidated citation list printed at the end of the document.

---

## 4. Detail response shapes

**`/countries/{slug}`** → `{ country{…, currency, tuition_min/max, living_cost_min/max, popular_subjects[], intakes[], counts{}, why_study[], visa_summary, visa_resources[], work_rights, post_study_work, student_life, faqs[], seo}, universities[], programs[], scholarships[], success_stories[], events[] }`

**`/universities/{slug}`** → `{ university{…, rankings[{provider, edition_year, rank_overall, rank_national, provenance}], program_count, institution_type, established_year, student_count, international_students, campus, accreditation, overview, student_life, accommodation, faqs[], website, application_portal, country{}, seo}, programs[], programs_by_level{}, departments[], scholarships[], professors[], success_stories[] }`

**`/programs/{slug}`** → `{ program, cost_estimate, scholarships, related[4], faculty }`
`program.requirements` = `{ academic{…}, english{…}, tests{…}, documents[], notes, provenance }`, with a flat alias `requirement_summary`. There is **no** `requirement` key.
`cost_estimate` = `{ currency, breakdown[{label, amount, included_in_total}], estimated_annual_total, one_time_costs }`.
`intakes[i]` = `{ id, label, term, month, year, status, application_opens, application_deadline, class_start, notes, provenance }`.

**`/scholarships/{slug}`** → flat object with `amount_type`, `amount_value`, `percentage`, coverage flags, `eligibility_summary`, `eligible_nationalities[]`, `min_cgpa`, `min_ielts`, `other_criteria[]`, `deadline`, `application_method`, `university{}`, `country{}`, `provenance`.

**`/faculties/{slug}`** → `{ faculty{id,name,slug,kind,dean_name,dean_title,website,email,overview,research_areas[],position,provenance,departments[],department_count}, university{name,slug,logo_url,country,country_slug}, programs[], programs_by_level{}, program_count, professors[] }`.

> **Two senses of "faculty".** The specification uses the word for both the academic division
> and the academic staff. The singular route `/faculty/{slug}` is a **professor**; the plural
> `/faculties/{slug}` is a **division** (Faculty of Engineering, Business School). The site
> mirrors this split, and a test asserts the two never converge.

**`/faculty/{slug}`** → `{ professor{…, research_interests[], research_keywords[], lab_name, lab_url, accepting_phd, email, email_status, email_source_url, matched_interests[], evidence[], provenance, bio}, publications[] }`.

**`/services/{slug}`** → `{ id, title, slug, icon, summary, category, is_featured, priority, hero_image, status, description, process[{step,title,detail}], benefits[], documents[], countries[], faqs[{q,a}], seo }`.

**`/success-stories/{slug}`** → `{ id, student_name, slug, headline, photo_url, university_name, program_name, degree_level, intake, year, scholarship, visa_result, is_featured, country{}, testimonial, published_at, journey[] }`.

**`/blog/{slug}`** → `{ post{…, body (markdown)}, related[] }`.

**`/events/{slug}`** → `{ id, title, slug, speaker, start_at, end_at, venue, mode, capacity, banner_url, status, is_featured, country_id, university_id, registration_url, description }`.

---

## 5. Student portal (token, no password)

A student is identified by the opaque `token` on their `student_profiles` row — the same token the
assessment hands back and the one in the `/my/{token}` URL. Nothing here is indexable; every portal
response is sent `Cache-Control: no-store` and the page is `noindex, nofollow, nocache`.

| Endpoint | Method | Notes |
| --- | --- | --- |
| `/portal/{token}` | GET | the whole portal in one call — see the shape below |
| `/portal/{token}/applications` | POST | `{program_slug, intake_label}` → `{duplicate, application{}}`. Re-posting the same pair returns `duplicate: true` instead of a second row |
| `/portal/{token}/applications/{application_id}` | GET | one application with its full `history[]` |
| `/portal/{token}/documents` | POST | multipart upload → `{document}`. ≤15 MB, MIME allow-list plus a magic-byte sniff; a mismatch is a 400 |
| `/portal/{token}/documents/{document_id}` | DELETE | removes the row and the stored object |
| `/portal/{token}/shortlist` | GET / POST | `{entity_type, slug, label, meta}`; an unknown `entity_type` is a 422 |
| `/portal/{token}/shortlist/sync` | POST | bulk-merges the browser's `scholara.shortlist` → `{added}` |
| `/portal/{token}/shortlist/{item_id}` | DELETE | — |
| `/portal/{token}/notifications/{note_id}/read` | POST | marks one notification read |

**`GET /portal/{token}`** → `{ profile, counts{}, applications[{id, reference, status,
progress_percent, intake_label, program, university, history[]}], documents[{…, download_url}],
document_checklist[], document_types[], shortlist[], notifications[], brochures[] }`.

`reference` is `APP-{YYYY}-{000001}`. `progress_percent` is `0` for `WITHDRAWN` and `REJECTED`,
otherwise `round((ACTIVE_STAGES.index(status) + 1) / 14 * 100)` — it is a position in a checklist,
never a prediction. The default checklist is `PASSPORT`, `BACHELOR_TRANSCRIPT`, `IELTS_TRMC`, `CV`,
`SOP`; document statuses are `UPLOADED`, `UNDER_REVIEW`, `ACCEPTED`, `REJECTED`, `EXPIRED`.

### Private documents

`download_url` is **not** a path to a file. It is
`/api/v1/documents/file/{b64url(key|expiry)}.{b64url(hmac[:24])}`, signed with the server secret and
valid for **300 seconds**. `GET /documents/file/{signed_token}` is the only way to read a document:
it verifies the HMAC, rejects an expired or tampered token with a 403, and streams the bytes with
`Cache-Control: no-store`. Nothing in `data/private/` is reachable by any other route.

---

## 6. Admin API

All admin endpoints require `Authorization: Bearer <JWT>`.

### Sign-in and MFA

Login is **two-step once a staff account has MFA enrolled**.

| Endpoint | Method | Notes |
| --- | --- | --- |
| `/admin/auth/login` | POST | `{email, password}` → either `{access_token, mfa_required: false, user{…, mfa_enrolled}}`, or `{mfa_required: true, email, message}` **with no token**, or `423` when the account is locked |
| `/admin/auth/mfa/challenge` | POST | step two: `{email, password, code}` → `{access_token, user}`. `code` is a 6-digit TOTP or a `xxxx-xxxx-xxxx` recovery code |
| `/admin/auth/me` | GET | current user plus `mfa_enrolled` and `mfa_required` |
| `/admin/auth/mfa/status` | GET | enrolment state and remaining recovery codes |
| `/admin/auth/mfa/enroll` | POST | returns the secret and `otpauth://` URI; not active until confirmed |
| `/admin/auth/mfa/confirm` | POST | `{code}` → activates MFA and issues **8** single-use recovery codes, shown once |
| `/admin/auth/mfa/disable` | POST | requires a current code; audit-logged |

TOTP is 6 digits on a 30-second step with ±1 step of drift allowed. Recovery codes are stored as
SHA-256 hashes and consumed on use.

### Staff workspaces

| Endpoint | Method | Role | Returns |
| --- | --- | --- | --- |
| `/admin/dashboard` | GET | counselor+ | `counters{}`, `lead_pipeline[]`, `lead_sources[]`, `top_searches[]`, `upcoming_deadlines[]` |
| `/admin/counselor/dashboard` | GET | counselor+ | `{counselor, kpis{}, follow_ups[], leads[], applications[], appointments[], documents_pending[], notifications[]}` — scoped to the signed-in counselor |
| `/admin/applications/board` | GET | counselor+ | `{total, stages[], counts{}, items[]}` across the 16 application statuses |
| `/admin/applications/{id}/status` | POST | counselor+ | `{status, note}` → moves the application and appends an `application_status_history` row |
| `/admin/applications/{id}/assign` | POST | senior+ | reassigns the owning counselor |
| `/admin/documents/queue` | GET | counselor+ | `{total, statuses[], counts{}, items[]}` |
| `/admin/documents/{id}/review` | POST | counselor+ | `{status, note}` → `ACCEPTED` / `REJECTED` / `UNDER_REVIEW` / `EXPIRED` |
| `/admin/documents/{id}/link` | POST | counselor+ | mints a fresh 300-second signed URL for a student document |
| `/admin/notifications/inbox` | GET | any staff | unread first |
| `/admin/notifications/{id}/read` · `/admin/notifications/read-all` | POST | any staff | — |
| `/admin/audit/log` | GET | senior+ | `{total, page_size, items[], filters{actions[], entity_types[]}}` |
| `/admin/analytics` | GET | counselor+ | `events[]`, `search_total`, `top_countries[]`, `top_subjects[]`, `top_degrees[]` |

### Governance and operations

| Endpoint | Method | Role | Notes |
| --- | --- | --- | --- |
| `/admin/assistant/conversations` | GET | senior+ | `{total, ungrounded, ungrounded_rate, items[]}`. The ungrounded rate is the health metric for the AI |
| `/admin/assistant/reindex` | POST | data manager+ | rebuilds the knowledge index from current records |
| `/admin/assistant/providers` | GET | data manager+ | Grok / Mistral / Gemini status: priority, whether a key is set, model, base URL, last error. **Never returns key material** |
| `/admin/assistant/providers/{name}/test` | POST | admin | live round trip against one provider → `{ok, model, latency_ms, reply}` or `{ok:false, error}`. Unknown name → 404 |
| `/admin/jobs` | GET | admin | the scheduled jobs and their last run |
| `/admin/jobs/{name}/run` | POST | admin | runs one now; an unknown name is a 404 |

Jobs: `reindex_knowledge` (1 h), `campaign_metrics` (30 min), `verification_sweep` (6 h),
`deadline_reminders` (12 h), `data_quality_digest` (7 d).

| Endpoint | Method | Role | Notes |
| --- | --- | --- | --- |
| `/admin/auth/login` | POST | — | `{email, password}` → `{access_token, user}` |
| `/admin/auth/me` | GET | any staff | current user |
| `/admin/dashboard` | GET | counselor+ | `counters{}`, `lead_pipeline[]`, `lead_sources[]`, `top_searches[]`, `upcoming_deadlines[]` |
| `/admin/analytics` | GET | counselor+ | `events[]`, `search_total`, `top_countries[]`, `top_subjects[]`, `top_degrees[]` |
| `/admin/resources` | GET | staff | the 36 manageable resource keys |
| `/admin/settings` | GET / **PUT** | admin | PUT returns `{ok:true}`; re-fetch to read back |
| `/admin/{resource}` | GET / POST | role-gated | GET is paginated with `q`, `page`, `page_size` |
| `/admin/{resource}/{id}` | GET / **PUT** / DELETE | role-gated | PUT is a partial update; DELETE needs senior rights |

### Generic CRUD

**Resources (36):** `analytics-events`, `application-history`, `applications`, `appointments`,
`assistant-conversations`, `audit-logs`, `blog-posts`, `brochures`, `campaigns`, `countries`,
`departments`, `documents`, `event-registrations`, `events`, `faqs`, `homepage-sections`,
`homepage-slides`, `leads`, `notifications`, `partnerships`, `professors`, `program-intakes`,
`program-requirements`, `programs`, `publications`, `research-areas`, `scholarships`,
`search-queries`, `services`, `shortlist-items`, `student-profiles`, `success-stories`,
`universities`, `university-rankings`, `users`, `verifications`.

**Read-only (5)** — POST, PUT and DELETE all return **405**: `audit-logs`, `analytics-events`,
`search-queries`, `assistant-conversations`, `application-history`. These are evidence, not
content; they are corrected by fixing the thing they describe, never by editing the record.

Every mutation writes an `audit_logs` row with the actor, action, entity and changed field list.

### Integrations

`/marketing/links` (authenticated) returns the Facebook page, Messenger, WhatsApp click-to-chat,
Ads Manager, Meta Business Suite, Google Ads, Search Console and Business Profile URLs, plus
ready-made UTM landing links.

| Endpoint | Method | Notes |
| --- | --- | --- |
| `/whatsapp/webhook` | GET / POST | GET is Meta's `hub.challenge` verification. POST validates the `X-Hub-Signature-256` HMAC and returns **401** on a bad signature before parsing anything |
| `/whatsapp/send` | POST | staff-only outbound send; no-ops with a clear message until real credentials are set |
| `/whatsapp/simulate` | POST | staff-only. Injects a fake inbound message so the conversation UI and routing can be exercised without the Business API |
| `/assistant/tools` | GET | the tool catalogue the assistant may call — each entry names the records it reads. Useful for auditing what the AI can reach |
| `/api/health` | GET | liveness; outside `/api/v1` |

### Rate limits

Fixed window, first rule to match wins, disabled when `RATE_LIMIT_ENABLED=false`. Every response
carries `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`; a rejection is a
**429** with `Retry-After` and a body of `{detail, retry_after_seconds}`.

| Rule | Budget | Rule | Budget |
| --- | --- | --- | --- |
| `login` | 10 / 5 min | `assistant` | 30 / h |
| `mfa` | 10 / 5 min | `brochure` | 20 / h |
| `leads` | 8 / h | `engines` (match, eligibility) | 60 / h |
| `appointments` | 6 / h | `documents` | 40 / h |
| `event_register` | 10 / h | `track` | 600 / h |
| `admin` | 600 / min | `public` | 300 / min |

---

## 7. Demo credentials and sample slugs

| Role | Email | Password |
| --- | --- | --- |
| Admin | `admin@scholara.com.bd` | `Admin@12345` |
| Senior counselor | `senior@scholara.com.bd` | `Senior@12345` |
| Counselor | `counselor@scholara.com.bd` | `Counselor@123` |
| Data manager | `data@scholara.com.bd` | `Data@12345` |

Country `united-states` · university `university-of-texas-at-arlington` ·
programme `university-of-texas-at-arlington-msc-artificial-intelligence` ·
scholarship `global-excellence-scholarship-united-kingdom` ·
faculty `dr-amara-chowdhury-university-of-texas-at-arlington` · service `career-counseling` ·
story `rahim-ahmed-university-of-texas-at-arlington-2026` ·
event `uk-university-admission-day-uttara` ·
post `fall-2027-usa-application-timeline-for-bangladeshi-students` ·
demo profile token `demo-student`.
