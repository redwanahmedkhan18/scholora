"""W13 - change detection, version history and staleness.

These tests call the service directly for classification and versioning, and the real import and
review endpoints for the end-to-end path, because the guarantee that matters is behavioural:
a changed fee must stop being presentable as verified, and the value it replaced must still exist.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from app.models.crm import CatalogueChange, DataVerification
from app.models.education import Country, Program, University
from app.services import catalogue_changes as changes
from app.services import importer
from sqlalchemy import select

from task2_helpers import cleanup, new_session, utc


def client_decide(db, batch) -> int:
    """Approve every row of a staged batch, the way the review endpoint does it."""
    from app.models.crm import ImportRow

    changed = 0
    for row in db.query(ImportRow).filter(ImportRow.batch_id == batch.id).all():
        row.decision = "APPROVE"
        changed += 1
    db.commit()
    return changed


def _country(db, iso2="US"):
    return db.execute(select(Country).where(Country.iso2 == iso2)).scalars().first()


def _university(db, slug, *, country=None, source_url="https://w13.example.edu/official"):
    row = University(
        name=f"W13 {slug}", slug=slug, country_id=(country or _country(db)).id, city="Testville",
        overview="W13 test institution.", confidence="NEEDS_REVIEW",
        source_type="TIER_1_OFFICIAL_UNIVERSITY", source_url=source_url,
        retrieved_at=utc(-2), verified_at=None, review_due_at=utc(90),
        verified_by=None,
    )
    db.add(row)
    db.flush()
    return row


def _program(db, university, slug, *, tuition=20000.0, currency="USD", confidence="NEEDS_REVIEW"):
    row = Program(
        university_id=university.id, name=f"W13 Programme {slug}", slug=slug,
        degree_level="MASTER", award="MSc", discipline="Computer Science", currency=currency,
        tuition_annual=tuition, overview="W13 test programme.", is_published=True,
        confidence=confidence, source_type="TIER_1_OFFICIAL_UNIVERSITY",
        source_url="https://w13.example.edu/programme", retrieved_at=utc(-2),
        verified_at=None, review_due_at=utc(90),
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def db():
    """A private session per test.

    The suite's shared session stays open for the whole run; mixing it with API calls lets a
    read transaction hold SQLite's single write lock and turn a plain failure into "database is
    locked". This one is opened and closed per test, so no test can leak a transaction into the
    next.
    """
    session = new_session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()

# =====================================================================
# classification
# =====================================================================
@pytest.mark.parametrize("field,previous,new,expected", [
    ("tuition_annual", 20000, 20000, changes.NO_CHANGE),
    ("tuition_annual", "Master of Science", "master  of   science", changes.NO_CHANGE),
    ("name", "MSc (Data Science)", "MSc - Data Science", changes.FORMAT_ONLY),
    ("tuition_annual", 20000, 22000, changes.MATERIAL),
    ("tuition_annual", 20000, -5, changes.SUSPICIOUS),
    ("tuition_annual", 20000, 90000, changes.SUSPICIOUS),
    ("ielts_overall", 6.5, 7.0, changes.MATERIAL),
    ("ielts_overall", 6.5, 12.0, changes.SUSPICIOUS),
    ("min_cgpa", 3.0, -1, changes.SUSPICIOUS),
    ("year", 2026, 2027, changes.MATERIAL),
    ("year", 2026, 1804, changes.SUSPICIOUS),
    ("degree_level", "MASTER", "DIPLOMA", changes.MATERIAL),
    ("degree_level", "MASTER", "PGDIP", changes.SUSPICIOUS),
    ("currency", "USD", "GBP", changes.MATERIAL),
    ("currency", "USD", "XYZ", changes.SUSPICIOUS),
    ("application_deadline", "2026-06-01", "2025-01-01", changes.SUSPICIOUS),
    ("application_deadline", "2026-06-01", "2026-07-01", changes.MATERIAL),
])
def test_change_classification(field, previous, new, expected):
    assert changes.classify(field, previous, new) == expected


def test_a_formatting_only_difference_is_never_stored_as_a_version():
    db = new_session()
    try:
        assert changes.record(db, entity_type="program", entity_id=1, field_name="name",
                              previous="MSc Data Science", new="msc  data science",
                              actor="importer") is None
        assert changes.record(db, entity_type="program", entity_id=1, field_name="name",
                              previous="MSc Data Science", new="MSc Advanced Data Science",
                              actor="importer") is not None
        db.commit()
    finally:
        db.query(CatalogueChange).filter(CatalogueChange.entity_type == "program",
                                         CatalogueChange.entity_id == 1).delete()
        db.commit()
        db.close()


def test_versions_are_sequenced_per_field():
    db = new_session()
    try:
        first = changes.record(db, entity_type="program", entity_id=990001,
                               field_name="tuition_annual", previous=1, new=2, actor="a")
        second = changes.record(db, entity_type="program", entity_id=990001,
                                field_name="tuition_annual", previous=2, new=3, actor="b")
        other = changes.record(db, entity_type="program", entity_id=990001,
                               field_name="currency", previous="USD", new="GBP", actor="c")
        db.commit()
        assert (first.version, second.version, other.version) == (1, 2, 1)
        history = changes.history(db, "program", 990001)
        assert [c.version for c in history if c.field_name == "tuition_annual"] == [2, 1]
    finally:
        db.query(CatalogueChange).filter(CatalogueChange.entity_id == 990001).delete()
        db.commit()
        db.close()


# =====================================================================
# staleness
# =====================================================================
class _Stub:
    def __init__(self, verified_at=None, review_due_at=None):
        self.verified_at = verified_at
        self.review_due_at = review_due_at


def test_staleness_states_are_explicit():
    now = datetime.now(timezone.utc)
    fresh = changes.staleness(_Stub(verified_at=now - timedelta(days=10)), now=now,
                              window_days=365)
    assert fresh.state == changes.CURRENT and fresh.window_days == 365

    old = changes.staleness(_Stub(verified_at=now - timedelta(days=400)), now=now,
                            window_days=365)
    assert old.state == changes.STALE and old.days_since_verification == 400

    overdue = changes.staleness(_Stub(verified_at=now - timedelta(days=5),
                                      review_due_at=now - timedelta(days=1)), now=now)
    assert overdue.state == changes.REVIEW_OVERDUE

    unknown = changes.staleness(_Stub(), now=now)
    assert unknown.state == changes.UNKNOWN and unknown.window_days is None or unknown.state == changes.UNKNOWN


def test_no_configured_window_is_unknown_not_current():
    """No threshold defined means nobody has answered the question, not that the data is fresh."""
    now = datetime.now(timezone.utc)
    result = changes.staleness(_Stub(verified_at=now - timedelta(days=5)), now=now,
                              window_days=0)
    assert result.state == changes.UNKNOWN
    assert result.window_days is None


# =====================================================================
# end to end: an import that changes a verified value
# =====================================================================
def test_an_import_that_changes_a_verified_fee_preserves_the_old_value(db):
    from app.services import review

    university = _university(db, "w13-import-uni")
    programme = _program(db, university, "w13-import-prog", tuition=20000.0)
    # Pretend a human verified the fee yesterday.
    programme.confidence = "VERIFIED"
    programme.verified_at = utc(-1)
    programme.verified_by = "reviewer@scholara.com.bd"
    register = DataVerification(
        entity_type="program", entity_id=programme.id, field_name="tuition_annual",
        field_value="20000.0", status="VERIFIED", source_url="https://w13.example.edu/official",
        source_type="TIER_1_OFFICIAL_UNIVERSITY", verified_at=utc(-1),
        verified_by="reviewer@scholara.com.bd",
    )
    db.add(register)
    db.commit()
    try:
        batch = importer.stage(db, entity_type="program", rows=[{
            "university": "w13-import-uni", "name": programme.name, "slug": programme.slug,
            "degree_level": "MASTER", "award": "MSc", "discipline": "Computer Science",
            "currency": "USD", "tuition_annual": 26000, "is_published": True,
            "source_url": "https://w13.example.edu/new-fee", "source_type": "TIER_1_OFFICIAL_UNIVERSITY",
            "confidence": "VERIFIED",
        }], source_url="https://w13.example.edu/new-fee", actor="uploader@scholara.com.bd")
        # A verified value with its own source is never applied by an upload: the row arrives as
        # a CONFLICT and only a reviewer's decision puts it through.
        assert [r.action for r in batch.rows] == ["CONFLICT"]
        assert all(r.decision == "SKIP" for r in batch.rows)
        # The proposal is visible before anything is applied.
        staged = db.execute(select(CatalogueChange).where(
            CatalogueChange.entity_type == "program",
            CatalogueChange.entity_id == programme.id,
            CatalogueChange.field_name == "tuition_annual",
            CatalogueChange.status == "PENDING")).scalars().all()
        assert len(staged) == 1, "a conflict must be visible to a reviewer before it is applied"
        assert staged[0].previous_value == "20000.0"
        assert staged[0].new_value in ("26000", "26000.0")

        decision = client_decide(db, batch)
        assert decision == 1, "the reviewer's approval must reach the row"
        result = importer.commit_batch(db, batch, actor="reviewer@scholara.com.bd")
        assert result["updated"] == 1

        db.expire_all()
        after = db.get(Program, programme.id)
        assert float(after.tuition_annual) == 26000.0
        # The new figure arrives unverified, and the old verification is not grafted onto it.
        assert after.confidence == "NEEDS_REVIEW"
        assert after.verified_at is None

        change = db.execute(select(CatalogueChange).where(
            CatalogueChange.entity_type == "program",
            CatalogueChange.entity_id == programme.id,
            CatalogueChange.field_name == "tuition_annual")).scalars().first()
        assert change is not None, "the superseded value must survive the change"
        assert change.previous_value == "20000.0"
        assert change.new_value in ("26000", "26000.0")
        assert change.classification in (changes.MATERIAL, changes.SUSPICIOUS)
        assert change.source_url == "https://w13.example.edu/new-fee"
        assert change.actor == "uploader@scholara.com.bd"

        # And the derived entity state is derived, not asserted by the uploader.
        assert review.recompute_entity_confidence(db, "program", programme.id) != "VERIFIED"
    finally:
        db.query(CatalogueChange).filter(CatalogueChange.entity_id == programme.id,
                                         CatalogueChange.entity_type == "program").delete()
        db.query(DataVerification).filter(DataVerification.entity_id == programme.id,
                                          DataVerification.entity_type == "program").delete()
        db.commit()
        cleanup(db, programme, university)


def test_replaying_the_same_import_records_no_second_version(db):
    university = _university(db, "w13-replay-uni")
    programme = _program(db, university, "w13-replay-prog", tuition=20000.0)
    db.commit()
    payload = {
        "university": "w13-replay-uni", "name": programme.name, "slug": programme.slug,
        "degree_level": "MASTER", "award": "MSc", "discipline": "Computer Science",
        "currency": "USD", "tuition_annual": 21000, "is_published": True,
        "source_url": "https://w13.example.edu/replay",
        "source_type": "TIER_1_OFFICIAL_UNIVERSITY",
    }
    try:
        first = importer.stage(db, entity_type="program", rows=[dict(payload)],
                               source_url="https://w13.example.edu/replay", actor="importer")
        importer.commit_batch(db, first, actor="importer")
        second = importer.stage(db, entity_type="program", rows=[dict(payload)],
                                source_url="https://w13.example.edu/replay", actor="importer")
        result = importer.commit_batch(db, second, actor="importer")
        assert result["updated"] == 0, "an identical replay must not count as a change"
        rows = db.execute(select(CatalogueChange).where(
            CatalogueChange.entity_id == programme.id,
            CatalogueChange.field_name == "tuition_annual")).scalars().all()
        assert len(rows) == 1, "one change, one version"
    finally:
        db.query(CatalogueChange).filter(CatalogueChange.entity_id == programme.id,
                                         CatalogueChange.entity_type == "program").delete()
        db.query(DataVerification).filter(DataVerification.entity_id == programme.id,
                                          DataVerification.entity_type == "program").delete()
        db.commit()
        cleanup(db, programme, university)


def test_approving_a_change_does_not_by_itself_make_the_field_verified(client, api, auth, db):
    university = _university(db, "w13-approve-uni")
    programme = _program(db, university, "w13-approve-prog")
    change = changes.record(db, entity_type="program", entity_id=programme.id,
                            field_name="tuition_annual", previous=20000, new=24000,
                            actor="importer", source_url="https://w13.example.edu/x")
    db.commit()
    try:
        res = client.post(f"{api}/admin/verification/changes/{change.id}/decide",
                          headers=auth, json={"status": "APPROVED"})
        assert res.status_code == 200, res.text
        db.expire_all()
        assert db.get(CatalogueChange, change.id).status == "APPROVED"
        # Approval is a statement about the evidence; the stored record keeps its own state.
        assert db.get(Program, programme.id).confidence != "VERIFIED"
    finally:
        db.query(CatalogueChange).filter(CatalogueChange.entity_id == programme.id,
                                         CatalogueChange.entity_type == "program").delete()
        db.commit()
        cleanup(db, programme, university)


def test_rejecting_a_change_requires_a_reason(client, api, auth, db):
    university = _university(db, "w13-reject-uni")
    programme = _program(db, university, "w13-reject-prog")
    change = changes.record(db, entity_type="program", entity_id=programme.id,
                            field_name="tuition_annual", previous=20000, new=99000,
                            actor="importer")
    db.commit()
    try:
        res = client.post(f"{api}/admin/verification/changes/{change.id}/decide",
                          headers=auth, json={"status": "REJECTED"})
        assert res.status_code == 400
        res = client.post(f"{api}/admin/verification/changes/{change.id}/decide",
                          headers=auth, json={"status": "REJECTED",
                                              "note": "the source is a different campus"})
        assert res.status_code == 200, res.text
        db.expire_all()
        row = db.get(CatalogueChange, change.id)
        assert row.status == "REJECTED" and row.decided_by
        assert row.decision_note == "the source is a different campus"
    finally:
        db.query(CatalogueChange).filter(CatalogueChange.entity_id == programme.id,
                                         CatalogueChange.entity_type == "program").delete()
        db.commit()
        cleanup(db, programme, university)


def test_the_change_queue_is_paginated_and_filterable(client, api, auth, db):
    res = client.get(f"{api}/admin/verification/changes",
                     headers=auth, params={"limit": 2, "offset": 0})
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"total", "limit", "offset", "items"}
    assert body["limit"] == 2 and len(body["items"]) <= 2


def test_the_change_queue_requires_review_authority(client, api):
    res = client.get(f"{api}/admin/verification/changes")
    assert res.status_code in (401, 403)
