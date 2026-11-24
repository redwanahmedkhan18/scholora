# 03 · Data Model

42 tables across three modules, owned by Alembic (`alembic/versions/20261003_dc186cc99406_initial_schema.py`).
SQLite in this build; PostgreSQL + pgvector in production. A 43rd table, `alembic_version`, is
created and managed by Alembic itself.

---

## 1. The mixins

Every model composes from three mixins in `app/models/base.py`.

### `TimestampMixin`
`created_at`, `updated_at`.

### `SeoMixin`
`seo_title`, `seo_description`, `og_image`, plus the canonical `slug` where applicable.

### `VerificationMixin` — the important one

Applied to every table that holds a fact a student might act on: `universities`,
`university_rankings`, `programs`, `program_requirements`, `program_intakes`, `scholarships`,
`professors`.

| Column | Meaning |
| --- | --- |
| `source_url` | The exact page the value came from. |
| `source_type` | `TIER_1_OFFICIAL_UNIVERSITY`, `TIER_2_GOVERNMENT`, `TIER_3_RANKING_PROVIDER`, `TIER_4_PARTNER_DOCUMENT`, `TIER_5_OTHER`. |
| `retrieved_at` | When the value was pulled. |
| `verified_at` | When a human last confirmed it. |
| `verified_by` | Which staff member. |
| `confidence` | `VERIFIED` · `NEEDS_REVIEW` · `OUTDATED` · `CONFLICTING` · `UNVERIFIED`. |
| `review_due_at` | When it must be re-checked. |

A record with no `source_url` can never be `VERIFIED`. See document 05 for the rules this enforces.

---

## 2. Education module (`app/models/education.py`)

```
countries ──┬── cities
            ├── universities ──┬── university_rankings      (provider × edition_year)
            │                  ├── university_partnerships  (INTERNAL ONLY)
            │                  ├── departments ── programs
            │                  └── professors ── professor_publications
            ├── scholarships (also linked to a university)
            └── research_areas
                                programs ──┬── program_requirements  (1:1)
                                           └── program_intakes       (1:N)
```

| Table | Notable columns |
| --- | --- |
| `cities` | `name`, `slug`, `country_id`. Lookup table behind the city filter on university search. |
| `countries` | `name`, `slug`, `iso2`, `region`, `flag_emoji`, `tagline`, `currency`, `tuition_min/max`, `living_cost_min/max`, `popular_subjects[]`, `intakes[]`, `why_study[]`, `visa_summary`, `visa_resources[{label,url}]`, `work_rights`, `post_study_work`, `student_life`, `faqs[]`, `status` (`ACTIVE`/`COMING_SOON`), `is_featured`, `priority`. |
| `universities` | `name`, `slug`, `city`, `institution_type`, `established_year`, `student_count`, `international_students`, `campus`, `accreditation`, `overview`, `student_life`, `accommodation`, `faqs[]`, `website`, `application_portal`, `is_featured`, `partner_status`, + `VerificationMixin`. |
| `university_rankings` | `provider`, `edition_year`, `rank_overall`, `rank_national`, + verification. **One row per provider per year.** Providers are never averaged. |
| `university_partnerships` | `status`, `representative`, `commission_terms`, `agreement_expiry`. **Never serialized to a public endpoint.** |
| `faculties` | `university_id`, `name`, `slug`, `kind` (FACULTY/SCHOOL/COLLEGE/INSTITUTE), `dean_name`, `dean_title`, `website`, `email`, `overview`, `research_areas`, `position`, SEO + verification fields, `UNIQUE (university_id, name)`. The Faculty/School level of the hierarchy. |
| `departments` | `university_id`, `faculty_id` → `faculties`, `name`, `slug`, `website`, `overview`, `research_areas`. |

Together these implement the specified four-level hierarchy: **University → Faculty/School →
Department → Programme**. The division was free text on `departments` until migration
`55b14f98dd2d` promoted it to its own table with a dean, a website and provenance of its own.
| `programs` | `name`, `slug`, `degree_level` (`BACHELOR`/`MASTER`/`PHD`/`DIPLOMA`/`FOUNDATION`), `award`, `duration_months`, `credits`, `study_mode`, `language`, `campus`, `specializations[]`, `overview`, `career_outcomes`, `application_procedure`, `tuition_annual`, `living_cost_annual`, `currency`, `is_published`, + verification. |
| `program_requirements` | Academic: `min_qualification`, `min_cgpa`, `cgpa_scale`, `relevant_majors[]`, `required_subjects[]`, `prerequisites[]`, `degree_equivalency`, `study_gap_policy`, `max_study_gap_years`, `work_experience_years`, `research_experience`. English: `ielts_overall`, `ielts_band_min`, `toefl_ibt`, `pte_academic`, `duolingo`, `cambridge`, `moi_accepted`, `other_accepted_tests[]`. Tests: `gre_required`, `gre_score`, `gmat_required`, `gmat_score`. Plus `documents[]`, `notes`, verification. |
| `program_intakes` | `label`, `term`, `month`, `year`, `status` (`OPEN`/`CLOSING_SOON`/`CLOSED`/`ANNOUNCED`), `application_opens`, `application_deadline`, `class_start`, `notes`, verification. **Per programme, not per university.** |
| `scholarships` | `name`, `slug`, `provider`, `degree_levels[]`, `disciplines[]`, `amount_type` (`FIXED`/`FULL`/`PERCENTAGE`), `amount_value`, `percentage`, `currency`, `covers_tuition`, `covers_living`, `stipend`, `eligibility_summary`, `eligible_nationalities[]`, `min_cgpa`, `cgpa_scale`, `min_ielts`, `other_criteria[]`, `deadline`, `application_method`, `is_featured`, verification. |
| `research_areas` | Controlled vocabulary for faculty matching. |
| `professors` | `full_name`, `slug`, `title`, `photo_url`, `department_id`, `research_interests[]`, `research_keywords[]`, `lab_name`, `lab_url`, `accepting_phd`, `email`, **`email_status`** (`OFFICIAL_UNIVERSITY_EMAIL` / `PUBLIC_PROFESSIONAL_EMAIL` / `UNAVAILABLE`), `email_source_url`, `bio`, verification. |
| `professor_publications` | `title`, `venue`, `year`, `url`, `coauthors[]`. The evidence behind a research match. |

---

## 3. Content module (`app/models/content.py`)

| Table | Purpose |
| --- | --- |
| `site_settings` | Single row (id=1). Brand, colours, logo, about, office address/hours, phones[], emails[], map embed, WhatsApp (enabled/number/default message), Facebook (page URL/ID/plugin/Messenger), social links, GA4/GTM/Google Ads/Search Console/Meta Pixel/Meta domain/TikTok/Bing IDs, consent requirement, SEO defaults, `site_url`, `organization_type`, `founding_year`, and the rotation settings (`hero_rotation_enabled`, `hero_rotation_seconds`, `hero_time_scheduling_enabled`, `story_rotation_seconds`). |
| `homepage_slides` | `key`, `eyebrow`, `title`, `subtitle`, `description`, `highlights[]`, `background_media`, `mobile_media`, `media_type`, `gradient`, `theme_color`, `country_id`, `focus` (`COUNTRY`/`SCHOLARSHIP`/`STORY`/`PHD`/`SERVICE`/`EVENT`), `focus_ref`, primary and secondary CTA label/URL, `stats[]`, **`duration_seconds`**, `priority`, `status`, `start_at`, `end_at`, `day_start_hour`, `day_end_hour`, `campaign_id`, `impressions`, `clicks`. |
| `homepage_sections` | `key`, `title`, `subtitle`, `position`, `is_enabled`, `config`. Controls what appears below the hero and in what order. |
| `services` | `title`, `slug`, `icon`, `summary`, `category`, `is_featured`, `priority`, `hero_image`, `status`, `description`, `process[{step,title,detail}]`, `benefits[]`, `documents[]`, `countries[]`, `faqs[]`, SEO. |
| `success_stories` | `student_name`, `slug`, `headline`, `photo_url`, university/programme/degree/intake/year, `scholarship`, `visa_result`, `testimonial`, `journey[]`, `country_id`, `is_published`, `is_featured`, **`consent_reference`**, `published_at`. |
| `events` | `title`, `slug`, `speaker`, `start_at`, `end_at`, `venue`, `mode` (`ONLINE`/`OFFLINE`/`HYBRID`), `capacity`, `banner_url`, `status`, `is_featured`, `country_id`, `university_id`, `registration_url`, `description`. |
| `event_registrations` | `event_id`, `full_name`, `phone`, `email`, `preferred_country`, `degree_level`. |
| `blog_posts` | `title`, `slug`, `excerpt`, `cover_image`, `author`, `category`, `tags[]`, `reading_minutes`, `is_published`, `published_at`, `body` (markdown), SEO. |
| `faqs` | `question`, `answer`, `category`, `scope`, `position`. |
| `ad_campaigns` | `name`, `channel` (`FACEBOOK`/`GOOGLE`/`…`), `objective`, `status`, `landing_path`, `utm_source/medium/campaign/content`, `total_budget`, `spend_to_date`, `currency`, `impressions`, `clicks`, `leads`, `start_at`, `end_at`. **Spend is never public.** |

---

## 4. CRM module (`app/models/crm.py`)

| Table | Purpose |
| --- | --- |
| `users` | Staff and student accounts. `email`, `password_hash` (pbkdf2), `full_name`, `role`, `is_active`, `mfa_enabled`, `last_login_at`. |
| `student_profiles` | The full profile from the assessment: identity, academics (qualification, major, institution, CGPA + scale, graduation year, study gap, backlogs, completed subjects), English and standardised tests, MOI availability, experience and research, budget and funding, preferences, career goal, consent flags, `token` for retrieval. |
| `leads` | `reference`, name/phone/email, `message`, interest country/degree/subject, `intake`, `stage`, `source`, `channel_detail`, `utm{}`, `page_path`, `consent_contact`, `assigned_to`, `profile_id`. |
| `appointments` | `reference`, name/phone/email, `service_type`, `counselor_name`, `scheduled_date`, `scheduled_time`, `mode`, `status`, `notes`, `utm{}`. |
| `applications` | `student_profile_id`, `program_id`, `intake_id`, `stage`, `submitted_at`, `decision`, `decision_at`. |
| `application_status_history` | Append-only stage transitions with actor and timestamp. |
| `documents` | `student_profile_id`, `doc_type`, `storage_key`, `mime`, `size`, `status`, `reviewed_by`. **Private storage; access only via short-lived signed URLs.** |
| `brochures` | `token`, `version`, `student_name`, `degree_level`, `target_intake`, `payload` (the full rendered JSON), `channel`, `created_at`. |
| `whatsapp_contacts` / `whatsapp_conversations` / `whatsapp_messages` | Click-to-chat records now; ready for the WhatsApp Business API later. |
| `data_verifications` | The verification queue: `entity_type`, `entity_id`, `field_name`, `field_value`, `source_url`, `source_type`, `status`, `conflicting_value`, `conflicting_source_url`, `assigned_to`, `review_due_at`, `resolved_at`. **Conflicts live here instead of overwriting the record.** |
| `search_queries` | What students actually searched: `q`, `country`, `subject`, `degree_level`, `filters`, `results_count`, `session_id`. Feeds "what students search for" on the dashboard and content planning. |
| `analytics_events` | First-party events: `name`, `path`, `entity_type`, `entity_id`, `utm{}`, `meta{}`, `session_id`. |
| `audit_logs` | `actor_id`, `actor_email`, `action`, `entity_type`, `entity_id`, `detail{}`. Written on every staff mutation. |
| `notifications` | `user_id`, `kind`, `title`, `body`, `read_at`. |
| `shortlist_items` | `student_profile_id`, `entity_type` (`university`/`program`/`scholarship`/`faculty`), `entity_id`, `slug`, `label`, `meta{}`, `note`, `priority`. The server-side mirror of the browser's `scholara.shortlist`; synced on portal sign-in. |
| `assistant_conversations` | `session_id`, `student_profile_id`, `question`, `answer`, `intent`, `tools_used[]`, `sources[]`, **`grounded`**, **`escalated`**, `latency_ms`, `rating`. Every assistant turn is logged so the ungrounded rate can be audited at `/admin/assistant/conversations`. |
| `knowledge_chunks` | `entity_type`, `entity_id`, `slug`, `title`, `content`, `url_path`, `source_url`, `source_type`, `verified_at`, `confidence`, `token_count`, `lexical_vector`, `embedding`. The retrieval index — 117 chunks built only from our own verified records, rebuilt by the `reindex_knowledge` job. `lexical_vector` drives BM25 today; `embedding` is reserved for pgvector. |

---

## 5. Enumerations (canonical values)

| Enum | Values |
| --- | --- |
| **Confidence** | `VERIFIED`, `NEEDS_REVIEW`, `OUTDATED`, `CONFLICTING`, `UNVERIFIED` |
| **Source type** | `TIER_1_OFFICIAL_UNIVERSITY`, `TIER_2_GOVERNMENT`, `TIER_3_RANKING_PROVIDER`, `TIER_4_PARTNER_DOCUMENT`, `TIER_5_OTHER` |
| **Eligibility verdict** | `MEETS`, `DOES_NOT_MEET`, `PARTIALLY_MEETS`, `REQUIRES_REVIEW`, `INFORMATION_UNAVAILABLE` |
| **Email status** | `OFFICIAL_UNIVERSITY_EMAIL`, `PUBLIC_PROFESSIONAL_EMAIL`, `UNAVAILABLE` |
| **Degree level** | `BACHELOR`, `MASTER`, `PHD`, `DIPLOMA`, `FOUNDATION` |
| **Intake status** | `OPEN`, `CLOSING_SOON`, `CLOSED`, `ANNOUNCED` |
| **Budget status** | `WITHIN_STATED_BUDGET`, `SLIGHTLY_ABOVE_BUDGET`, `ABOVE_STATED_BUDGET`, `BUDGET_NOT_PROVIDED` |
| **Lead stage** | `NEW`, `CONTACTED`, `COUNSELING`, `SHORTLIST`, `DOCUMENTS`, `APPLICATION`, `OFFER`, `VISA`, `ENROLLED`, `LOST` |
| **Role** | `ADMIN`, `SENIOR_COUNSELOR`, `DATA_MANAGER`, `APPLICATION_OFFICER`, `COUNSELOR`, `STUDENT` |
| **Country status** | `ACTIVE`, `COMING_SOON` |
| **Slide focus** | `COUNTRY`, `SCHOLARSHIP`, `STORY`, `PHD`, `SERVICE`, `EVENT` |

## 6. Fields that must never reach a public endpoint

`university_partnerships.*` (commission terms, agreement expiry) · `ad_campaigns.total_budget`,
`spend_to_date` · `professors.email` when `email_status = UNAVAILABLE` · `users.password_hash` ·
`documents.storage_key` · `leads.*` and `appointments.*` beyond the submitter's own reference ·
`student_profiles.*` except through the owner's token.

This is enforced in `app/api/serializers.py`, which is an explicit allow-list rather than a model
dump — the reason Pydantic read-schemas were rejected.

## 7. Seeded demo dataset

14 countries · 23 universities · 55 programmes (with requirements and intakes) · 15 scholarships ·
10 professors with publications · 16 services · 14 success stories · 5 events · 6 blog posts ·
12 FAQs · 9 homepage slides · 14 homepage sections · 2 active campaigns · 4 staff users · 6 demo
leads across every pipeline stage.
