"""Staff-side workflow endpoints that need real logic rather than generic CRUD.

Registered BEFORE the generic ``/{resource}`` catch-all in ``admin.py`` so these concrete
paths win. Covers: staff MFA (§89), the counselor dashboard (§59), the application pipeline
(§53-§56), document review with signed URLs (§57, §90), the notification inbox (§60), the
audit-log viewer (§91), scheduled-job control and assistant reindexing (§64).
"""
from __future__ import annotations

import logging
import secrets
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
from app.core.deps import (
    MFA_CHALLENGE_SCOPE,
    MFA_ENROLMENT_SCOPE,
    current_user,
    enrolling_user,
    require_role,
    require_staff,
)
from app.core.security import create_access_token, decode_token, verify_password
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
from app.services import extraction, read_audit, retrieval, tasks
from app.services import notifications as notify

log = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


def _audit(db: Session, user: User, action: str, entity_type: str,
           entity_id: Optional[int] = None, detail: Optional[Dict[str, Any]] = None) -> None:
    db.add(AuditLog(actor_id=user.id, actor_email=user.email, action=action,
                    entity_type=entity_type, entity_id=entity_id, detail=detail or {}))


def _audit_read(db: Session, user: User, resource: str, operation: str, *,
                subject_id: Optional[int] = None) -> None:
    """One event per staff request that exposes student data (W7-T4). Fails closed."""
    try:
        read_audit.audit_student_read(db, user, resource=resource, operation=operation,
                                      subject_id=subject_id)
    except read_audit.AuditUnavailable:
        raise HTTPException(
            503, "This view is unavailable because the access could not be recorded."
        ) from None

# ====================================================================== MFA
#
# The shape of this exchange, and why:
#
#   POST /auth/login            -> {"mfa_required": true, "challenge_token": ...}   (no session)
#   POST /auth/mfa/challenge    -> full session, from a TOTP *or* a recovery code
#
# A login that needs a second factor never receives an access token, and the challenge token
# it receives instead is scoped (refused by every `current_user`/`require_*` dependency),
# short-lived, and useless once the code attached to it has been spent. Codes are single-use:
# the 30-second time-step of the last accepted TOTP code is stored on the user, so replaying a
# code inside its drift window - which the earlier implementation allowed for up to 90 seconds -
# is refused.
class MfaVerifyIn(BaseModel):
    code: str


class MfaChallengeIn(BaseModel):
    """Step two. `challenge_token` is the supported path; email+password is kept because the
    operational shell scripts and third-party clients post it, and it carries both factors."""

    challenge_token: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    code: str


class MfaRecoverIn(BaseModel):
    email: str
    password: str
    recovery_code: str


class MfaRegenerateIn(BaseModel):
    code: str


class MfaResetIn(BaseModel):
    user_id: int
    code: str


def _utcnow() -> datetime:
    """Naive UTC, matching the DateTime columns. Mixing aware and naive values is what makes
    comparisons fail on PostgreSQL, so every write goes through this one function."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _issue_challenge(user: User) -> str:
    """A short-lived token that says exactly one thing: this password was correct."""
    ttl = max(1, int(settings.mfa_challenge_ttl_seconds))
    return create_access_token(
        str(user.id), user.role,
        extra={
            "scope": MFA_CHALLENGE_SCOPE,
            "jti": secrets.token_urlsafe(12),
            "exp": datetime.now(timezone.utc) + timedelta(seconds=ttl),
        },
    )


def _user_from_challenge(token: str, db: Session) -> User:
    try:
        payload = decode_token(token)
    except Exception as exc:
        raise HTTPException(401, "This sign-in attempt has expired. Start again.") from exc
    if payload.get("scope") != MFA_CHALLENGE_SCOPE:
        raise HTTPException(401, "This is not a sign-in challenge. Start again.")
    try:
        user_id = int(payload.get("sub", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(401, "This sign-in attempt is not valid. Start again.") from exc
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "This sign-in attempt is not valid. Start again.")
    return user


def _assert_not_locked(user: User) -> None:
    if user.locked_until and user.locked_until > _utcnow():
        raise HTTPException(423, "Too many incorrect codes. This account is locked for a few minutes.")


def _record_mfa_failure(db: Session, user: User) -> None:
    user.failed_login_count = (user.failed_login_count or 0) + 1
    if user.failed_login_count >= 5:
        user.locked_until = _utcnow() + timedelta(minutes=15)
    db.add(AuditLog(actor_id=user.id, actor_email=user.email, action="MFA_FAILED",
                    entity_type="users", entity_id=user.id,
                    detail={"attempts": user.failed_login_count}))
    db.commit()


def _accept_second_factor(db: Session, user: User, code: str) -> str:
    """Verify a TOTP or a recovery code, burning whichever was used.

    Returns "totp" or "recovery". Raises 401 with a specific message otherwise, so the UI can
    distinguish a wrong code from an already-used one without guessing.
    """
    typed = (code or "").strip()
    if not typed:
        _record_mfa_failure(db, user)
        raise HTTPException(401, "Enter the code from your authenticator app.")

    step = mfa.match_step(user.mfa_secret or "", typed)
    if step is not None:
        if user.mfa_last_used_step is not None and step <= user.mfa_last_used_step:
            _record_mfa_failure(db, user)
            raise HTTPException(
                401, "That code has already been used. Wait for your authenticator to show the next one."
            )
        user.mfa_last_used_step = step
        return "totp"

    remaining, used = mfa.consume_recovery_code(user.mfa_recovery_codes or [], typed)
    if used:
        user.mfa_recovery_codes = remaining
        return "recovery"

    _record_mfa_failure(db, user)
    raise HTTPException(401, "That code is not valid. Check your device clock and try again.")


def _notify_security_change(db: Session, user: User, trigger: str, **fields: Any) -> None:
    """Announce a security change: staff inbox always, email when SMTP is configured.

    Delegates to `notifications.security_notice` so the in-app record and the outbound copy
    are produced by one function - the CLI reset path uses the same one.
    """
    try:
        notify.security_notice(db, user, trigger, **fields)
    except Exception:            # a notification must never fail the security operation
        log.warning("security notice %s could not be recorded", trigger, exc_info=True)


@router.get("/auth/mfa/status")
def mfa_status(user: User = Depends(enrolling_user)):
    return {
        "enrolled": bool(user.mfa_confirmed_at),
        "pending_enrolment": bool(user.mfa_secret and not user.mfa_confirmed_at),
        "required": user.role in settings.mfa_required_role_list,
        "recovery_codes_remaining": mfa.recovery_code_count(user.mfa_recovery_codes),
        "issuer": settings.mfa_issuer,
        "challenge_ttl_seconds": settings.mfa_challenge_ttl_seconds,
    }


@router.post("/auth/mfa/enroll")
def mfa_enroll(db: Session = Depends(get_db), user: User = Depends(enrolling_user)):
    """Issue a fresh secret. It is not an active factor until a code confirms it.

    Rotating an existing secret deliberately clears the confirmation first: an unconfirmed
    secret must never be what the login gate trusts.
    """
    secret = mfa.generate_secret()
    user.mfa_secret = secret
    user.mfa_confirmed_at = None
    user.mfa_last_used_step = None
    otpauth_url = mfa.provisioning_uri(secret, user.email, settings.mfa_issuer)
    _audit(db, user, "MFA_ENROLL_START", "users", user.id)
    db.commit()
    # The secret is returned to its owner, once, over an authenticated channel. It is never
    # written to a log, an audit row, or any public endpoint.
    return {
        "secret": secret,
        "otpauth_url": otpauth_url,
        "qr_data_uri": mfa.qr_data_uri(otpauth_url),
        "manual_entry": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
        "digits": mfa.DIGITS, "period": mfa.PERIOD,
        "instructions": (
            "Scan the QR code with Google Authenticator, Microsoft Authenticator, Authy or "
            "1Password, or type the setup key, then enter the 6-digit code it shows."
        ),
    }


@router.post("/auth/mfa/confirm")
def mfa_confirm(body: MfaVerifyIn, db: Session = Depends(get_db), user: User = Depends(enrolling_user)):
    if not user.mfa_secret:
        raise HTTPException(400, "Start enrolment first.")
    step = mfa.match_step(user.mfa_secret, body.code)
    if step is None:
        _audit(db, user, "MFA_CONFIRM_FAILED", "users", user.id)
        db.commit()
        raise HTTPException(400, "That code is not valid. Check your device clock and try again.")
    plain, hashed = mfa.issue_recovery_codes(int(settings.mfa_recovery_code_count))
    user.mfa_recovery_codes = hashed
    user.mfa_last_used_step = step
    user.mfa_confirmed_at = _utcnow()
    user.failed_login_count = 0
    user.locked_until = None
    _audit(db, user, "MFA_ENABLED", "users", user.id,
           detail={"recovery_codes": len(plain)})
    _notify_security_change(db, user, "MFA_CHANGED", change="Two-factor authentication was turned on")
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
    if not (user.mfa_secret and user.mfa_confirmed_at):
        raise HTTPException(400, "MFA is not enabled on this account.")
    _accept_second_factor(db, user, body.code)
    user.mfa_secret = None
    user.mfa_confirmed_at = None
    user.mfa_recovery_codes = []
    user.mfa_last_used_step = None
    _audit(db, user, "MFA_DISABLED", "users", user.id)
    _notify_security_change(db, user, "MFA_CHANGED", change="Two-factor authentication was turned off")
    db.commit()
    return {"ok": True}


@router.post("/auth/mfa/challenge")
def mfa_challenge(body: MfaChallengeIn, db: Session = Depends(get_db)):
    """Second step of login for an MFA-enrolled account. Accepts a TOTP or a recovery code."""
    if body.challenge_token:
        user = _user_from_challenge(body.challenge_token, db)
    elif body.email and body.password:
        # Compatibility path for the shell smoke tests and any older client: the password is
        # re-proved here, so this is still two factors, not a shortcut around the gate.
        candidate = db.execute(
            select(User).where(User.email == body.email.lower().strip())
        ).scalars().first()
        if (not candidate or not verify_password(body.password, candidate.password_hash)
                or not candidate.is_active):
            raise HTTPException(401, "Invalid email or password")
        user = candidate
    else:
        raise HTTPException(400, "Start signing in again.")

    if not (user.mfa_secret and user.mfa_confirmed_at):
        raise HTTPException(400, "This account does not have two-factor authentication enabled.")
    _assert_not_locked(user)

    method = _accept_second_factor(db, user, body.code)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _utcnow()
    _audit(db, user, "LOGIN_MFA", "users", user.id, detail={"method": method})
    db.commit()
    return {"access_token": create_access_token(str(user.id), user.role), "token_type": "bearer",
            "mfa_method": method,
            "user": {"id": user.id, "email": user.email, "full_name": user.full_name,
                     "role": user.role, "mfa_enrolled": True}}


@router.post("/auth/mfa/recover")
def mfa_recover(body: MfaRecoverIn, db: Session = Depends(get_db)):
    """I lost my authenticator. Password + one unused recovery code gets back to enrolment.

    The recovery code is spent, and the answer is an *enrolment-scoped* token - not a session.
    The caller must register a new authenticator and confirm a fresh code before the account
    is usable again, so losing a device can never leave an account with a second factor the
    owner no longer controls.
    """
    user = db.execute(
        select(User).where(User.email == body.email.lower().strip())
    ).scalars().first()
    if not user or not verify_password(body.password, user.password_hash) or not user.is_active:
        raise HTTPException(401, "Invalid email or password")
    _assert_not_locked(user)

    remaining, used = mfa.consume_recovery_code(user.mfa_recovery_codes or [], body.recovery_code)
    if not used:
        _record_mfa_failure(db, user)
        raise HTTPException(401, "That recovery code is not valid or has already been used.")

    user.mfa_recovery_codes = remaining
    user.failed_login_count = 0
    user.locked_until = None
    _audit(db, user, "MFA_RECOVERY_CODE_USED", "users", user.id,
           detail={"remaining": len(remaining)})
    _notify_security_change(db, user, "MFA_CHANGED",
                            change="A recovery code was used to start re-enrolment")
    db.commit()
    return {
        "access_token": create_access_token(str(user.id), user.role,
                                            extra={"scope": MFA_ENROLMENT_SCOPE}),
        "token_type": "bearer",
        "scope": MFA_ENROLMENT_SCOPE,
        "recovery_codes_remaining": len(remaining),
        "message": ("Recovery code accepted. Set up your authenticator again: this token only "
                    "permits /admin/auth/mfa/enroll and /admin/auth/mfa/confirm."),
    }


@router.post("/auth/mfa/recovery-codes/regenerate")
def mfa_regenerate_recovery_codes(body: MfaRegenerateIn, db: Session = Depends(get_db),
                                  user: User = Depends(current_user)):
    """Replace every recovery code. The old ones stop working the moment this succeeds."""
    if not (user.mfa_secret and user.mfa_confirmed_at):
        raise HTTPException(400, "Enable two-factor authentication first.")
    _assert_not_locked(user)
    method = _accept_second_factor(db, user, body.code)
    plain, hashed = mfa.issue_recovery_codes(int(settings.mfa_recovery_code_count))
    user.mfa_recovery_codes = hashed
    _audit(db, user, "MFA_RECOVERY_CODES_REGENERATED", "users", user.id,
           detail={"count": len(plain), "method": method})
    _notify_security_change(db, user, "MFA_CHANGED", change="Recovery codes were regenerated")
    db.commit()
    return {"ok": True, "recovery_codes": plain,
            "warning": "The previous codes no longer work. Store these now; they are not shown again."}


@router.post("/auth/mfa/reset")
def mfa_reset(body: MfaResetIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Administrator-initiated reset of *another* account's two-factor enrolment.

    Requires an ADMIN session and a live code from the acting administrator's own
    authenticator, so a stolen session alone cannot strip the second factor off anyone. The
    affected account is notified and the action is audited against the actor.
    """
    if user.role != "ADMIN":
        raise HTTPException(403, "Only an administrator can reset another account's two-factor authentication.")
    if body.user_id == user.id:
        raise HTTPException(400, "Use your own security page to change your two-factor settings.")
    target = db.get(User, body.user_id)
    if not target:
        raise HTTPException(404, "User not found")
    _assert_not_locked(user)
    if not (user.mfa_secret and user.mfa_confirmed_at):
        raise HTTPException(400, "Your own account must have two-factor authentication enabled.")
    _accept_second_factor(db, user, body.code)

    target.mfa_secret = None
    target.mfa_confirmed_at = None
    target.mfa_recovery_codes = []
    target.mfa_last_used_step = None
    target.failed_login_count = 0
    target.locked_until = None
    _audit(db, user, "MFA_RESET_BY_ADMIN", "users", target.id,
           detail={"target_email": target.email, "target_role": target.role})
    _notify_security_change(db, target, "MFA_RESET",
                            change=f"An administrator ({user.email}) reset two-factor authentication")
    db.commit()
    return {"ok": True, "message": (
        f"Two-factor authentication was reset for {target.email}. They will be asked to enrol "
        "a new authenticator at their next sign-in."
    )}

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

    # Exposes lead names, phones, applications, appointments and pending documents.
    _audit_read(db, user, "counselor-dashboard", "dashboard")

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
    _audit_read(db, user, "applications", "board")
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
    # Student names, portal tokens and a freshly minted signed URL per row.
    _audit_read(db, user, "documents", "queue")
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
        or_(Notification.user_id == user.id, Notification.user_id.is_(None)),
        # An EMAIL row is the delivery record for a mailbox, not a second inbox item.
        notify.inbox_filter(),
    ).order_by(desc(Notification.created_at))
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = db.execute(stmt.limit(limit)).scalars().all()
    # Shared (``user_id IS NULL``) notifications include student-triggered ones whose body
    # carries the student's name, so the inbox is a student-data read.
    _audit_read(db, user, "notifications", "inbox")
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
    # The student's own words: question and answer text.
    _audit_read(db, user, "assistant-conversations", "list")
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
