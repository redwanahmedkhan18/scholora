"""W12 - the human verification workflow.

The properties under test are the ones an auditor would probe: can an uploader certify its own
data, does one field decision promote a whole record, can a decision be recorded without a reason,
and can anyone below review authority reach these endpoints.
"""
from __future__ import annotations

import os

import pytest
from app.core.security import create_access_token
from app.models.crm import CatalogueChange, DataVerification, ImportBatch, ImportRow, User
from app.models.education import Country, Program, University
from app.services import importer, review
from sqlalchemy import select

from task2_helpers import cleanup, new_session, utc


def _staff_headers(role: str) -> dict:
    db = new_session()
    try:
        user = User(email=f"{role.lower()}-{os.urandom(4).hex()}@scholara.test",
                    full_name=f"{role} Probe", password_hash="x", role=role, is_active=True)
        db.add(user)
        db.commit()
        token = create_access_token(str(user.id), role)
        return {"Authorization": f"Bearer {token}", "email": user.email}
    finally:
        db.close()


def _country(db, iso2="US"):
    return db.execute(select(Country).where(Country.iso2 == iso2)).scalars().first()


def _university(db, slug):
    row = University(name=f"W12 {slug}", slug=slug, country_id=_country(db).id, city="Testville",
                     overview="W12 test institution.", confidence="NEEDS_REVIEW",
                     source_type="TIER_1_OFFICIAL_UNIVERSITY",
                     source_url="https://w12.example.edu/official", retrieved_at=utc(-1),
                     review_due_at=utc(90))
    db.add(row)
    db.flush()
    return row


def _program(db, university, slug, tuition=20000.0):
    row = Program(university_id=university.id, name=f"W12 Programme {slug}", slug=slug,
                  degree_level="MASTER", award="MSc", discipline="Computer Science",
                  currency="USD", tuition_annual=tuition, overview="W12 test programme.",
                  is_published=True, confidence="NEEDS_REVIEW",
                  source_type="TIER_1_OFFICIAL_UNIVERSITY",
                  source_url="https://w12.example.edu/programme", retrieved_at=utc(-1),
                  review_due_at=utc(90))
    db.add(row)
    db.flush()
    return row


def _register(db, entity_type, entity_id, field, value, status="NEEDS_REVIEW", **extra):
    row = DataVerification(entity_type=entity_type, entity_id=entity_id, field_name=field,
                           field_value=str(value), status=status,
                           source_url="https://w12.example.edu/official",
                           source_type="TIER_1_OFFICIAL_UNIVERSITY", **extra)
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
# 1. an upload cannot certify itself
# =====================================================================
def test_an_import_claiming_verified_never_produces_a_verified_record(db):
    try:
        batch = importer.stage(db, entity_type="university", rows=[{
            "name": "W12 Imported Trust", "slug": "w12-imported-trust",
            "country": "united-states", "city": "Testville",
            "source_url": "https://w12.example.edu/official",
            "confidence": "VERIFIED",
        }], source_url="https://w12.example.edu/official", actor="uploader@scholara.test")
        importer.commit_batch(db, batch, actor="uploader@scholara.test")

        db.expire_all()
        row = db.execute(select(University).where(
            University.slug == "w12-imported-trust")).scalars().first()
        assert row is not None
        assert row.confidence == "NEEDS_REVIEW", (
            "a spreadsheet asserting VERIFIED must not certify itself")
        assert row.verified_at is None and row.verified_by is None

        register = db.execute(select(DataVerification).where(
            DataVerification.entity_type == "university",
            DataVerification.entity_id == row.id)).scalars().all()
        assert register and all(r.status == "NEEDS_REVIEW" for r in register)
        assert all(r.source_url == "https://w12.example.edu/official" for r in register)
    finally:
        db.query(DataVerification).filter(DataVerification.entity_type == "university",
                                          DataVerification.field_value.like("%W12%")).delete(
            synchronize_session=False)
        db.query(University).filter(University.slug == "w12-imported-trust").delete()
        batch_ids = [b.id for b in db.query(ImportBatch).filter(
            ImportBatch.uploaded_by == "uploader@scholara.test").all()]
        if batch_ids:
            db.query(ImportRow).filter(ImportRow.batch_id.in_(batch_ids)).delete(
                synchronize_session=False)
            db.query(ImportBatch).filter(ImportBatch.id.in_(batch_ids)).delete(
                synchronize_session=False)
        db.commit()


# =====================================================================
# 2. a field decision is not an entity decision
# =====================================================================
def test_one_verified_field_does_not_verify_the_whole_record(client, api, auth, db):
    university = _university(db, "w12-partial-uni")
    programme = _program(db, university, "w12-partial-prog")
    fee = _register(db, "program", programme.id, "tuition_annual", 20000.0)
    name = _register(db, "program", programme.id, "name", programme.name)
    db.commit()
    try:
        res = client.post(f"{api}/admin/verification/{fee.id}/resolve", headers=auth,
                          json={"status": "VERIFIED"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["field_status"] == "VERIFIED"
        assert body["entity_status"] == "NEEDS_REVIEW", (
            "one field cannot stand for the other fields of the record")
        db.expire_all()
        assert db.get(Program, programme.id).confidence == "NEEDS_REVIEW"

        res = client.post(f"{api}/admin/verification/{name.id}/resolve", headers=auth,
                          json={"status": "VERIFIED"})
        assert res.status_code == 200, res.text
        assert res.json()["entity_status"] == "VERIFIED", (
            "with every registered field verified the record may be presented")
        db.expire_all()
        entity = db.get(Program, programme.id)
        assert entity.confidence == "VERIFIED" and entity.verified_by
    finally:
        db.query(DataVerification).filter(DataVerification.entity_type == "program",
                                          DataVerification.entity_id == programme.id).delete()
        db.commit()
        cleanup(db, programme, university)


def test_a_disputed_field_dominates_the_record_state(client, api, auth, db):
    university = _university(db, "w12-dispute-uni")
    programme = _program(db, university, "w12-dispute-prog")
    fee = _register(db, "program", programme.id, "tuition_annual", 20000.0)
    other = _register(db, "program", programme.id, "name", programme.name)
    db.commit()
    try:
        client.post(f"{api}/admin/verification/{fee.id}/resolve", headers=auth,
                    json={"status": "VERIFIED"})
        res = client.post(f"{api}/admin/verification/{other.id}/resolve", headers=auth,
                          json={"status": "CONFLICTING", "note": "two sources disagree"})
        assert res.status_code == 200, res.text
        assert res.json()["entity_status"] == "CONFLICTING"
        db.expire_all()
        entity = db.get(Program, programme.id)
        assert entity.confidence == "CONFLICTING"
        assert entity.verified_at is None, "a disputed record cannot keep a verification date"
    finally:
        db.query(DataVerification).filter(DataVerification.entity_type == "program",
                                          DataVerification.entity_id == programme.id).delete()
        db.commit()
        cleanup(db, programme, university)


# =====================================================================
# 3. a refusal carries a reason
# =====================================================================
def test_a_non_verified_decision_requires_a_note(client, api, auth, db):
    university = _university(db, "w12-note-uni")
    programme = _program(db, university, "w12-note-prog")
    field = _register(db, "program", programme.id, "tuition_annual", 20000.0)
    db.commit()
    try:
        res = client.post(f"{api}/admin/verification/{field.id}/resolve", headers=auth,
                          json={"status": "OUTDATED"})
        assert res.status_code == 400
        assert "note" in res.json()["detail"].lower()
        db.expire_all()
        assert db.get(DataVerification, field.id).status == "NEEDS_REVIEW", (
            "a refused decision must change nothing")

        res = client.post(f"{api}/admin/verification/{field.id}/resolve", headers=auth,
                          json={"status": "OUTDATED", "note": "the fee page 404s"})
        assert res.status_code == 200, res.text
    finally:
        db.query(DataVerification).filter(DataVerification.entity_type == "program",
                                          DataVerification.entity_id == programme.id).delete()
        db.commit()
        cleanup(db, programme, university)


def test_an_unknown_status_is_refused(client, api, auth, db):
    university = _university(db, "w12-status-uni")
    programme = _program(db, university, "w12-status-prog")
    field = _register(db, "program", programme.id, "tuition_annual", 20000.0)
    db.commit()
    try:
        res = client.post(f"{api}/admin/verification/{field.id}/resolve", headers=auth,
                          json={"status": "APPROVED_PLEASE"})
        assert res.status_code == 400
    finally:
        db.query(DataVerification).filter(DataVerification.entity_type == "program",
                                          DataVerification.entity_id == programme.id).delete()
        db.commit()
        cleanup(db, programme, university)


# =====================================================================
# 4. segregation of duties
# =====================================================================
def test_the_uploader_cannot_approve_its_own_import(client, api, db):
    headers = _staff_headers("DATA_MANAGER")
    try:
        batch = importer.stage(db, entity_type="university", rows=[{
            "name": "W12 Self Cert", "slug": "w12-self-cert", "country": "united-states",
            "city": "Testville", "source_url": "https://w12.example.edu/self",
        }], source_url="https://w12.example.edu/self", actor=headers["email"])
        importer.commit_batch(db, batch, actor=headers["email"])
        db.expire_all()
        entity = db.execute(select(University).where(
            University.slug == "w12-self-cert")).scalars().first()
        assert entity is not None
        field = db.execute(select(DataVerification).where(
            DataVerification.entity_type == "university",
            DataVerification.entity_id == entity.id)).scalars().first()
        assert field is not None
        field_id = field.id

        # End the local read transaction before the API writes: SQLite takes one writer, and a
        # test session holding a read lock would stall the request rather than fail plainly.
        db.commit()

        res = client.post(f"{api}/admin/verification/{field_id}/resolve",
                          headers={"Authorization": headers["Authorization"]},
                          json={"status": "VERIFIED"})
        assert res.status_code == 409, res.text
        assert "second reviewer" in res.json()["detail"]
        db.expire_all()
        assert db.get(DataVerification, field_id).status == "NEEDS_REVIEW"
        db.commit()

        # A different reviewer may approve it.
        other = _staff_headers("DATA_MANAGER")
        res = client.post(f"{api}/admin/verification/{field_id}/resolve",
                          headers={"Authorization": other["Authorization"]},
                          json={"status": "VERIFIED"})
        assert res.status_code == 200, res.text
    finally:
        db.rollback()
        db.query(DataVerification).filter(
            DataVerification.entity_type == "university",
            DataVerification.entity_id.in_(select(University.id).where(
                University.slug == "w12-self-cert"))).delete(synchronize_session=False)
        db.query(University).filter(University.slug == "w12-self-cert").delete()
        batch_ids = [b.id for b in db.query(ImportBatch).filter(
            ImportBatch.entity_type == "university",
            ImportBatch.source_url == "https://w12.example.edu/self").all()]
        if batch_ids:
            db.query(ImportRow).filter(ImportRow.batch_id.in_(batch_ids)).delete(
                synchronize_session=False)
            db.query(ImportBatch).filter(ImportBatch.id.in_(batch_ids)).delete(
                synchronize_session=False)
        db.commit()


# =====================================================================
# 5. authorization
# =====================================================================
def test_review_endpoints_require_authority(client, api, db):
    university = _university(db, "w12-authz-uni")
    programme = _program(db, university, "w12-authz-prog")
    field = _register(db, "program", programme.id, "tuition_annual", 20000.0)
    db.commit()
    try:
        assert client.post(f"{api}/admin/verification/{field.id}/resolve",
                           json={"status": "VERIFIED"}).status_code in (401, 403)

        counselor = _staff_headers("COUNSELOR")
        res = client.post(f"{api}/admin/verification/{field.id}/resolve",
                          headers={"Authorization": counselor["Authorization"]},
                          json={"status": "VERIFIED"})
        assert res.status_code == 403, "a counselor is below review authority"
        db.expire_all()
        assert db.get(DataVerification, field.id).status == "NEEDS_REVIEW"
    finally:
        db.query(DataVerification).filter(DataVerification.entity_type == "program",
                                          DataVerification.entity_id == programme.id).delete()
        db.commit()
        cleanup(db, programme, university)


def test_a_student_cannot_reach_the_review_queue(client, api, student_token):
    res = client.get(f"{api}/admin/verification/queue",
                     headers={"Authorization": f"Bearer {student_token}"})
    assert res.status_code in (401, 403)


# =====================================================================
# 6. review detail, evidence and history
# =====================================================================
def test_review_detail_shows_evidence_and_pending_changes(client, api, auth, db):
    university = _university(db, "w12-detail-uni")
    programme = _program(db, university, "w12-detail-prog")
    _register(db, "program", programme.id, "tuition_annual", 20000.0)
    change = CatalogueChange(entity_type="program", entity_id=programme.id,
                             field_name="tuition_annual", previous_value="20000.0",
                             new_value="26000", classification="MATERIAL", status="PENDING",
                             actor="importer", source_url="https://w12.example.edu/new",
                             version=1)
    db.add(change)
    db.commit()
    try:
        res = client.get(f"{api}/admin/verification/entity/program/{programme.id}",
                         headers=auth)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["entity_type"] == "program" and body["entity_id"] == programme.id
        assert body["derived_status"] == "NEEDS_REVIEW"
        assert body["fields"] and body["fields"][0]["source_url"]
        assert body["changes"][0]["previous_value"] == "20000.0"
        assert body["changes"][0]["new_value"] == "26000"
    finally:
        db.query(CatalogueChange).filter(CatalogueChange.entity_id == programme.id,
                                         CatalogueChange.entity_type == "program").delete()
        db.query(DataVerification).filter(DataVerification.entity_type == "program",
                                          DataVerification.entity_id == programme.id).delete()
        db.commit()
        cleanup(db, programme, university)


def test_review_detail_cannot_address_student_data(client, api, auth):
    res = client.get(f"{api}/admin/verification/entity/student_profile/1", headers=auth)
    assert res.status_code == 404, "the review surface must not be able to read student rows"


def test_the_review_queue_is_bounded_and_paginated(client, api, auth):
    res = client.get(f"{api}/admin/verification/queue", headers=auth,
                     params={"status": "NEEDS_REVIEW", "limit": 3, "offset": 0})
    assert res.status_code == 200, res.text
    body = res.json()
    assert "items" in body and len(body["items"]) <= 3


def test_a_verified_field_is_reported_with_its_reviewer(client, api, auth, db):
    university = _university(db, "w12-reviewer-uni")
    programme = _program(db, university, "w12-reviewer-prog")
    field = _register(db, "program", programme.id, "tuition_annual", 20000.0)
    db.commit()
    try:
        res = client.post(f"{api}/admin/verification/{field.id}/resolve", headers=auth,
                          json={"status": "VERIFIED"})
        assert res.status_code == 200
        db.expire_all()
        row = db.get(DataVerification, field.id)
        assert row.verified_by and row.verified_at
        assert row.status == "VERIFIED"
    finally:
        db.query(DataVerification).filter(DataVerification.entity_type == "program",
                                          DataVerification.entity_id == programme.id).delete()
        db.commit()
        cleanup(db, programme, university)


def test_recompute_is_derived_not_asserted(db):
    university = _university(db, "w12-derived-uni")
    programme = _program(db, university, "w12-derived-prog")
    try:
        assert review.recompute_entity_confidence(db, "program", programme.id) == "NEEDS_REVIEW"
        _register(db, "program", programme.id, "tuition_annual", 1, status="VERIFIED")
        db.commit()
        assert review.recompute_entity_confidence(db, "program", programme.id) == "VERIFIED"
        _register(db, "program", programme.id, "name", "x", status="NEEDS_REVIEW")
        db.commit()
        assert review.recompute_entity_confidence(db, "program", programme.id) == "NEEDS_REVIEW"
    finally:
        db.query(DataVerification).filter(DataVerification.entity_type == "program",
                                          DataVerification.entity_id == programme.id).delete()
        db.commit()
        cleanup(db, programme, university)


# =====================================================================
# 7. the trust columns cannot be written around the workflow
# =====================================================================
def test_generic_crud_cannot_certify_a_record(client, api, db):
    """A COUNSELOR PUTting confidence=VERIFIED used to bypass the whole review workflow."""
    university = _university(db, "w12-crud-uni")
    programme = _program(db, university, "w12-crud-prog")
    db.commit()
    try:
        counselor = _staff_headers("COUNSELOR")
        res = client.put(f"{api}/admin/programs/{programme.id}",
                         headers={"Authorization": counselor["Authorization"]},
                         json={"confidence": "VERIFIED", "verified_by": "self",
                               "verified_at": "2026-01-01T00:00:00", "overview": "edited"})
        assert res.status_code == 200, res.text
        db.expire_all()
        row = db.get(Program, programme.id)
        assert row.overview == "edited", "ordinary fields still save"
        assert row.confidence != "VERIFIED", "a form write cannot certify a record"
        assert row.verified_by is None and row.verified_at is None

        # Even a reviewer role cannot set it through the form: the decision has its own path.
        manager = _staff_headers("DATA_MANAGER")
        res = client.post(f"{api}/admin/programs", headers={"Authorization": manager["Authorization"]},
                          json={"university_id": university.id, "name": "W12 CRUD Created",
                                "slug": "w12-crud-created", "degree_level": "MASTER",
                                "currency": "USD", "tuition_annual": 1000,
                                "confidence": "VERIFIED"})
        assert res.status_code == 200, res.text
        db.expire_all()
        created = db.execute(select(Program).where(
            Program.slug == "w12-crud-created")).scalars().first()
        assert created is not None and created.confidence != "VERIFIED"
    finally:
        db.query(Program).filter(Program.slug == "w12-crud-created").delete()
        db.commit()
        cleanup(db, programme, university)


def test_the_verification_register_cannot_be_written_by_a_counselor(client, api, db):
    counselor = _staff_headers("COUNSELOR")
    res = client.post(f"{api}/admin/verifications",
                      headers={"Authorization": counselor["Authorization"]},
                      json={"entity_type": "program", "entity_id": 1,
                            "field_name": "tuition_annual", "field_value": "1"})
    assert res.status_code == 403, "the register is written through the review workflow"

    manager = _staff_headers("DATA_MANAGER")
    res = client.post(f"{api}/admin/verifications",
                      headers={"Authorization": manager["Authorization"]},
                      json={"entity_type": "program", "entity_id": 999001,
                            "field_name": "tuition_annual", "field_value": "1",
                            "status": "VERIFIED"})
    assert res.status_code == 200, res.text
    assert res.json()["status"] != "VERIFIED", (
        "status is a decision, so a create must not carry it in")
    db.query(DataVerification).filter(DataVerification.entity_id == 999001).delete()
    db.commit()


def test_a_stale_expected_status_is_refused(client, api, auth, db):
    """Two reviewers on one field: the second cannot overwrite the first's decision."""
    university = _university(db, "w12-race-uni")
    programme = _program(db, university, "w12-race-prog")
    field = _register(db, "program", programme.id, "tuition_annual", 20000.0)
    db.commit()
    field_id = field.id
    db.commit()
    try:
        first = client.post(f"{api}/admin/verification/{field_id}/resolve", headers=auth,
                            json={"status": "VERIFIED", "expected_status": "NEEDS_REVIEW"})
        assert first.status_code == 200, first.text

        second = client.post(f"{api}/admin/verification/{field_id}/resolve", headers=auth,
                             json={"status": "OUTDATED", "note": "changed my mind",
                                   "expected_status": "NEEDS_REVIEW"})
        assert second.status_code == 409, (
            "a decision made against a stale view must not overwrite the newer one")
        db.expire_all()
        assert db.get(DataVerification, field_id).status == "VERIFIED"
    finally:
        db.rollback()
        db.query(DataVerification).filter(DataVerification.entity_id == programme.id).delete()
        db.commit()
        cleanup(db, programme, university)
