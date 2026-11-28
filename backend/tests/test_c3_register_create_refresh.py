"""C2-12 — creating a register row refreshes the entity it belongs to, in the same transaction.

The register is the evidence an entity's trust is *derived* from, and every mutation path is
supposed to write that derivation back. Creation did not: ``POST /admin/verifications`` appended a
``NEEDS_REVIEW`` claim and returned 200 while the entity column still said ``VERIFIED``, so the
record described a register that had already moved. Nothing was certified that should not have been
(a new claim is never evidence *for* a value), but the state was stale, and the documented contract
said every register mutation refreshes the entity.

These tests check the properties that make the refresh safe as well as present:

* the entity is re-derived from the *whole* register and its pending changes, not from the new row;
* creation can only ever lower trust, and never stamps the creating account as the verifier;
* a batch of creates refreshes each affected entity and no others;
* the refresh adds no history rows and exactly one audit entry;
* the refresh is part of the create's transaction: if the write fails, neither the claim nor the
  entity change is left behind.
"""
from __future__ import annotations

import os

import pytest
from app.core.security import create_access_token
from app.models.crm import AuditLog, CatalogueChange, DataVerification, User
from app.models.education import Country, Program, University
from app.services import review
from sqlalchemy import select

from skip_policy import skip_pg_only
from task2_helpers import new_session, utc

OFFICIAL = "TIER_1_OFFICIAL_UNIVERSITY"
SOURCE = "https://c2-12.example.edu/official"


def _staff(role: str = "DATA_MANAGER") -> dict:
    db = new_session()
    try:
        user = User(email=f"c212-{os.urandom(4).hex()}@scholara.test",
                    full_name=f"C2-12 {role}", password_hash="x", role=role, is_active=True)
        db.add(user)
        db.commit()
        return {"Authorization": f"Bearer {create_access_token(str(user.id), role)}",
                "email": user.email}
    finally:
        db.close()


def _program(db, *, confidence: str = "VERIFIED", verify_registered: bool = True):
    country = db.execute(select(Country).where(Country.iso2 == "US")).scalars().first()
    slug = f"c212-{os.urandom(4).hex()}"
    university = University(name=f"C2-12 {slug}", slug=slug, country_id=country.id,
                            city="Testville", confidence="NEEDS_REVIEW", source_type=OFFICIAL,
                            source_url=SOURCE, retrieved_at=utc(-1))
    db.add(university)
    db.flush()
    program = Program(university_id=university.id, name=f"C2-12 Programme {slug}",
                      slug=f"p-{slug}", degree_level="MASTER", award="MSc",
                      discipline="Computer Science", currency="USD",
                      tuition_annual=1.0, duration_months=24, overview="C2-12 fixture.",
                      is_published=True, confidence=confidence,
                      verified_by="seed@c2-12.test" if confidence == "VERIFIED" else None,
                      source_type=OFFICIAL, source_url=SOURCE, retrieved_at=utc(-1),
                      review_due_at=utc(90))
    db.add(program)
    db.flush()
    if verify_registered:
        for field, value in (("tuition_annual", "1.0"), ("duration_months", "24")):
            db.add(DataVerification(entity_type="program", entity_id=program.id,
                                    field_name=field, field_value=value, status="VERIFIED",
                                    verified_by="seed@c2-12.test", source_url=SOURCE,
                                    source_type=OFFICIAL))
        db.flush()
    return university, program


def _drop(db, university, program):
    for row in db.execute(
        select(DataVerification).where(
            DataVerification.entity_type == "program", DataVerification.entity_id == program.id)
    ).scalars():
        db.delete(row)
    for row in db.execute(
        select(CatalogueChange).where(
            CatalogueChange.entity_type == "program", CatalogueChange.entity_id == program.id)
    ).scalars():
        db.delete(row)
    db.commit()
    db.delete(program)
    db.delete(university)
    db.commit()


def _create(client, api, headers, program_id, field, value):
    return client.post(f"{api}/admin/verifications", headers=headers,
                       json={"entity_type": "program", "entity_id": program_id,
                             "field_name": field, "field_value": value, "source_url": SOURCE})


def test_creating_a_claim_refreshes_the_entity(client, api):
    db = new_session()
    try:
        headers = _staff()
        university, program = _program(db)
        db.commit()
        program_id = program.id
        assert review.recompute_entity_confidence(db, "program", program_id) == "VERIFIED"

        response = _create(client, api, headers, program_id, "overview", "C2-12 fixture. (new)")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "NEEDS_REVIEW"

        db.expire_all()
        entity = db.get(Program, program_id)
        derived = review.recompute_entity_confidence(db, "program", program_id)
        assert derived == "NEEDS_REVIEW"
        assert entity.confidence == derived, "the entity must not describe the register as it was"
        assert entity.verified_by is None
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_creation_cannot_promote_or_attribute_the_creator(client, api):
    """A created row is never evidence *for* a value, and never names the creator as verifier."""
    db = new_session()
    try:
        headers = _staff()
        university, program = _program(db, confidence="NEEDS_REVIEW")
        db.commit()
        program_id = program.id

        response = _create(client, api, headers, program_id, "overview", "C2-12 fixture.")
        assert response.status_code == 200, response.text

        db.expire_all()
        entity = db.get(Program, program_id)
        assert entity.confidence == "NEEDS_REVIEW"
        assert entity.verified_by is None
        assert entity.verified_at is None
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_forged_status_on_create_is_stripped_and_still_refreshes(client, api):
    db = new_session()
    try:
        headers = _staff()
        university, program = _program(db)
        db.commit()
        program_id = program.id

        response = client.post(f"{api}/admin/verifications", headers=headers,
                               json={"entity_type": "program", "entity_id": program_id,
                                     "field_name": "overview", "field_value": "forged claim",
                                     "status": "VERIFIED", "verified_by": "someone else",
                                     "source_url": SOURCE})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "NEEDS_REVIEW"
        assert body["verified_by"] is None

        db.expire_all()
        assert db.get(Program, program_id).confidence == "NEEDS_REVIEW"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_a_batch_of_creates_refreshes_every_affected_entity_and_no_other(client, api):
    db = new_session()
    try:
        headers = _staff()
        uni_a, program_a = _program(db)
        uni_b, program_b = _program(db)
        uni_c, program_c = _program(db)
        db.commit()
        ids = {"a": program_a.id, "b": program_b.id, "c": program_c.id}

        for field, value in (("overview", "First new claim."), ("award", "MSc")):
            assert _create(client, api, headers, ids["a"], field, value).status_code == 200
        assert _create(client, api, headers, ids["b"], "overview", "Second new claim.").status_code == 200

        db.expire_all()
        assert db.get(Program, ids["a"]).confidence == "NEEDS_REVIEW"
        assert db.get(Program, ids["b"]).confidence == "NEEDS_REVIEW"
        assert db.get(Program, ids["c"]).confidence == "VERIFIED", "an untouched entity is untouched"
        for pid in ids.values():
            assert db.get(Program, pid).confidence == \
                review.recompute_entity_confidence(db, "program", pid)
        _drop(db, uni_a, program_a)
        _drop(db, uni_b, program_b)
        _drop(db, uni_c, program_c)
    finally:
        db.rollback()
        db.close()


def test_refresh_adds_no_history_and_one_audit_entry(client, api):
    db = new_session()
    try:
        headers = _staff()
        university, program = _program(db)
        db.commit()
        program_id = program.id
        from sqlalchemy import func
        audit_watermark = db.scalar(select(func.max(AuditLog.id))) or 0
        before_changes = len(db.execute(
            select(CatalogueChange).where(
                CatalogueChange.entity_type == "program",
                CatalogueChange.entity_id == program_id)).scalars().all())

        assert _create(client, api, headers, program_id, "overview", "C2-12 claim.").status_code == 200

        db.expire_all()
        after_changes = len(db.execute(
            select(CatalogueChange).where(
                CatalogueChange.entity_type == "program",
                CatalogueChange.entity_id == program_id)).scalars().all())
        assert after_changes == before_changes, "a refresh is not a change"

        creates = list(db.execute(
            select(AuditLog).where(AuditLog.entity_type == "verifications",
                                   AuditLog.action == "CREATE",
                                   AuditLog.id > audit_watermark)).scalars().all())
        assert len(creates) == 1, "a refresh is not an auditable action of its own"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_refresh_uses_the_whole_register_not_the_new_row(client, api):
    """One pending field keeps the record withheld; approving the set promotes it."""
    db = new_session()
    try:
        headers = _staff()
        university, program = _program(db)
        db.commit()
        program_id = program.id
        pending = DataVerification(entity_type="program", entity_id=program_id,
                                   field_name="award", field_value="MSc", status="NEEDS_REVIEW",
                                   source_url=SOURCE, source_type=OFFICIAL)
        db.add(pending)
        db.commit()

        assert _create(client, api, headers, program_id, "discipline",
                       "Computer Science").status_code == 200
        db.expire_all()
        assert db.get(Program, program_id).confidence == "NEEDS_REVIEW"

        created = db.execute(
            select(DataVerification).where(
                DataVerification.entity_type == "program",
                DataVerification.entity_id == program_id,
                DataVerification.field_name == "discipline")).scalars().first()
        for claim in (pending, created):
            review.resolve_field(db, claim, status="VERIFIED", actor_email="dm@c2-12.test")
            db.commit()
        assert review.recompute_entity_confidence(db, "program", program_id) == "VERIFIED"
        db.expire_all()
        assert db.get(Program, program_id).confidence == "VERIFIED"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_create_for_a_missing_entity_does_not_fail_or_touch_other_entities(client, api):
    db = new_session()
    try:
        headers = _staff()
        university, program = _program(db)
        db.commit()
        program_id = program.id
        missing = 99_000_000

        response = _create(client, api, headers, missing, "overview", "Orphan claim.")
        assert response.status_code == 200, response.text

        db.expire_all()
        assert db.get(Program, program_id).confidence == "VERIFIED", "unrelated entity untouched"
        for row in db.execute(
            select(DataVerification).where(
                DataVerification.entity_type == "program",
                DataVerification.entity_id == missing)).scalars():
            db.delete(row)
        db.commit()
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_the_refresh_is_part_of_the_create_transaction(monkeypatch, client, api):
    """If the write fails, neither the claim nor the entity change may survive."""
    db = new_session()
    try:
        headers = _staff()
        university, program = _program(db)
        db.commit()
        program_id = program.id

        import app.api.v1.routers.admin as admin_router

        def refuse(db_, resource):
            raise RuntimeError("commit failed after the refresh")

        monkeypatch.setattr(admin_router, "_commit_or_refuse", refuse)
        with pytest.raises(RuntimeError):
            _create(client, api, headers, program_id, "overview", "Never committed.")

        db.expire_all()
        assert db.get(Program, program_id).confidence == "VERIFIED"
        assert db.execute(
            select(DataVerification).where(
                DataVerification.entity_type == "program",
                DataVerification.entity_id == program_id,
                DataVerification.field_name == "overview")).scalars().first() is None
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


@pytest.mark.pg
def test_pg_create_and_refresh_are_atomic(client, api):
    """On PostgreSQL, a create that loses its transaction leaves neither row nor state behind."""
    db = new_session()
    try:
        if db.get_bind().dialect.name != "postgresql":
            skip_pg_only("this proves PostgreSQL transaction behaviour")
        headers = _staff()
        university, program = _program(db)
        db.commit()
        program_id = program.id

        import app.api.v1.routers.admin as admin_router

        original = admin_router._commit_or_refuse

        def refuse(session, resource):
            session.rollback()
            raise RuntimeError("simulated failure before commit")

        try:
            admin_router._commit_or_refuse = refuse
            with pytest.raises(RuntimeError):
                _create(client, api, headers, program_id, "overview", "Never committed.")
        finally:
            admin_router._commit_or_refuse = original

        db.expire_all()
        assert db.get(Program, program_id).confidence == "VERIFIED"
        assert db.execute(
            select(DataVerification).where(
                DataVerification.entity_type == "program",
                DataVerification.entity_id == program_id,
                DataVerification.field_name == "overview")).scalars().first() is None

        # ...and the successful path still refreshes.
        assert _create(client, api, headers, program_id, "overview", "Committed.").status_code == 200
        db.expire_all()
        assert db.get(Program, program_id).confidence == \
            review.recompute_entity_confidence(db, "program", program_id) == "NEEDS_REVIEW"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()
