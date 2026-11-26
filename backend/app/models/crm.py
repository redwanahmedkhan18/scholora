"""People, pipeline and governance: users, students, leads, applications, documents,
brochures, WhatsApp conversations, analytics, verification and audit."""
from datetime import date, datetime
from typing import Any, List, Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, JSONType


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(40), default="STUDENT")
    # ADMIN / SENIOR_COUNSELOR / COUNSELOR / DATA_MANAGER / APPLICATION_OFFICER / STUDENT (§91)
    phone: Mapped[Optional[str]] = mapped_column(String(40))
    avatar_url: Mapped[Optional[str]] = mapped_column(String(600))
    is_active: Mapped[bool] = mapped_column(default=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # --- multi-factor authentication (§91) -------------------------------
    # NB: there is deliberately no `mfa_enabled` flag. Enrolment state is derived from
    # mfa_secret + mfa_confirmed_at; a separate boolean drifted out of sync and made
    # the seeded admin look protected when it had no secret at all.
    mfa_secret: Mapped[Optional[str]] = mapped_column(String(64))
    mfa_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    mfa_recovery_codes: Mapped[Optional[list]] = mapped_column(JSONType, default=list)
    # The TOTP time-step of the last accepted code. A code belongs to a 30-second step and
    # was previously accepted for the whole drift window, so a captured code could be
    # replayed for up to 90 seconds. Anything at or below this step is now refused.
    mfa_last_used_step: Mapped[Optional[int]] = mapped_column(Integer)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime)


class StudentProfile(Base, TimestampMixin):
    """§32 - the profile that feeds the eligibility and matching engines."""
    __tablename__ = "student_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    public_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    email: Mapped[Optional[str]] = mapped_column(String(200))
    phone: Mapped[Optional[str]] = mapped_column(String(40))
    nationality: Mapped[str] = mapped_column(String(80), default="Bangladesh")
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date)
    city: Mapped[Optional[str]] = mapped_column(String(120))
    # Academics
    highest_qualification: Mapped[Optional[str]] = mapped_column(String(120))
    major: Mapped[Optional[str]] = mapped_column(String(160))
    institution: Mapped[Optional[str]] = mapped_column(String(200))
    cgpa: Mapped[Optional[float]] = mapped_column(Float)
    cgpa_scale: Mapped[Optional[float]] = mapped_column(Float, default=4.0)
    graduation_year: Mapped[Optional[int]] = mapped_column(Integer)
    study_gap_years: Mapped[Optional[float]] = mapped_column(Float, default=0)
    backlogs: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    completed_subjects: Mapped[List[str]] = mapped_column(JSONType, default=list)
    # Tests
    english_test: Mapped[Optional[str]] = mapped_column(String(40))
    ielts_overall: Mapped[Optional[float]] = mapped_column(Float)
    ielts_lowest_band: Mapped[Optional[float]] = mapped_column(Float)
    toefl_ibt: Mapped[Optional[float]] = mapped_column(Float)
    pte_academic: Mapped[Optional[float]] = mapped_column(Float)
    duolingo: Mapped[Optional[float]] = mapped_column(Float)
    moi_available: Mapped[bool] = mapped_column(default=False)
    gre_score: Mapped[Optional[str]] = mapped_column(String(40))
    gmat_score: Mapped[Optional[str]] = mapped_column(String(40))
    # Experience & research
    work_experience_years: Mapped[Optional[float]] = mapped_column(Float, default=0)
    job_title: Mapped[Optional[str]] = mapped_column(String(160))
    research_experience: Mapped[Optional[str]] = mapped_column(Text)
    publications_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    research_interests: Mapped[List[str]] = mapped_column(JSONType, default=list)
    projects: Mapped[Optional[str]] = mapped_column(Text)
    # Preferences
    budget_per_year: Mapped[Optional[float]] = mapped_column(Float)
    budget_currency: Mapped[str] = mapped_column(String(8), default="USD")
    funding_source: Mapped[Optional[str]] = mapped_column(String(120))
    preferred_countries: Mapped[List[str]] = mapped_column(JSONType, default=list)
    preferred_universities: Mapped[List[str]] = mapped_column(JSONType, default=list)
    target_degree_level: Mapped[Optional[str]] = mapped_column(String(20))
    target_discipline: Mapped[Optional[str]] = mapped_column(String(160))
    target_intake: Mapped[Optional[str]] = mapped_column(String(60))
    career_goal: Mapped[Optional[str]] = mapped_column(Text)
    consent_marketing: Mapped[bool] = mapped_column(default=False)
    source: Mapped[str] = mapped_column(String(40), default="WEBSITE")


class Lead(Base, TimestampMixin):
    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(40), index=True)
    email: Mapped[Optional[str]] = mapped_column(String(200))
    message: Mapped[Optional[str]] = mapped_column(Text)
    interest_country: Mapped[Optional[str]] = mapped_column(String(120))
    interest_degree: Mapped[Optional[str]] = mapped_column(String(40))
    interest_subject: Mapped[Optional[str]] = mapped_column(String(160))
    intake: Mapped[Optional[str]] = mapped_column(String(60))
    source: Mapped[str] = mapped_column(String(40), default="WEBSITE")
    # WEBSITE / WHATSAPP / FACEBOOK / MESSENGER / GOOGLE_ADS / EVENT / REFERRAL / WALK_IN
    channel_detail: Mapped[Optional[str]] = mapped_column(String(160))
    campaign_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ad_campaigns.id"), index=True)
    utm: Mapped[dict] = mapped_column(JSONType, default=dict)
    page_path: Mapped[Optional[str]] = mapped_column(String(300))
    student_profile_id: Mapped[Optional[int]] = mapped_column(ForeignKey("student_profiles.id"))
    stage: Mapped[str] = mapped_column(String(30), default="NEW", index=True)
    assigned_to: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    priority: Mapped[str] = mapped_column(String(20), default="NORMAL")
    next_action_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    consent_contact: Mapped[bool] = mapped_column(default=True)


class Appointment(Base, TimestampMixin):
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(40))
    email: Mapped[Optional[str]] = mapped_column(String(200))
    service_type: Mapped[str] = mapped_column(String(60), default="CAREER_COUNSELING")
    counselor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    counselor_name: Mapped[Optional[str]] = mapped_column(String(160))
    scheduled_date: Mapped[date] = mapped_column(Date)
    scheduled_time: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(20), default="OFFLINE")
    status: Mapped[str] = mapped_column(String(20), default="REQUESTED")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("leads.id"))


class Application(Base, TimestampMixin):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    student_profile_id: Mapped[int] = mapped_column(ForeignKey("student_profiles.id"), index=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"), index=True)
    intake_label: Mapped[Optional[str]] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(40), default="PLANNING", index=True)
    counselor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    decision_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class ApplicationStatusHistory(Base, TimestampMixin):
    __tablename__ = "application_status_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    from_status: Mapped[Optional[str]] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40))
    changed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    note: Mapped[Optional[str]] = mapped_column(Text)


class Document(Base, TimestampMixin):
    """§57/§90 - metadata only; files live in private object storage behind signed URLs."""
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_profile_id: Mapped[int] = mapped_column(ForeignKey("student_profiles.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(60))
    storage_key: Mapped[str] = mapped_column(String(400))
    original_filename: Mapped[Optional[str]] = mapped_column(String(300))
    mime_type: Mapped[Optional[str]] = mapped_column(String(120))
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="UPLOADED")
    review_note: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[Optional[date]] = mapped_column(Date)


class Brochure(Base, TimestampMixin):
    """§38 - personalized, versioned, source-backed degree brochure."""
    __tablename__ = "brochures"
    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    student_name: Mapped[str] = mapped_column(String(160))
    student_profile_id: Mapped[Optional[int]] = mapped_column(ForeignKey("student_profiles.id"))
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"), index=True)
    degree_level: Mapped[str] = mapped_column(String(20))
    target_intake: Mapped[Optional[str]] = mapped_column(String(60))
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    generated_by: Mapped[str] = mapped_column(String(40), default="WEBSITE")
    downloads: Mapped[int] = mapped_column(Integer, default=0)


class WhatsAppContact(Base, TimestampMixin):
    __tablename__ = "whatsapp_contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    wa_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    profile_name: Mapped[Optional[str]] = mapped_column(String(160))
    lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("leads.id"))
    opted_in: Mapped[bool] = mapped_column(default=True)


class WhatsAppConversation(Base, TimestampMixin):
    __tablename__ = "whatsapp_conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("whatsapp_contacts.id"), index=True)
    state: Mapped[str] = mapped_column(String(60), default="MENU")
    context: Mapped[dict] = mapped_column(JSONType, default=dict)
    handed_off: Mapped[bool] = mapped_column(default=False)
    assigned_to: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class WhatsAppMessage(Base, TimestampMixin):
    __tablename__ = "whatsapp_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("whatsapp_conversations.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # IN / OUT
    body: Mapped[str] = mapped_column(Text)
    intent: Mapped[Optional[str]] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)


class DataVerification(Base, TimestampMixin):
    """§39/§70 - field-level provenance + conflict register."""
    __tablename__ = "data_verifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(60), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    field_name: Mapped[str] = mapped_column(String(80))
    field_value: Mapped[Optional[str]] = mapped_column(String(400))
    source_url: Mapped[Optional[str]] = mapped_column(String(600))
    source_type: Mapped[str] = mapped_column(String(60), default="TIER_1_OFFICIAL_UNIVERSITY")
    retrieved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    verified_by: Mapped[Optional[str]] = mapped_column(String(160))
    review_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="VERIFIED")
    conflict_with: Mapped[Optional[str]] = mapped_column(Text)


class SearchQueryLog(Base, TimestampMixin):
    """§93 - what students actually want."""
    __tablename__ = "search_queries"
    id: Mapped[int] = mapped_column(primary_key=True)
    query: Mapped[Optional[str]] = mapped_column(String(300))
    country: Mapped[Optional[str]] = mapped_column(String(120))
    degree_level: Mapped[Optional[str]] = mapped_column(String(40))
    subject: Mapped[Optional[str]] = mapped_column(String(160))
    intake: Mapped[Optional[str]] = mapped_column(String(60))
    results_count: Mapped[int] = mapped_column(Integer, default=0)
    channel: Mapped[str] = mapped_column(String(30), default="WEBSITE")


class AnalyticsEvent(Base, TimestampMixin):
    __tablename__ = "analytics_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    path: Mapped[Optional[str]] = mapped_column(String(300))
    entity_type: Mapped[Optional[str]] = mapped_column(String(60))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    campaign_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ad_campaigns.id"))
    utm: Mapped[dict] = mapped_column(JSONType, default=dict)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    actor_email: Mapped[Optional[str]] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[Optional[str]] = mapped_column(String(60))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    ip_address: Mapped[Optional[str]] = mapped_column(String(60))


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), default="WEBSITE")  # WHATSAPP/EMAIL/SMS/WEBSITE
    recipient: Mapped[str] = mapped_column(String(200))
    trigger: Mapped[str] = mapped_column(String(60))
    title: Mapped[Optional[str]] = mapped_column(String(200))
    body: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # routing + read state so both the student portal and staff inbox can use one table
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    student_profile_id: Mapped[Optional[int]] = mapped_column(ForeignKey("student_profiles.id"), index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(40))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    action_url: Mapped[Optional[str]] = mapped_column(String(400))
    severity: Mapped[str] = mapped_column(String(20), default="INFO")  # INFO/WARNING/CRITICAL
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class ShortlistItem(Base, TimestampMixin):
    """§30 - a shortlist that survives switching device, once the student has a profile."""
    __tablename__ = "shortlist_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_profile_id: Mapped[int] = mapped_column(ForeignKey("student_profiles.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(30), index=True)  # program/university/scholarship
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    slug: Mapped[str] = mapped_column(String(220))
    label: Mapped[str] = mapped_column(String(300))
    meta: Mapped[Optional[dict]] = mapped_column(JSONType, default=dict)
    note: Mapped[Optional[str]] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=0)


class ImportBatch(Base, TimestampMixin):
    """A staged data load. Nothing reaches a live table until a human commits the batch.

    The platform's integrity rule is that every important field carries a source and that
    conflicting sources are surfaced rather than silently overwritten. A bulk importer that
    wrote straight into `universities` would break both, so uploads land here first: rows are
    parsed, matched against existing records, diffed field by field, and classified as
    CREATE / UPDATE / CONFLICT / UNCHANGED for review.
    """
    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)   # country|university|program|scholarship
    filename: Mapped[Optional[str]] = mapped_column(String(300))
    source_url: Mapped[Optional[str]] = mapped_column(String(600))
    source_type: Mapped[str] = mapped_column(String(60), default="TIER_1_OFFICIAL_UNIVERSITY")
    retrieved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="STAGED", index=True)  # STAGED|COMMITTED|DISCARDED
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    created_rows: Mapped[int] = mapped_column(Integer, default=0)
    updated_rows: Mapped[int] = mapped_column(Integer, default=0)
    conflict_rows: Mapped[int] = mapped_column(Integer, default=0)
    skipped_rows: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(160))
    committed_by: Mapped[Optional[str]] = mapped_column(String(160))
    committed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    rows: Mapped[List["ImportRow"]] = relationship(back_populates="batch",
                                                   cascade="all, delete-orphan")


class ImportRow(Base, TimestampMixin):
    """One staged record, with the field-level diff that review is based on."""
    __tablename__ = "import_rows"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    line_number: Mapped[int] = mapped_column(Integer, default=0)
    natural_key: Mapped[Optional[str]] = mapped_column(String(300), index=True)
    action: Mapped[str] = mapped_column(String(20), default="CREATE", index=True)
    target_id: Mapped[Optional[int]] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    # field -> {"current":…, "incoming":…, "conflict":bool}
    diff: Mapped[dict] = mapped_column(JSONType, default=dict)
    errors: Mapped[List[str]] = mapped_column(JSONType, default=list)
    decision: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)  # PENDING|APPROVE|SKIP
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[Optional[str]] = mapped_column(Text)
    batch: Mapped[ImportBatch] = relationship(back_populates="rows")


class KnowledgeChunk(Base, TimestampMixin):
    """§85 - retrieval corpus for the assistant. Built only from our own verified records,
    so the assistant can cite a source for every sentence it grounds an answer on."""
    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    slug: Mapped[Optional[str]] = mapped_column(String(220), index=True)
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    url_path: Mapped[Optional[str]] = mapped_column(String(300))
    source_url: Mapped[Optional[str]] = mapped_column(String(600))
    source_type: Mapped[Optional[str]] = mapped_column(String(60))
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    confidence: Mapped[Optional[str]] = mapped_column(String(30))
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    # Sparse lexical vector (term -> weight). On PostgreSQL an additional pgvector
    # column is created by the migration and used in preference to this.
    lexical_vector: Mapped[Optional[dict]] = mapped_column(JSONType, default=dict)
    embedding: Mapped[Optional[list]] = mapped_column(JSONType, default=list)
    # Which vectoriser produced `embedding`. A provider or dimension change makes stored
    # vectors incomparable, so the admin screen reports drift instead of silently degrading.
    embedding_model: Mapped[Optional[str]] = mapped_column(String(80), index=True)
    embedding_dim: Mapped[Optional[int]] = mapped_column(Integer)


class AssistantConversation(Base, TimestampMixin):
    """§85 - every assistant exchange is retained with the tools it called and the
    sources it cited, so an answer can be audited after the fact."""
    __tablename__ = "assistant_conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    student_profile_id: Mapped[Optional[int]] = mapped_column(ForeignKey("student_profiles.id"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    intent: Mapped[Optional[str]] = mapped_column(String(60))
    tools_used: Mapped[Optional[list]] = mapped_column(JSONType, default=list)
    sources: Mapped[Optional[list]] = mapped_column(JSONType, default=list)
    grounded: Mapped[bool] = mapped_column(default=True)
    escalated: Mapped[bool] = mapped_column(default=False)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    rating: Mapped[Optional[int]] = mapped_column(Integer)


class SourceCheck(Base, TimestampMixin):
    """
    One row per distinct citation URL, not per field.

    The ratio is the whole point: 2,385 unverified field claims across 58 URLs. A verifier
    who works field by field faces an impossible list; a verifier who works page by page
    opens 58 pages and confirms everything each one supports. This table is the page-level
    view, and the place an automated reachability check records what it found.

    `state` deliberately separates two things that look alike and are not:
      OK          - the page answered successfully
      BROKEN      - the server answered, and said the page is gone or erroring
      UNREACHABLE - we could not reach it at all (DNS, timeout, no egress)
      UNCHECKED   - never looked
    UNREACHABLE must never be presented as BROKEN. A sandbox with no outbound network
    would otherwise mark every citation on the platform as dead.
    """
    __tablename__ = "source_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(600), unique=True, index=True)
    state: Mapped[str] = mapped_column(String(20), default="UNCHECKED", index=True)
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    final_url: Mapped[Optional[str]] = mapped_column(String(600))
    note: Mapped[Optional[str]] = mapped_column(String(300))
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
