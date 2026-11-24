# 06 · SEO, Marketing & Ads Playbook

Covers the three things the brief added on top of the platform itself: **linking the Facebook page**,
**contacting the consultancy**, and **running ads** — plus the SEO architecture that makes the
organic side work.

---

## 1. The strategy in one paragraph

A consultancy website usually has about twelve indexable pages and competes for one phrase
("study abroad consultancy Dhaka") against everyone else. Scholara instead publishes a page for
every country, university, programme, scholarship, supervisor, service and story — hundreds of
pages that each answer one long-tail question a student actually types ("MSc artificial
intelligence UTA IELTS requirement", "fully funded scholarship UK Bangladeshi students"). Paid
traffic then does the job it is good at: pushing high-intent visitors into the profile assessment,
where they become attributable leads.

---

## 2. URL architecture

```
/                                                  home (rotating hero)
/study-destinations                                destination index
/study-destinations/{country}                      e.g. /study-destinations/united-kingdom
/universities                                      search
/universities/{country}/{university}               e.g. /universities/united-states/arizona-state-university
/programs                                          search
/programs/{program}                                e.g. /programs/…-msc-artificial-intelligence
/scholarships  /scholarships/{scholarship}
/phd                                               supervisor discovery
/faculty/{professor}
/services  /services/{service}
/success-stories  /success-stories/{story}
/events  /blog  /blog/{post}  /faqs
/profile-assessment  /contact  /appointments
/compare  /shortlist  /search                      noindex (tools, not content)
/brochure/{token}                                  noindex, nocache, private
/admin/*                                           noindex, disallowed
```

The university URL carries the country segment deliberately: it mirrors how students search
("university of X in Canada") and it keeps the path hierarchical for crawlers.

---

## 3. On-page SEO

| Element | Implementation |
| --- | --- |
| Metadata | `generateMetadata` per route, pulling the entity's own `seo_title` / `seo_description` with a sensible fallback. Title template `%s | Scholara`. |
| Canonical | `alternates.canonical` on every page. |
| Open Graph / Twitter | Site-wide defaults from settings, overridden per entity; articles emit `type: article` with `publishedTime` and author. |
| Headings | One `h1` per page, descriptive `h2`/`h3` per section. |
| Internal linking | Every detail page links to its country, university, related programmes, relevant scholarships and matching faculty. The homepage rail links country → university → programme. |
| Pagination | `?page=` with a server-rendered pager, so crawlers can reach deep results. |
| `sitemap.xml` | Generated at runtime from `/sitemap-data` — every country, university, programme, scholarship, faculty member, service, story and post, with `lastModified` and tuned priorities. Revalidates hourly. |
| `robots.txt` | Allows everything except `/admin`, `/brochure/`, `/api/`, `/search`, `/shortlist`, `/compare`; declares the sitemap and host. |
| Performance | Detail pages are pre-rendered (ISR, 5-minute revalidate); the homepage rotates without further requests. |
| Mobile | Mobile-first layout, bottom navigation, 44 px tap targets. |

### Structured data (JSON-LD)

| Page | Schema |
| --- | --- |
| Every page (root layout) | `EducationalOrganization` (name, description, address in Uttara, phone, email, opening hours, `sameAs` to Facebook/Instagram/LinkedIn/YouTube) + `WebSite` with `SearchAction` |
| University | `CollegeOrUniversity` |
| Programme | `Course` |
| Country, university, service, FAQ page | `FAQPage` |
| Event | `Event` with attendance mode, location, free `Offer` |
| Blog post | `BlogPosting` |
| Contact | `ContactPage` |

### Google Search Console

Set `google_site_verification` in **Admin → Site settings → Google & Meta tracking**; it is rendered
into `<head>` automatically. Then submit `https://yourdomain/sitemap.xml`. `meta_domain_verification`
(Meta) and `bing_verification` work the same way.

---

## 4. Linking the Facebook page

All of it is configuration, not code. **Admin → Site settings → Facebook & social.**

| Setting | Effect on the site |
| --- | --- |
| `facebook_page_url` | Footer link, contact page card, `sameAs` in the organisation JSON-LD, the Facebook panel |
| `facebook_page_id` | Used by the page plugin and for Messenger deep links |
| `facebook_page_plugin_enabled` | Shows the page panel on the contact page and in the footer area |
| `messenger_url` + `messenger_enabled` | "Message us" button (`m.me/...`) |
| `instagram_url`, `linkedin_url`, `youtube_url`, `tiktok_url`, `x_url` | Footer icons and `sameAs` entries |

Because these are settings, pointing the site at a different page is a thirty-second edit with no
deployment.

> Note on the in-app preview: the workspace preview iframe has no network access, so the embedded
> Facebook page plugin will not render there. It works normally on a real domain.

---

## 5. Contact channels

Six routes, all driven by settings so none of them are hard-coded:

| Channel | Where it appears |
| --- | --- |
| **Office visit** | Address, hours and an embedded map on `/contact` |
| **WhatsApp** | Floating button on every public page, bottom-nav item, buttons on forms. The message is prefilled and page-aware ("…(page: /programs/…)"), so the counselor knows the context before replying |
| **Phone** | `tel:` links on `/contact` and in the footer |
| **Email** | `mailto:` links for formal enquiries and documents |
| **Facebook / Messenger** | Page link and `m.me` deep link |
| **Forms** | `/contact` enquiry form → `/leads`; `/appointments` booking form → `/appointments`; event registration → `/events/register` |

Every submission records its UTM parameters, the page path and an explicit consent flag, and returns
a reference number to the student.

---

## 6. Ads

### 6.1 What is wired

| Integration | Setting | Behaviour |
| --- | --- | --- |
| **Meta Pixel** | `meta_pixel_id` | Loads only when a real ID is present. Fires `PageView`, and `Lead` / `Contact` / `CompleteRegistration` / `Search` from the first-party `track()` helper |
| **Meta Conversions API** | `META_CAPI_TOKEN` (backend `.env`) | `POST /marketing/conversion` relays server-side events with **SHA-256 hashed** email and phone. With no token the event is logged locally instead of being dropped |
| **GA4** | `ga4_measurement_id` | `gtag.js` with `send_page_view: false`; the app sends page views itself so client-side navigation is counted correctly |
| **Google Tag Manager** | `gtm_container_id` | Optional container if you prefer managing tags in GTM |
| **Google Ads** | `google_ads_conversion_id` + `google_ads_conversion_label` | Configured alongside GA4 for conversion import |
| **TikTok** | `tiktok_pixel_id` | Same pattern |
| **Search Console / Bing / Meta domain** | verification fields | Rendered into `<head>` |

**Placeholder safety:** values like `G-XXXXXXXXXX`, `AW-XXXXXXXXX` or `000000000000000` are treated
as absent. A fresh install loads zero third-party scripts and the marketing dashboard shows
"Not connected".

**Consent:** `analytics_consent_required` is on by default, in line with Meta and Google consent
requirements in the regions you advertise to.

### 6.2 Attribution chain

```
Ad click (UTM link)
   → landing page
   → track.ts captures utm_source/medium/campaign/content/term into sessionStorage
   → every form POST attaches { utm } and page_path
   → backend maps utm_campaign → AdCampaign
   → lead lands in the pipeline already attributed
   → POST /marketing/conversion → Meta CAPI (hashed PII)
   → admin: campaign row shows budget, spend, clicks and the leads it produced
```

Because the chain starts with our own `track.ts` and ends in our own `leads` table, attribution
survives ad blockers and cookie restrictions that break pixel-only setups.

### 6.3 Campaign-driven homepage

A campaign can be attached to a homepage slide (`campaign_id` + `priority`). Raise the priority of
the "Study in the UK" slide while the UK campaign is running and paid visitors land on a hero that
matches the ad they just clicked, without a separate landing page.

### 6.4 UTM builder

**Admin → Marketing & ads → UTM link builder** composes the exact URL to paste into Facebook Ads
Manager or Google Ads, with a copy button. `/marketing/links` also returns ready-made landing links
for the home page, the profile assessment and the main destination pages.

### 6.5 Suggested campaign structure

| Objective | Landing page | Primary event |
| --- | --- | --- |
| Cold awareness — destination interest | `/study-destinations/{country}` | `PageView`, `Search` |
| Mid-funnel — "am I eligible?" | `/profile-assessment` | `Lead` on assessment submit |
| High intent — book a counselor | `/appointments` | `Schedule` |
| Event promotion | `/events` | `CompleteRegistration` |
| Retargeting — shortlist abandoners | `/shortlist` → `/compare` | `Lead` |

Naming convention: `utm_campaign = {intake}_{country}_{objective}` → `fall2027_uk_assessment`.
Keep `utm_content` at the creative level so you can tell which video produced the enrolments.

### 6.6 Events the platform already records

`page_view`, `search`, `program_view`, `university_view`, `scholarship_view`, `shortlist_add`,
`shortlist_remove`, `compare_view`, `assessment_submit`, `eligibility_check`, `brochure_generated`,
`brochure_view`, `brochure_print`, `lead_submit`, `appointment_booked`, `event_register`,
`whatsapp_click`, `assistant_question`.

Each goes to the first-party `/track` endpoint and, when configured, mirrors to `gtag` and `fbq`.

---

## 7. Content plan

The `search_queries` table records what students actually searched for, and the admin dashboard
surfaces it as "what students search for". Write the blog from that list, not from a keyword tool.

Durable topics for this audience: intake timelines per country and year; what a visa officer wants
to see in financial documents; MOI versus IELTS and who really accepts it; study-gap policies by
country; the real first-year cost in each destination; how to email a potential PhD supervisor;
scholarship deadlines by intake; credential equivalency for Bangladeshi degrees.

Each article should link to the relevant destination, programme search and the profile assessment —
that is the path from an organic reader to an attributable lead.

---

## 8. Launch checklist

- [ ] Replace every placeholder ID in **Admin → Site settings** with real values
- [ ] Set `site_url` to the production domain (it drives canonicals and the sitemap)
- [ ] Verify the domain in Search Console and submit `sitemap.xml`
- [ ] Verify the domain in Meta Business Suite; set `meta_domain_verification`
- [ ] Create the Meta Pixel; set `meta_pixel_id`; generate a CAPI token into the backend `.env`
- [ ] Create the GA4 property; set `ga4_measurement_id`; link Google Ads
- [ ] Confirm the Facebook page URL, page ID and Messenger link
- [ ] Confirm WhatsApp number, default message and office hours
- [ ] Create the first campaigns in `/admin/campaigns` with matching UTM values
- [ ] Attach campaign IDs to the relevant homepage slides and set their priority
- [ ] Test one lead end to end: ad link → assessment → lead → correct campaign attribution
- [ ] Re-check `robots.txt` and that `/admin`, `/brochure`, `/compare`, `/shortlist` are excluded
