"""Notification fan-out (§60).

One table drives the staff inbox, the student portal and the outbound channels. Delivery
adapters are pluggable: WhatsApp and email are attempted when credentials exist, otherwise
the row stays QUEUED and is visible in the admin so nothing disappears silently.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.crm import Notification, User

TEMPLATES: dict[str, dict[str, str]] = {
    "LEAD_CREATED": {
        "title": "New enquiry: {name}",
        "body": "{name} ({phone}) asked about {interest}. Source: {source}.",
        "severity": "INFO",
    },
    "APPOINTMENT_BOOKED": {
        "title": "Appointment request: {name}",
        "body": "{name} requested {service} on {date} at {time} ({mode}).",
        "severity": "INFO",
    },
    "APPLICATION_STATUS": {
        "title": "Application {reference} is now {status}",
        "body": "{program} — moved from {previous} to {status}.",
        "severity": "INFO",
    },
    "DOCUMENT_UPLOADED": {
        "title": "Document uploaded: {doc_type}",
        "body": "{student} uploaded {filename} for review.",
        "severity": "INFO",
    },
    "DOCUMENT_REVIEWED": {
        "title": "Document {status}: {doc_type}",
        "body": "{note}",
        "severity": "INFO",
    },
    "DEADLINE_APPROACHING": {
        "title": "Deadline in {days} days: {program}",
        "body": "{intake} closes on {deadline}. Confirm the student's documents are ready.",
        "severity": "WARNING",
    },
    "VERIFICATION_DUE": {
        "title": "{count} records need verification",
        "body": "Fields past their review date are showing as NEEDS_REVIEW on the public site.",
        "severity": "WARNING",
    },
    "DATA_CONFLICT": {
        "title": "Conflicting sources: {entity}",
        "body": "{field} differs between sources. Resolve before the figure misleads a student.",
        "severity": "CRITICAL",
    },
}


def create(
    db: Session,
    trigger: str,
    *,
    recipient: str = "staff",
    channel: str = "WEBSITE",
    user_id: Optional[int] = None,
    student_profile_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    action_url: Optional[str] = None,
    severity: Optional[str] = None,
    commit: bool = True,
    **fields: Any,
) -> Notification:
    template = TEMPLATES.get(trigger, {})
    try:
        title = template.get("title", trigger.replace("_", " ").title()).format(**fields)
        body = template.get("body", "").format(**fields)
    except (KeyError, IndexError):
        title = trigger.replace("_", " ").title()
        body = ", ".join(f"{k}={v}" for k, v in fields.items())

    note = Notification(
        channel=channel, recipient=recipient, trigger=trigger, title=title[:200], body=body,
        status="QUEUED", user_id=user_id, student_profile_id=student_profile_id,
        entity_type=entity_type, entity_id=entity_id, action_url=action_url,
        severity=severity or template.get("severity", "INFO"),
    )
    db.add(note)
    if commit:
        db.commit()
        db.refresh(note)
    _deliver(db, note)
    return note


def broadcast_to_role(db: Session, role: str, trigger: str, **kwargs: Any) -> int:
    """Notify every active staff member holding a role."""
    users = list(db.scalars(select(User).where(User.role == role, User.is_active.is_(True))))
    for user in users:
        create(db, trigger, recipient=user.email, user_id=user.id, commit=False, **kwargs)
    db.commit()
    return len(users)


def _deliver(db: Session, note: Notification) -> None:
    """Attempt outbound delivery. Website notifications are delivered by being readable."""
    if note.channel == "WEBSITE":
        note.status = "SENT"
        note.sent_at = datetime.utcnow()
        db.commit()
        return

    if note.channel == "WHATSAPP" and settings.whatsapp_access_token:
        try:
            from app.services import whatsapp

            whatsapp.send_text(note.recipient, f"{note.title}\n\n{note.body}")
            note.status = "SENT"
            note.sent_at = datetime.utcnow()
        except Exception:
            note.status = "FAILED"
        db.commit()
        return

    # No transport configured: leave QUEUED so it is visible and retryable in the admin.


def mark_read(db: Session, note_id: int, *, user_id: Optional[int] = None,
              student_profile_id: Optional[int] = None) -> bool:
    note = db.get(Notification, note_id)
    if not note:
        return False
    if user_id and note.user_id and note.user_id != user_id:
        return False
    if student_profile_id and note.student_profile_id and note.student_profile_id != student_profile_id:
        return False
    note.read_at = datetime.utcnow()
    db.commit()
    return True


def unread_count(db: Session, *, user_id: Optional[int] = None,
                 student_profile_id: Optional[int] = None) -> int:
    stmt = select(Notification).where(Notification.read_at.is_(None))
    if user_id:
        stmt = stmt.where(Notification.user_id == user_id)
    if student_profile_id:
        stmt = stmt.where(Notification.student_profile_id == student_profile_id)
    return len(list(db.scalars(stmt)))
