"""Admin / counselor API: auth, dashboards, generic CMS CRUD, verification queue, analytics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from slugify import slugify
from sqlalchemy import text, func, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import MFA_ENROLMENT_SCOPE, current_user, require_role, require_staff
from app.core.security import create_access_token, verify_password
from app.models.content import (AdCampaign, BlogPost, Event, EventRegistration, Faq,
                                HomepageSection, HomepageSlide, Service, SiteSetting, SuccessStory)
from app.models.crm import (AnalyticsEvent, Application, ApplicationStatusHistory, Appointment,
                            AssistantConversation, AuditLog, Brochure, DataVerification, Document,
                            Lead, Notification, SearchQueryLog, ShortlistItem, StudentProfile,
                            User, WhatsAppConversation)
from app.models.education import (Country, Department, Faculty, Professor, Program, ProgramIntake,
                                  ProgramRequirement, Publication, ResearchArea, Scholarship,
                                  University, UniversityPartnership, UniversityRanking)

router = APIRouter(tags=["admin"])

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
}

# Resources that are records of what happened, not content. They must never be edited.
READ_ONLY_RESOURCES = {
    "audit-logs", "analytics-events", "search-queries", "assistant-conversations",
    "application-history",
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

    if user.mfa_secret and user.mfa_confirmed_at:
        db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="LOGIN_MFA_REQUIRED"))
        db.commit()
        return {"mfa_required": True, "email": user.email,
                "message": "Enter the 6-digit code from your authenticator app."}

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
        "recent_leads": [S.lead(l) for l in recent_leads],
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
            "top_countries": sorted([{"key": k, "count": v} for k, v in by_country.items()],
                                    key=lambda x: -x["count"])[:10],
            "top_subjects": sorted([{"key": k, "count": v} for k, v in by_subject.items()],
                                   key=lambda x: -x["count"])[:10],
            "top_degrees": sorted([{"key": k, "count": v} for k, v in by_degree.items()],
                                  key=lambda x: -x["count"])[:10]}


# ------------------------------------------------------------------ settings
@router.get("/settings")
def admin_settings(db: Session = Depends(get_db), user: User = Depends(require_role("ADMIN"))):
    row = db.get(SiteSetting, 1)
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


@router.put("/settings")
def update_settings(payload: Dict[str, Any], db: Session = Depends(get_db),
                    user: User = Depends(require_role("ADMIN"))):
    row = db.get(SiteSetting, 1)
    for k, v in payload.items():
        if hasattr(row, k) and k not in ("id", "created_at"):
            setattr(row, k, v)
    db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="UPDATE_SETTINGS",
                    entity_type="site_settings", entity_id=1, detail={"fields": list(payload)}))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ generic CRUD
def _model(resource: str):
    if resource not in RESOURCES:
        raise HTTPException(404, f"Unknown resource '{resource}'")
    return RESOURCES[resource]


def _row_to_dict(row) -> Dict[str, Any]:
    return {c.name: S._dt(getattr(row, c.name)) for c in row.__table__.columns}


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
    return {"total": total, "page": page, "page_size": page_size,
            "items": [_row_to_dict(r) for r in rows[start:start + page_size]]}


@router.get("/{resource}/{row_id}")
def get_row(resource: str, row_id: int, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    model, _, _ = _model(resource)
    row = db.get(model, row_id)
    if not row:
        raise HTTPException(404, "Not found")
    return _row_to_dict(row)


def _assert_writable(resource: str) -> None:
    if resource in READ_ONLY_RESOURCES:
        raise HTTPException(405, f"'{resource}' is an immutable audit record and cannot be modified.")


@router.post("/{resource}")
def create_row(resource: str, payload: Dict[str, Any], db: Session = Depends(get_db),
               user: User = Depends(require_staff)):
    model, slug_field, _ = _model(resource)
    data = {k: v for k, v in payload.items() if hasattr(model, k) and k != "id"}
    if slug_field and not data.get("slug") and data.get(slug_field):
        data["slug"] = slugify(str(data[slug_field]))
    if model is User and payload.get("password"):
        from app.core.security import hash_password
        data["password_hash"] = hash_password(payload["password"])
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
        row.password_hash = hash_password(payload.pop("password"))
    for k, v in payload.items():
        if hasattr(row, k) and k not in ("id", "created_at"):
            setattr(row, k, v)
    db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="UPDATE",
                    entity_type=resource, entity_id=row_id, detail={"fields": list(payload)}))
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
        if result.rowcount:
            detached[table] = int(result.rowcount)
    return detached


@router.delete("/{resource}/{row_id}")
def delete_row(resource: str, row_id: int, db: Session = Depends(get_db),
               user: User = Depends(require_role("SENIOR_COUNSELOR"))):
    _assert_writable(resource)
    model, _, _ = _model(resource)
    row = db.get(model, row_id)
    if not row:
        raise HTTPException(404, "Not found")

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
