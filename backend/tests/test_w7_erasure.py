"""W7-T2: transactional, complete erasure of a student profile.

Every assertion here reads *actual database state* through its own session. A test that only
asserted "the endpoint did not raise" would have passed against the broken implementation that
returned HTTP 500, so none of these do that.

The dependency tree built by :func:`build_tree` mirrors the real ownership graph: a profile with
documents (including a real file on disk), applications with status history, shortlist items,
brochures, notifications, leads with an appointment and a full WhatsApp chain, an assistant
session and assistant conversations.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict

import pytest
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.models.content import Event, EventRegistration
from app.models.crm import (
    Application,
    ApplicationStatusHistory,
    Appointment,
    AssistantConversation,
    AssistantSession,
    Brochure,
    Document,
    Lead,
    Notification,
    ShortlistItem,
    StudentProfile,
    User,
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppMessage,
)


# --------------------------------------------------------------------- helpers
def session():
    """A fresh session, so no identity-map cache can make a stale row look deleted."""
    return SessionLocal()


def audit_mark() -> int:
    """Highest audit_logs id now. SQLite recycles integer PKs after a delete, so a *previous*
    test can leave an ERASE row under the same entity_id; only rows above this mark are ours."""
    from app.models.crm import AuditLog
    from sqlalchemy import func, select

    with session() as db:
        return int(db.execute(select(func.coalesce(func.max(AuditLog.id), 0))).scalar_one())


def erase_audits_since(mark: int, entity_id: int) -> int:
    from app.models.crm import AuditLog
    from sqlalchemy import func, select

    with session() as db:
        return int(db.execute(select(func.count()).select_from(AuditLog)
                              .where(AuditLog.action == "ERASE",
                                     AuditLog.entity_id == entity_id,
                                     AuditLog.id > mark)).scalar_one())


def count(model, **where) -> int:
    with session() as db:
        from sqlalchemy import func, select

        stmt = select(func.count()).select_from(model)
        for key, value in where.items():
            stmt = stmt.where(getattr(model, key) == value)
        return int(db.execute(stmt).scalar_one())


def column(model, field, **where):
    with session() as db:
        from sqlalchemy import select

        stmt = select(getattr(model, field))
        for key, value in where.items():
            stmt = stmt.where(getattr(model, key) == value)
        return db.execute(stmt).scalars().first()


def build_tree(name: str) -> Dict[str, Any]:
    """Create one profile and a complete set of dependents. Returns every id involved."""
    ids: Dict[str, Any] = {"name": name}
    with session() as db:
        profile = StudentProfile(public_token=f"tok-{name}-{os.urandom(6).hex()}",
                                 full_name=f"{name} Student", email=f"{name}@example.com",
                                 phone="+8801712345678", research_interests=[], completed_subjects=[],
                                 preferred_countries=[], preferred_universities=[], source="WEBSITE")
        db.add(profile)
        db.flush()
        pid = profile.id
        ids["profile_id"] = pid
        ids["public_token"] = profile.public_token

        # A real file on disk, so "documents are gone" can be proven, not assumed.
        from app.core import storage

        stored = storage.save_upload(owner_scope=f"documents/{pid}", filename=f"{name}.pdf",
                                     content=b"%PDF-1.4\n" + name.encode(),
                                     declared_mime="application/pdf")
        doc = Document(student_profile_id=pid, doc_type="PASSPORT", storage_key=stored.key,
                       original_filename=f"{name}.pdf", status="UPLOADED")
        db.add(doc)
        db.flush()
        ids["storage_key"] = stored.key

        app_row = Application(reference=f"APP-{name}-1", student_profile_id=pid,
                              program_id=_any_program_id(db), intake_label="Fall 2027",
                              status="PLANNING")
        db.add(app_row)
        db.flush()
        db.add(ApplicationStatusHistory(application_id=app_row.id, to_status="PLANNING"))
        db.add(ShortlistItem(student_profile_id=pid, entity_type="program", entity_id=1,
                             slug=f"{name}-slug", label=f"{name} label"))
        db.add(Brochure(token=f"bro-{name}-{os.urandom(4).hex()}", student_name=f"{name} Student",
                        student_profile_id=pid, program_id=_any_program_id(db),
                        degree_level="MASTER", generated_by="test", payload={}))
        db.add(Notification(channel="WHATSAPP", recipient="+8801712345678", trigger="TEST",
                            student_profile_id=pid, title="Hi"))

        lead = Lead(reference=f"LEAD-{name}-1", full_name=f"{name} Student",
                    phone="+8801712345678", email=f"{name}@example.com",
                    student_profile_id=pid, stage="NEW")
        db.add(lead)
        db.flush()
        ids["lead_id"] = lead.id

        db.add(Appointment(reference=f"APT-{name}-1", full_name=f"{name} Student",
                           phone="+8801712345678", email=f"{name}@example.com",
                           service_type="CONSULTATION", scheduled_date=date(2027, 1, 1),
                           scheduled_time="10:00", mode="ONLINE", status="BOOKED", lead_id=lead.id))
        contact = WhatsAppContact(wa_id=f"wa-{name}", profile_name=f"{name} Student",
                                  lead_id=lead.id, opted_in=True)
        db.add(contact)
        db.flush()
        ids["contact_id"] = contact.id
        conv = WhatsAppConversation(contact_id=contact.id, state="OPEN", context={})
        db.add(conv)
        db.flush()
        db.add(WhatsAppMessage(conversation_id=conv.id, direction="IN", body=f"{name} private text",
                               payload={}))

        from app.core import assistant_sessions as sessions_module

        credential, sess_row = sessions_module.issue(db, profile_token=profile.public_token)
        ids["session_id"] = sess_row.id
        db.add(AssistantConversation(session_id=f"sid-{name}",
                                     session_ref_id=sess_row.id, student_profile_id=pid,
                                     question=f"{name} private question", answer="answer",
                                     grounded=True, escalated=False))
        db.commit()
    return ids


def _any_program_id(db) -> int:
    from app.models.education import Program
    from sqlalchemy import select

    pid = db.execute(select(Program.id).order_by(Program.id)).scalars().first()
    assert pid is not None, "seed data must contain at least one programme"
    return pid


def dependent_counts(pid: int) -> Dict[str, int]:
    return {
        "student_profiles": count(StudentProfile, id=pid),
        "documents": count(Document, student_profile_id=pid),
        "applications": count(Application, student_profile_id=pid),
        "shortlist_items": count(ShortlistItem, student_profile_id=pid),
        "brochures": count(Brochure, student_profile_id=pid),
        "notifications": count(Notification, student_profile_id=pid),
        "leads": count(Lead, student_profile_id=pid),
        "assistant_sessions": count(AssistantSession, student_profile_id=pid),
        "assistant_conversations": count(AssistantConversation, student_profile_id=pid),
    }


@pytest.fixture
def tree():
    return build_tree(f"erase{os.urandom(4).hex()}")


# ------------------------------------------------------------------ 1, 2, 3, 4
def test_erasure_succeeds_and_removes_the_profile(client, api, auth, tree):
    res = client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}", headers=auth)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["erased"]["assistant_sessions_revoked"] == 1
    assert count(StudentProfile, id=tree["profile_id"]) == 0


def test_erasure_removes_every_dependent_student_record(client, api, auth, tree):
    pid = tree["profile_id"]
    before = dependent_counts(pid)
    assert before["documents"] == 1 and before["applications"] == 1
    assert before["leads"] == 1 and before["assistant_conversations"] == 1

    res = client.delete(f"{api}/admin/student-profiles/{pid}", headers=auth)
    assert res.status_code == 200, res.text

    after = dependent_counts(pid)
    assert all(value == 0 for value in after.values()), after
    # Leaf rows reached through the lead must be gone too.
    assert count(Appointment, lead_id=tree["lead_id"]) == 0
    assert count(WhatsAppContact, id=tree["contact_id"]) == 0
    with session() as db:
        from sqlalchemy import text

        assert db.execute(text("select count(*) from whatsapp_conversations where contact_id=:c"),
                          {"c": tree["contact_id"]}).scalar_one() == 0
        assert db.execute(text("select count(*) from application_status_history "
                               "where application_id not in (select id from applications)")).scalar_one() == 0


def test_erasure_revokes_before_deleting_assistant_sessions(client, api, auth, tree, monkeypatch):
    """The revocation must happen, and must happen before the row disappears."""
    from app.services import erasure as erasure_module

    seen: Dict[str, Any] = {}
    real = erasure_module.sessions.revoke_for_profile

    def spy(db, profile_id, *args, **kwargs):
        seen["called_with_profile"] = profile_id
        seen["commit_flag"] = kwargs.get("commit")
        result = real(db, profile_id, *args, **kwargs)
        # Read through the erasure's own session: the update is flushed but not committed, which
        # is exactly what "inside the same transaction" means.
        db.expire_all()
        row = db.get(AssistantSession, tree["session_id"])
        seen["row_still_present"] = row is not None
        seen["revoked_during_erasure"] = bool(row and row.revoked_at)
        seen["reason"] = row.revoked_reason if row else None
        return result

    monkeypatch.setattr(erasure_module.sessions, "revoke_for_profile", spy)
    res = client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}", headers=auth)
    assert res.status_code == 200, res.text
    assert seen["called_with_profile"] == tree["profile_id"]
    assert seen["commit_flag"] is False, "revocation must join the erasure transaction"
    assert seen["row_still_present"] is True, "revocation must precede deletion"
    assert seen["revoked_during_erasure"] is True
    assert seen["reason"] == "PROFILE_ERASED"
    assert count(AssistantSession, student_profile_id=tree["profile_id"]) == 0


def test_erasure_leaves_another_students_records_untouched(client, api, auth):
    keep = build_tree(f"keep{os.urandom(4).hex()}")
    doomed = build_tree(f"gone{os.urandom(4).hex()}")

    res = client.delete(f"{api}/admin/student-profiles/{doomed['profile_id']}", headers=auth)
    assert res.status_code == 200, res.text

    survivor = dependent_counts(keep["profile_id"])
    assert all(value == 1 for value in survivor.values()), survivor
    assert count(Appointment, lead_id=keep["lead_id"]) == 1
    assert count(WhatsAppContact, id=keep["contact_id"]) == 1
    sess = column(AssistantSession, "revoked_at", id=keep["session_id"])
    assert sess is None, "another student's session must not be revoked"


# ------------------------------------------------------------------------- 6
def test_erased_documents_cannot_be_accessed_afterwards(client, api, auth, tree):
    from app.core import storage

    key = tree["storage_key"]
    assert storage.read(key), "the object must exist before erasure"
    res = client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}", headers=auth)
    assert res.status_code == 200, res.text

    with pytest.raises(storage.StorageError):
        storage.read(key)
    # And no signed URL can be minted for a row that no longer exists.
    assert count(Document, student_profile_id=tree["profile_id"]) == 0


# ------------------------------------------------------------------------- 7
def test_audit_record_contains_no_student_pii(client, api, auth, tree):
    pid = tree["profile_id"]
    name = f"{tree['name']} Student"
    mark = audit_mark()
    res = client.delete(f"{api}/admin/student-profiles/{pid}", headers=auth)
    assert res.status_code == 200, res.text

    with session() as db:
        from app.models.crm import AuditLog
        from sqlalchemy import select

        row = db.execute(select(AuditLog).where(AuditLog.action == "ERASE",
                                                AuditLog.entity_id == pid,
                                                AuditLog.id > mark)).scalars().first()
        assert row is not None, "erasure must be audited"
        assert row.entity_type == "student-profiles"
        blob = f"{row.detail!r}{row.actor_email!r}"
        for secret in (name, f"{tree['name']}@example.com", "+8801712345678", "private question",
                       "private text", tree["storage_key"]):
            assert secret not in blob, f"audit record leaked {secret!r}"
        assert "erased" in row.detail


# ------------------------------------------------------------------------- 8
def test_a_counselor_without_senior_rights_cannot_erase(client, api, tree):
    with session() as db:
        user = User(email=f"counselor-{os.urandom(4).hex()}@example.com", full_name="Junior",
                    password_hash="x", role="COUNSELOR", is_active=True)
        db.add(user)
        db.commit()
        token = create_access_token(str(user.id), "COUNSELOR")
    res = client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}",
                        headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403, res.text
    assert count(StudentProfile, id=tree["profile_id"]) == 1


# --------------------------------------------------------------------- 9, 10
def test_missing_profile_returns_404(client, api, auth):
    res = client.delete(f"{api}/admin/student-profiles/99999999", headers=auth)
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_repeated_deletion_is_a_clean_404(client, api, auth, tree):
    pid = tree["profile_id"]
    mark = audit_mark()
    first = client.delete(f"{api}/admin/student-profiles/{pid}", headers=auth)
    assert first.status_code == 200
    second = client.delete(f"{api}/admin/student-profiles/{pid}", headers=auth)
    assert second.status_code == 404
    assert erase_audits_since(mark, pid) == 1, "a failed repeat must not write a second success audit"


# ------------------------------------------------------------------------ 11
def test_a_database_failure_rolls_everything_back_and_leaves_objects_intact(client, api, auth, tree):
    """Required property: a failed transaction must not delete external objects.

    The failure is injected *after* the deletes and the outbox enqueue, at the point the
    pending row is flushed: a pre-existing queue row for the same storage key trips the unique
    index. That is a real conflict, not a simulated one, and it exercises the 409 mapping.
    """
    from app.models.crm import PendingObjectDeletion
    from sqlalchemy import func, select

    key = tree["storage_key"]
    with session() as db:
        db.add(PendingObjectDeletion(storage_key=key, reason="PROFILE_ERASED"))
        db.commit()
        pending_before = db.execute(select(func.count()).select_from(PendingObjectDeletion)
                                    ).scalar_one()
    mark = audit_mark()

    res = client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}", headers=auth)
    assert res.status_code == 409, res.text
    assert "nothing was changed" in res.json()["detail"].lower()

    # Every row must still be there.
    assert dependent_counts(tree["profile_id"])["student_profiles"] == 1
    for table, where in (
        (Document, {"student_profile_id": tree["profile_id"]}),
        (Application, {"student_profile_id": tree["profile_id"]}),
        (Lead, {"student_profile_id": tree["profile_id"]}),
        (ShortlistItem, {"student_profile_id": tree["profile_id"]}),
        (AssistantConversation, {"student_profile_id": tree["profile_id"]}),
        (Appointment, {"lead_id": tree["lead_id"]}),
    ):
        assert count(table, **where) == 1, f"{table.__name__} was not rolled back"
    # The session revocation must be part of the rollback.
    assert column(AssistantSession, "revoked_at", id=tree["session_id"]) is None
    # The stored object must be untouched, and readable.
    from app.core import storage

    assert storage.read(key), "a failed transaction must not delete the object"
    with session() as db:
        pending_after = db.execute(select(func.count()).select_from(PendingObjectDeletion)
                                   ).scalar_one()
    assert pending_after == pending_before, "no queue rows may be committed by a rolled-back erasure"
    assert erase_audits_since(mark, tree["profile_id"]) == 0


# ------------------------------------------------------------------------ 12
def test_unrelated_business_records_are_not_deleted(client, api, auth, tree):
    """A lead with no profile, an event registration and a campaign must survive."""
    with session() as db:
        orphan_lead = Lead(reference=f"ORPHAN-{os.urandom(4).hex()}", full_name="Different Person",
                           phone="+8801799999999", stage="NEW")
        db.add(orphan_lead)
        db.add(EventRegistration(event_id=_any_event_id(db), full_name="Event Person",
                                 phone="+8801788888888", email="event@example.com"))
        db.commit()
        orphan_id = orphan_lead.id
        from sqlalchemy import func, select

        events_before = db.execute(select(func.count()).select_from(EventRegistration)).scalar_one()

    res = client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}", headers=auth)
    assert res.status_code == 200, res.text

    assert count(Lead, id=orphan_id) == 1, "an unrelated lead must survive"
    with session() as db:
        from sqlalchemy import func, select

        assert db.execute(select(func.count()).select_from(EventRegistration)).scalar_one() == events_before


def _any_event_id(db) -> int:
    from sqlalchemy import select


    eid = db.execute(select(Event.id).order_by(Event.id)).scalars().first()
    if eid is None:
        ev = Event(title="Test event", slug=f"test-{os.urandom(3).hex()}", start_at="2027-01-01")
        db.add(ev)
        db.flush()
        eid = ev.id
    return eid


# ---------------------------------------------------- environment guard (12b)
def test_the_test_database_enforces_foreign_keys(db):
    """The erasure plan is only meaningful against a database that really enforces its keys.

    SQLite needs the PRAGMA switched on per connection and silently accepts orphan rows without
    it, which is why this is asserted rather than assumed. PostgreSQL always enforces them, so
    there the assertion is that the constraints actually exist.
    """
    from sqlalchemy import text

    if db.get_bind().dialect.name == "postgresql":
        count = db.execute(text(
            "select count(*) from pg_constraint where contype = 'f' "
            "and connamespace = current_schema()::regnamespace")).scalar_one()
        assert int(count) > 0, "the schema must declare foreign keys"
        return
    assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


# ------------------------------------------- post-commit processing failure (Finding 2)
# The commit at the end of the eraser cannot be undone. A failure in the object-deletion step
# that follows it must therefore never be reported as a rollback: the caller would conclude the
# student's records are intact when they are already gone.
def test_post_commit_processing_failure_is_reported_as_incomplete_not_rolled_back(
        client, api, auth, tree, monkeypatch):
    from app.models.crm import PendingObjectDeletion
    from app.services import erasure as erasure_module
    from sqlalchemy import func, select

    def explode(*args, **kwargs):
        raise RuntimeError("simulated storage backend defect with internal detail")

    monkeypatch.setattr(erasure_module, "process_pending_object_deletions", explode)
    mark = audit_mark()

    res = client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}", headers=auth)
    assert res.status_code == 200, f"a committed erasure must not be answered as a failure: {res.text}"
    body = res.json()
    assert body["ok"] is True
    assert body["complete"] is False, "file deletion was not confirmed, so this is not complete"
    assert body["erased"]["storage_unconfirmed"] is True
    assert body["objects_pending"] >= 1
    assert "nothing was changed" not in res.text.lower(), "the erasure had already committed"
    # No internal detail and no student identifiers may escape.
    for leak in ("simulated", "RuntimeError", "Traceback", tree["name"], "storage_key"):
        assert leak not in res.text, f"response leaked {leak!r}"

    # The database erasure is real: the records are gone.
    assert dependent_counts(tree["profile_id"])["student_profiles"] == 0
    assert count(Document, student_profile_id=tree["profile_id"]) == 0
    # ...and the deletion work is preserved for retry, not silently dropped.
    with session() as db:
        pending = db.execute(select(func.count()).select_from(PendingObjectDeletion)
                             .where(PendingObjectDeletion.storage_key == tree["storage_key"])
                             ).scalar_one()
    assert pending == 1, "an unconfirmed deletion must stay queued"
    # The ERASE audit still describes only the committed work.
    assert erase_audits_since(mark, tree["profile_id"]) == 1


def test_the_pending_work_from_a_failed_post_commit_step_is_retryable(
        client, api, auth, tree, monkeypatch):
    from app.core import storage
    from app.models.crm import PendingObjectDeletion
    from app.services import erasure as erasure_module
    from sqlalchemy import func, select

    key = tree["storage_key"]
    assert storage.exists(key), "precondition: the object is on disk"

    monkeypatch.setattr(erasure_module, "process_pending_object_deletions",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}",
                         headers=auth).status_code == 200
    assert storage.exists(key), "a failed step must leave the object in place"

    # The scheduled job runs later with the real processor and finishes the job.
    monkeypatch.undo()
    result = erasure_module.process_pending_object_deletions(None or _session_for_job())
    assert result["objects_deleted"] >= 1
    assert not storage.exists(key), "a confirmed deletion must leave no object behind"
    with session() as db:
        left = db.execute(select(func.count()).select_from(PendingObjectDeletion)
                          .where(PendingObjectDeletion.storage_key == key)).scalar_one()
    assert left == 0, "the queue entry may only be removed after verified absence"

    # Re-running is a no-op rather than a second destructive pass.
    assert erasure_module.process_pending_object_deletions(_session_for_job())["objects_deleted"] == 0


def _session_for_job():
    return SessionLocal()
