# 01 · Product Requirements

**Product:** Scholara — Global Education Intelligence & Application Platform
**Owner:** an international education consultancy, Uttara, Dhaka, Bangladesh
**Type:** single-organisation platform. Not a SaaS. Not multi-tenant. One brand, one database, one admin.

---

## 1. Problem

A Bangladeshi student deciding where to study abroad is forced to assemble the decision from
fragments: a consultancy's brochure, a Facebook post, a university page that may be two years out of
date, a forum thread, and a counselor's memory. The costly failures are predictable:

- applying to a programme whose CGPA or English requirement they never actually met;
- budgeting tuition and discovering living cost, health insurance and deposits later;
- missing a programme-specific deadline because they read the university's *general* intake calendar;
- believing a "90% visa success" claim that nobody can substantiate;
- for PhD applicants, emailing a professor at an address somebody guessed.

Agencies compound this. Their sites market services; they do not expose data a student can check.

## 2. Product thesis

Be the consultancy whose website is the most **checkable**. Publish the requirement, the fee, the
deadline — and next to each one, the URL it came from, what kind of source that is, when it was
retrieved, when it was last verified, and how confident we are. A student who can verify us trusts
us; a student who trusts us books the counseling session.

## 3. Scope

### In scope

Website + WhatsApp + counselor office as the three channels. Public research experience, student
tools (assessment, eligibility, costing, matching, shortlist, comparison, brochure), PhD supervisor
intelligence, lead/CRM pipeline, events and appointments, content marketing, admin CMS, marketing &
ads control centre, analytics, AI assistant.

### Explicitly out of scope

Multi-tenancy, reselling the platform, white-labelling, a student-facing mobile app (the site is
mobile-first instead), and any feature that requires predicting an admission or visa decision.

## 4. Personas

| Persona | Needs |
| --- | --- |
| **Undergraduate applicant** (HSC/A-level) | Which countries are affordable; what IELTS they need; whether their result is enough; what the first year really costs. |
| **Master's applicant** (working or fresh graduate) | Programme-level requirements against their exact CGPA and backlogs; study-gap policy; scholarships; intake deadlines. |
| **PhD applicant** | Supervisors whose published research matches their interests, with evidence; funding; whether the professor is accepting students; a contactable, *sourced* email. |
| **Parent / sponsor** | Total annual cost, funding routes, the consultancy's legitimacy. |
| **Counselor** (staff) | A student's full profile, eligibility report, shortlist and documents in one place; a pipeline that does not lose leads. |
| **Data manager** (staff) | A verification queue; the ability to record conflicting sources rather than overwrite them. |
| **Marketing manager** (staff) | Facebook page + ads, pixels, UTM attribution from ad click to enrolled student, homepage slides that match the campaign. |
| **Admin** | Everything, plus audit logs and role management. |

## 5. Functional requirements

### 5.1 Rotating homepage — the signature requirement

| ID | Requirement |
| --- | --- |
| HP-1 | The homepage content changes continuously. Default cycle **30 seconds** per slide; `duration_seconds` is stored per slide and may override the default. |
| HP-2 | Rotation is **client-side**. The full active slide set is fetched once with the page. No page reload, and no new server request per cycle. |
| HP-3 | The next slide is preloaded (media and country-linked content) before it is shown. |
| HP-4 | Slides are admin-controlled: priority, `ACTIVE/PAUSED/SCHEDULED` status, start and end dates, and optional day-hour windows. |
| HP-5 | The active hero slide drives the country-linked rail beneath it — featured universities, scholarships and success stories follow the slide's country. |
| HP-6 | Manual controls (previous / next / pause / dot navigation) and keyboard access are available; the timer pauses on hover/focus. |
| HP-7 | `prefers-reduced-motion` disables the animated transitions; content still rotates. |
| HP-8 | A campaign may be attached to a slide so that paid traffic lands on a hero that matches the ad. |

Seeded: 9 slides × 30s = a 270-second full cycle.

### 5.2 Discovery and data

| ID | Requirement |
| --- | --- |
| D-1 | **Country discovery**: destination list and a page per country with cost ranges, intakes, visa summary and resources, work rights, post-study work, student life, FAQs, and linked universities/programmes/scholarships/stories/events. Countries may be marked `COMING_SOON`. |
| D-2 | **University search** filterable by name, country, city, degree level, subject, tuition range, ranking provider, scholarship availability, intake, maximum IELTS, maximum CGPA and featured status, with sorting and pagination. |
| D-3 | **University profile**: overview, institution type, established year, student and international-student counts, campus, accreditation, rankings (one row per provider per edition year), departments, programmes grouped by level, scholarships, faculty, success stories, FAQs, official website and application portal. |
| D-4 | **Programme hierarchy**: University → Faculty/School → Department → Programme. |
| D-5 | **Programme profile**: award, duration, credits, study mode, language, campus, specialisations, overview, career outcomes, application procedure, full requirement set, intakes, cost estimate, scholarships, related programmes and faculty. |
| D-6 | **Requirements are programme-specific**, never inherited from the university: academic (minimum qualification, CGPA + scale, relevant majors, required subjects, prerequisites, degree equivalency, study-gap policy, work experience, research experience), English (IELTS overall and minimum band, TOEFL, PTE, Duolingo, Cambridge, MOI acceptance, other accepted tests), standardised tests (GRE/GMAT required + score), and a document checklist. |
| D-7 | **Intake engine**: intakes are per programme with term, month, year, status, application opens, application deadline, class start and notes — not a shared university calendar. |
| D-8 | **Rankings** store provider, edition year, overall rank, national rank and source. Providers are never merged into "the" rank. |
| D-9 | **Scholarship engine**: provider, degree levels, disciplines, amount type (`FIXED` / `FULL` / `PERCENTAGE`), value, currency, coverage (tuition / living / stipend), eligibility summary, eligible nationalities, minimum CGPA and IELTS, other criteria, deadline and application method. |
| D-10 | **Search**: one query across universities, programmes, countries, scholarships and faculty. |

### 5.3 Student tools

| ID | Requirement |
| --- | --- |
| S-1 | **Student profile** capturing academics, English and standardised tests, experience and research, budget and funding, and preferences. |
| S-2 | **Eligibility engine** returning a per-criterion comparison. Verdicts are limited to `MEETS`, `DOES_NOT_MEET`, `PARTIALLY_MEETS`, `REQUIRES_REVIEW`, `INFORMATION_UNAVAILABLE`. |
| S-3 | **No fabricated probabilities.** No "95% chance of admission", ever. See document 05. |
| S-4 | **Cost engine**: tuition + living + insurance + fees, an annual total, one-time costs separated, and a comparison against the student's stated budget (`WITHIN_STATED_BUDGET`, `SLIGHTLY_ABOVE_BUDGET`, …). |
| S-5 | **Matching engine** returning ranked programmes with a score, the eligibility breakdown, the cost position, explicit reasons per factor, available scholarships, the next intake and research alignment. Subject filtering is *soft* — a near-miss is down-weighted, not discarded. |
| S-6 | **Scholarship personalisation**: scholarships whose stated criteria the student appears to meet, phrased exactly that way. |
| S-7 | **Shortlist** of programmes, universities and scholarships (browser-local; can be handed to a counselor). |
| S-8 | **Comparison** of up to four programmes side by side, with identical field definitions in every column and a source row. |
| S-9 | **Brochure generator**, degree-specific (Bachelor / Master / PhD), personalised, private-token URL, printable to PDF, carrying the eligibility table, cost table, requirement set, document checklist, intakes, funding, and a full source list. |

### 5.4 PhD intelligence

| ID | Requirement |
| --- | --- |
| P-1 | Faculty records with title, department, research interests and keywords, lab name and URL, whether they are accepting PhD students, and publications. |
| P-2 | **Research matching** from the student's stated interests, returning matched interests and the **evidence** behind the match. |
| P-3 | **Email policy**: an address appears only if it came from an official university page or a public professional source, and is labelled `OFFICIAL_UNIVERSITY_EMAIL`, `PUBLIC_PROFESSIONAL_EMAIL` or `UNAVAILABLE`. Addresses are **never inferred** from a pattern. |

### 5.5 Consultancy operations

| ID | Requirement |
| --- | --- |
| C-1 | **Lead management** with a staged pipeline (`NEW → CONTACTED → COUNSELING → SHORTLIST → DOCUMENTS → APPLICATION → …`), source, channel detail, UTM attribution and consent flags. |
| C-2 | **Applications** with status history. |
| C-3 | **Documents** in private storage, reachable only through short-lived signed URLs. |
| C-4 | **Appointments** — office, online or phone — with service type, counselor, date/time and a reference. |
| C-5 | **Events** with registration, capacity, mode and venue. |
| C-6 | **Counselor and admin dashboards**: counters, pipeline, lead sources, searches students actually ran, upcoming deadlines, verification backlog. |
| C-7 | **Notifications** and **audit logs** for every staff mutation. |
| C-8 | **WhatsApp**: click-to-chat with a prefilled, page-aware message; contact, conversation and message records for when the Business API is connected. |

### 5.6 Content & marketing

| ID | Requirement |
| --- | --- |
| M-1 | Admin CMS over homepage slides and sections, countries, universities, departments, programmes, requirements, intakes, scholarships, rankings, faculty, publications, partnerships, services, success stories, events, blog, FAQs, campaigns, leads, appointments, users and the verification queue. |
| M-2 | **Facebook page linking** — page URL/ID stored in settings, surfaced as a page panel, a Messenger button and footer links. |
| M-3 | **Contact options** — office address and map, phones, emails, WhatsApp, Messenger, Facebook, an enquiry form and an appointment booking form. |
| M-4 | **Ads capability** — Meta Pixel + Conversions API, GA4, GTM, Google Ads conversion ID/label, Search Console verification, a UTM builder, campaign records with budget/spend/clicks, and campaign-linked homepage slides. |
| M-5 | Success stories require a recorded **consent reference** before publication. |

### 5.7 AI assistant

| ID | Requirement |
| --- | --- |
| A-1 | The assistant answers from the platform's own data via tool calls and retrieval. |
| A-2 | It **must not invent university data**. If a figure is not in the database, it says so and points to the official source. |
| A-3 | It never states an admission probability and never produces an unsourced email address. |

## 6. Non-functional requirements

| Area | Requirement |
| --- | --- |
| **Mobile-first** | Designed for a mid-range Android phone on mobile data. Bottom navigation: Home, Search, Shortlist, WhatsApp, Profile. Floating WhatsApp button on every public page. |
| **Performance** | Detail pages pre-rendered and revalidated (300s detail, 120–300s lists). The homepage rotates without additional requests. |
| **SEO** | The URL architecture in §5 of document 06, canonical URLs on every page, per-entity metadata, JSON-LD (`EducationalOrganization`, `WebSite`, `CollegeOrUniversity`, `Course`, `FAQPage`, `Event`, `BlogPosting`, `ContactPage`), a generated `sitemap.xml` from live data and a `robots.txt` that excludes `/admin`, `/brochure`, `/search`, `/shortlist` and `/compare`. |
| **Accessibility** | Semantic landmarks, labelled controls, visible focus, reduced-motion support, sufficient contrast. |
| **Security** | HTTPS, RBAC with a role hierarchy, staff MFA, private document storage with short-lived signed URLs, audit logs, webhook signature validation, rate limiting, security headers. |
| **Privacy** | Explicit contact consent on every form; marketing consent separate; PII hashed (SHA-256) before any server-side conversion event leaves the platform. |
| **Data integrity** | Document 05 is binding. |

## 7. Acceptance criteria (the ones that decide "done")

1. The homepage changes every 30 seconds without a page reload and without a request per cycle, and the interval is editable in the admin.
2. Every student-facing figure on a university, programme, requirement, intake, scholarship, ranking and faculty record displays a source, a source type, a verification date and a confidence state.
3. An eligibility result never shows a number that resembles an admission probability.
4. A scholarship result is phrased as "stated eligibility criteria appear to be met".
5. A faculty email is shown only with one of the three defined labels, and `UNAVAILABLE` is a normal, visible outcome.
6. Two sources that disagree produce a `CONFLICTING` record that is surfaced in the verification queue, not silently overwritten.
7. No student-facing list in the frontend is hard-coded; deleting the row removes it from the site.
8. The site is usable one-handed on a 360 px-wide screen, with WhatsApp always one tap away.
9. `sitemap.xml` is generated from live data and every detail page has a canonical URL.
10. Leads carry their UTM parameters from first click through to the pipeline.

### How each one is proved

Nine of the ten are enforced by the automated suite (`cd backend && python -m pytest`, 88 tests);
the tenth is checked by hand on a phone.

| # | Proved by |
| --- | --- |
| 1 | `test_api.py::test_homepage_rotation_contract` — one call returns every slide with its own `duration_seconds`, a global default of 30 and a `total_cycle_seconds`, so the client can cycle without asking again |
| 2 | `test_data_integrity.py::test_every_published_university_carries_provenance`, `::test_program_detail_exposes_source_and_verification` |
| 3 | `test_data_integrity.py::test_matching_never_emits_a_probability`, `::test_assistant_never_predicts_admission`, `::test_no_public_listing_claims_a_probability` — a regex battery run across the public listings, the match engine and the assistant |
| 4 | `test_data_integrity.py::test_scholarship_language_is_criteria_based` |
| 5 | `test_data_integrity.py::test_faculty_emails_are_labelled_and_never_inferred` |
| 6 | `test_data_integrity.py::test_conflicting_sources_are_surfaced_not_overwritten` |
| 7 | `test_api.py::test_homepage_sections_are_ordered_and_data_driven`, `::test_every_admin_resource_lists` |
| 8 | Manual check at 360 px. The bottom navigation and the floating WhatsApp button are in `site-chrome.tsx` and render on every public route |
| 9 | `scripts/smoke-test.sh` fetches `/sitemap.xml` (32.7 KB, generated from live rows) and 40 routes |
| 10 | `test_api.py` lead tests plus `scripts/verify-new.sh`, which posts a UTM-tagged lead and reads the parameters back off the pipeline record |

Criterion 3 is the one to re-run after **any** change to the engines or the assistant. It is the
difference between a search tool and a platform that makes promises it cannot keep.
