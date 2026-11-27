"""H-1 - a direct catalogue edit cannot keep a verification it just invalidated.

The regression this file exists for: ``PUT /api/v1/admin/{resource}/{id}`` applies any content
column a payload names, and before this campaign it left ``confidence``, ``verified_at``,
``verified_by`` and the source untouched. A counselor could therefore change a tuition figure and
the read-time policy went on presenting the new number as verified against the old evidence -
a two-request path to a false official figure.

Every assertion below is on stored state or on the public payload, never on a status code alone:
the failure mode was a *successful* response that quietly kept a false claim.
"""
from __future__ import annotations

import os

import pytest
from app.models.crm import AuditLog, CatalogueChange, DataVerification, User
from app.models.education import (
    Country,
    Faculty,
    Professor,
    Program,
    ProgramIntake,
    ProgramRequirement,
    Scholarship,
    University,
    UniversityRanking,
)
from app.services import catalogue_writes, review
from sqlalchemy import select

from task2_helpers import new_session, utc


def _staff_headers(role: str) -> dict:
    from app.core.security import create_access_token

    db = new_session()
    try:
        user = User(email=f"{role.lower()}-{os.urandom(4).hex()}@h1.scholara.test",
                    full_name=f"{role} Probe", password_hash="x", role=role, is_active=True)
        db.add(user)
        db.commit()
        token = create_access_token(str(user.id), role)
        return {"Authorization": f"Bearer {token}"}
    finally:
        db.close()


def _verified_program(db, slug: str, tuition: float = 20000.0):
    """A programme whose tuition genuinely passed review: state, reviewer, source and dates."""
    country = db.execute(select(Country).where(Country.iso2 == "US")).scalars().first()
    university = University(name=f"H1 {slug} Uni", slug=f"h1-{slug}-uni", country_id=country.id,
                            city="Testville", overview="H1 test institution.",
                            confidence="NEEDS_REVIEW",
                            source_url="https://h1.example.edu/official",
                            source_type="TIER_1_OFFICIAL_UNIVERSITY")
    db.add(university)
    db.flush()
    programme = Program(university_id=university.id, name=f"H1 {slug} Programme",
                        slug=f"h1-{slug}-prog", degree_level="MASTER", award="MSc",
                        discipline="Computer Science", currency="USD", tuition_annual=tuition,
                        overview="H1 test programme.", is_published=True,
                        confidence="VERIFIED", verified_by="registrar@h1.example.edu",
                        verified_at=utc(-3), review_due_at=utc(300),
                        source_url="https://h1.example.edu/programme-fees",
                        source_type="TIER_1_OFFICIAL_UNIVERSITY", retrieved_at=utc(-3))
    db.add(programme)
    db.flush()
    return university, programme


def _changes(db, entity_type: str, entity_id: int):
    return db.execute(select(CatalogueChange).where(
        CatalogueChange.entity_type == entity_type,
        CatalogueChange.entity_id == entity_id)).scalars().all()


@pytest.fixture
def db():
    session = new_session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _cleanup(db, university: University | None):
    if university is None:
        return
    programme_ids = [p.id for p in db.query(Program).filter(
        Program.university_id == university.id).all()]
    entity_ids = {"program": programme_ids,
                  "university": [university.id]}
    if programme_ids:
        for model in (ProgramIntake, ProgramRequirement):
            entity_type = "program_intake" if model is ProgramIntake else "program_requirement"
            kids = [k.id for k in db.query(model).filter(
                model.program_id.in_(programme_ids)).all()]
            entity_ids[entity_type] = kids
            db.query(model).filter(model.program_id.in_(programme_ids)).delete(
                synchronize_session=False)
    for model, entity_type in ((UniversityRanking, "university_ranking"),
                               (Faculty, "faculty"), (Professor, "professor")):
        rows = [r.id for r in db.query(model).filter(
            model.university_id == university.id).all()]
        entity_ids[entity_type] = rows
        db.query(model).filter(model.university_id == university.id).delete(
            synchronize_session=False)
    db.query(Program).filter(Program.university_id == university.id).delete(
        synchronize_session=False)
    for change in db.query(CatalogueChange).all():
        if change.entity_id in entity_ids.get(change.entity_type, []):
            db.delete(change)
    for row in db.query(DataVerification).all():
        if row.entity_id in entity_ids.get(row.entity_type, []):
            db.delete(row)
    db.query(Scholarship).filter(Scholarship.slug == "h1-merit").delete(
        synchronize_session=False)
    db.query(University).filter(University.id == university.id).delete(synchronize_session=False)
    db.commit()


# =====================================================================
# 1. the original attack, end to end
# =====================================================================
def test_a_counsellor_edit_of_a_verified_fee_withholds_the_verification(client, api, db):
    """The exact reported path: verified tuition, COUNSELOR, one generic PUT."""
    university, programme = _verified_program(db, "attack")
    db.commit()
    before = client.get(f"{api}/programs/{programme.slug}").json()["program"]["provenance"]
    assert before["verified"] is True, "precondition: the record starts out genuinely verified"
    # Ids are reused as earlier tests clean up, so "the audit row for entity 12" is ambiguous
    # across files. Everything this request writes has an id above this watermark.
    audit_watermark = db.execute(select(AuditLog.id).order_by(AuditLog.id.desc())).scalars().first() or 0

    try:
        res = client.put(f"{api}/admin/programs/{programme.id}",
                         headers=_staff_headers("COUNSELOR"),
                         json={"tuition_annual": 1500.0})
        assert res.status_code == 200, res.text

        db.expire_all()
        row = db.get(Program, programme.id)
        assert row.tuition_annual == 1500.0, "the edit still applies"
        assert row.confidence == "NEEDS_REVIEW", "a new figure is not a verified figure"
        assert row.verified_at is None and row.verified_by is None
        assert row.review_due_at is None, "the old review deadline described the old figure"

        # What a student sees, which is what matters.
        after = client.get(f"{api}/programs/{programme.slug}").json()["program"]["provenance"]
        assert after["verified"] is False
        assert after["verification_reason"] == "NOT_VERIFIED"
        assert after["verified_at"] is None

        # The change is auditable, with both values and the person responsible.
        changes = _changes(db, "program", programme.id)
        assert len(changes) == 1, changes
        change = changes[0]
        assert change.field_name == "tuition_annual"
        assert change.previous_value == "20000.0"
        assert change.new_value == "1500.0"
        assert change.actor and change.actor.endswith("@h1.scholara.test")
        assert change.status == "PENDING", "an applied edit still awaits a human decision"

        # The field register describes the claim that is now stored, not the old one.
        register = db.execute(select(DataVerification).where(
            DataVerification.entity_type == "program",
            DataVerification.entity_id == programme.id)).scalars().all()
        assert register, "the edited value must be registered for review"
        assert all(r.status == "NEEDS_REVIEW" for r in register)
        assert all(r.verified_at is None and r.verified_by is None for r in register)
        assert any(r.field_value == "1500.0" for r in register)

        # The audit line names the fields and the outcome, never the values.
        audit = db.execute(select(AuditLog).where(
            AuditLog.action == "UPDATE", AuditLog.entity_type == "programs",
            AuditLog.entity_id == programme.id,
            AuditLog.id > audit_watermark)).scalars().first()
        assert audit is not None
        assert audit.detail["invalidated_verification"] is True
        assert audit.detail["material_fields"] == ["tuition_annual"]
        assert "1500" not in str(audit.detail) and "20000" not in str(audit.detail)
    finally:
        _cleanup(db, university)


# =====================================================================
# 2. what must NOT invalidate
# =====================================================================
def test_resubmitting_the_same_value_changes_nothing(client, api, db):
    university, programme = _verified_program(db, "same")
    db.commit()
    try:
        res = client.put(f"{api}/admin/programs/{programme.id}",
                         headers=_staff_headers("COUNSELOR"),
                         json={"tuition_annual": 20000.0})
        assert res.status_code == 200, res.text
        db.expire_all()
        row = db.get(Program, programme.id)
        assert row.confidence == "VERIFIED", "saving a form without edits must not unverify"
        assert row.verified_by == "registrar@h1.example.edu"
        assert not _changes(db, "program", programme.id), "no change, no history row"
    finally:
        _cleanup(db, university)


def test_a_formatting_only_change_is_not_material(client, api, db):
    university, programme = _verified_program(db, "format")
    db.commit()
    try:
        res = client.put(f"{api}/admin/programs/{programme.id}",
                         headers=_staff_headers("COUNSELOR"),
                         json={"overview": "H1  test   programme."})
        assert res.status_code == 200, res.text
        db.expire_all()
        row = db.get(Program, programme.id)
        assert row.confidence == "VERIFIED", "whitespace is not a factual change"
        assert not _changes(db, "program", programme.id)
    finally:
        _cleanup(db, university)


@pytest.mark.parametrize("payload", [
    {"is_featured": True},
    {"seo_title": "A better title for search"},
    {"slug": "h1-operational-prog"},
    {"og_image": "/img/cover.png"},
])
def test_editorial_and_exposure_fields_keep_the_verification(client, api, db, payload):
    """Presentation, routing and feature flags are not claims about the world."""
    university, programme = _verified_program(db, "ops")
    db.commit()
    try:
        res = client.put(f"{api}/admin/programs/{programme.id}",
                         headers=_staff_headers("COUNSELOR"), json=payload)
        assert res.status_code == 200, res.text
        db.expire_all()
        row = db.get(Program, programme.id)
        assert row.confidence == "VERIFIED", f"{payload} is not a factual claim"
        assert row.verified_by == "registrar@h1.example.edu"
        assert not _changes(db, "program", programme.id)
    finally:
        _cleanup(db, university)


# =====================================================================
# 3. provenance cannot certify, trust columns cannot be written
# =====================================================================
def test_restating_the_source_does_not_certify_the_value(client, api, db):
    university, programme = _verified_program(db, "prov")
    programme.confidence = "NEEDS_REVIEW"
    programme.verified_at = None
    programme.verified_by = None
    db.commit()
    try:
        res = client.put(f"{api}/admin/programs/{programme.id}",
                         headers=_staff_headers("COUNSELOR"),
                         json={"source_url": "https://h1.example.edu/another-page",
                               "source_type": "TIER_1_OFFICIAL_UNIVERSITY"})
        assert res.status_code == 200, res.text
        db.expire_all()
        row = db.get(Program, programme.id)
        assert row.source_url == "https://h1.example.edu/another-page", "evidence is writable"
        assert row.confidence == "NEEDS_REVIEW", "evidence supports a decision, it is not one"
        assert row.verified_by is None
    finally:
        _cleanup(db, university)


def test_trust_columns_in_the_payload_are_still_refused(client, api, db):
    """Defence in depth: the service refuses them too, not only the router's blocklist."""
    university, programme = _verified_program(db, "trust")
    programme.confidence = "NEEDS_REVIEW"
    programme.verified_at = None
    programme.verified_by = None
    db.commit()
    try:
        res = client.put(f"{api}/admin/programs/{programme.id}",
                         headers=_staff_headers("COUNSELOR"),
                         json={"confidence": "VERIFIED", "verified_by": "self",
                               "verified_at": "2026-01-01T00:00:00",
                               "review_due_at": "2030-01-01T00:00:00"})
        assert res.status_code == 200, res.text
        db.expire_all()
        row = db.get(Program, programme.id)
        assert row.confidence == "NEEDS_REVIEW"
        assert row.verified_by is None and row.verified_at is None
        # The payload's deadline was refused; the stored one is simply the old value, not 2030.
        assert row.review_due_at is None or row.review_due_at.year < 2030
    finally:
        _cleanup(db, university)


# =====================================================================
# 4. the edit cannot be re-certified by approving an unrelated field
# =====================================================================
def test_approving_another_field_cannot_re_certify_the_edited_one(db):
    """The register row appended for the edit is what keeps the derived state honest."""
    university, programme = _verified_program(db, "derive")
    other = DataVerification(
        entity_type="program", entity_id=programme.id, field_name="duration_months",
        field_value="24", status="VERIFIED", verified_at=utc(-1), verified_by="r@h1.example.edu",
        source_url="https://h1.example.edu/programme", source_type="TIER_1_OFFICIAL_UNIVERSITY")
    db.add(other)
    db.flush()

    catalogue_writes.apply_direct_edit(
        db, model=Program, row=programme, payload={"tuition_annual": 900.0},
        actor="editor@h1.scholara.test", blocked=catalogue_writes.TRUST_FIELDS)
    db.flush()

    # Every *other* field of the record is verified; the edited one is not.
    assert review.recompute_entity_confidence(db, "program", programme.id) != "VERIFIED", (
        "deriving the entity state must not re-certify a value nobody reviewed")
    try:
        _cleanup(db, university)
    finally:
        db.rollback()


# =====================================================================
# 5. every governed resource obeys the same rule
# =====================================================================
def _make_governed_rows(db, university: University, programme: Program):
    """One minimal verified row per governed resource, plus the field to edit on each."""
    faculty = Faculty(university_id=university.id, name="H1 Faculty of Science",
                      slug="h1-faculty", kind="FACULTY", confidence="VERIFIED",
                      verified_by="r@h1.example.edu", verified_at=utc(-2), review_due_at=utc(90),
                      source_url="https://h1.example.edu/faculty",
                      source_type="TIER_1_OFFICIAL_UNIVERSITY")
    professor = Professor(university_id=university.id, full_name="H1 Professor",
                          slug="h1-professor", title="Reader", confidence="VERIFIED",
                          verified_by="r@h1.example.edu", verified_at=utc(-2),
                          review_due_at=utc(90), source_url="https://h1.example.edu/profile",
                          source_type="TIER_1_OFFICIAL_UNIVERSITY")
    intake = ProgramIntake(program_id=programme.id, label="September 2027", term="Fall",
                           year=2027, status="OPEN", confidence="VERIFIED",
                           verified_by="r@h1.example.edu", verified_at=utc(-2),
                           review_due_at=utc(90), source_url="https://h1.example.edu/intakes",
                           source_type="TIER_1_OFFICIAL_UNIVERSITY")
    requirement = ProgramRequirement(program_id=programme.id, min_cgpa=3.0, cgpa_scale=4.0,
                                     ielts_overall=6.5, confidence="VERIFIED",
                                     verified_by="r@h1.example.edu", verified_at=utc(-2),
                                     review_due_at=utc(90),
                                     source_url="https://h1.example.edu/entry",
                                     source_type="TIER_1_OFFICIAL_UNIVERSITY")
    scholarship = Scholarship(name="H1 Global Merit Award", slug="h1-merit",
                              provider="H1 University", degree_levels="MASTER",
                              currency="USD", amount_value=5000.0, confidence="VERIFIED",
                              verified_by="r@h1.example.edu", verified_at=utc(-2),
                              review_due_at=utc(90), source_url="https://h1.example.edu/awards",
                              source_type="TIER_4_OFFICIAL_SCHOLARSHIP_PROVIDER")
    ranking = UniversityRanking(university_id=university.id, provider="H1 Ranking",
                                edition_year=2027, rank_overall=42, confidence="VERIFIED",
                                verified_by="r@h1.example.edu", verified_at=utc(-2),
                                review_due_at=utc(90), source_url="https://h1.example.edu/rank",
                                source_type="TIER_5_OFFICIAL_RANKING_PROVIDER")
    for row in (faculty, professor, intake, requirement, scholarship, ranking):
        db.add(row)
    db.flush()
    return {
        "faculties": (faculty, {"dean_name": "Professor Edited"}),
        "professors": (professor, {"title": "Professor"}),
        "program-intakes": (intake, {"label": "September 2028"}),
        "program-requirements": (requirement, {"ielts_overall": 7.5}),
        "scholarships": (scholarship, {"amount_value": 9000.0}),
        "university-rankings": (ranking, {"rank_overall": 7}),
        "universities": (university, {"established_year": 1911}),
        "programs": (programme, {"application_fee": 90.0}),
    }


def test_every_governed_resource_invalidates_its_verification(client, api, db):
    """The rule is a property of the model mixin, so it must hold for all of them."""
    university, programme = _verified_program(db, "all")
    university.confidence = "VERIFIED"
    university.verified_by = "r@h1.example.edu"
    university.verified_at = utc(-2)
    university.review_due_at = utc(90)
    db.commit()
    cases = _make_governed_rows(db, university, programme)
    db.commit()
    try:
        for resource, (row, payload) in cases.items():
            headers = _staff_headers("COUNSELOR")
            res = client.put(f"{api}/admin/{resource}/{row.id}", headers=headers, json=payload)
            assert res.status_code == 200, (resource, res.text)
            db.expire_all()
            fresh = db.get(type(row), row.id)
            assert fresh.confidence == "NEEDS_REVIEW", (
                f"{resource}: a material edit must withdraw verification")
            assert fresh.verified_at is None and fresh.verified_by is None, resource
            assert fresh.review_due_at is None, resource
    finally:
        db.query(Scholarship).filter(Scholarship.slug == "h1-merit").delete(
            synchronize_session=False)
        db.commit()
        _cleanup(db, university)


# =====================================================================
# 6. atomicity: no half-applied edit
# =====================================================================
def test_a_failed_edit_leaves_neither_the_value_nor_the_trust_state(client, api, db,
                                                                   monkeypatch):
    """The value, the invalidation and the history commit together or not at all."""
    university, programme = _verified_program(db, "atomic")
    db.commit()

    def _explode(*args, **kwargs):
        raise RuntimeError("change log unavailable")

    from app.services import catalogue_changes
    monkeypatch.setattr(catalogue_changes, "record", _explode)
    try:
        # The request fails; the point of the test is what the database holds afterwards.
        with pytest.raises(RuntimeError):
            client.put(f"{api}/admin/programs/{programme.id}",
                       headers=_staff_headers("COUNSELOR"),
                       json={"tuition_annual": 123.0})
        db.expire_all()
        row = db.get(Program, programme.id)
        assert row.tuition_annual == 20000.0, "the value must not survive a failed transaction"
        assert row.confidence == "VERIFIED", "nor may the record be left unverified"
        assert row.verified_by == "registrar@h1.example.edu"
        assert not _changes(db, "program", programme.id)
    finally:
        _cleanup(db, university)


# =====================================================================
# 7. the policy itself, as data
# =====================================================================
def test_the_material_field_policy_is_explicit_and_stable():
    """A second list to keep in sync is how the original hole happened; assert this one holds."""
    assert catalogue_writes.governs(Program) and catalogue_writes.governs(University)
    assert catalogue_writes.governs(Scholarship) and catalogue_writes.governs(ProgramIntake)
    assert catalogue_writes.governs(ProgramRequirement)
    assert catalogue_writes.governs(Faculty) and catalogue_writes.governs(Professor)
    assert catalogue_writes.governs(UniversityRanking)
    # Not governed: these carry no verification claim at all.
    assert not catalogue_writes.governs(Country)
    assert not catalogue_writes.governs(User)
    # The entity type comes from the existing policy map, not a private copy.
    assert catalogue_writes.entity_type_for(Program) == "program"
    assert catalogue_writes.entity_type_for(UniversityRanking) == "university_ranking"
    # Trust and provenance columns are never a trigger and never certifying.
    for field in ("confidence", "verified_at", "verified_by", "review_due_at",
                  "source_url", "source_type"):
        assert field in catalogue_writes.UNWRITABLE_FIELDS
    # A factual column is never accidentally in the non-factual allowlist.
    for field in ("tuition_annual", "application_deadline", "amount_value", "min_cgpa",
                  "rank_overall", "full_name", "name"):
        assert field not in catalogue_writes.NON_FACTUAL_FIELDS
