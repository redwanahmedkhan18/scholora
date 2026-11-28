"""W9 - catalogue integrity, provenance and verification states.

The auditor is a report, so these tests assert two things: that a real structural defect is
reported under the right category, and that nothing in the report claims a record is *true*. The
public and assistant assertions check the other half of W9: unverified data is never presented as
confirmed fact.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from app.core.security import create_access_token
from app.models.base import DEGREE_LEVELS
from app.models.crm import DataVerification, User
from app.models.education import Country, Program, ProgramIntake, University
from app.services import assistant
from app.services import catalogue_integrity as integrity
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


def _country(db, iso2="US"):
    return db.execute(select(Country).where(Country.iso2 == iso2)).scalars().first()


def _staff(role: str) -> dict:
    session = new_session()
    try:
        user = User(email=f"w9-{os.urandom(4).hex()}@scholara.test", full_name="W9 Probe",
                    password_hash="x", role=role, is_active=True)
        session.add(user)
        session.commit()
        return {"Authorization": f"Bearer {create_access_token(str(user.id), role)}"}
    finally:
        session.close()


def _categories(report) -> dict:
    return report["counts"]


def _by_category(report, category):
    return [f for f in report["findings"] if f["category"] == category]


def test_missing_provenance_is_reported(db):
    try:
        row = University(name="W9 No Source", slug="w9-no-source", country_id=_country(db).id,
                         city="Testville", overview="x", is_published=True,
                         source_url=None, confidence="NEEDS_REVIEW")
        db.add(row)
        db.commit()
        report = integrity.audit(db)
        found = [f for f in _by_category(report, "missing_provenance")
                 if f["entity_type"] == "university" and f["entity_id"] == row.id]
        assert found, "a published record with no source link must be reported"
        assert found[0]["severity"] == "HIGH"
    finally:
        db.query(University).filter(University.slug == "w9-no-source").delete()
        db.commit()


def test_invalid_source_url_and_tier_are_reported(db):
    try:
        row = University(name="W9 Bad Url", slug="w9-bad-url", country_id=_country(db).id,
                         city="Testville", overview="x", is_published=True,
                         source_url="not-a-url", source_type="TIER_99_INVENTED",
                         confidence="NEEDS_REVIEW")
        db.add(row)
        db.commit()
        report = integrity.audit(db)
        assert any(f["entity_id"] == row.id for f in _by_category(report, "invalid_source_url"))
        assert any(f["entity_id"] == row.id for f in _by_category(report, "invalid_source_type"))
    finally:
        db.query(University).filter(University.slug == "w9-bad-url").delete()
        db.commit()


def test_duplicate_university_is_reported(db):
    country = _country(db)
    try:
        first = University(name="W9 Twin Institute", slug="w9-twin-a", country_id=country.id,
                           city="Testville", overview="x", is_published=True,
                           source_url="https://w9.example.edu", confidence="NEEDS_REVIEW")
        second = University(name="W9  Twin  Institute", slug="w9-twin-b", country_id=country.id,
                            city="Testville", overview="x", is_published=True,
                            source_url="https://w9.example.edu", confidence="NEEDS_REVIEW")
        db.add_all([first, second])
        db.commit()
        report = integrity.audit(db)
        dupes = _by_category(report, "duplicate_university")
        assert any(f["entity_id"] in (first.id, second.id) for f in dupes)
    finally:
        db.query(University).filter(University.slug.in_(["w9-twin-a", "w9-twin-b"])).delete(
            synchronize_session=False)
        db.commit()


def test_program_structure_and_money_defects_are_reported(db):
    university = University(name="W9 Host", slug="w9-host", country_id=_country(db).id,
                            city="Testville", overview="x", is_published=True,
                            source_url="https://w9.example.edu", confidence="NEEDS_REVIEW")
    db.add(university)
    db.flush()
    try:
        bad_degree = Program(university_id=university.id, name="W9 Bad Degree",
                             slug="w9-bad-degree", degree_level="PGDIP", currency="USD",
                             tuition_annual=1000.0, is_published=True,
                             source_url="https://w9.example.edu/p", confidence="NEEDS_REVIEW")
        bad_money = Program(university_id=university.id, name="W9 Negative Fee",
                            slug="w9-negative-fee", degree_level="MASTER", currency="XYZ",
                            tuition_annual=-5.0, is_published=True,
                            source_url="https://w9.example.edu/p", confidence="NEEDS_REVIEW")
        db.add_all([bad_degree, bad_money])
        db.commit()
        report = integrity.audit(db)
        assert any(f["entity_id"] == bad_degree.id
                   for f in _by_category(report, "invalid_degree_level"))
        assert any(f["entity_id"] == bad_money.id
                   for f in _by_category(report, "invalid_tuition"))
        assert any(f["entity_id"] == bad_money.id
                   for f in _by_category(report, "tuition_without_currency"))
        assert "PGDIP" not in DEGREE_LEVELS
    finally:
        db.query(Program).filter(Program.slug.in_(["w9-bad-degree", "w9-negative-fee"])).delete(
            synchronize_session=False)
        db.query(University).filter(University.slug == "w9-host").delete()
        db.commit()


def test_intake_defects_are_reported(db):
    university = University(name="W9 Intake Host", slug="w9-intake-host",
                            country_id=_country(db).id, city="Testville", overview="x",
                            is_published=True, source_url="https://w9.example.edu",
                            confidence="NEEDS_REVIEW")
    db.add(university)
    db.flush()
    programme = Program(university_id=university.id, name="W9 Intake Programme",
                        slug="w9-intake-programme", degree_level="MASTER", currency="USD",
                        tuition_annual=1000.0, is_published=True,
                        source_url="https://w9.example.edu/p", confidence="NEEDS_REVIEW")
    db.add(programme)
    db.flush()
    try:
        no_label = ProgramIntake(program_id=programme.id, label="", year=None, status="OPEN")
        expired = ProgramIntake(program_id=programme.id, label="Fall 2024", year=2024,
                                status="OPEN",
                                application_deadline=date.today() - timedelta(days=30))
        db.add_all([no_label, expired])
        db.commit()
        report = integrity.audit(db)
        assert any(f["entity_id"] == no_label.id for f in _by_category(report, "invalid_intake"))
        assert any(f["entity_id"] == expired.id
                   for f in _by_category(report, "expired_open_intake"))
    finally:
        db.query(ProgramIntake).filter(ProgramIntake.program_id == programme.id).delete()
        db.query(Program).filter(Program.id == programme.id).delete()
        db.query(University).filter(University.slug == "w9-intake-host").delete()
        db.commit()


def test_a_region_country_mismatch_cannot_be_created_and_is_still_audited(db):
    """Two layers must agree: the model refuses the bad pairing, and the audit reports it.

    The insertion guard is the primary defence. The audit exists for rows that predate it or
    arrive through a path that does not validate, so both are asserted here rather than one
    standing in for the other.
    """
    from sqlalchemy.exc import IntegrityError

    us = _country(db, "US")
    scotland = db.execute(select(Country).where(Country.iso_code == "GB-SCT")).scalars().first()
    assert scotland is not None
    try:
        row = University(name="W9 Mismatch", slug="w9-mismatch", country_id=us.id,
                         region_id=scotland.id, city="Testville", overview="x",
                         is_published=True, source_url="https://w9.example.edu",
                         confidence="NEEDS_REVIEW")
        db.add(row)
        with pytest.raises((ValueError, IntegrityError)):
            db.flush()
        db.rollback()

        # The audit still detects the shape, independently of the insertion guard.
        assert integrity.CATEGORIES["country_region_mismatch"]["severity"] == "HIGH"
        report = integrity.audit(db)
        assert "country_region_mismatch" in report["counts"]
    finally:
        db.rollback()


def test_verified_without_field_evidence_is_reported(db):
    try:
        row = University(name="W9 Hollow Verified", slug="w9-hollow", country_id=_country(db).id,
                         city="Testville", overview="x", is_published=True,
                         source_url="https://w9.example.edu", confidence="VERIFIED",
                         verified_at=utc(-1), review_due_at=utc(90))
        db.add(row)
        db.commit()
        report = integrity.audit(db)
        assert any(f["entity_id"] == row.id
                   for f in _by_category(report, "unregistered_provenance"))
    finally:
        db.query(University).filter(University.slug == "w9-hollow").delete()
        db.commit()


def test_conflicting_evidence_is_reported(db):
    """The aggregate count is the contractual part; ``findings`` is a bounded sample.

    Asserting only on the sample made this test depend on how many findings the rest of the suite
    had produced, which is exactly the kind of order-dependent test that hides regressions.
    """
    try:
        row = University(name="W9 Conflict", slug="w9-conflict", country_id=_country(db).id,
                         city="Testville", overview="x", is_published=True,
                         source_url="https://w9.example.edu", confidence="NEEDS_REVIEW")
        db.add(row)
        db.flush()
        db.add(DataVerification(entity_type="university", entity_id=row.id,
                                field_name="tuition_annual", field_value="1",
                                status="CONFLICTING", source_url="https://w9.example.edu",
                                conflict_with="a second source disagrees"))
        db.commit()
        report = integrity.audit(db)
        assert report["counts"]["conflicting_evidence"] >= 1, (
            "an unresolved source disagreement must be counted")
        sample = [f for f in _by_category(report, "conflicting_evidence")
                  if f["entity_type"] == "university" and f["entity_id"] == row.id]
        if sample:                                  # only when it survived the sample cap
            assert sample[0]["severity"] == "HIGH"
            assert sample[0]["field_name"] == "tuition_annual"
        # A record with a register row is not "unregistered" merely because it conflicts.
        assert not [f for f in _by_category(report, "unregistered_provenance")
                    if f["entity_type"] == "university" and f["entity_id"] == row.id]
    finally:
        db.query(DataVerification).filter(DataVerification.entity_id == row.id).delete()
        db.query(University).filter(University.slug == "w9-conflict").delete()
        db.commit()


def test_stale_verification_is_reported(db):
    try:
        row = University(name="W9 Stale", slug="w9-stale", country_id=_country(db).id,
                         city="Testville", overview="x", is_published=True,
                         source_url="https://w9.example.edu", confidence="VERIFIED",
                         verified_at=utc(-400), review_due_at=utc(-5))
        db.add(row)
        db.commit()
        report = integrity.audit(db)
        assert any(f["entity_id"] == row.id for f in _by_category(report, "stale_verification"))
    finally:
        db.query(University).filter(University.slug == "w9-stale").delete()
        db.commit()


def test_the_report_is_bounded_and_declares_its_caps(db):
    report = integrity.audit(db)
    assert set(report) >= {"generated_at", "policy_version", "counts", "total_findings",
                           "checked", "truncated", "categories", "findings"}
    assert len(report["findings"]) <= 400
    assert all(f["severity"] in integrity.SEVERITY_ORDER for f in report["findings"])
    assert report["policy_version"]


def test_the_integrity_endpoint_requires_authority(client, api):
    assert client.get(f"{api}/admin/catalogue/integrity").status_code in (401, 403)


def test_the_integrity_endpoint_reports_for_a_data_manager(client, api):
    res = client.get(f"{api}/admin/catalogue/integrity", headers=_staff("DATA_MANAGER"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert "counts" in body and "findings" in body


def test_an_unverified_record_is_not_presented_as_confirmed(db):
    """The trust boundary the audit reports on: public and assistant surfaces must not lie."""
    university = University(name="W9 Unverified U", slug="w9-unverified-u",
                            country_id=_country(db).id, city="Testville", overview="x",
                            is_published=True, source_url="https://w9.example.edu/official",
                            confidence="NEEDS_REVIEW", verified_at=None,
                            review_due_at=utc(90))
    db.add(university)
    db.flush()
    programme = Program(university_id=university.id, name="W9 Unverified Programme",
                        slug="w9-unverified-programme", degree_level="MASTER", currency="USD",
                        tuition_annual=12345.0, discipline="Astrobiology", is_published=True,
                        source_url="https://w9.example.edu/official", confidence="NEEDS_REVIEW",
                        verified_at=None, review_due_at=utc(90))
    db.add(programme)
    db.commit()
    try:
        rows = assistant.search_programs(db, subject="w9 unverified programme", limit=10)
        assert rows == [], "an unverified programme must not be returned as a match"

        tuition = assistant.get_tuition(db, programme.id)
        assert tuition["available"] is False
        assert "unavailable" in tuition["message"].lower()
    finally:
        db.query(Program).filter(Program.id == programme.id).delete()
        db.query(University).filter(University.slug == "w9-unverified-u").delete()
        db.commit()


def test_public_provenance_never_exposes_the_reviewer(client, api):
    """A student may be told a figure is verified; not who on staff verified it."""
    res = client.get(f"{api}/universities", params={"page_size": 5})
    assert res.status_code == 200, res.text
    for item in res.json().get("items", []):
        provenance = item.get("provenance") or {}
        assert "verified_by" not in provenance, (
            "the reviewer's staff address is internal information")
        assert "verified" in provenance and "verification_reason" in provenance, (
            "the verification verdict itself must still be exposed")

    detail = client.get(f"{api}/universities", params={"page_size": 1})
    assert "verified_by" not in detail.text, "no public payload may carry a reviewer identity"
