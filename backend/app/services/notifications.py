"""Notification fan-out (§60).

One table drives the staff inbox, the student portal and the outbound channels. Delivery
adapters are pluggable: WhatsApp and email are attempted when credentials exist, otherwise
the row stays QUEUED and is visible in the admin so nothing disappears silently.

Two kinds of row live here and they are deliberately distinguishable:

* an **inbox row** (channel WEBSITE/WHATSAPP) - the record an operator or student reads;
* an **email copy** (channel EMAIL) - the delivery record for the message that went to the
  recipient's mailbox. It is kept out of the inbox by ``INBOX_CHANNELS`` so one event does
  not look like two.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Coroutine, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.crm import Notification, User

log = logging.getLogger(__name__)

#: Channels that produce something to read in the in-app inbox. An EMAIL row is a delivery
#: record for a mailbox, not a second inbox item.
INBOX_CHANNELS = ("WEBSITE", "WHATSAPP", "SMS", "IN_APP")

#: Triggers important enough to also leave the building by email. All of them are security
#: events about the recipient's own account, which is the one category worth an outbound
#: message; marketing blasts are not part of this application.
EMAIL_TRIGGERS = frozenset({"MFA_CHANGED", "MFA_RESET"})

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
    "MFA_CHANGED": {
        "title": "Two-factor authentication changed",
        "body": "{change}. If this was not you, contact an administrator immediately.",
        "severity": "WARNING",
    },
    "MFA_RESET": {
        "title": "Two-factor authentication was reset by an administrator",
        "body": "{change}. You will be asked to enrol an authenticator app at your next sign-in.",
        "severity": "CRITICAL",
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
    _deliver(db, note, commit=commit)
    return note


def broadcast_to_role(db: Session, role: str, trigger: str, **kwargs: Any) -> int:
    """Notify every active staff member holding a role."""
    users = list(db.scalars(select(User).where(User.role == role, User.is_active.is_(True))))
    for user in users:
        create(db, trigger, recipient=user.email, user_id=user.id, commit=False, **kwargs)
    db.commit()
    return len(users)


def _run_async(coro_factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Run an async delivery call from synchronous code in any calling context.

    FastAPI serves ``async def`` endpoints on the event-loop thread and sync endpoints (and
    Celery tasks) on a worker thread. ``asyncio.run`` is correct on the latter but raises
    ``RuntimeError`` on the former, which would turn real sends into silent FAILED rows. When
    a loop is already running, the coroutine is executed on a private loop in a helper thread
    and its result returned, so delivery happens in both contexts.

    ``coro_factory`` is called inside the target context so a coroutine is never constructed
    in one thread and awaited in another.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="scholara-whatsapp") as pool:
        return pool.submit(lambda: asyncio.run(coro_factory())).result()


def _deliver(db: Session, note: Notification, commit: bool = True) -> None:
    """Attempt outbound delivery. Website notifications are delivered by being readable."""
    if note.channel == "WEBSITE":
        note.status = "SENT"
        note.sent_at = datetime.utcnow()
        if commit:
            db.commit()
        return

    if note.channel == "WHATSAPP" and settings.whatsapp_access_token:
        try:
            from app.services import whatsapp

            # `send_message` reports HTTP rejections as a status code instead of raising, so
            # the result must be inspected: marking every attempt SENT hid real failures.
            result = _run_async(lambda: whatsapp.send_message(note.recipient, f"{note.title}\n\n{note.body}"))
            status = result.get("status") if isinstance(result, dict) else None
            if isinstance(status, int) and 200 <= status < 300:
                note.status = "SENT"
                note.sent_at = datetime.utcnow()
            else:
                log.warning("notifications: WhatsApp delivery rejected with status %r", status)
                note.status = "FAILED"
        except Exception as exc:  # noqa: BLE001 - the row records the failure, not the process
            log.warning("notifications: WhatsApp delivery failed (%s)", type(exc).__name__)
            note.status = "FAILED"
        if commit:
            db.commit()
        return

    if note.channel == "EMAIL":
        _deliver_email(note)
        if commit:
            db.commit()
        return

    # No transport configured: leave QUEUED so it is visible and retryable in the admin.


def _deliver_email(note: Notification) -> None:
    """Attempt one email and record the server's answer on the row.

    Never raises: a mailbox problem must not fail the security operation that triggered the
    notice. The row keeps the verbatim (scrubbed) error so the admin can see *why*.
    """
    from app.services import email, email_templates

    rendered = email_templates.security_notice(
        note.title or "Security notice",
        note.body or "",
        action_url=note.action_url,
        account=note.recipient,
    )
    result = email.send(note.recipient, rendered.subject, rendered.text, rendered.html)
    if result.ok:
        note.status = "SENT"
        note.sent_at = datetime.utcnow()
    elif result.detail.get("skipped"):
        # Nothing was attempted because SMTP is not configured: QUEUED is the honest state.
        note.status = "QUEUED"
        note.body = f"{note.body or ''}\n\n[not sent: {result.error}]".strip()
        log.info("notifications: email copy for %s left QUEUED - %s", note.trigger, result.error)
    else:
        note.status = "FAILED"
        # The reason belongs on the record, not only in the log: an operator looking at the
        # admin sees why the message did not arrive and what to fix.
        note.body = f"{note.body or ''}\n\n[delivery failed after {result.attempts} attempt(s): {result.error}]".strip()
        log.warning("notifications: email copy for %s failed - %s", note.trigger, result.error)


def security_notice(db: Session, user: User, trigger: str, **fields: Any) -> Notification:
    """Record a security event in the inbox and, when SMTP exists, email the account holder.

    The single entry point for MFA enrol/disable/reset/recovery so the in-app record and the
    outbound copy can never drift apart. Delivery failures are recorded, never raised: the
    security change itself has already succeeded by the time this runs.
    """
    note = create(db, trigger, recipient=user.email, user_id=user.id,
                  entity_type="users", entity_id=user.id, action_url="/admin",
                  commit=False, **fields)
    if trigger not in EMAIL_TRIGGERS or not user.email:
        db.commit()
        return note

    copy = Notification(
        channel="EMAIL", recipient=user.email, trigger=trigger,
        title=note.title, body=note.body, status="QUEUED",
        # user_id is recorded for traceability, but INBOX_CHANNELS keeps it out of the inbox.
        user_id=user.id, entity_type="users", entity_id=user.id,
        action_url=note.action_url, severity=note.severity,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    _deliver_email(copy)
    db.commit()
    return note


def inbox_filter():
    """The clause that keeps mailbox delivery records out of the in-app inbox."""
    return Notification.channel.in_(INBOX_CHANNELS)


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
    stmt = select(Notification).where(Notification.read_at.is_(None), inbox_filter())
    if user_id:
        stmt = stmt.where(Notification.user_id == user_id)
    if student_profile_id:
        stmt = stmt.where(Notification.student_profile_id == student_profile_id)
    return len(list(db.scalars(stmt)))
