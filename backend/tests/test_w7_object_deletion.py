"""W7-T2 remediation: database and object-storage erasure must both be failure-safe.

The database half is transactional. Object storage is not, so erasure records every key in a
durable ``pending_object_deletions`` outbox *inside* the transaction and removes the object only
after the commit, confirming the backend afterwards. These tests drive each failure path and
assert real state, never just absence of an exception.
"""
from __future__ import annotations

import os
from typing import Any, Dict

import pytest
from app.core import storage
from app.core.database import SessionLocal
from app.models.crm import Document, PendingObjectDeletion, StudentProfile
from sqlalchemy import func, select


# --------------------------------------------------------------------- helpers
def session():
    return SessionLocal()


def count(model, **where) -> int:
    with session() as db:
        stmt = select(func.count()).select_from(model)
        for key, value in where.items():
            stmt = stmt.where(getattr(model, key) == value)
        return int(db.execute(stmt).scalar_one())


def make_profile(docs: int = 1) -> Dict[str, Any]:
    tag = f"obj{os.urandom(4).hex()}"
    with session() as db:
        profile = StudentProfile(public_token=f"tok-{tag}", full_name=f"{tag} Person",
                                 email=f"{tag}@x.test", phone="+8801700002222",
                                 research_interests=[], completed_subjects=[],
                                 preferred_countries=[], preferred_universities=[],
                                 source="WEBSITE")
        db.add(profile)
        db.flush()
        keys = []
        for i in range(docs):
            stored = storage.save_upload(owner_scope=f"documents/{profile.id}",
                                         filename=f"{tag}-{i}.pdf",
                                         content=b"%PDF-1.4 " + tag.encode(),
                                         declared_mime="application/pdf")
            db.add(Document(student_profile_id=profile.id, doc_type="CV",
                            storage_key=stored.key, original_filename=f"{tag}-{i}.pdf",
                            status="UPLOADED"))
            keys.append(stored.key)
        db.commit()
        return {"profile_id": profile.id, "keys": keys, "tag": tag}


def pending_keys() -> set[str]:
    with session() as db:
        return set(db.execute(select(PendingObjectDeletion.storage_key)).scalars().all())


# ------------------------------------------------- 2. storage failure is durable
def test_a_storage_failure_leaves_a_durable_pending_deletion(client, api, auth, monkeypatch):
    tree = make_profile()

    def refusing(key: str) -> None:
        raise storage.StorageError("backend refused the delete")

    monkeypatch.setattr(storage, "remove_verified", refusing)

    res = client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}", headers=auth)
    assert res.status_code == 200, res.text
    body = res.json()
    # The database half succeeded; the object half did not, and the API must say so.
    assert body["complete"] is False
    assert body["objects_pending"] == 1
    assert count(StudentProfile, id=tree["profile_id"]) == 0

    key = tree["keys"][0]
    assert key in pending_keys(), "the failed deletion must stay queued"
    assert storage.read(key), "the object is still there, and we must not pretend otherwise"
    with session() as db:
        row = db.execute(select(PendingObjectDeletion)
                         .where(PendingObjectDeletion.storage_key == key)).scalars().first()
        assert row is not None and row.attempts == 1
        assert row.last_error  # a reason is recorded, but it is not student data


# -------------------------------------------------- 3. retry completes the work
def test_a_retry_deletes_the_object_and_clears_the_queue(client, api, auth, monkeypatch):
    from app.services import erasure

    tree = make_profile()
    key = tree["keys"][0]
    monkeypatch.setattr(storage, "remove_verified",
                        lambda k: (_ for _ in ()).throw(storage.StorageError("down")))
    assert client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}",
                         headers=auth).json()["complete"] is False
    assert key in pending_keys()

    monkeypatch.undo()
    with session() as db:
        result = erasure.process_pending_object_deletions(db, keys=tree["keys"])
    assert result == {"objects_deleted": 1, "objects_pending": 0}
    assert key not in pending_keys(), "a confirmed deletion leaves nothing behind"
    assert not storage.exists(key), "the object must actually be gone"
    with pytest.raises(storage.StorageError):
        storage.read(key)


# ------------------------------------------- 4. reprocessing a completed one is safe
def test_reprocessing_after_completion_is_a_no_op(client, api, auth, monkeypatch):
    from app.services import erasure

    tree = make_profile()
    key = tree["keys"][0]
    monkeypatch.setattr(storage, "remove_verified",
                        lambda k: (_ for _ in ()).throw(storage.StorageError("down")))
    client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}", headers=auth)
    monkeypatch.undo()

    with session() as db:
        assert erasure.process_pending_object_deletions(db, keys=tree["keys"])["objects_deleted"] == 1
    # Running it again must not resurrect work, error, or touch anything else.
    with session() as db:
        assert erasure.process_pending_object_deletions(db, keys=tree["keys"]) == {
            "objects_deleted": 0, "objects_pending": 0}
        assert erasure.process_pending_object_deletions(db, keys=[key]) == {
            "objects_deleted": 0, "objects_pending": 0}


# --------------------------- 5. one failure must not complete the other objects
def test_one_failed_object_does_not_mark_the_others_complete(client, api, auth, monkeypatch):
    from app.services import erasure

    tree = make_profile(docs=3)
    bad = tree["keys"][1]
    good = [k for k in tree["keys"] if k != bad]

    real = storage.remove_verified

    def selective(key: str) -> None:
        if key == bad:
            raise storage.StorageError("only this one fails")
        real(key)

    monkeypatch.setattr(storage, "remove_verified", selective)
    body = client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}",
                         headers=auth).json()
    assert body["complete"] is False
    assert body["objects_pending"] == 1

    left = pending_keys()
    assert bad in left, "the failed key must stay queued"
    for key in good:
        assert key not in left, "a confirmed deletion must not stay queued"
        assert not storage.exists(key), f"{key} should have been deleted"

    monkeypatch.undo()
    with session() as db:
        assert erasure.process_pending_object_deletions(db, keys=tree["keys"])["objects_deleted"] == 1
    assert not storage.exists(bad)


# ------------------------------- 6. one profile cannot delete another's objects
def test_erasure_cannot_touch_another_profiles_objects(client, api, auth):
    doomed = make_profile()
    keep = make_profile()
    kept_object = keep["keys"][0]
    before = storage.read(kept_object)

    res = client.delete(f"{api}/admin/student-profiles/{doomed['profile_id']}", headers=auth)
    assert res.status_code == 200, res.text

    assert storage.read(kept_object) == before, "another student's object must be untouched"
    assert kept_object not in pending_keys()
    assert count(Document, student_profile_id=keep["profile_id"]) == 1


# ---------------------------------- 7. signed URLs stop working after deletion
def test_a_pre_issued_signed_url_is_inaccessible_after_erasure(client, api, auth):
    tree = make_profile()
    key = tree["keys"][0]
    url = storage.signed_url(key)
    assert client.get(url).status_code == 200, "the link works before erasure"

    res = client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}", headers=auth)
    assert res.status_code == 200 and res.json()["complete"] is True

    assert client.get(url).status_code == 404
    assert not storage.exists(key)


# ------------------------------------- 8. the queue holds no unnecessary PII
def test_pending_queue_records_contain_no_student_pii(client, api, auth, monkeypatch):
    tree = make_profile()
    monkeypatch.setattr(storage, "remove_verified",
                        lambda k: (_ for _ in ()).throw(storage.StorageError("down")))
    client.delete(f"{api}/admin/student-profiles/{tree['profile_id']}", headers=auth)

    with session() as db:
        rows = db.execute(select(PendingObjectDeletion)).scalars().all()
        assert rows, "a pending row must exist for this test to mean anything"
        for row in rows:
            blob = repr({c.name: getattr(row, c.name) for c in row.__table__.columns})
            for secret in (f"{tree['tag']} Person", f"{tree['tag']}@x.test", "+8801700002222"):
                assert secret not in blob, f"queue row leaked {secret!r}"
        # Exactly the columns needed to retry and report, and no more.
        assert set(c.name for c in PendingObjectDeletion.__table__.columns) == {
            "id", "storage_key", "reason", "attempts", "last_attempt_at", "last_error",
            "created_at", "updated_at",
        }
