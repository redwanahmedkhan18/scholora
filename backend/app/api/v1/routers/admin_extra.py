"""Staff-side workflow endpoints that need real logic rather than generic CRUD.

Registered BEFORE the generic ``/{resource}`` catch-all in ``admin.py`` so these concrete
paths win. Covers: staff MFA (§89), the counselor dashboard (§59), the application pipeline
(§53-§56), document review with signed URLs (§57, §90), the notification inbox (§60), the
audit-log viewer (§91), scheduled-job control and assistant reindexing (§64).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.core import llm, mfa, storage
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import current_user, enrolling_user, require_role, require_staff
from app.core.security import create_access_token, verify_password
from app.models.base import APPLICATION_STATUSES
from app.models.crm import (
    Application,
    ApplicationStatusHistory,
    Appointment,
    AssistantConversation,
    AuditLog,
    Document,
    Lead,
    Notification,
    StudentProfile,
    User,
)
from app.models.education import Program, University
from app.services import extraction, retrieval, tasks
from app.services import notifications as notify

router = APIRouter(tags=["admin"])


def _audit(db: Session, user: User, action: str, entity_type: str,
           entity_id: Optional[int] = None, detail: Optional[Dict[str, Any]] = None) -> None:
    db.add(AuditLog(actor_id=user.id, actor_email=user.email, action=action,
                    entity_type=entity_type, entity_id=entity_id, detail=detail or {}))


# ====================================================================== MFA
class MfaVerifyIn(BaseModel):
    code: str


class MfaChallengeIn(BaseModel):
    email: str
    password: str
    code: str


@router.get("/auth/mfa/status")
def mfa_status(user: User = Depends(enrolling_user)):
    return {
        "enrolled": bool(user.mfa_confirmed_at),
        "required": user.role in settings.mfa_required_role_list,
        "recovery_codes_remaining": len(user.mfa_recovery_codes or []),
        "issuer": settings.mfa_issuer,
    }


@router.post("/auth/mfa/enroll")
def mfa_enroll(db: Session = Depends(get_db), user: User = Depends(enrolling_user)):
    """Issue a fresh secret. It is not active until a valid code confirms it."""
    secret = mfa.generate_secret()
    user.mfa_secret = secret
    user.mfa_confirmed_at = None
    _audit(db, user, "MFA_ENROLL_START", "users", user.id)
    db.commit()
    return {
        "secret": secret,
        "otpauth_url": mfa.provisioning_uri(secret, user.email, settings.mfa_issuer),
        "manual_entry": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
        "digits": 6, "period": 30,
        "instructions": "Add the key to Google Authenticator, Authy or 1Password, then enter the 6-digit code to confirm.",
    }


@router.post("/auth/mfa/confirm")
def mfa_confirm(body: MfaVerifyIn, db: Session = Depends(get_db), user: User = Depends(enrolling_user)):
    if not user.mfa_secret:
        raise HTTPException(400, "Start enrolment first.")
    if not mfa.verify(user.mfa_secret, body.code):
        raise HTTPException(400, "That code is not valid. Check your device clock and try again.")
    plain, hashed = mfa.issue_recovery_codes()
    user.mfa_recovery_codes = hashed
    user.mfa_confirmed_at = datetime.now(timezone.utc)
    _audit(db, user, "MFA_ENABLED", "users", user.id)
    db.commit()
    # Both factors have now been demonstrated in this exchange - the password that minted the
    # enrolment token, and possession of the secret just proved by the code. Hand back a real
    # session so a user who was forced here by the mandatory-MFA rule is not left holding a
    # token that can do nothing but enrol.
    return {
        "ok": True, "recovery_codes": plain,
        "warning": "Store these now. Each works once and they are not shown again.",
        "access_token": create_access_token(str(user.id), user.role),
        "token_type": "bearer",
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name,
                 "role": user.role, "mfa_enrolled": True},
    }


@router.post("/auth/mfa/disable")
def mfa_disable(body: MfaVerifyIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if user.role in settings.mfa_required_role_list:
        raise HTTPException(403, f"MFA is mandatory for the {user.role} role and cannot be switched off.")
    if not (user.mfa_secret and mfa.verify(user.mfa_secret, body.code)):
        raise HTTPException(400, "Enter a valid code to disable MFA.")
    user.mfa_secret = None
    user.mfa_confirmed_at = None
    user.mfa_recovery_codes = []
    _audit(db, user, "MFA_DISABLED", "users", user.id)
    db.commit()
    return {"ok": True}


@router.post("/auth/mfa/challenge")
def mfa_challenge(body: MfaChallengeIn, db: Session = Depends(get_db)):
    """Second step of login for an MFA-enrolled account. Accepts a TOTP or a recovery code."""
    user = db.execute(select(User).where(User.email == body.email.lower().strip())).scalars().first()
    if not user or not verify_password(body.password, user.password_hash) or not user.is_active:
        raise HTTPException(401, "Invalid email or password")
    if user.locked_until and user.locked_until > datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(423, "Account temporarily locked after repeated failed codes.")
    if not (user.mfa_secret and user.mfa_confirmed_at):
        raise HTTPException(400, "This account does not have MFA enabled.")

    ok = mfa.verify(user.mfa_secret, body.code)
    if not ok:
        remaining, used = mfa.consume_recovery_code(user.mfa_recovery_codes or [], body.code)
        if used:
            user.mfa_recovery_codes = remaining
            ok = True

    if not ok:
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= 5:
            user.locked_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=15)
        db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="MFA_FAILED",
                        entity_type="users", entity_id=user.id,
                        detail={"attempts": user.failed_login_count}))
        db.commit()
        raise HTTPException(401, "Invalid authentication code")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc)
    db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="LOGIN_MFA",
                    entity_type="users", entity_id=user.id))
    db.commit()
    return {"access_token": create_access_token(str(user.id), user.role), "token_type": "bearer",
            "user": {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role}}


# ======================================================= counselor dashboard
@router.get("/counselor/dashboard")
def counselor_dashboard(db: Session = Depends(get_db), user: User = Depends(require_staff)):
    """My students, my applications, what is overdue, what closes soon."""
    today = date.today()
    now = datetime.utcnow()

    my_leads = db.execute(
        select(Lead).where(Lead.assigned_to == user.id).order_by(desc(Lead.updated_at)).limit(50)
    ).scalars().all()
    if not my_leads:  # a new counselor sees the unassigned queue instead of an empty page
        my_leads = db.execute(
            select(Lead).where(Lead.assigned_to.is_(None), Lead.stage != "LOST")
            .order_by(desc(Lead.created_at)).limit(20)
        ).scalars().all()

    my_apps = db.execute(
        select(Application).where(
            or_(Application.counselor_id == user.id, Application.counselor_id.is_(None))
        ).order_by(desc(Application.updated_at)).limit(50)
    ).scalars().all()

    upcoming = db.execute(
        select(Appointment).where(
            Appointment.scheduled_date >= today,
            Appointment.status.in_(["REQUESTED", "CONFIRMED"]),
        ).order_by(Appointment.scheduled_date.asc()).limit(20)
    ).scalars().all()

    pending_docs = db.execute(
        select(Document).where(Document.status == "UPLOADED")
        .order_by(desc(Document.created_at)).limit(25)
    ).scalars().all()

    stale = db.execute(
        select(Lead).where(
            Lead.assigned_to == user.id,
            Lead.stage.notin_(["LOST", "DEPARTED"]),
            Lead.updated_at < now - timedelta(days=7),
        ).order_by(Lead.updated_at.asc()).limit(15)
    ).scalars().all()

    inbox = db.execute(
        select(Notification).where(
            or_(Notification.user_id == user.id, Notification.user_id.is_(None)),
            Notification.read_at.is_(None),
        ).order_by(desc(Notification.created_at)).limit(20)
    ).scalars().all()

    def _app_brief(a: Application) -> Dict[str, Any]:
        program = db.get(Program, a.program_id)
        profile = db.get(StudentProfile, a.student_profile_id)
        university = db.get(University, program.university_id) if program else None
        return {"id": a.id, "reference": a.reference, "status": a.status,
                "student": profile.full_name if profile else None,
                "student_token": profile.public_token if profile else None,
                "program": program.name if program else None,
                "university": university.name if university else None,
                "intake_label": a.intake_label, "updated_at": S._dt(a.updated_at)}

    return {
        "counselor": {"id": user.id, "name": user.full_name, "role": user.role},
        "kpis": {
            "my_leads": len(my_leads),
            "my_applications": len(my_apps),
            "appointments_upcoming": len(upcoming),
            "documents_awaiting_review": len(pending_docs),
            "needs_follow_up": len(stale),
            "unread_notifications": len(inbox),
        },
        "leads": [{"id": lead_row.id, "reference": lead_row.reference, "full_name": lead_row.full_name,
                   "phone": lead_row.phone,
                   "stage": lead_row.stage, "source": lead_row.source, "priority": lead_row.priority,
                   "interest": " / ".join(x for x in [lead_row.interest_subject, lead_row.interest_degree,
                                                      lead_row.interest_country] if x) or None,
                   "intake": lead_row.intake, "next_action_at": S._dt(lead_row.next_action_at),
                   "updated_at": S._dt(lead_row.updated_at)} for lead_row in my_leads],
        "applications": [_app_brief(a) for a in my_apps],
        "pipeline": {
            status: sum(1 for a in my_apps if a.status == status)
            for status in APPLICATION_STATUSES if any(a.status == status for a in my_apps)
        },
        "appointments": [{"id": a.id, "reference": a.reference, "full_name": a.full_name,
                          "phone": a.phone, "date": S._dt(a.scheduled_date),
                          "time": a.scheduled_time, "mode": a.mode, "status": a.status}
                         for a in upcoming],
        "documents_pending": [{"id": d.id, "doc_type": d.doc_type, "filename": d.original_filename,
                               "student_profile_id": d.student_profile_id,
                               "uploaded_at": S._dt(d.created_at)} for d in pending_docs],
        "follow_ups": [{"id": lead_row.id, "full_name": lead_row.full_name, "phone": lead_row.phone,
                        "stage": lead_row.stage,
                        "days_idle": (now - lead_row.updated_at).days} for lead_row in stale],
        "notifications": [{"id": n.id, "title": n.title, "body": n.body, "severity": n.severity,
                           "action_url": n.action_url, "at": S._dt(n.created_at)} for n in inbox],
    }


# ======================================================== application pipeline
class StatusIn(BaseModel):
    status: str
    note: Optional[str] = None


@router.get("/applications/board")
def applications_board(
    db: Session = Depends(get_db), user: User = Depends(require_staff),
    status: Optional[str] = None, q: Optional[str] = None,
):
    stmt = select(Application).order_by(desc(Application.updated_at))
    if status:
        stmt = stmt.where(Application.status == status)
    if q:
        stmt = stmt.where(Application.reference.ilike(f"%{q}%"))
    rows = db.execute(stmt.limit(300)).scalars().all()

    items = []
    for a in rows:
        program = db.get(Program, a.program_id)
        profile = db.get(StudentProfile, a.student_profile_id)
        university = db.get(University, program.university_id) if program else None
        items.append({
            "id": a.id, "reference": a.reference, "status": a.status,
            "intake_label": a.intake_label, "notes": a.notes,
            "counselor_id": a.counselor_id,
            "student": {"id": profile.id, "name": profile.full_name, "email": profile.email,
                        "phone": profile.phone, "token": profile.public_token} if profile else None,
            "program": {"id": program.id, "name": program.name, "slug": program.slug,
                        "degree_level": program.degree_level} if program else None,
            "university": {"id": university.id, "name": university.name} if university else None,
            "submitted_at": S._dt(a.submitted_at), "decision_at": S._dt(a.decision_at),
            "updated_at": S._dt(a.updated_at),
        })
    counts = {s: 0 for s in APPLICATION_STATUSES}
    for status_row in db.execute(select(Application.status, func.count(Application.id))
                                 .group_by(Application.status)).all():
        counts[status_row[0]] = status_row[1]
    return {"stages": APPLICATION_STATUSES, "counts": counts, "total": len(items), "items": items}


@router.post("/applications/{application_id}/status")
def set_application_status(application_id: int, body: StatusIn, db: Session = Depends(get_db),
                           user: User = Depends(require_staff)):
    if body.status not in APPLICATION_STATUSES:
        raise HTTPException(400, f"Unknown status. Allowed: {', '.join(APPLICATION_STATUSES)}")
    row = db.get(Application, application_id)
    if not row:
        raise HTTPException(404, "Application not found")

    previous = row.status
    row.status = body.status
    if body.status == "SUBMITTED" and not row.submitted_at:
        row.submitted_at = datetime.utcnow()
    if body.status in {"UNCONDITIONAL_OFFER", "CONDITIONAL_OFFER", "REJECTED"} and not row.decision_at:
        row.decision_at = datetime.utcnow()
    if row.counselor_id is None:
        row.counselor_id = user.id

    db.add(ApplicationStatusHistory(application_id=row.id, from_status=previous,
                                    to_status=body.status, changed_by=user.id, note=body.note))
    _audit(db, user, "APPLICATION_STATUS", "applications", row.id,
           {"from": previous, "to": body.status})
    db.commit()

    program = db.get(Program, row.program_id)
    profile = db.get(StudentProfile, row.student_profile_id)
    notify.create(
        db, "APPLICATION_STATUS", recipient=profile.email or "student" if profile else "student",
        student_profile_id=row.student_profile_id, reference=row.reference, status=body.status,
        previous=previous, program=program.name if program else "your programme",
        entity_type="application", entity_id=row.id,
        action_url=f"/my/{profile.public_token}" if profile else None,
    )
    return {"ok": True, "status": row.status, "previous": previous}


@router.post("/applications/{application_id}/assign")
def assign_application(application_id: int, counselor_id: int, db: Session = Depends(get_db),
                       user: User = Depends(require_role("SENIOR_COUNSELOR"))):
    row = db.get(Application, application_id)
    if not row:
        raise HTTPException(404, "Application not found")
    row.counselor_id = counselor_id
    _audit(db, user, "APPLICATION_ASSIGN", "applications", row.id, {"counselor_id": counselor_id})
    db.commit()
    return {"ok": True}


# ========================================================== document review
class DocReviewIn(BaseModel):
    status: str
    review_note: Optional[str] = None
    expires_at: Optional[date] = None


DOC_STATUSES = ["UPLOADED", "UNDER_REVIEW", "ACCEPTED", "REJECTED", "EXPIRED"]


@router.get("/documents/queue")
def documents_queue(db: Session = Depends(get_db), user: User = Depends(require_staff),
                    status: Optional[str] = None, student_profile_id: Optional[int] = None):
    stmt = select(Document).order_by(desc(Document.created_at))
    if status:
        stmt = stmt.where(Document.status == status)
    if student_profile_id:
        stmt = stmt.where(Document.student_profile_id == student_profile_id)
    rows = db.execute(stmt.limit(200)).scalars().all()
    items = []
    for d in rows:
        profile = db.get(StudentProfile, d.student_profile_id)
        items.append({
            "id": d.id, "doc_type": d.doc_type, "original_filename": d.original_filename,
            "mime_type": d.mime_type, "size_bytes": d.size_bytes, "status": d.status,
            "review_note": d.review_note, "expires_at": S._dt(d.expires_at),
            "uploaded_at": S._dt(d.created_at),
            "student": {"id": profile.id, "name": profile.full_name, "token": profile.public_token}
            if profile else None,
            # Signed, expiring, and generated per request — never a durable public link.
            "download_url": storage.signed_url(d.storage_key),
        })
    counts = {s: 0 for s in DOC_STATUSES}
    for row in db.execute(select(Document.status, func.count(Document.id))
                          .group_by(Document.status)).all():
        counts[row[0]] = row[1]
    return {"statuses": DOC_STATUSES, "counts": counts, "total": len(items), "items": items}


@router.post("/documents/{document_id}/review")
def review_document(document_id: int, body: DocReviewIn, db: Session = Depends(get_db),
                    user: User = Depends(require_staff)):
    if body.status not in DOC_STATUSES:
        raise HTTPException(400, f"Unknown status. Allowed: {', '.join(DOC_STATUSES)}")
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    doc.status = body.status
    doc.review_note = body.review_note
    if body.expires_at:
        doc.expires_at = body.expires_at
    _audit(db, user, "DOCUMENT_REVIEW", "documents", doc.id, {"status": body.status})
    db.commit()

    profile = db.get(StudentProfile, doc.student_profile_id)
    notify.create(
        db, "DOCUMENT_REVIEWED", recipient=(profile.email if profile and profile.email else "student"),
        student_profile_id=doc.student_profile_id, doc_type=doc.doc_type, status=body.status,
        note=body.review_note or f"Your {doc.doc_type.replace('_', ' ').lower()} is now {body.status.lower()}.",
        entity_type="document", entity_id=doc.id, severity="WARNING" if body.status == "REJECTED" else "INFO",
        action_url=f"/my/{profile.public_token}" if profile else None,
    )
    return {"ok": True, "status": doc.status}


@router.get("/documents/{document_id}/link")
def document_link(document_id: int, db: Session = Depends(get_db), user: User = Depends(require_staff),
                  ttl: int = Query(default=300, ge=30, le=3600)):
    """Mint a fresh short-lived link. Every mint is audited."""
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    _audit(db, user, "DOCUMENT_ACCESS", "documents", doc.id, {"ttl": ttl})
    db.commit()
    return {"url": storage.signed_url(doc.storage_key, ttl), "expires_in_seconds": ttl}


# ============================================================ notifications
@router.get("/notifications/inbox")
def notifications_inbox(db: Session = Depends(get_db), user: User = Depends(require_staff),
                        unread_only: bool = False, limit: int = Query(default=50, le=200)):
    stmt = select(Notification).where(
        or_(Notification.user_id == user.id, Notification.user_id.is_(None))
    ).order_by(desc(Notification.created_at))
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = db.execute(stmt.limit(limit)).scalars().all()
    return {
        "unread": notify.unread_count(db, user_id=user.id),
        "items": [{"id": n.id, "title": n.title, "body": n.body, "trigger": n.trigger,
                   "channel": n.channel, "severity": n.severity, "status": n.status,
                   "entity_type": n.entity_type, "entity_id": n.entity_id,
                   "action_url": n.action_url, "read": n.read_at is not None,
                   "at": S._dt(n.created_at)} for n in rows],
    }


@router.post("/notifications/{note_id}/read")
def notification_read(note_id: int, db: Session = Depends(get_db), user: User = Depends(require_staff)):
    if not notify.mark_read(db, note_id, user_id=user.id):
        raise HTTPException(404, "Notification not found")
    return {"ok": True}


@router.post("/notifications/read-all")
def notifications_read_all(db: Session = Depends(get_db), user: User = Depends(require_staff)):
    rows = db.execute(select(Notification).where(
        or_(Notification.user_id == user.id, Notification.user_id.is_(None)),
        Notification.read_at.is_(None),
    )).scalars().all()
    for n in rows:
        n.read_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "marked": len(rows)}


# ============================================================== audit viewer
@router.get("/audit/log")
def audit_log(db: Session = Depends(get_db), user: User = Depends(require_role("SENIOR_COUNSELOR")),
              actor: Optional[str] = None, action: Optional[str] = None,
              entity_type: Optional[str] = None, days: int = Query(default=30, ge=1, le=365),
              page: int = 1, page_size: int = Query(default=50, le=200)):
    since = datetime.utcnow() - timedelta(days=days)
    stmt = select(AuditLog).where(AuditLog.created_at >= since)
    if actor:
        stmt = stmt.where(AuditLog.actor_email.ilike(f"%{actor}%"))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(
        stmt.order_by(desc(AuditLog.created_at)).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()

    actions = [r[0] for r in db.execute(
        select(AuditLog.action, func.count(AuditLog.id)).where(AuditLog.created_at >= since)
        .group_by(AuditLog.action).order_by(desc(func.count(AuditLog.id)))
    ).all()]
    entities = [r[0] for r in db.execute(
        select(AuditLog.entity_type).where(AuditLog.created_at >= since,
                                           AuditLog.entity_type.is_not(None))
        .group_by(AuditLog.entity_type)
    ).all()]

    return {
        "total": total, "page": page, "page_size": page_size,
        "filters": {"actions": actions, "entity_types": sorted(e for e in entities if e)},
        "items": [{"id": r.id, "actor_id": r.actor_id, "actor_email": r.actor_email,
                   "action": r.action, "entity_type": r.entity_type, "entity_id": r.entity_id,
                   "detail": r.detail, "ip_address": getattr(r, "ip_address", None),
                   "at": S._dt(r.created_at)} for r in rows],
    }


# ====================================================== jobs + assistant ops
@router.get("/jobs")
def list_jobs(user: User = Depends(require_role("DATA_MANAGER"))):
    return {
        "scheduler": tasks.scheduler_mode(),
        "jobs": [{"name": name, "interval_seconds": tasks.SCHEDULE[name],
                  "description": (tasks.JOBS[name].__doc__ or "").strip().split("\n")[0]}
                 for name in tasks.JOBS],
    }


@router.post("/jobs/{name}/run")
def run_job(name: str, db: Session = Depends(get_db),
            user: User = Depends(require_role("DATA_MANAGER"))):
    try:
        result = tasks.run_job(name)
    except KeyError as exc:
        raise HTTPException(404, f"Unknown job '{name}'") from exc
    _audit(db, user, "JOB_RUN", "jobs", None, {"job": name, "result": str(result)[:400]})
    db.commit()
    return {"ok": True, "result": result}


@router.get("/assistant/providers")
def assistant_providers(user: User = Depends(require_role("DATA_MANAGER"))):
    """Grok / Mistral / Gemini status. Reports key presence only, never key material."""
    return llm.health()


@router.post("/assistant/providers/{name}/test")
def assistant_provider_test(name: str, db: Session = Depends(get_db),
                            user: User = Depends(require_role("ADMIN"))):
    """Live round trip against one provider so an operator can verify a key and model name."""
    if name not in llm.PROVIDER_META:
        raise HTTPException(404, f"Unknown provider '{name}'")
    result = llm.ping(name)
    _audit(db, user, "LLM_PROVIDER_TEST", "assistant", None,
           {"provider": name, "ok": result.get("ok"), "error": result.get("error")})
    db.commit()
    return result


@router.post("/assistant/reindex")
def assistant_reindex(db: Session = Depends(get_db),
                      user: User = Depends(require_role("DATA_MANAGER"))):
    counts = retrieval.rebuild_index(db)
    _audit(db, user, "ASSISTANT_REINDEX", "knowledge_chunks", None, counts)
    db.commit()
    return {"ok": True, **counts}


@router.get("/assistant/conversations")
def assistant_conversations(db: Session = Depends(get_db), user: User = Depends(require_staff),
                            grounded: Optional[bool] = None, escalated: Optional[bool] = None,
                            limit: int = Query(default=50, le=200)):
    stmt = select(AssistantConversation).order_by(desc(AssistantConversation.created_at))
    if grounded is not None:
        stmt = stmt.where(AssistantConversation.grounded.is_(grounded))
    if escalated is not None:
        stmt = stmt.where(AssistantConversation.escalated.is_(escalated))
    rows = db.execute(stmt.limit(limit)).scalars().all()
    total = db.execute(select(func.count(AssistantConversation.id))).scalar_one()
    ungrounded = db.execute(select(func.count(AssistantConversation.id))
                            .where(AssistantConversation.grounded.is_(False))).scalar_one()
    return {
        "total": total,
        "ungrounded": ungrounded,
        "ungrounded_rate": round(ungrounded / total * 100, 1) if total else 0.0,
        "items": [{"id": r.id, "session_id": r.session_id, "question": r.question,
                   "answer": r.answer, "intent": r.intent, "grounded": r.grounded,
                   "escalated": r.escalated, "tools_used": r.tools_used, "sources": r.sources,
                   "latency_ms": r.latency_ms, "at": S._dt(r.created_at)} for r in rows],
    }


# --------------------------------------------------------------- live site extraction
class ExtractIn(BaseModel):
    # Advice-driving input: forbid extras so a misspelled field cannot be silently
    # dropped and change what gets read. Same rule as ProfileIn and MatchIn.
    model_config = {"extra": "forbid"}

    name: str
    country: str = ""
    instruction: str = ""
    website: str = ""
    max_pages: int = 6
    use_llm: bool = True


@router.post("/extraction/run")
def extraction_run(payload: ExtractIn, db: Session = Depends(get_db),
                   user: User = Depends(require_role("DATA_MANAGER"))) -> Dict[str, Any]:
    """
    Read a university's own website and answer a question from it.

    Staff-only and DATA_MANAGER-gated. This drives outbound traffic to third-party
    sites under the consultancy's name, and its output is unverified by definition, so
    it is not something to expose to the public or to a counselor who might paste a
    result straight into a student chat.
    """
    if not payload.name.strip() and not payload.website.strip():
        raise HTTPException(status_code=422, detail="Give a university name or a website.")
    result = extraction.run(
        db, name=payload.name, country=payload.country,
        instruction=payload.instruction, website=payload.website,
        max_pages=max(1, min(payload.max_pages, 12)), use_llm=payload.use_llm)
    result["requested_by"] = user.email
    return result
