"""Object storage against a real filesystem backend.

The erasure path is covered elsewhere with fakes that raise on command. This module exercises the
backend that actually runs in a single-node deployment - real files, real permissions - for the
questions a fake cannot answer: does a stored document really get hardened, does a traversal
attempt really fail, does deleting twice really stay quiet, and does a signed link survive a
round trip while an expired one does not.

S3 is not exercised: ``storage_backend=s3`` needs credentials this environment does not have, and
``S3Backend`` is marked ``pragma: no cover`` in the source for that reason. That stays a declared
limitation rather than a claim.
"""
from __future__ import annotations

import os

import pytest
from app.core import storage

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def test_a_stored_document_is_private_on_disk():
    stored = storage.save_upload(owner_scope="students/1", filename="transcript.pdf",
                                 content=PDF, declared_mime="application/pdf")
    try:
        assert storage.exists(stored.key) is True
        assert storage.read(stored.key) == PDF
        mode = os.stat(storage.backend._path(stored.key)).st_mode & 0o777
        assert mode == 0o600, f"a student document must not be world-readable, mode={oct(mode)}"
    finally:
        storage.remove(stored.key)


def test_a_missing_object_is_reported_not_invented():
    with pytest.raises(storage.StorageError):
        storage.read("students/1/does-not-exist.pdf")
    assert storage.exists("students/1/does-not-exist.pdf") is False
    # Removing something that is already gone is success, not an error: erasure is retried.
    storage.remove_verified("students/1/does-not-exist.pdf")


def test_a_key_cannot_escape_the_storage_root():
    """The traversal guard is the only thing between a key and the rest of the filesystem."""
    from app.core.storage import LocalBackend

    backend = LocalBackend()
    for key in ("../../etc/passwd", "students/../../secrets.txt"):
        with pytest.raises(storage.StorageError):
            backend.put(key, b"x")
        with pytest.raises(storage.StorageError):
            backend.get(key)


def test_a_signed_link_round_trips_and_expires():
    key = "students/7/cv.pdf"
    token = storage.sign_key(key, 60)
    assert storage.verify_signature(token) == key

    with pytest.raises(storage.StorageError):
        storage.verify_signature(token[:-2] + "aa")          # tampered
    expired = storage.sign_key(key, -1)
    with pytest.raises(storage.StorageError) as error:
        storage.verify_signature(expired)
    assert "expired" in str(error.value).lower()


def test_an_upload_that_fails_leaves_nothing_behind():
    before = set(os.listdir(storage.backend.root)) if storage.backend.root.exists() else set()
    with pytest.raises(storage.StorageError):
        storage.save_upload(owner_scope="students/2", filename="notes.txt",
                            content=b"hello")          # unsupported type
    with pytest.raises(storage.StorageError):
        storage.save_upload(owner_scope="students/2", filename="fake.pdf",
                            content=b"not really a pdf")
    after = set(os.listdir(storage.backend.root)) if storage.backend.root.exists() else set()
    assert after == before, "a rejected upload must not leave a partial object"


def test_a_deletion_that_cannot_be_confirmed_raises(monkeypatch):
    """Erasure may not claim a document is gone when the backend cannot confirm it."""
    stored = storage.save_upload(owner_scope="students/3", filename="scan.pdf",
                                 content=PDF, declared_mime="application/pdf")
    try:
        monkeypatch.setattr(storage.backend, "exists", lambda key: True)  # delete silently fails
        with pytest.raises(storage.StorageError):
            storage.remove_verified(stored.key)
    finally:
        monkeypatch.undo()
        storage.remove(stored.key)


def test_the_outbox_row_is_what_tracks_failed_deletions(db):
    """A storage failure leaves a durable record, not a log line."""
    from app.models.crm import PendingObjectDeletion
    from sqlalchemy import select, text

    key = "students/4/queued.pdf"
    db.execute(text("DELETE FROM pending_object_deletions WHERE storage_key = :k"), {"k": key})
    db.add(PendingObjectDeletion(storage_key=key, reason="PROFILE_ERASED", attempts=0))
    db.commit()

    from app.services import erasure

    real = storage.remove_verified
    try:
        def boom(_key: str) -> None:
            raise storage.StorageError("backend unavailable")

        storage.remove_verified = boom
        counts = erasure.process_pending_object_deletions(db, keys=[key])
    finally:
        storage.remove_verified = real

    assert counts["objects_pending"] == 1
    assert counts["objects_deleted"] == 0
    row = db.scalars(select(PendingObjectDeletion).where(
        PendingObjectDeletion.storage_key == key)).first()
    assert row is not None and row.attempts >= 1 and row.last_error
    db.delete(row)
    db.commit()
