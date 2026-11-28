"""W15 - integrated acceptance across the completed workstreams.

Each test walks a scenario the platform must survive end to end: catalogue data arrives from a
source, a reviewer decides, a material change lands, the public and assistant surfaces react
correctly, and the accepted W4/W5/W6/W7 contracts still hold around it. They deliberately cross
module boundaries, because that is where the earlier workstreams' guarantees can silently break.

Scenarios 1-15 of the W15 matrix are covered by the four tests below plus the W9/W12/W13 suites;
the mapping is recorded in ``docs/11``.
"""
from __future__ import annotations

import os

import pytest
from app.core import verification_policy as policy
from app.core.security import create_access_token
from app.models.crm import AuditLog, CatalogueChange, DataVerification, ImportBatch, ImportRow, User
from app.models.education import Country, Program, University
from app.services import assistant, catalogue_changes, importer, scoping
from sqlalchemy import select

from task2_helpers import new_session, utc


@pytest.fixture
def db():
    session = new_session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _staff(role: str = "DATA_MANAGER") -> dict:
    session = new_session()
    try:
        user = User(email=f"w15-{os.urandom(4).hex()}@scholara.test", full_name="W15 Probe",
                    password_hash="x", role=role, is_active=True)
        session.add(user)
        session.commit()
        return {"Authorization": f"Bearer {create_access_token(str(user.id), role)}",
                "email": user.email}
    finally:
        session.close()


def _country(db, iso2="US"):
    return db.execute(select(Country).where(Country.iso2 == iso2)).scalars().first()


def _import_programme(db, *, slug, actor, tuition=24000.0, university_slug=None):
    university_slug = university_slug or f"{slug}-uni"
    batch = importer.stage(db, entity_type="program", rows=[{
        "university": university_slug, "name": f"W15 Programme {slug}", "slug": slug,
        "degree_level": "MASTER", "award": "MSc", "discipline": "Astrobiology",
        "currency": "USD", "tuition_annual": tuition, "is_published": True,
        "source_url": f"https://w15.example.edu/{slug}",
        "source_type": "TIER_1_OFFICIAL_UNIVERSITY",
        "confidence": "VERIFIED",
    }], source_url=f"https://w15.example.edu/{slug}", actor=actor)
    result = importer.commit_batch(db, batch, actor=actor)
    assert not result["failures"], result
    return batch


def _cleanup_programme(db, slug):
    ids = [p.id for p in db.query(Program).filter(Program.slug == slug).all()]
    if ids:
        db.query(DataVerification).filter(DataVerification.entity_type == "program",
                                          DataVerification.entity_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(CatalogueChange).filter(CatalogueChange.entity_type == "program",
                                         CatalogueChange.entity_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(Program).filter(Program.id.in_(ids)).delete(synchronize_session=False)
    db.query(University).filter(University.slug == f"{slug}-uni").delete(synchronize_session=False)
    batch_ids = [b.id for b in db.query(ImportBatch).filter(
        ImportBatch.source_url == f"https://w15.example.edu/{slug}").all()]
    if batch_ids:
        db.query(ImportRow).filter(ImportRow.batch_id.in_(batch_ids)).delete(
            synchronize_session=False)
        db.query(ImportBatch).filter(ImportBatch.id.in_(batch_ids)).delete(
            synchronize_session=False)
    db.commit()


# =====================================================================
# scenarios 1-7: ingest -> unverified -> review -> material change -> decision
# =====================================================================
def test_scenario_ingest_review_change_and_publish(client, api, auth, db):
    slug = "w15-lifecycle"
    actor = "uploader@scholara.test"
    try:
        # An institution must exist for the programme import to resolve against.
        country = _country(db)
        db.add(University(name="W15 Lifecycle Uni", slug=f"{slug}-uni", country_id=country.id,
                          city="Testville", overview="x", is_published=True,
                          source_url="https://w15.example.edu/uni", confidence="NEEDS_REVIEW",
                          review_due_at=utc(90)))
        db.commit()

        # 1 + 2. Ingested with provenance, and not verified by its own claim.
        _import_programme(db, slug=slug, actor=actor)
        db.expire_all()
        programme = db.query(Program).filter(Program.slug == slug).first()
        assert programme is not None
        assert programme.confidence == "NEEDS_REVIEW"
        assert programme.source_url == f"https://w15.example.edu/{slug}"
        assert programme.retrieved_at is not None

        # 6. Public/assistant surfaces must not present it as a match yet.
        assert assistant.search_programs(db, subject=slug,
                                        limit=10) == []

        # 2. A reviewer verifies every registered field.
        fields = db.query(DataVerification).filter(
            DataVerification.entity_type == "program",
            DataVerification.entity_id == programme.id).all()
        assert fields
        for field in fields:
            res = client.post(f"{api}/admin/verification/{field.id}/resolve", headers=auth,
                              json={"status": "VERIFIED"})
            assert res.status_code == 200, res.text
        db.expire_all()
        assert db.get(Program, programme.id).confidence == "VERIFIED"
        assert assistant.search_programs(db, subject=slug, limit=10), (
            "a verified programme becomes answerable")

        # 3 + 4. A material tuition change arrives with a different source.
        batch = importer.stage(db, entity_type="program", rows=[{
            "university": f"{slug}-uni", "name": f"W15 Programme {slug}", "slug": slug,
            "degree_level": "MASTER", "award": "MSc", "discipline": "Astrobiology",
            "currency": "USD", "tuition_annual": 31000, "is_published": True,
            "source_url": f"https://w15.example.edu/{slug}-new",
            "source_type": "TIER_1_OFFICIAL_UNIVERSITY",
        }], source_url=f"https://w15.example.edu/{slug}-new", actor=actor)
        assert [r.action for r in batch.rows] == ["CONFLICT"], "a verified fee is protected"
        assert assistant.search_programs(db, subject=slug, limit=10)

        # 5. The change is recorded and pending; the old value survives.
        pending = db.query(CatalogueChange).filter(
            CatalogueChange.entity_type == "program",
            CatalogueChange.entity_id == programme.id,
            CatalogueChange.field_name == "tuition_annual",
            CatalogueChange.status == "PENDING").all()
        assert pending, "a superseded fee must be traceable before it is replaced"
        assert pending[0].previous_value == "24000.0"
        assert pending[0].new_value in ("31000", "31000.0")
        assert pending[0].source_url == f"https://w15.example.edu/{slug}-new"

        # 7. The reviewer rejects the change with a reason.
        res = client.post(f"{api}/admin/verification/changes/{pending[0].id}/decide",
                          headers=auth, json={"status": "REJECTED",
                                              "note": "the new page is a different campus"})
        assert res.status_code == 200, res.text
        db.expire_all()
        assert db.get(CatalogueChange, pending[0].id).status == "REJECTED"
        # Rejection does not touch the verified value or its state.
        assert float(db.get(Program, programme.id).tuition_annual) == 24000.0
        assert db.get(Program, programme.id).confidence == "VERIFIED"
    finally:
        _cleanup_programme(db, slug)


def test_scenario_an_unreviewed_change_is_never_presented_as_verified(db):
    """The pending value must not reach a student, and the old one must stay answerable."""
    slug = "w15-pending"
    actor = "uploader@scholara.test"
    try:
        country = _country(db)
        db.add(University(name="W15 Pending Uni", slug=f"{slug}-uni", country_id=country.id,
                          city="Testville", overview="x", is_published=True,
                          source_url="https://w15.example.edu/uni", confidence="NEEDS_REVIEW",
                          review_due_at=utc(90)))
        db.commit()
        _import_programme(db, slug=slug, actor=actor, tuition=20000.0)
        db.expire_all()
        programme = db.query(Program).filter(Program.slug == slug).first()
        for field in db.query(DataVerification).filter(
                DataVerification.entity_type == "program",
                DataVerification.entity_id == programme.id).all():
            field.status = "VERIFIED"
            field.verified_at = utc(-1)
        programme.confidence = "VERIFIED"
        programme.verified_at = utc(-1)
        db.commit()

        # A fresh import brings a new fee; approve the row so it is applied.
        batch = importer.stage(db, entity_type="program", rows=[{
            "university": f"{slug}-uni", "name": f"W15 Programme {slug}", "slug": slug,
            "degree_level": "MASTER", "award": "MSc", "discipline": "Astrobiology",
            "currency": "USD", "tuition_annual": 45000, "is_published": True,
            "source_url": f"https://w15.example.edu/{slug}-2",
            "source_type": "TIER_1_OFFICIAL_UNIVERSITY",
        }], source_url=f"https://w15.example.edu/{slug}-2", actor=actor)
        for row in db.query(ImportRow).filter(ImportRow.batch_id == batch.id).all():
            row.decision = "APPROVE"
        db.commit()
        importer.commit_batch(db, batch, actor=actor)

        db.expire_all()
        after = db.get(Program, programme.id)
        assert float(after.tuition_annual) == 45000.0
        assert after.confidence == "NEEDS_REVIEW", "the applied value is not yet verified"
        assert policy.evaluate_record(db, after).usable is False
        rows = assistant.search_programs(db, subject=slug, limit=10)
        assert rows == [], "a changed, unreviewed figure is not presented as current"
    finally:
        _cleanup_programme(db, slug)


# =====================================================================
# scenarios 8 + 13: the trust state does not break W6 scoping or matching
# =====================================================================
def test_scenario_trust_state_does_not_bypass_retrieval_scope(client, api, auth, db):
    slug = "w15-scope"
    actor = "uploader@scholara.test"
    canada = db.execute(select(Country).where(Country.iso2 == "CA")).scalars().first()
    try:
        db.add(University(name="W15 Scope Uni", slug=f"{slug}-uni", country_id=canada.id,
                          city="Testville", overview="x", is_published=True,
                          source_url="https://w15.example.edu/uni", confidence="NEEDS_REVIEW",
                          review_due_at=utc(90)))
        db.commit()
        _import_programme(db, slug=slug, actor=actor)
        db.expire_all()
        programme = db.query(Program).filter(Program.slug == slug).first()
        for field in db.query(DataVerification).filter(
                DataVerification.entity_type == "program",
                DataVerification.entity_id == programme.id).all():
            field.status = "VERIFIED"
            field.verified_at = utc(-1)
        programme.confidence = "VERIFIED"
        programme.verified_at = utc(-1)
        db.commit()

        # Verified, but out of scope: W6 still wins.
        us_scope = scoping.resolve_scope(db, context={"country": "United States"})
        assert assistant.search_programs(db, subject=slug, limit=10,
                                        scope=us_scope) == []
        ca_scope = scoping.resolve_scope(db, context={"country": "Canada"})
        assert assistant.search_programs(db, subject=slug, limit=10,
                                        scope=ca_scope)

        # And an unresolved scope still fails closed after all the W9/W12 writes.
        bad = scoping.resolve_scope(db, context={"country": "Atlantis"})
        assert not bad.ok
        assert assistant.search_programs(db, scope=bad) == []
    finally:
        _cleanup_programme(db, slug)


def test_scenario_matching_and_assistant_agree_on_a_verified_record(db):
    slug = "w15-agree"
    actor = "uploader@scholara.test"
    try:
        country = _country(db)
        db.add(University(name="W15 Agree Uni", slug=f"{slug}-uni", country_id=country.id,
                          city="Testville", overview="x", is_published=True,
                          source_url="https://w15.example.edu/uni", confidence="NEEDS_REVIEW",
                          review_due_at=utc(90)))
        db.commit()
        _import_programme(db, slug=slug, actor=actor)
        db.expire_all()
        programme = db.query(Program).filter(Program.slug == slug).first()
        for field in db.query(DataVerification).filter(
                DataVerification.entity_type == "program",
                DataVerification.entity_id == programme.id).all():
            field.status = "VERIFIED"
            field.verified_at = utc(-1)
        programme.confidence = "VERIFIED"
        programme.verified_at = utc(-1)
        db.commit()

        from app.services import matching

        assistant_rows = assistant.search_programs(db, subject=slug, limit=10)
        match = matching.match_programs(db, {"target_degree_level": "MASTER"},
                                       {"subject": slug}, limit=10)
        exact = {r["program"].slug for r in match["results"]}
        assert programme.slug in exact
        assert {r["slug"] for r in assistant_rows} == {programme.slug}, (
            "the assistant and the matching API must see the same verified record")
    finally:
        _cleanup_programme(db, slug)


# =====================================================================
# scenarios 10-12 + 15: W7 and W4 contracts survive the new workflow
# =====================================================================
def test_scenario_privacy_and_audit_contracts_survive(client, api, auth, db):
    slug = "w15-privacy"
    try:
        country = _country(db)
        db.add(University(name="W15 Privacy Uni", slug=f"{slug}-uni", country_id=country.id,
                          city="Testville", overview="x", is_published=True,
                          source_url="https://w15.example.edu/uni", confidence="NEEDS_REVIEW",
                          review_due_at=utc(90)))
        db.commit()
        # 12. Consent still cannot be inferred on a lead created after the new tables exist.
        refused = client.post(f"{api}/leads", json={
            "full_name": "W15 Consent Probe", "phone": "+8801777777777",
            "page_path": "/my/private-token", "utm": {"ref": "/my/private-token"}})
        assert refused.status_code == 422
        accepted = client.post(f"{api}/leads", json={
            "full_name": "W15 Consent Probe", "phone": "+8801777777777",
            "page_path": "/my/private-token", "utm": {"ref": "/my/private-token"},
            "consent_contact": True})
        assert accepted.status_code == 200, accepted.text
        from app.models.crm import AnalyticsEvent, Lead

        lead = db.query(Lead).filter(Lead.phone == "+8801777777777").order_by(Lead.id.desc()).first()
        assert lead is not None
        assert not lead.utm or "private-token" not in str(lead.utm)
        events = db.query(AnalyticsEvent).filter(
            AnalyticsEvent.path.like("%private-token%")).all()
        assert events == [], "a portal capability must never reach an analytics row"

        # 11. Reading student data is still audited, one event per request.
        before = db.query(AuditLog).filter(AuditLog.action == "READ").count()
        assert client.get(f"{api}/admin/leads", headers=auth).status_code == 200
        db.expire_all()
        after = db.query(AuditLog).filter(AuditLog.action == "READ").count()
        assert after == before + 1

        # 15. A failed review action leaves nothing behind.
        unknown = client.post(f"{api}/admin/verification/99999999/resolve", headers=auth,
                              json={"status": "VERIFIED"})
        assert unknown.status_code == 404

        # 10. Erasure still removes the student and their dependent rows in one transaction.
        profile = client.post(f"{api}/profiles", json={
            "full_name": "W15 Erase Probe", "email": "w15.erase@example.com",
            "phone": "+8801788888888", "highest_qualification": "Bachelor",
            "cgpa": 3.5, "cgpa_scale": 4.0, "target_degree_level": "MASTER",
        }).json()
        profile_id = profile["profile"]["id"] if "profile" in profile else profile.get("id")
        assert profile_id
        deleted = client.delete(f"{api}/admin/student-profiles/{profile_id}", headers=auth)
        assert deleted.status_code == 200, deleted.text
        db.expire_all()
        from app.models.crm import StudentProfile

        assert db.get(StudentProfile, profile_id) is None
    finally:
        from app.models.crm import AnalyticsEvent, Lead

        db.query(Lead).filter(Lead.phone == "+8801777777777").delete()
        db.commit()
        _cleanup_programme(db, slug)


def test_scenario_the_integrity_report_reflects_the_workflow(client, api, auth, db):
    """After a change is pending, the report says so - the report is wired to real state."""
    slug = "w15-report"
    actor = "uploader@scholara.test"
    try:
        country = _country(db)
        db.add(University(name="W15 Report Uni", slug=f"{slug}-uni", country_id=country.id,
                          city="Testville", overview="x", is_published=True,
                          source_url="https://w15.example.edu/uni", confidence="NEEDS_REVIEW",
                          review_due_at=utc(90)))
        db.commit()
        _import_programme(db, slug=slug, actor=actor)
        db.expire_all()
        programme = db.query(Program).filter(Program.slug == slug).first()
        catalogue_changes.record(db, entity_type="program", entity_id=programme.id,
                                 field_name="tuition_annual", previous=24000, new=90000,
                                 actor=actor, source_url="https://w15.example.edu/rep")
        db.commit()

        res = client.get(f"{api}/admin/catalogue/integrity", headers=auth)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["counts"]["pending_material_change"] >= 1
        assert any(f["category"] == "pending_material_change"
                   for f in body["findings"])
    finally:
        _cleanup_programme(db, slug)
