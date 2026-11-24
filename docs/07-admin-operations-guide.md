# 07 · Admin Operations Guide

For the people who run the platform day to day. `https://yourdomain/admin`.

---

## 1. Signing in and roles

| Role | Can do |
| --- | --- |
| **Admin** | Everything, including site settings, staff users and deletions |
| **Senior counselor** | All content and CRM, including deletions |
| **Data manager** | Education data and the verification queue |
| **Application officer** | Applications, documents, appointments |
| **Counselor** | Leads, appointments, student profiles, read-only data |

Higher roles inherit everything below them. Every change you make is written to the audit log with
your name — that is a feature, not surveillance.

Every staff account should have **MFA** enabled. Once it is, signing in is two steps: email and
password, then a 6-digit code from your authenticator app. You are given **8 recovery codes** when
you enrol — they are shown once, each works once, and they are the only way back in if you lose the
phone. Ten failed attempts locks the account; an admin unlocks it from **Security**.

### The navigation

- **Overview** — Dashboard, My desk, Notifications, Marketing & ads, Site settings
- **Work** — Applications board, Documents queue
- **Website** — homepage slides, homepage sections, services, success stories, events, blog, FAQs
- **Education data** — countries, universities, departments, programmes, requirements, intakes, scholarships, rankings, faculty, publications, research areas, partnerships, verification queue
- **CRM** — leads, appointments, student profiles, applications, documents, shortlists, brochures, event registrations, staff users
- **Marketing** — ad campaigns
- **Governance** — audit log, assistant transcripts, search queries, analytics events, application history
- **Admin only** — Operations (scheduled jobs), Security (MFA and lockouts)

---

## 2. The dashboard

Fourteen counters (new leads, leads in 7 days, active students, appointments, applications, offers,
visa cases, brochures, universities, programmes, scholarships, faculty, WhatsApp opens, records
needing verification), the lead pipeline by stage, lead sources, what students searched for,
upcoming application deadlines and recent activity.

If **Needs verification** is above zero an amber panel appears. Treat it as a work queue.

---

## 3. Running the rotating homepage

This is the feature students notice first. **Admin → Homepage slides.**

### How rotation works

The site loads the active slide set **once** and then cycles in the browser. A visitor who stays on
the homepage sees all nine seeded slides over 270 seconds without a single extra request.

### Fields that matter

| Field | What it does |
| --- | --- |
| `duration_seconds` | How long this slide stays on screen. Default **30**. Shorten a simple slide to 20, lengthen a dense one to 40 |
| `priority` | Higher shows earlier in the cycle. Use 100 for the campaign you are currently running |
| `status` | `ACTIVE` shows it · `PAUSED` hides it without deleting · `SCHEDULED` respects the dates below |
| `start_at` / `end_at` | Window for seasonal slides — an intake deadline, an event week |
| `day_start_hour` / `day_end_hour` | Optional hour-of-day window (enable `hero_time_scheduling_enabled` in settings first) |
| `focus` + `country_id` | Drives the content rail under the hero. A `COUNTRY` slide pointing at Canada makes the rail show Canadian universities, scholarships and stories |
| `cta_label` / `cta_url` + secondary pair | The two buttons. Keep the primary action concrete |
| `gradient` / `theme_color` / `background_media` / `mobile_media` | Appearance. A gradient always works; set `mobile_media` separately so a wide photo is not cropped badly on a phone |
| `highlights[]` / `stats[]` | The bullet list and the two small stat chips |
| `campaign_id` | Ties the slide to an ad campaign |

### Changing the global default

**Site settings → Homepage rotation**: `hero_rotation_seconds` (default 30),
`hero_rotation_enabled` (turn rotation off entirely and pin the top-priority slide),
`hero_time_scheduling_enabled`, `story_rotation_seconds`.

### A good cycle

1. The destination you are currently advertising (priority 100)
2. A funding or scholarship slide
3. A proof slide — a recent success story
4. A service slide — PhD supervisor matching, or visa guidance
5. The next event

Keep it to seven to nine slides. More than that and most visitors never see the end of the cycle.

### Homepage sections

**Admin → Homepage sections** controls everything below the hero: `position` sets the order,
`is_enabled` hides a section without deleting it. Out of season, disable Events instead of leaving
an empty block.

---

## 4. Adding education data

Order matters because of the foreign keys:

```
Country → University → Department → Programme → Requirements → Intakes
                    └→ Rankings    └→ Scholarships
                    └→ Faculty     └→ Publications
```

### Adding a university

1. **Countries** — make sure the country exists and is not `COMING_SOON`.
2. **Universities → New record.** Fill name, city, institution type, established year, student counts, campus, accreditation, overview, website and application portal.
3. **Provenance is mandatory**: `source_url` (the university's own page), `source_type = TIER_1_OFFICIAL_UNIVERSITY`, `retrieved_at`, `verified_at`, `verified_by`, `confidence`, `review_due_at`.
4. **Rankings** — one row per provider per edition year. Never merge providers.
5. **Departments** — then programmes under them.

### Adding a programme

1. **Programmes → New record** linked to the university, with level, award, duration, credits, study mode, language, campus, tuition and living cost.
2. **Programme requirements** — one row for this programme. Do not copy the university's general requirements; students are rejected over exactly this difference.
3. **Programme intakes** — one row per intake with opens / deadline / class start. **This is the highest-risk data on the platform.** Re-verify every cycle.
4. Set `is_published` only once requirements and at least one intake exist.

### The verification queue

**Education data → Verification queue** lists every field that needs attention.

- `NEEDS_REVIEW` — past its review date. Open the source, confirm, stamp `verified_at`.
- `OUTDATED` — the source has changed. Update the value; the old one stays in the verification record.
- `CONFLICTING` — two sources disagree. **Do not pick one and move on.** Re-check the official page; if they genuinely disagree, leave it conflicting so the site shows both with their sources.

Cadence: intakes every cycle; requirements and tuition annually and before each intake opens;
scholarships annually and 60 days before each deadline; rankings on publication; faculty annually.

### Faculty

`email_status` is the field that matters. Use `OFFICIAL_UNIVERSITY_EMAIL` only for an address on the
university's own page, `PUBLIC_PROFESSIONAL_EMAIL` for a lab site, conference page or paper, and
`UNAVAILABLE` otherwise — in which case leave the email blank and the site will show the
department's official contact route instead. **Never type an address you worked out from a
pattern.** Always fill `email_source_url` when an address is shown.

---

## 5. Content

| Resource | Notes |
| --- | --- |
| **Services** | `process[]` is the step-by-step the student sees; `benefits[]`, `documents[]`, `faqs[]` fill the rest of the page. `is_featured` + `priority` control the homepage block |
| **Success stories** | **A `consent_reference` is required before publishing.** Record the programme, intake, scholarship and visa outcome exactly — no rounding up |
| **Events** | Set mode, venue, capacity and times. `/events` only lists upcoming events; registration is built in |
| **Blog** | `body` is markdown (`##`, `###`, `-`, `1.`, `**bold**`, `[link](url)`, `> quote`). Set `reading_minutes` and `published_at`, then `is_published` |
| **FAQs** | `category` groups them, `scope` targets them at a page, `position` orders them. They also emit `FAQPage` structured data |

---

## 6. CRM

### Leads

Every enquiry from the site, the assessment, an event or WhatsApp lands here with its reference,
source, channel detail, UTM parameters, page path and consent flag.

Pipeline (12 stages): `NEW → CONTACTED → PROFILE_COLLECTED → COUNSELING → SHORTLIST → DOCUMENTS →
APPLICATION → OFFER → VISA → PRE_DEPARTURE → DEPARTED` — or `LOST`, with a reason.

House rules: move the stage the same day you act; never contact a lead whose `consent_contact` is
false; put what was actually agreed in the notes — the next counselor may be the one who follows up.

### Appointments

Booked from `/appointments` with service type, mode (office / online / phone), date, time and notes.
Confirm on WhatsApp with the counselor's name and what to bring. Office hours are a settings field,
so change them there rather than in any page text.

### Staff users

**CRM → Staff users.** Create accounts with the lowest role that works. Deactivate rather than
delete, so the audit trail stays intact. Enable MFA for every account.

---

## 7. My desk, applications and documents

### My desk (`/admin/counselor`)

Your own queue, not everyone's: today's follow-ups, your leads, your applications, your
appointments, documents waiting on your review, and your unread notifications, with KPIs across the
top. Start the day here.

### Applications board (`/admin/applications`)

A column per status, with counts. Moving a card writes an **append-only** `application_status_history`
row with your name, the old status, the new status and your note — so "who moved this and why" is
always answerable. The 16 statuses:

```
PLANNING → ELIGIBILITY_CHECKED → DOCUMENTS_PENDING → APPLICATION_PREPARATION → SUBMITTED →
UNDER_REVIEW → CONDITIONAL_OFFER → UNCONDITIONAL_OFFER → DEPOSIT → COE_I20_CAS →
VISA_PREPARATION → VISA_SUBMITTED → VISA_GRANTED → COMPLETED
```

plus the two terminal states `WITHDRAWN` and `REJECTED`. Each application carries a reference like
`APP-2026-000001`.

The progress bar a student sees is **position in this list**, not a prediction. It is
`(index + 1) / 14` for active applications and `0` for withdrawn or rejected ones. Never describe it
to a student as a chance of success.

### Documents queue (`/admin/documents`)

Everything students have uploaded, filtered by status: `UPLOADED` → `UNDER_REVIEW` → `ACCEPTED` or
`REJECTED`, with `EXPIRED` for documents that have aged out (police clearances, medicals, test
reports). Reject with a reason — the student sees it in their portal.

The default checklist is passport, bachelor transcript, IELTS TRMC, CV and SOP; a programme's own
`documents[]` list adds to it.

**Handling rules.** Documents are private. They are never in a public folder and never have a
permanent URL. Opening one mints a signed link that **expires in five minutes**; do not paste that
link into WhatsApp or email, because it is a key. Uploads are capped at 15 MB and are checked by
magic bytes, not by file extension — a `.pdf` that is not a PDF is refused.

### The student portal (`/my/{token}`)

Students see their own applications, progress, documents and checklist, shortlist, notifications and
brochures. The token in the URL is the credential, so treat it like a password: send it only to the
student, over a channel they already use with you. The page is `noindex` and never cached.

---

## 8. Governance screens

### Notifications (`/admin/notifications`)

Assignments, status changes, new documents and deadline reminders, unread first.

### Audit log (`/admin/audit`)

Every staff mutation: who, what action, which entity, which fields and when. Filterable by action
and entity type. It is append-only — the raw table under **Governance** cannot be edited or deleted,
and neither can analytics events, search queries, assistant transcripts or application history. If
one of those records is wrong, fix the thing it describes; the history stays.

### Assistant transcripts

Every AI answer with its intent, tools used, sources, latency and — most importantly — whether it
was **grounded** and whether it **escalated** to a human. The ungrounded rate is the number to
watch: a rising rate means students are asking about things the database does not contain yet. Read
the ungrounded questions as a content backlog.

### Operations (`/admin/operations`, admin only)

The scheduled jobs and their last run, each with a **Run now** button:

| Job | Every | What it does |
| --- | --- | --- |
| `reindex_knowledge` | 1 h | rebuilds the assistant's index from current records |
| `campaign_metrics` | 30 min | refreshes ad spend, clicks and attributed leads |
| `verification_sweep` | 6 h | flips records past `review_due_at` to `NEEDS_REVIEW` |
| `deadline_reminders` | 12 h | intakes and scholarships inside 30 / 14 / 7 days |
| `data_quality_digest` | 7 d | the weekly summary for the data manager |

Run `reindex_knowledge` by hand after a bulk data import, otherwise the assistant will answer from
yesterday's records.

### AI providers (same screen)

The assistant can be powered by **Grok, Mistral or Gemini** — whichever the consultancy holds a key
for. The panel lists all three with their priority order, whether a key is set, the model in use
and the last error, and gives each a **Test** button that does a live round trip and reports the
latency or the exact failure.

Things worth knowing:

* **You do not need all three.** One key is enough. The others sit idle as automatic backups.
* **Order matters.** They are tried top to bottom; the first that answers is used and the rest are
  never called, so you are only billed for one.
* **No key is fine.** The assistant still works, answering from templates built on verified
  records rather than generated prose. Nothing breaks.
* **Keys are not editable here.** They live in `backend/.env` and are never displayed in the admin
  or sent to the browser. Changing one is a server task, not a CMS task.
* If a test fails with "model not found", the provider has renamed the model. Set the matching
  `*_MODEL` variable to a current name — no code change, no release.

Whichever provider answers, the reply is scrubbed by the same guardrails before a student sees it
(document 05 §6), so switching provider cannot change what the platform is allowed to claim.

### Security (`/admin/security`, admin only)

MFA enrolment state per account, remaining recovery codes, lockouts and recent sign-ins. Disabling
someone's MFA is itself audit-logged.

---

## 9. Marketing & ads

See document 06 for the full playbook. Day to day:

- **Integrations table** — green means the ID is set, amber means the script is not loading.
- **Channel links** — one-click access to the Facebook page, Messenger, WhatsApp, Ads Manager, Meta Business Suite, Google Ads, Search Console and Business Profile.
- **UTM link builder** — build the exact URL before you paste it into an ad. Never run an ad to a bare URL; you lose the attribution permanently.
- **Campaigns** — budget, spend, clicks and the leads each produced. Attach `campaign_id` to a homepage slide so the hero matches the ad.

---

## 10. Site settings

One record drives the whole site.

| Group | What to keep accurate |
| --- | --- |
| **Brand** | Name, tagline, about, logo, colours |
| **Office & contact** | Address, hours, phones, emails, map embed — these appear on every page and in the structured data |
| **WhatsApp** | Number, default message, on/off |
| **Facebook & social** | Page URL and ID, page plugin, Messenger, other networks |
| **Google & Meta tracking** | GA4, GTM, Google Ads, Search Console, Meta Pixel, Meta domain, TikTok, Bing, consent requirement |
| **SEO** | Default title and description, OG image, **`site_url`** (drives canonicals and the sitemap — must be the production domain) |
| **Homepage rotation** | Default duration, enable/disable, time scheduling |

Changes appear on the public site within about a minute (ISR revalidation).

---

## 11. Editing records

Each resource page is a searchable, paginated table. **Edit** opens a side panel with a generated
form; **Raw JSON** is there for array and object fields when the form is awkward. Arrays and nested
objects are edited as JSON and validated when the field loses focus.

Deleting requires senior rights and cannot be undone. Prefer `status = PAUSED`, `is_published =
false` or `is_active = false`.

---

## 12. Weekly routine

**Daily** — My desk first: follow-ups, new leads, today's appointments, event registrations, the
documents queue, and anything in the verification queue marked `CONFLICTING`.

**Weekly** — clear the `NEEDS_REVIEW` backlog, check intake deadlines inside 60 days, review
campaign spend against leads produced, refresh the top homepage slide, publish one article from the
top searches list, and read the week's ungrounded assistant questions as a content backlog.

**Monthly** — re-verify tuition for featured universities, audit scholarship deadlines, review the
pipeline for stalled leads, check Search Console coverage, add the month's success stories (with
consent references).

**Each intake cycle** — re-verify **every** programme intake, refresh requirements for featured
programmes, rebuild the homepage cycle around the next intake, archive past events.
