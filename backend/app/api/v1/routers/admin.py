"""Admin / counselor API: auth, dashboards, generic CMS CRUD, verification queue, analytics."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from slugify import slugify
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.core import validation
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import (
    MFA_CHALLENGE_SCOPE,
    MFA_ENROLMENT_SCOPE,
    ROLE_HIERARCHY,
    current_user,
    require_role,
    require_staff,
)
from app.core.security import create_access_token, verify_password
from app.models.content import (
    AdCampaign,
    BlogPost,
    Event,
    EventRegistration,
    Faq,
    HomepageSection,
    HomepageSlide,
    Service,
    SiteSetting,
    SuccessStory,
)
from app.models.crm import (
    AnalyticsEvent,
    Application,
    ApplicationStatusHistory,
    Appointment,
    AssistantConversation,
    AuditLog,
    Brochure,
    DataVerification,
    Document,
    Lead,
    Notification,
    PendingObjectDeletion,
    SearchQueryLog,
    ShortlistItem,
    StudentProfile,
    User,
    WhatsAppConversation,
)
from app.models.education import (
    Country,
    Department,
    Faculty,
    Professor,
    Program,
    ProgramIntake,
    ProgramRequirement,
    Publication,
    ResearchArea,
    Scholarship,
    University,
    UniversityPartnership,
    UniversityRanking,
)
from app.services import catalogue_writes, erasure, read_audit

router = APIRouter(tags=["admin"])

log = logging.getLogger(__name__)

# key -> (model, slug source field or None, searchable fields)
RESOURCES: Dict[str, Any] = {
    "homepage-slides": (HomepageSlide, None, ["title", "key"]),
    "homepage-sections": (HomepageSection, None, ["key", "title"]),
    "countries": (Country, "name", ["name", "slug"]),
    "universities": (University, "name", ["name", "city"]),
    "university-rankings": (UniversityRanking, None, ["provider"]),
    "faculties": (Faculty, "name", ["name", "kind"]),
    "departments": (Department, "name", ["name"]),
    "programs": (Program, "name", ["name", "discipline"]),
    "program-requirements": (ProgramRequirement, None, []),
    "program-intakes": (ProgramIntake, None, ["label"]),
    "scholarships": (Scholarship, "name", ["name", "provider"]),
    "professors": (Professor, "full_name", ["full_name", "title"]),
    "publications": (Publication, None, ["title"]),
    "services": (Service, "title", ["title"]),
    "success-stories": (SuccessStory, "student_name", ["student_name", "university_name"]),
    "events": (Event, "title", ["title"]),
    "blog-posts": (BlogPost, "title", ["title"]),
    "faqs": (Faq, None, ["question"]),
    "campaigns": (AdCampaign, "name", ["name"]),
    "leads": (Lead, None, ["full_name", "phone", "reference"]),
    "appointments": (Appointment, None, ["full_name", "phone", "reference"]),
    "users": (User, None, ["full_name", "email"]),
    "partnerships": (UniversityPartnership, None, ["representative"]),
    "verifications": (DataVerification, None, ["entity_type", "field_name"]),
    "applications": (Application, None, ["reference", "status"]),
    "application-history": (ApplicationStatusHistory, None, ["to_status"]),
    "documents": (Document, None, ["doc_type", "original_filename", "status"]),
    "notifications": (Notification, None, ["title", "trigger", "recipient"]),
    "audit-logs": (AuditLog, None, ["actor_email", "action", "entity_type"]),
    "student-profiles": (StudentProfile, None, ["full_name", "email", "phone"]),
    "event-registrations": (EventRegistration, None, ["full_name", "email", "phone"]),
    "search-queries": (SearchQueryLog, None, ["query", "subject", "country"]),
    "shortlist-items": (ShortlistItem, None, ["label", "slug"]),
    "assistant-conversations": (AssistantConversation, None, ["question", "intent", "session_id"]),
    "brochures": (Brochure, None, ["student_name", "token"]),
    "research-areas": (ResearchArea, "name", ["name"]),
    "analytics-events": (AnalyticsEvent, None, ["name", "path"]),
    "pending-object-deletions": (PendingObjectDeletion, None, ["storage_key"]),
}

#: Columns that only the review path may set (W9/W12).
#:
#: ``confidence`` and its dates are decisions, not fields: they say a named person checked stated
#: evidence. The generic CRUD form takes any model attribute from the payload, so without this a
#: COUNSELOR could PUT ``{"confidence": "VERIFIED"}`` on a programme and the read-time policy would
#: present it to students as official - the entire verification workflow bypassed in one request.
#: Evidence columns (``source_url``, ``source_type``, ``retrieved_at``) stay writable: they support
#: a decision without making one.
TRUST_COLUMNS = frozenset({"confidence", "verified_at", "verified_by", "review_due_at"})

#: Resources whose *state* columns are review decisions and may not be set through CRUD.
REVIEW_STATE_COLUMNS = {"verifications": frozenset({"status"})}

# Resources that are records of what happened, not content. They must never be edited.
READ_ONLY_RESOURCES = {
    "audit-logs", "analytics-events", "search-queries", "assistant-conversations",
    "application-history",
    # A queued object deletion is outstanding privacy work, not editable content: staff can see
    # that it is pending, and cannot edit or delete the task out from under the retry job.
    "pending-object-deletions",
}


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    """Step 1. If the account has MFA enrolled, no token is issued here — the caller must
    complete POST /admin/auth/mfa/challenge with a TOTP or recovery code."""
    user = db.execute(select(User).where(User.email == body.email.lower().strip())).scalars().first()
    if not user or not verify_password(body.password, user.password_hash) or not user.is_active:
        raise HTTPException(401, "Invalid email or password")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if user.locked_until and user.locked_until > now:
        raise HTTPException(423, "Account temporarily locked. Try again shortly.")

    # A password published in the repository (in the seed data and, until now, on the login
    # screen) is not a credential. In production it is refused outright instead of quietly
    # opening the back office; the operator rotates it with `python -m app.mgmt_admin`.
    if settings.is_production and body.password in settings.published_password_list:
        db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="LOGIN_REFUSED_DEFAULT_PASSWORD",
                        entity_type="users", entity_id=user.id))
        db.commit()
        raise HTTPException(
            403,
            "This account still uses a published default password. An operator must set a new "
            "password before it can sign in.",
        )

    if user.mfa_secret and user.mfa_confirmed_at:
        db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="LOGIN_MFA_REQUIRED"))
        db.commit()
        # A scoped, short-lived handle for step two. It is not a session: every authenticated
        # dependency refuses it, so a password alone still grants nothing.
        return {"mfa_required": True, "email": user.email,
                "challenge_token": create_access_token(
                    str(user.id), user.role,
                    extra={"scope": MFA_CHALLENGE_SCOPE, "jti": secrets.token_urlsafe(12),
                           "exp": datetime.now(timezone.utc)
                           + timedelta(seconds=max(1, settings.mfa_challenge_ttl_seconds))}),
                "challenge_expires_in": max(1, settings.mfa_challenge_ttl_seconds),
                "mfa_method": "totp",
                "message": ("Open your authenticator app and enter the current 6-digit code, "
                            "or one of your recovery codes.")}

    # Mandatory-MFA roles that have not enrolled get an enrolment-scoped token, never a
    # session. Previously this branch returned a full token with "mfa_recommended": true,
    # so the policy in MFA_REQUIRED_ROLES was advisory for everything except switching MFA
    # off - an ADMIN could run the whole back office having never set up a second factor.
    if user.role in settings.mfa_required_role_list:
        db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="LOGIN_MFA_ENROLMENT_REQUIRED",
                        entity_type="users", entity_id=user.id, detail={"role": user.role}))
        db.commit()
        return {
            "access_token": create_access_token(str(user.id), user.role,
                                                extra={"scope": MFA_ENROLMENT_SCOPE}),
            "token_type": "bearer",
            "mfa_required": False,
            "mfa_enrolment_required": True,
            "scope": MFA_ENROLMENT_SCOPE,
            "message": (f"Multi-factor authentication is mandatory for the {user.role} role. "
                        "This token only permits /admin/auth/mfa/enroll and /admin/auth/mfa/confirm."),
            "user": {"id": user.id, "email": user.email, "full_name": user.full_name,
                     "role": user.role, "mfa_enrolled": False},
        }

    user.failed_login_count = 0
    user.last_login_at = datetime.now(timezone.utc)
    db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="LOGIN"))
    db.commit()
    return {"access_token": create_access_token(str(user.id), user.role),
            "token_type": "bearer",
            "mfa_required": False,
            "mfa_enrolment_required": False,
            "mfa_recommended": False,
            "user": {"id": user.id, "email": user.email, "full_name": user.full_name,
                     "role": user.role, "mfa_enrolled": bool(user.mfa_confirmed_at)}}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role,
            "mfa_enrolled": bool(user.mfa_confirmed_at),
            "mfa_required": user.role in settings.mfa_required_role_list}


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(require_staff)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    week = now - timedelta(days=7)

    def count(model, *where):
        stmt = select(func.count(model.id))
        for w in where:
            stmt = stmt.where(w)
        return db.execute(stmt).scalar_one()

    stage_rows = db.execute(select(Lead.stage, func.count(Lead.id)).group_by(Lead.stage)).all()
    source_rows = db.execute(select(Lead.source, func.count(Lead.id)).group_by(Lead.source)).all()
    top_searches = db.execute(
        select(SearchQueryLog.subject, SearchQueryLog.country, SearchQueryLog.degree_level,
               func.count(SearchQueryLog.id).label("n"))
        .group_by(SearchQueryLog.subject, SearchQueryLog.country, SearchQueryLog.degree_level)
        .order_by(func.count(SearchQueryLog.id).desc()).limit(10)).all()
    deadlines = db.execute(select(ProgramIntake).where(
        ProgramIntake.application_deadline.isnot(None),
        ProgramIntake.application_deadline >= now.date()).order_by(ProgramIntake.application_deadline).limit(8)).scalars().all()
    recent_leads = db.execute(select(Lead).order_by(Lead.created_at.desc()).limit(8)).scalars().all()
    campaigns = db.execute(select(AdCampaign)).scalars().all()
    lead_by_campaign = dict(db.execute(select(Lead.campaign_id, func.count(Lead.id))
                                       .group_by(Lead.campaign_id)).all())
    # ``recent_leads`` below is serialised with each lead's name, phone and email, so this
    # dashboard is a student-data read and is audited as one.
    _audit_read(db, user, "dashboard", "dashboard")
    return {
        "counters": {
            "new_leads": count(Lead, Lead.stage == "NEW"),
            "leads_7d": count(Lead, Lead.created_at >= week),
            "active_students": count(StudentProfile),
            "appointments": count(Appointment, Appointment.status.in_(["REQUESTED", "CONFIRMED"])),
            "applications": count(Application),
            "offers": count(Application, Application.status.in_(["CONDITIONAL_OFFER", "UNCONDITIONAL_OFFER"])),
            "visa_cases": count(Application, Application.status.in_(["VISA_PREPARATION", "VISA_SUBMITTED"])),
            "brochures": count(Brochure),
            "universities": count(University),
            "programs": count(Program),
            "scholarships": count(Scholarship),
            "professors": count(Professor),
            "whatsapp_open": count(WhatsAppConversation, WhatsAppConversation.handed_off.is_(True)),
            "needs_verification": count(DataVerification, DataVerification.status.in_(["NEEDS_REVIEW", "CONFLICTING", "OUTDATED"])),
        },
        "lead_pipeline": [{"stage": s, "count": c} for s, c in stage_rows],
        "lead_sources": [{"source": s, "count": c} for s, c in source_rows],
        "top_searches": [{"subject": s, "country": c, "degree_level": d, "count": n} for s, c, d, n in top_searches],
        "upcoming_deadlines": [{"label": i.label, "deadline": S._dt(i.application_deadline),
                                "program_id": i.program_id} for i in deadlines],
        "recent_leads": [S.lead(lead_row) for lead_row in recent_leads],
        "campaigns": [{**S.campaign(c, internal=True), "leads": lead_by_campaign.get(c.id, 0)} for c in campaigns],
    }


@router.get("/analytics")
def analytics(days: int = 30, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    events = db.execute(select(AnalyticsEvent.name, func.count(AnalyticsEvent.id))
                        .where(AnalyticsEvent.created_at >= since)
                        .group_by(AnalyticsEvent.name)).all()
    searches = db.execute(select(SearchQueryLog).where(SearchQueryLog.created_at >= since)).scalars().all()
    by_country: Dict[str, int] = {}
    by_subject: Dict[str, int] = {}
    by_degree: Dict[str, int] = {}
    for s in searches:
        if s.country:
            by_country[s.country] = by_country.get(s.country, 0) + 1
        if s.subject:
            by_subject[s.subject] = by_subject.get(s.subject, 0) + 1
        if s.degree_level:
            by_degree[s.degree_level] = by_degree.get(s.degree_level, 0) + 1
    return {"events": [{"name": n, "count": c} for n, c in events],
            "search_total": len(searches),
            "top_countries": [{"key": k, "count": v}
                              for k, v in sorted(by_country.items(), key=lambda kv: -kv[1])[:10]],
            "top_subjects": [{"key": k, "count": v}
                             for k, v in sorted(by_subject.items(), key=lambda kv: -kv[1])[:10]],
            "top_degrees": [{"key": k, "count": v}
                            for k, v in sorted(by_degree.items(), key=lambda kv: -kv[1])[:10]]}


# ------------------------------------------------------------------ settings
# The public contact details have their own validated endpoints as well as a place in the
# generic settings form. One row (SiteSetting id=1) stays the single source of truth: the
# header, footer, contact page, mobile navigation and WhatsApp button all read it through
# GET /public/contact, so there is no second copy to drift out of sync.
CONTACT_PHONE_FIELD = "Primary phone number"
CONTACT_FIELDS = ("phones", "emails", "whatsapp_number", "facebook_page_url")


class ContactSettingsIn(BaseModel):
    """The four published contact channels, validated and normalised before they are stored.

    Every rejection message is written for the administrator who typed the value, and the
    normalisation is shared with the generic settings endpoint so both routes agree.
    """

    primary_phone: str
    whatsapp_number: str
    email: str
    facebook_url: str
    office_address: Optional[str] = None
    office_hours: Optional[str] = None
    whatsapp_message: Optional[str] = None
    whatsapp_enabled: bool = True
    # The full list, so an administrator can retire a number that is no longer answered.
    # Omitting them keeps the stored extras (a caller that only edits the primary cannot
    # silently delete the office's second line); sending them replaces the list.
    extra_phones: Optional[list[str]] = None
    extra_emails: Optional[list[str]] = None

    @field_validator("extra_phones", mode="before")
    @classmethod
    def _extra_phones(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.splitlines()
        return validation.normalize_phone_list([str(v) for v in value], field="Additional phone number")

    @field_validator("extra_emails", mode="before")
    @classmethod
    def _extra_emails(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.splitlines()
        return validation.normalize_email_list([str(v) for v in value], field="Additional email address")

    @field_validator("primary_phone", mode="before")
    @classmethod
    def _primary_phone(cls, value: Any) -> str:
        return validation.normalize_phone(str(value or ""), field=CONTACT_PHONE_FIELD)

    @field_validator("whatsapp_number", mode="before")
    @classmethod
    def _whatsapp_number(cls, value: Any) -> str:
        return validation.normalize_phone(str(value or ""), field="WhatsApp number")

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, value: Any) -> str:
        return validation.normalize_email(str(value or ""), field="Public contact email")

    @field_validator("facebook_url", mode="before")
    @classmethod
    def _facebook_url(cls, value: Any) -> str:
        return validation.normalize_facebook_url(str(value or ""))


class ContactSettingsOut(BaseModel):
    primary_phone: str
    primary_phone_display: str
    extra_phones: list[str] = []
    whatsapp_number: str
    whatsapp_link_number: str
    email: str
    extra_emails: list[str] = []
    facebook_url: str
    office_address: Optional[str] = None
    office_hours: Optional[str] = None
    whatsapp_message: Optional[str] = None
    whatsapp_enabled: bool = True
    updated_at: Optional[str] = None


def contact_payload(row: SiteSetting) -> Dict[str, Any]:
    """Read the four published channels off the settings row, tolerating legacy values.

    A row seeded before this endpoint existed can hold something an entry form would reject
    (the shipped placeholder number, for instance). Reading never fails: unusable values are
    returned as-is so the administrator can see and correct them.
    """
    phones = [str(p) for p in (row.phones or []) if str(p).strip()]
    emails = [str(e) for e in (row.emails or []) if str(e).strip()]
    wa = (row.whatsapp_number or "").strip()
    return {
        "primary_phone": phones[0] if phones else "",
        "primary_phone_display": validation.phone_display(phones[0]) if phones else "",
        "extra_phones": phones[1:],
        "whatsapp_number": wa,
        "whatsapp_link_number": validation.whatsapp_number(wa),
        "email": emails[0] if emails else "",
        "extra_emails": emails[1:],
        "facebook_url": row.facebook_page_url or "",
        "office_address": row.office_address,
        "office_hours": row.office_hours,
        "whatsapp_message": row.whatsapp_default_message,
        "whatsapp_enabled": bool(row.whatsapp_enabled),
        "updated_at": S._dt(row.updated_at),
    }


def _settings_row(db: Session) -> SiteSetting:
    row = db.get(SiteSetting, 1)
    if row is None:  # the settings row is created by the migration/seed; absence is a 404
        raise HTTPException(status_code=404, detail="Site settings are not initialised")
    return row


@router.get("/settings/contact", response_model=ContactSettingsOut)
def admin_contact_settings(db: Session = Depends(get_db), user: User = Depends(require_role("ADMIN"))):
    """Exactly what the public website is publishing right now."""
    return contact_payload(_settings_row(db))


@router.put("/settings/contact", response_model=ContactSettingsOut)
def update_contact_settings(body: ContactSettingsIn, db: Session = Depends(get_db),
                            user: User = Depends(require_role("ADMIN"))):
    """Persist the four channels in one transaction and audit what actually changed.

    The API is the validation boundary, not the form: the same request can be made with curl.
    Extra entries already stored in the phone/email lists are kept behind the new primary one
    so an office with two numbers does not lose the second by editing the first.
    """
    row = _settings_row(db)
    before = contact_payload(row)

    old_phones = [str(p) for p in (row.phones or []) if str(p).strip()]
    old_emails = [str(e) for e in (row.emails or []) if str(e).strip()]
    extras = ([p for p in body.extra_phones if p != body.primary_phone]
              if body.extra_phones is not None
              else [p for p in old_phones if p != body.primary_phone])
    extra_mails = ([e for e in body.extra_emails if e != body.email]
                   if body.extra_emails is not None
                   else [e for e in old_emails if e != body.email])
    row.phones = [body.primary_phone] + extras
    row.emails = [body.email] + extra_mails
    row.whatsapp_number = body.whatsapp_number
    row.whatsapp_enabled = body.whatsapp_enabled
    row.facebook_page_url = body.facebook_url
    if body.office_address is not None:
        row.office_address = body.office_address
    if body.office_hours is not None:
        row.office_hours = body.office_hours
    if body.whatsapp_message is not None:
        row.whatsapp_default_message = body.whatsapp_message

    after = contact_payload(row)
    changed = {k: {"from": before.get(k), "to": after.get(k)}
               for k in ("primary_phone", "whatsapp_number", "email", "facebook_url")
               if before.get(k) != after.get(k)}
    db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="UPDATE_CONTACT_SETTINGS",
                    entity_type="site_settings", entity_id=1, detail={"changed": changed}))
    db.commit()
    db.refresh(row)
    return contact_payload(row)


@router.get("/settings")
def admin_settings(db: Session = Depends(get_db), user: User = Depends(require_role("ADMIN"))):
    return {c.name: getattr(_settings_row(db), c.name) for c in SiteSetting.__table__.columns}


SETTINGS_READ_ONLY = {"id", "created_at", "updated_at"}
# Values that end up in an href, a tel:/mailto: attribute or a rendered embed. They are
# scheme- and format-checked here because this endpoint is reachable with curl, not just
# through the form that happens to have a validation message.
SETTINGS_URL_FIELDS = {
    "facebook_page_url", "messenger_url", "instagram_url", "linkedin_url", "youtube_url",
    "tiktok_url", "x_url", "logo_url", "favicon_url", "default_og_image", "map_embed_url",
}


def _validate_settings_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a generic settings payload, or reject it with a readable 422.

    Unknown or read-only keys are dropped rather than written: the old implementation set any
    attribute that happened to exist on the row, which is how ``javascript:`` reached an
    ``<a href>`` on every page. ``(field, reason)`` pairs are surfaced as the HTTP error.
    """
    columns = {c.name for c in SiteSetting.__table__.columns} - SETTINGS_READ_ONLY
    clean: Dict[str, Any] = {}
    problems: list[Dict[str, str]] = []
    for key, value in payload.items():
        if key not in columns:
            continue
        try:
            if key == "phones":
                clean[key] = validation.normalize_phone_list(value or [], field="Phone number")
            elif key == "emails":
                clean[key] = validation.normalize_email_list(value or [], field="Email address")
            elif key == "whatsapp_number":
                text = str(value or "").strip()
                clean[key] = validation.normalize_phone(text, field="WhatsApp number") if text else None
            elif key == "facebook_page_url":
                text = str(value or "").strip()
                clean[key] = validation.normalize_facebook_url(text) if text else None
            elif key in SETTINGS_URL_FIELDS:
                text = str(value or "").strip()
                clean[key] = (validation.normalize_https_url(text, field=key.replace("_", " "),
                                                             allow_http=key == "map_embed_url")
                              if text else None)
            else:
                clean[key] = value
        except ValueError as exc:
            problems.append({"field": key, "message": str(exc)})
    if problems:
        raise HTTPException(status_code=422, detail=problems)
    return clean


@router.put("/settings")
def update_settings(payload: Dict[str, Any], db: Session = Depends(get_db),
                    user: User = Depends(require_role("ADMIN"))):
    """Every editable setting in one write, validated and audited field by field."""
    row = _settings_row(db)
    clean = _validate_settings_payload(payload)
    changed = {k: {"from": getattr(row, k), "to": v}
               for k, v in clean.items() if getattr(row, k) != v}
    for k, v in clean.items():
        setattr(row, k, v)
    db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="UPDATE_SETTINGS",
                    entity_type="site_settings", entity_id=1,
                    detail={"fields": sorted(changed), "count": len(changed)}))
    db.commit()
    return {"ok": True, "changed": sorted(changed)}


# ------------------------------------------------------------------ generic CRUD
# Columns that must never appear in an API response. Keyed by model so that adding a resource
# to RESOURCES cannot silently leak a new secret: a model without an entry keeps every column.
SECRET_COLUMNS: Dict[Any, frozenset] = {
    User: frozenset({"password_hash", "mfa_secret", "mfa_recovery_codes"}),
}


def _model(resource: str):
    if resource not in RESOURCES:
        raise HTTPException(404, f"Unknown resource '{resource}'")
    return RESOURCES[resource]


def _row_to_dict(row) -> Dict[str, Any]:
    """Serialise one row for the back office, minus the columns that are secrets.

    ``users`` is the row type where this matters: ``password_hash`` is an offline-crackable
    credential, ``mfa_secret`` is the TOTP seed and ``mfa_recovery_codes`` are the single-use
    codes printed at enrolment. Together they are the whole account, so they never travel to a
    browser - not to an ADMIN and not to any other staff role that may read this endpoint. The
    fields stay write-only: passwords arrive through create/update and are stored as a hash.
    """
    hidden = SECRET_COLUMNS.get(type(row), frozenset())
    return {c.name: S._dt(getattr(row, c.name))
            for c in row.__table__.columns if c.name not in hidden}


@router.get("/resources")
def list_resources(user: User = Depends(require_staff)):
    return sorted(RESOURCES.keys())


@router.get("/{resource}")
def list_rows(resource: str, q: Optional[str] = None, page: int = 1, page_size: int = 25,
              order: Optional[str] = None, db: Session = Depends(get_db),
              user: User = Depends(require_staff)):
    model, _, searchable = _model(resource)
    stmt = select(model)
    if q and searchable:
        from sqlalchemy import or_
        stmt = stmt.where(or_(*[getattr(model, f).ilike(f"%{q}%") for f in searchable]))
    if order and hasattr(model, order.lstrip("-")):
        col = getattr(model, order.lstrip("-"))
        stmt = stmt.order_by(col.desc() if order.startswith("-") else col)
    elif hasattr(model, "id"):
        stmt = stmt.order_by(model.id.desc())
    rows = db.execute(stmt).scalars().all()
    total = len(rows)
    start = (page - 1) * page_size
    if read_audit.is_student_resource(resource):
        # One event per request. The query string, the page number and the result count are
        # deliberately not recorded: they are caller input and can carry a student's name.
        _audit_read(db, user, resource, "list")
    return {"total": total, "page": page, "page_size": page_size,
            "items": [_row_to_dict(r) for r in rows[start:start + page_size]]}


@router.get("/{resource}/{row_id}")
def get_row(resource: str, row_id: int, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    model, _, _ = _model(resource)
    row = db.get(model, row_id)
    if not row:
        raise HTTPException(404, "Not found")
    if read_audit.is_student_resource(resource):
        _audit_read(db, user, resource, "detail", subject_id=row_id)
    return _row_to_dict(row)


def _audit_read(db: Session, user: User, resource: str, operation: str, *,
                subject_id: Optional[int] = None) -> None:
    """Record a staff read of student data, or refuse to serve it.

    Wrapped so every call site behaves identically: one event per request, and a 503 rather
    than an unaudited response if the record cannot be written.
    """
    try:
        read_audit.audit_student_read(db, user, resource=resource, operation=operation,
                                      subject_id=subject_id)
    except read_audit.AuditUnavailable:
        raise HTTPException(
            503, "This view is unavailable because the access could not be recorded."
        ) from None


def _assert_trust_authority(resource: str, user: User) -> None:
    """Writing the verification register is a review action, not data entry."""
    if resource not in REVIEW_STATE_COLUMNS:
        return
    if ROLE_HIERARCHY.get(user.role, 0) < ROLE_HIERARCHY.get("DATA_MANAGER", 100):
        raise HTTPException(
            403, "verification records are written through the review workflow, not by this role")


def _assert_writable(resource: str) -> None:
    if resource in READ_ONLY_RESOURCES:
        raise HTTPException(405, f"'{resource}' is an immutable audit record and cannot be modified.")


def _checked_password(password: Any) -> str:
    """Apply the staff-password policy before anything is hashed or stored.

    The generic CRUD form is not a validation boundary - anyone can POST here directly - so the
    policy is enforced on the way in. A rejected password is never stored: the 422 is raised
    before the row is built or the hash is replaced.
    """
    from app.core.passwords import password_policy_error

    error = password_policy_error(password)
    if error:
        raise HTTPException(422, error)
    return str(password)


@router.post("/{resource}")
def create_row(resource: str, payload: Dict[str, Any], db: Session = Depends(get_db),
               user: User = Depends(require_staff)):
    model, slug_field, _ = _model(resource)
    _assert_trust_authority(resource, user)
    blocked = TRUST_COLUMNS | REVIEW_STATE_COLUMNS.get(resource, frozenset())
    data = {k: v for k, v in payload.items()
            if hasattr(model, k) and k != "id" and k not in blocked}
    if slug_field and not data.get("slug") and data.get(slug_field):
        data["slug"] = slugify(str(data[slug_field]))
    if model is User and payload.get("password"):
        from app.core.security import hash_password
        data.pop("password", None)
        data["password_hash"] = hash_password(_checked_password(payload["password"]))
    row = model(**data)
    db.add(row)
    db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="CREATE", entity_type=resource))
    db.commit()
    db.refresh(row)
    return _row_to_dict(row)


@router.put("/{resource}/{row_id}")
def update_row(resource: str, row_id: int, payload: Dict[str, Any], db: Session = Depends(get_db),
               user: User = Depends(require_staff)):
    _assert_writable(resource)
    model, _, _ = _model(resource)
    row = db.get(model, row_id)
    if not row:
        raise HTTPException(404, "Not found")
    if model is User and payload.get("password"):
        from app.core.security import hash_password
        row.password_hash = hash_password(_checked_password(payload.pop("password")))
    _assert_trust_authority(resource, user)
    blocked = TRUST_COLUMNS | REVIEW_STATE_COLUMNS.get(resource, frozenset())
    detail: Dict[str, Any] = {"fields": list(payload)}
    if catalogue_writes.governs(model):
        # A catalogue record carries a verification claim, and this generic form is not the review
        # workflow. One function applies the values *and* invalidates a verification the edit no
        # longer supports, in this request's single transaction: confidence becomes NEEDS_REVIEW,
        # the reviewer and dates are cleared, the edited field is re-registered as a claim
        # awaiting review, and a CatalogueChange keeps the previous value beside the new one.
        # See app/services/catalogue_writes.py for why that is the importer's rule, not a new one.
        edit = catalogue_writes.apply_direct_edit(
            db, model=model, row=row, payload=payload, actor=user.email, blocked=blocked)
        detail["invalidated_verification"] = edit["invalidated"]
        if edit["invalidated"]:
            # Field names and the previous state only - never the values, which may be student-
            # facing content and do not belong in an audit line.
            detail["material_fields"] = edit["material_fields"]
            detail["previous_confidence"] = edit["previous_confidence"]
    else:
        for k, v in payload.items():
            if hasattr(row, k) and k not in ("id", "created_at") and k not in blocked:
                setattr(row, k, v)
    db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="UPDATE",
                    entity_type=resource, entity_id=row_id, detail=detail))
    db.commit()
    db.refresh(row)
    return _row_to_dict(row)


# Columns that point at a staff user. When that user is deleted the row they are attached to
# must survive: an application, a lead or an audit entry is a business record that outlives
# whoever happened to be handling it. Every one of these columns is nullable, so the delete
# detaches rather than cascades.
#
# audit_logs is the one that matters most. It also stores `actor_email`, so clearing actor_id
# keeps the trail readable ("who did this") while removing the foreign key - which is what lets
# the audit log stay immutable and still allow staff offboarding. Never add audit_logs to a
# cascade: erasing a departing employee's actions is exactly what an audit log exists to prevent.
_USER_REFERENCES: tuple[tuple[str, str], ...] = (
    ("audit_logs", "actor_id"),
    ("applications", "counselor_id"),
    ("application_status_history", "changed_by"),
    ("appointments", "counselor_id"),
    ("leads", "assigned_to"),
    ("notifications", "user_id"),
    ("student_profiles", "user_id"),
    ("whatsapp_conversations", "assigned_to"),
)


def _detach_user_references(db: Session, user_id: int) -> dict[str, int]:
    """Null out every FK pointing at this user. Returns {table: rows_detached}."""
    detached: dict[str, int] = {}
    for table, column in _USER_REFERENCES:
        result = db.execute(
            text(f"UPDATE {table} SET {column} = NULL WHERE {column} = :uid"), {"uid": user_id}
        )
        # ``execute`` returns CursorResult for DML; getattr keeps this honest across drivers
        # (and across SQLAlchemy versions where the static return type is the base Result).
        rowcount = getattr(result, "rowcount", 0)
        if rowcount:
            detached[table] = int(rowcount)
    return detached


@router.delete("/{resource}/{row_id}")
def delete_row(resource: str, row_id: int, db: Session = Depends(get_db),
               user: User = Depends(require_role("SENIOR_COUNSELOR"))):
    _assert_writable(resource)
    model, _, _ = _model(resource)
    row = db.get(model, row_id)
    if not row:
        raise HTTPException(404, "Not found")

    # A student profile is the root of an ownership tree with eight dependent tables, uploaded
    # documents in object storage and server-owned assistant sessions. A bare row delete can only
    # fail, so it is routed through the transactional eraser, which revokes sessions first and
    # removes the dependents leaf-first in one transaction.
    if model is StudentProfile:
        try:
            counts = erasure.erase_student_profile(
                db, row_id, actor_id=user.id, actor_email=user.email)
        except erasure.ProfileNotFound:
            raise HTTPException(404, "Not found") from None
        except erasure.ErasureConflict:
            # An expected data conflict: the eraser rolled back, nothing changed, and the client
            # can act on it. Distinct from a defect in our own code.
            raise HTTPException(
                409, "This profile still has records that block erasure; nothing was changed."
            ) from None
        except Exception:
            # Unexpected, and by construction pre-commit: the eraser no longer lets a failure in
            # the post-commit object deletion escape (it reports that as unconfirmed work below).
            # So the transaction is genuinely rolled back when we reach here.
            log.exception("student-profile erasure failed unexpectedly for id=%s", row_id)
            raise HTTPException(
                500, "The profile could not be erased and nothing was changed."
            ) from None
        body = {"ok": True, "erased": counts}
        if counts.get("storage_unconfirmed"):
            # The database erasure committed but file deletion could not be confirmed. Never
            # dress this up as a rollback: the records are gone and the caller must know that.
            body["complete"] = False
            body["objects_pending"] = counts.get("objects_pending", 0)
            body["detail"] = (
                "Student records were erased; some file deletions remain pending and will be "
                "retried."
            )
        elif counts.get("objects_pending"):
            # Be explicit rather than claiming a complete erasure: the database records are gone,
            # but some stored files are still queued and will be retried.
            body["complete"] = False
            body["objects_pending"] = counts["objects_pending"]
        else:
            body["complete"] = True
        return body

    detail: dict = {}
    if model is User:
        if row.id == user.id:
            raise HTTPException(400, "You cannot delete your own account.")
        detail["detached"] = _detach_user_references(db, row.id)

    db.delete(row)
    db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="DELETE",
                    entity_type=resource, entity_id=row_id, detail=detail))
    db.commit()
    return {"ok": True}
