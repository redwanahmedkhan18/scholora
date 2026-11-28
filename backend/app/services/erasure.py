"""Transactional erasure of one student profile and everything derived from it.

A student profile is the root of a small ownership tree. Eight tables carry a foreign key to
``student_profiles.id`` and several of them have children of their own, so deleting the profile
row alone fails on the first constraint it meets - which is what ``DELETE
/api/v1/admin/student-profiles/{id}`` used to do, returning an opaque 500.

The erasure is deliberately explicit rather than a database ``ON DELETE CASCADE``:

* A cascade would also delete rows reached from other directions, and nothing here can prove a
  future table will not quietly join the blast radius.
* Documents live in object storage as well as the database, and no foreign key can express that.
* The order matters and is auditable: leaves before parents, availability before ownership.

Everything below runs in **one** transaction. The caller commits, or this module rolls the whole
thing back - including the assistant-session revocation, which is why ``revoke_for_profile`` is
invoked with ``commit=False``.

Storage objects are enqueued for deletion *inside* the transaction and removed *after* the
commit, then confirmed. Two failure modes are not equal and both are now excluded:

* Deleting the object before the commit means a rollback restores document rows whose files are
already gone.
* Deleting it with an unverified, best-effort call means erasure can report success while the
  bytes are still readable, which is a false privacy statement.

Each key therefore becomes a durable ``pending_object_deletions`` row in the same transaction.
The row is only removed once the storage backend confirms the object is absent, so a failure
stays visible and retryable instead of vanishing.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import assistant_sessions as sessions
from app.core import storage
from app.models.base import utcnow
from app.models.crm import (
    Application,
    ApplicationStatusHistory,
    Appointment,
    AssistantConversation,
    AssistantSession,
    AuditLog,
    Brochure,
    Document,
    Lead,
    Notification,
    PendingObjectDeletion,
    ShortlistItem,
    StudentProfile,
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppMessage,
)

log = logging.getLogger(__name__)


class ProfileNotFound(LookupError):
    """The requested profile does not exist (or was already erased)."""


class ErasureConflict(RuntimeError):
    """An expected data conflict stopped the erasure (for example a dependent row a new table
    now holds). The transaction was rolled back, so nothing changed."""


def _ids(db: Session, column, *criteria) -> list[Any]:
    return list(db.execute(select(column).where(*criteria)).scalars().all())


def erase_student_profile(
    db: Session,
    profile_id: int,
    *,
    actor_id: Optional[int] = None,
    actor_email: Optional[str] = None,
) -> Dict[str, Any]:
    """Erase one profile and every record that exists only because of it.

    Returns a counts summary suitable for an audit record: no personal data is included.
    Raises :class:`ProfileNotFound` when the row is absent, and re-raises any database error
    after rolling back, so the caller can answer without leaking internals.
    """
    profile = db.get(StudentProfile, profile_id)
    if profile is None:
        raise ProfileNotFound(f"student profile {profile_id} does not exist")

    counts: Dict[str, Any] = {}
    try:
        # 1. Security first. Revoking before removing anything else means a session can never be
        #    observed as "live" for a profile that is mid-erasure, even inside this transaction.
        #    commit=False keeps it in this transaction so a later failure undoes it too.
        counts["assistant_sessions_revoked"] = sessions.revoke_for_profile(
            db, profile_id, commit=False)

        # 2. Collect the identifiers we need before the rows that hold them are gone.
        lead_ids = _ids(db, Lead.id, Lead.student_profile_id == profile_id)
        application_ids = _ids(db, Application.id, Application.student_profile_id == profile_id)
        contact_ids = (
            _ids(db, WhatsAppContact.id, WhatsAppContact.lead_id.in_(lead_ids))
            if lead_ids else []
        )
        conversation_ids = (
            _ids(db, WhatsAppConversation.id, WhatsAppConversation.contact_id.in_(contact_ids))
            if contact_ids else []
        )
        storage_keys = _ids(db, Document.storage_key, Document.student_profile_id == profile_id)

        # 3. Delete leaves before parents, so no constraint is ever violated mid-transaction.
        if conversation_ids:
            counts["whatsapp_messages"] = _bulk(
                db, delete(WhatsAppMessage)
                .where(WhatsAppMessage.conversation_id.in_(conversation_ids)))
            counts["whatsapp_conversations"] = _bulk(
                db, delete(WhatsAppConversation)
                .where(WhatsAppConversation.contact_id.in_(contact_ids)))
        if contact_ids:
            counts["whatsapp_contacts"] = _bulk(
                db, delete(WhatsAppContact).where(WhatsAppContact.lead_id.in_(lead_ids)))
        if lead_ids:
            # An appointment and a lead both describe the same person; the appointment carries
            # their name, phone, email and free-text notes, so it is student-owned personal data.
            counts["appointments"] = _bulk(
                db, delete(Appointment).where(Appointment.lead_id.in_(lead_ids)))

        if application_ids:
            counts["application_history"] = _bulk(
                db, delete(ApplicationStatusHistory)
                .where(ApplicationStatusHistory.application_id.in_(application_ids)))
        counts["applications"] = _bulk(
            db, delete(Application).where(Application.student_profile_id == profile_id))
        counts["documents"] = _bulk(
            db, delete(Document).where(Document.student_profile_id == profile_id))
        counts["assistant_conversations"] = _bulk(
            db, delete(AssistantConversation)
            .where(AssistantConversation.student_profile_id == profile_id))
        counts["assistant_sessions"] = _bulk(
            db, delete(AssistantSession).where(AssistantSession.student_profile_id == profile_id))
        counts["shortlist_items"] = _bulk(
            db, delete(ShortlistItem).where(ShortlistItem.student_profile_id == profile_id))
        counts["brochures"] = _bulk(
            db, delete(Brochure).where(Brochure.student_profile_id == profile_id))
        counts["notifications"] = _bulk(
            db, delete(Notification).where(Notification.student_profile_id == profile_id))
        counts["leads"] = _bulk(
            db, delete(Lead).where(Lead.student_profile_id == profile_id))

        # 4. Enqueue the object deletions. They are NOT touched yet: the transaction may still
        #    fail, and a rolled-back erasure must leave every object where it is. The row is
        #    durable, so a storage failure after the commit is retried rather than lost.
        for key in storage_keys:
            db.add(PendingObjectDeletion(storage_key=key, reason="PROFILE_ERASED"))
        counts["document_files_queued"] = len(storage_keys)

        # 5. Finally the root row.
        db.delete(profile)
        db.flush()

        if actor_email is not None or actor_id is not None:
            # Audit content is deliberately non-personal: counts and the identifier only.
            db.add(AuditLog(actor_id=actor_id, actor_email=actor_email, action="ERASE",
                            entity_type="student-profiles", entity_id=profile_id,
                            detail={"erased": counts}))
        db.commit()
    except IntegrityError as exc:
        # An expected dependency conflict (a table that references this profile but is not in the
        # erasure plan). Distinct from an internal error, so the caller can answer 409 rather
        # than pretending the server broke.
        db.rollback()
        log.warning("student profile %s could not be erased: %s", profile_id, type(exc).__name__)
        raise ErasureConflict("the profile still has dependent records") from exc
    except Exception:
        db.rollback()
        raise

    # 6. Only now may external objects disappear. This runs after the commit, so an exception
    #    here can no longer be undone: the records are already gone. Letting it escape would
    #    report the erasure as failed when it had in fact succeeded, and a caller that retries on
    #    error would act on a false picture. So it is contained and returned as unconfirmed work:
    #    whatever was not proven absent stays in the durable outbox for the scheduled retry.
    try:
        counts.update(process_pending_object_deletions(db, keys=storage_keys))
    except Exception:
        db.rollback()  # the session may be unusable after the failure; the commit stands
        log.exception("post-commit object deletion failed for profile id=%s", profile_id)
        counts["objects_deleted"] = 0
        counts["objects_pending"] = _queued(db, storage_keys)
        counts["storage_unconfirmed"] = True

    log.info("erased student profile id=%s %s", profile_id, counts)
    return counts


def _is_postgres(db: Session) -> bool:
    try:
        return db.get_bind().dialect.name == "postgresql"
    except Exception:            # noqa: BLE001 - a detached session is simply not PostgreSQL
        return False


def claim_pending_object_deletions(
    db: Session, *, keys: Optional[list[str]] = None, limit: int = 200
) -> list[PendingObjectDeletion]:
    """Take ownership of up to ``limit`` outbox rows for this transaction.

    On PostgreSQL this is ``SELECT ... FOR UPDATE SKIP LOCKED``: the rows it returns are locked
    until the transaction ends, and a second worker skips exactly those rows and takes the rest.
    Without it two workers selected the same row, both deleted the object and both deleted the
    row - double work and inflated counters (proved by ``tests/test_pg_concurrency.py``).

    A crashed worker's transaction is rolled back by the server, so its locks vanish and the rows
    are immediately available again: recovery needs no lease, no timeout and no second table.

    SQLite has no row locks. There the statement is the plain select and correctness rests on the
    single-writer model plus the idempotent delete, which is the honest guarantee that platform
    offers.
    """
    stmt = select(PendingObjectDeletion).order_by(PendingObjectDeletion.id)
    if keys is not None:
        if not keys:
            return []
        stmt = stmt.where(PendingObjectDeletion.storage_key.in_(keys))
    stmt = stmt.limit(limit)
    if _is_postgres(db):
        stmt = stmt.with_for_update(skip_locked=True)
    return list(db.execute(stmt).scalars())


def process_pending_object_deletions(
    db: Session, *, keys: Optional[list[str]] = None, limit: int = 200
) -> Dict[str, int]:
    """Delete queued objects, confirming each one. Idempotent and safe to re-run.

    A confirmed deletion removes its outbox row, so reprocessing finds nothing to do and no key
    is retained longer than it must be. Each key is handled independently: one failure cannot
    mark another object complete, and the keys are exact storage keys, so no other object can be
    touched. Rows are claimed for the duration of the transaction (see
    :func:`claim_pending_object_deletions`), so two workers divide the queue instead of racing
    over it.
    """
    rows = claim_pending_object_deletions(db, keys=keys, limit=limit)
    deleted = failed = 0
    for row in rows:
        row.attempts = (row.attempts or 0) + 1
        row.last_attempt_at = utcnow()
        try:
            storage.remove_verified(row.storage_key)
        except storage.StorageError as exc:
            row.last_error = str(exc)[:200]
            failed += 1
            continue
        row.last_error = None
        # Delete by identity and check the row count: an ORM delete of a row another worker has
        # already removed raises StaleDataError, which would abort the whole batch and lose the
        # outcomes of every other row in it.
        removed = cast(CursorResult, db.execute(
            delete(PendingObjectDeletion).where(PendingObjectDeletion.id == row.id)
        )).rowcount
        if removed == 1:
            deleted += 1
        # The row is gone either way, so drop the ORM copy: keeping it would leave an expired
        # object pointing at a row that no longer exists, and any later access would raise
        # ObjectDeletedError inside an unrelated part of the caller.
        db.expunge(row)
    db.commit()

    if failed:
        log.warning("%d stored object(s) still pending deletion", failed)
    return {"objects_deleted": deleted, "objects_pending": failed}


def _queued(db: Session, keys: list[str]) -> int:
    """How many of these keys still wait in the outbox.

    Used only when post-commit processing could not report for itself. It counts rows rather than
    assuming either outcome, so a failure to delete is never reported as a success.
    """
    if not keys:
        return 0
    return int(db.execute(
        select(func.count()).select_from(PendingObjectDeletion)
        .where(PendingObjectDeletion.storage_key.in_(keys))).scalar_one() or 0)


def _bulk(db: Session, statement) -> int:
    result = db.execute(statement.execution_options(synchronize_session=False))
    # ``execute`` returns CursorResult for DML; getattr keeps this honest across drivers and
    # across SQLAlchemy versions where the static return type is the base Result.
    return int(getattr(result, "rowcount", 0) or 0)
