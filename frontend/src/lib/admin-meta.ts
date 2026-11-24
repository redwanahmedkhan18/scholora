/** Resource key → human label + the columns worth showing in the list table. */
export const RESOURCE_META: Record<string, { label: string; group: string; columns: string[]; hint?: string }> = {
  "homepage-slides": { label: "Homepage slides", group: "Website", columns: ["id", "key", "title", "focus", "priority", "duration_seconds", "status"], hint: "Rotation order is driven by priority (high first). duration_seconds controls how long each slide stays on screen." },
  "homepage-sections": { label: "Homepage sections", group: "Website", columns: ["id", "key", "title", "position", "is_enabled"], hint: "Position controls the order of the sections below the hero. Disable a section to hide it without deleting it." },
  services: { label: "Services", group: "Website", columns: ["id", "title", "category", "is_featured", "priority", "status"] },
  "success-stories": { label: "Success stories", group: "Website", columns: ["id", "student_name", "university_name", "program_name", "year", "is_published", "is_featured"], hint: "Only publish a story once you hold the student's written consent. Record the consent reference." },
  events: { label: "Events", group: "Website", columns: ["id", "title", "start_at", "venue", "mode", "status", "is_featured"] },
  "blog-posts": { label: "Blog posts", group: "Website", columns: ["id", "title", "category", "is_published", "published_at", "reading_minutes"] },
  faqs: { label: "FAQs", group: "Website", columns: ["id", "question", "category", "scope", "position"] },

  countries: { label: "Countries", group: "Education data", columns: ["id", "name", "iso2", "region", "status", "is_featured", "priority"] },
  universities: { label: "Universities", group: "Education data", columns: ["id", "name", "city", "institution_type", "partner_status", "is_featured", "confidence"], hint: "Every university must carry a source URL, source type and verification date before confidence can move to VERIFIED." },
  faculties: { label: "Faculties & schools", group: "Education data", columns: ["id", "university_id", "name", "kind", "dean_name", "confidence"], hint: "Academic divisions: University > Faculty/School > Department > Programme. 'Faculty' here means the division; individual academics live under Faculty (professors)." },
  departments: { label: "Departments", group: "Education data", columns: ["id", "university_id", "name", "faculty_id"] },
  programs: { label: "Programmes", group: "Education data", columns: ["id", "name", "university_id", "degree_level", "tuition_annual", "currency", "is_published", "confidence"] },
  "program-requirements": { label: "Programme requirements", group: "Education data", columns: ["id", "program_id", "min_cgpa", "ielts_overall", "moi_accepted", "confidence"] },
  "program-intakes": { label: "Programme intakes", group: "Education data", columns: ["id", "program_id", "label", "status", "application_deadline", "class_start", "confidence"] },
  scholarships: { label: "Scholarships", group: "Education data", columns: ["id", "name", "provider", "amount_type", "amount_value", "deadline", "confidence"] },
  "university-rankings": { label: "Rankings", group: "Education data", columns: ["id", "university_id", "provider", "edition_year", "rank_overall", "confidence"], hint: "Store one row per provider per edition year. Never merge providers into a single rank." },
  professors: { label: "Faculty (academics)", group: "Education data", columns: ["id", "full_name", "university_id", "title", "accepting_phd", "email_status", "confidence"], hint: "Email status must be OFFICIAL_UNIVERSITY_EMAIL, PUBLIC_PROFESSIONAL_EMAIL or UNAVAILABLE. Never enter an inferred address." },
  publications: { label: "Publications", group: "Education data", columns: ["id", "professor_id", "title", "venue", "year"] },
  partnerships: { label: "Partnerships", group: "Education data", columns: ["id", "university_id", "status", "representative", "agreement_expiry"], hint: "Internal commercial data. Never exposed on public endpoints." },
  verifications: { label: "Verification queue", group: "Education data", columns: ["id", "entity_type", "entity_id", "field_name", "field_value", "status", "review_due_at"], hint: "CONFLICTING rows mean two sources disagree. Resolve by re-checking the official source — never silently overwrite." },

  leads: { label: "Leads", group: "CRM", columns: ["id", "reference", "full_name", "phone", "stage", "source", "interest_country", "created_at"] },
  appointments: { label: "Appointments", group: "CRM", columns: ["id", "reference", "full_name", "scheduled_date", "scheduled_time", "mode", "status"] },
  users: { label: "Staff users", group: "CRM", columns: ["id", "email", "full_name", "role", "is_active", "mfa_enabled"], hint: "Roles: ADMIN, SENIOR_COUNSELOR, DATA_MANAGER, APPLICATION_OFFICER, COUNSELOR, STUDENT." },
  campaigns: { label: "Ad campaigns", group: "Marketing", columns: ["id", "name", "channel", "objective", "status", "total_budget", "spend_to_date", "clicks"] },

  applications: { label: "Applications", group: "Students", columns: ["id", "reference", "student_profile_id", "program_id", "status", "intake_label", "counselor_id", "submitted_at"], hint: "Use the pipeline board under Student workflow to move a status — it writes history and notifies the student. Editing here is for corrections only." },
  "student-profiles": { label: "Student profiles", group: "Students", columns: ["id", "full_name", "email", "phone", "target_degree_level", "target_discipline", "target_intake", "created_at"], hint: "public_token is the student's private portal key at /my/{token}. Treat it like a password." },
  documents: { label: "Documents", group: "Students", columns: ["id", "student_profile_id", "doc_type", "original_filename", "status", "size_bytes", "created_at"], hint: "Files are private. Use Document review to open one — it mints an expiring link and records the access." },
  "shortlist-items": { label: "Saved shortlists", group: "Students", columns: ["id", "student_profile_id", "entity_type", "label", "slug", "priority"] },
  brochures: { label: "Brochures", group: "Students", columns: ["id", "token", "student_name", "program_id", "degree_level", "version", "downloads", "created_at"] },
  "event-registrations": { label: "Event registrations", group: "Students", columns: ["id", "event_id", "full_name", "phone", "email", "status", "created_at"] },
  notifications: { label: "Notification log", group: "Students", columns: ["id", "trigger", "channel", "recipient", "title", "severity", "status", "created_at"] },

  "research-areas": { label: "Research areas", group: "Education data", columns: ["id", "name", "slug", "field"] },
  "audit-logs": { label: "Audit log (raw)", group: "Governance", columns: ["id", "created_at", "actor_email", "action", "entity_type", "entity_id"], hint: "Append-only. Use the Audit log screen for filtering — this raw view cannot be edited or deleted." },
  "assistant-conversations": { label: "Assistant transcripts", group: "Governance", columns: ["id", "created_at", "session_id", "question", "intent", "grounded", "escalated", "latency_ms"], hint: "Read-only record of every AI answer, its tools and its sources. Review ungrounded answers to find gaps in the database." },
  "search-queries": { label: "Search queries", group: "Governance", columns: ["id", "created_at", "query", "country", "degree_level", "subject", "results_count"], hint: "What students actually look for. Zero-result queries are the strongest signal of missing data." },
  "analytics-events": { label: "Analytics events", group: "Governance", columns: ["id", "created_at", "name", "path", "entity_type", "entity_id", "campaign_id"] },
  "application-history": { label: "Application history", group: "Governance", columns: ["id", "created_at", "application_id", "from_status", "to_status", "changed_by", "note"], hint: "Append-only stage transitions behind the applications board. Read-only: correct an application by moving its stage, not by editing history." },
};

export const RESOURCE_GROUPS = ["Website", "Education data", "CRM", "Students", "Marketing", "Governance"];
