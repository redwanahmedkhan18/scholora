# 05 · Data Integrity & Trust Policy

**This document is binding.** It is the difference between Scholara and a brochure site. Every rule
below is enforced in code, not left to good intentions. If a change to the platform would break one
of these rules, the change does not ship.

---

## 1. The principle

> A student is about to spend several years and several million taka on a decision. Anything we
> publish must be traceable to a source they can open themselves, and anything we do not know must
> be visibly labelled as unknown rather than quietly filled in.

Three consequences follow, and they run through the whole system:

1. **Provenance is a first-class column, not a footnote.**
2. **"We don't know" is a valid, displayable answer.**
3. **Disagreement between sources is data, not an error to be smoothed away.**

---

## 2. Provenance on every important field

Every table holding a student-actionable fact carries the `VerificationMixin`:

| Column | Rule |
| --- | --- |
| `source_url` | The exact page, not the university's homepage. |
| `source_type` | One of the five tiers below. |
| `retrieved_at` | When the value was captured. |
| `verified_at` | When a human last confirmed it against the source. |
| `verified_by` | Which staff member. Accountability has a name. |
| `confidence` | One of the five states below. |
| `review_due_at` | When it must be re-checked. |

Applies to: `universities`, `university_rankings`, `programs`, `program_requirements`,
`program_intakes`, `scholarships`, `professors`.

### Source tiers

| Tier | Meaning | Use for |
| --- | --- | --- |
| `TIER_1_OFFICIAL_UNIVERSITY` | The institution's own site or portal | requirements, fees, deadlines, programme facts |
| `TIER_2_GOVERNMENT` | Immigration, ministry, regulator | visa rules, work rights, post-study work |
| `TIER_3_RANKING_PROVIDER` | QS, THE, ARWU, US News … | rankings only |
| `TIER_4_PARTNER_DOCUMENT` | Signed partner material | intake notes, partner-specific scholarships |
| `TIER_5_OTHER` | Anything else | background only — **never** the sole basis for a published requirement |

A programme requirement sourced only at Tier 5 is not publishable.

### Confidence states

| State | Meaning | How it appears |
| --- | --- | --- |
| `VERIFIED` | Human-checked against a Tier 1–3 source within the review window | green badge |
| `NEEDS_REVIEW` | Imported or past its review date; not yet re-confirmed | amber badge, "confirm before acting" |
| `OUTDATED` | The source has moved on | orange badge, value shown with its date |
| `CONFLICTING` | Two credible sources disagree | red badge, **both values shown** |
| `UNVERIFIED` | No usable source | grey badge, or the field is hidden entirely |

**A record with no `source_url` can never be `VERIFIED`.**

### Conflict handling

When a new value contradicts a stored one:

1. The stored value is **not** overwritten.
2. A `data_verifications` row is created with `status = CONFLICTING`, holding both values and both source URLs.
3. The entity's `confidence` becomes `CONFLICTING`.
4. The public page shows both figures with their sources and dates, and says plainly that the sources disagree.
5. The row enters the admin verification queue with a review date.

Silently picking a winner is prohibited. A student who sees "the university page says IELTS 6.5, the
faculty page says 7.0 — check with us before you book the test" is better served than one who sees a
confident, possibly wrong, 6.5.

---

## 3. Eligibility: verdicts, never probabilities

### The only permitted verdicts

| Verdict | Meaning |
| --- | --- |
| `MEETS` | The stated requirement is met by the information provided. |
| `DOES_NOT_MEET` | The stated requirement is not met. |
| `PARTIALLY_MEETS` | Some components met, others not (e.g. IELTS overall fine, one band low). |
| `REQUIRES_REVIEW` | A human must judge (unusual qualification, equivalency, study gap). |
| `INFORMATION_UNAVAILABLE` | Either the student did not provide it, or the university has not published it. |

### Prohibited outputs

- ❌ "95% chance of admission" · ❌ "You will get in" · ❌ "Guaranteed offer"
- ❌ "High / medium / low chance" presented as a prediction
- ❌ Any percentage, score or gauge that a reader could mistake for an admission probability
- ❌ Visa success rates attached to an individual student

An internal **match score** exists for ranking results. It is presented as *fit with your stated
preferences and constraints* — budget, level, subject, intake, research alignment — and it is always
shown next to the per-criterion eligibility table, never instead of it.

### Required phrasing

| Situation | Say |
| --- | --- |
| Requirement met | "Meets the stated minimum of 3.0/4.0." |
| Requirement unmet | "The stated minimum is 6.5 overall; you entered 6.0." |
| Not published | "The university has not published this requirement. Confirm with the department." |
| Not provided | "You have not entered this yet." |
| Scholarship | **"Stated eligibility criteria appear to be met."** Never "you qualify", never "you will receive". |
| Any verdict | "This is a comparison against published criteria, not an admission decision." |

Every eligibility surface — the wizard results, the programme page, the brochure — carries that last
line.

---

## 4. Cost: the whole number

Tuition alone is the single most misleading figure in this industry. A cost estimate must show:

- annual tuition;
- estimated annual living cost for that city;
- compulsory health insurance;
- compulsory fees (student services, etc.);
- one-time costs (application fee, deposit) **separated** from the recurring annual total;
- the estimated annual total;
- the student's budget position against it (`WITHIN_STATED_BUDGET`, `SLIGHTLY_ABOVE_BUDGET`, …) and the size of any gap.

Estimates are labelled estimates, with the currency and the date the figures were verified.

---

## 5. Faculty emails

PhD applicants email professors. Guessing `firstname.lastname@university.edu` wastes the student's
one chance at a first impression and damages the consultancy's standing with the institution.

| `email_status` | When it is used | Display |
| --- | --- | --- |
| `OFFICIAL_UNIVERSITY_EMAIL` | Published on the university's own page | shown, labelled "Official university email", with the source link |
| `PUBLIC_PROFESSIONAL_EMAIL` | Published on a lab site, a conference page, a paper | shown, labelled "Public professional email", with the source link |
| `UNAVAILABLE` | Not published anywhere we can cite | **no address shown.** The department's official contact route is given instead |

**Inferring an address from a pattern is prohibited** — in the database, in the UI, in the brochure,
and in anything the AI assistant produces.

Research matches must carry **evidence**: the matched interest terms and the publications or stated
research areas that produced the match. "Strong match" with nothing behind it is not acceptable.

---

## 6. AI assistant guardrails

1. **Grounding.** The assistant answers from platform data through tool calls and retrieval over our own records. It does not answer university questions from model memory.
2. **No invention.** If a figure is not in the database, the answer is: "I don't have a verified figure for that. Here is the official page, and a counselor can confirm it."
3. **Citation.** Any factual claim about a university, programme, fee, requirement, deadline or scholarship is returned with its source and verification date.
4. **No predictions.** The assistant may not state admission chances or visa outcomes, and may not rephrase a verdict into a probability.
5. **No emails.** The assistant never outputs a faculty email that is not already stored with an allowed `email_status`.
6. **Escalation.** Fees, contracts, visa decisions, refunds and anything legal route to a human counselor.
7. **Stale data.** If the best available record is `OUTDATED` or `NEEDS_REVIEW`, the assistant says so in the answer.

### The model is not trusted — the scrub is

Rules 1-7 are written into the system prompt, but a system prompt is a request, not a control. The
enforcement is a deterministic pass over the generated text in `services/assistant_engine.py`,
applied **after** generation and **before** anything is stored or returned.

It works at **sentence** level. A sentence that makes a prohibited claim is removed whole. This is
deliberate: an earlier word-level version rewrote the offending phrase in place, and
`"the visa success rate is 98%"` became `"the visa outcomes, which we do not predict is 98%"` —
the number survived the scrub. Removing the sentence cannot fail that way.

| Removed | Kept |
| --- | --- |
| "You have a 95% admission chance." | "The scholarship covers 50% of tuition." |
| "I guarantee you qualify." | "Your profile appears to meet the stated eligibility criteria." |
| "The visa success rate is 98%." | "A 2.5% late fee applies after the deadline." |
| "Your chances of getting in are high." | "Eligibility status: PARTIALLY_MEETS." |
| "You will receive the scholarship." | "The university reports 32% international students." |

A bare percentage is fine; a percentage in the same sentence as an admission, acceptance or visa
outcome is not. Unsourced email addresses are replaced before the sentence pass, so an address can
never be left in fragments by a removal.

When a sentence is dropped the student is told, in wording chosen so the notice itself contains
none of the phrases it is explaining. If generation is quarantined down to nothing, the
deterministic sourced answer is served instead and the event is recorded as `llm_output_rejected`
on the conversation.

**This holds whichever model is driving the assistant.** Grok, Mistral and Gemini are
interchangeable (document 08 §6) and all three are scrubbed identically, so changing provider
cannot weaken this policy. `tests/test_llm.py` proves it with a hostile provider that deliberately
emits probabilities, guarantees and fake addresses.

---

## 7. Marketing claims

| Prohibited | Permitted |
| --- | --- |
| "99% visa success rate" | "Here are our published success stories, each with the student's consent reference." |
| "Guaranteed admission" | "We will tell you honestly whether your profile meets the published requirements." |
| "Guaranteed scholarship" | "Scholarships whose stated criteria your profile appears to meet." |
| Invented student testimonials | Stories published only with a recorded `consent_reference` |
| Unattributed statistics | Counts computed live from the database (`/stats`) |

Success stories are published only when `consent_reference` is filled in, and each page states that
individual results depend on the student's profile, the university's decision and the visa
authority.

---

## 8. Operating the verification queue

| Data class | Review cadence |
| --- | --- |
| Tuition and fees | annually, and immediately when a new academic year is announced |
| Programme requirements | annually, and before each intake opens |
| Intake dates and deadlines | **every cycle** — this is the highest-risk data on the platform |
| Scholarships | annually, and 60 days before each stated deadline |
| Rankings | on each provider's publication date |
| Faculty (interests, accepting status, email) | annually |
| Visa and work rights | on policy change; monitored at Tier 2 |

**Workflow:** `review_due_at` passes → the record flips to `NEEDS_REVIEW` and appears in the queue →
a data manager re-checks the official source → either `verified_at` is stamped and confidence
returns to `VERIFIED`, or the value is updated (with the old one retained in the verification
record), or a `CONFLICTING` row is raised. Every step writes an audit log.

The admin dashboard surfaces the backlog as a counter; it is meant to be driven to zero, not ignored.

---

## 9. Privacy and consent

- Every form carries an explicit **contact consent** checkbox. Marketing consent is separate and never pre-ticked as a condition of service.
- Documents live in private storage and are reachable only through short-lived signed URLs.
- Any PII sent to a third-party conversion API (Meta CAPI) is **SHA-256 hashed** server-side first. Raw email and phone never leave the platform.
- Student profiles are retrievable only with the owner's token.
- Brochure URLs are tokenised, `noindex`, `nocache`, and excluded in `robots.txt`.
- Staff access to student data is role-gated and audit-logged.

---

## 10. The test

Before anything student-facing ships, it must survive four questions:

1. **Can the student verify this themselves?** If not, where is the source link?
2. **Would this still be true if the university changed its page yesterday?** If not, where is the verification date?
3. **Could a reasonable person read this as a promise?** If yes, rewrite it.
4. **If this turns out to be wrong, who is accountable and when was it last checked?** If there is no answer, it is not ready.
