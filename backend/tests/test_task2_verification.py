"""W10 acceptance tests - one read-time verification policy, enforced everywhere.

The policy is tested directly with fixed timestamps (so the result never depends on when the
suite runs), then through the three paths a student can actually see: retrieval, the assistant's
structured tools and the public serializers. The demonstration corpus is deliberately
unverified, so it also proves the site does not dress demo data up as fact.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from app.core import verification_policy as policy
from app.core.config import settings
from app.models.crm import KnowledgeChunk

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def record(**overrides):
    base = {
        "confidence": "VERIFIED",
        "verified_at": NOW - timedelta(days=30),
        "source_url": "https://www.university.edu/admissions/fees",
        "source_type": "TIER_1_OFFICIAL_UNIVERSITY",
        "review_due_at": NOW + timedelta(days=60),
        "verified_by": "Rumana Sultana",
        "provenance": None,
    }
    base.update(overrides)
    return base


def test_a_verified_official_current_record_is_usable():
    decision = policy.evaluate(record(), now=NOW)
    assert decision.usable is True
    assert decision.reason == policy.REASON_OK
    assert decision.policy_version == policy.POLICY_VERSION


def test_unverified_record_is_never_usable():
    decision = policy.evaluate(record(confidence="UNVERIFIED"), now=NOW)
    assert decision.usable is False
    assert decision.reason == policy.REASON_NOT_VERIFIED


def test_a_missing_verification_date_blocks_the_record():
    decision = policy.evaluate(record(verified_at=None), now=NOW)
    assert decision.usable is False
    assert decision.reason == policy.REASON_NO_VERIFICATION_DATE


def test_a_missing_official_source_blocks_the_record():
    assert policy.evaluate(record(source_url=""), now=NOW).reason == policy.REASON_NO_SOURCE
    assert policy.evaluate(record(source_url=None), now=NOW).reason == policy.REASON_NO_SOURCE


def test_the_review_deadline_is_enforced():
    decision = policy.evaluate(record(review_due_at=NOW - timedelta(seconds=1)), now=NOW)
    assert decision.usable is False
    assert decision.reason == policy.REASON_REVIEW_OVERDUE


def test_maximum_verification_age_is_enforced(monkeypatch):
    monkeypatch.setattr(settings, "policy_max_age_days", 365, raising=False)

    at_limit = policy.evaluate(record(verified_at=NOW - timedelta(days=365),
                                      review_due_at=NOW + timedelta(days=10)), now=NOW)
    assert at_limit.usable is True, "a record exactly at the limit is still inside the window"

    just_over = policy.evaluate(record(verified_at=NOW - timedelta(days=366),
                                       review_due_at=NOW + timedelta(days=10)), now=NOW)
    assert just_over.usable is False
    assert just_over.reason == policy.REASON_STALE
    assert just_over.age_days == 366


def test_policy_max_age_days_is_actually_applied(monkeypatch):
    """A different configured window must change the verdict, not just sit in settings."""
    row = record(verified_at=NOW - timedelta(days=200), review_due_at=NOW + timedelta(days=10))
    monkeypatch.setattr(settings, "policy_max_age_days", 365, raising=False)
    assert policy.evaluate(row, now=NOW).usable is True
    monkeypatch.setattr(settings, "policy_max_age_days", 90, raising=False)
    assert policy.evaluate(row, now=NOW).reason == policy.REASON_STALE


def test_conflicting_records_are_withheld():
    from_confidence = policy.evaluate(record(confidence="CONFLICTING"), now=NOW)
    assert from_confidence.usable is False
    assert from_confidence.reason == policy.REASON_CONFLICTING

    external = policy.evaluate(record(), now=NOW, has_conflict=True)
    assert external.usable is False
    assert external.reason == policy.REASON_CONFLICTING


def test_demo_provenance_is_never_usable_even_if_marked_verified():
    from_provenance = policy.evaluate(record(provenance="DEMO"), now=NOW)
    assert from_provenance.usable is False
    assert from_provenance.reason == policy.REASON_DEMO

    # The seed stamps its rows with a human-readable sample marker rather than a provenance
    # column, so that marker is honoured too.
    sample = policy.evaluate(record(verified_by="Sample dataset - replace with a verified source extract"),
                             now=NOW)
    assert sample.usable is False
    assert sample.reason == policy.REASON_DEMO


@pytest.mark.parametrize("source_type", ["TIER_7_THIRD_PARTY", "OWN_WEBSITE", "OWN_WEBSITE_IMPORT",
                                         "SECONDARY", "TIER_6_INTERNAL_CONSULTANCY",
                                         "OFFICIAL_UNIVERSITY_WEBSITE", "", None])
def test_secondary_sources_alone_can_never_make_a_record_verified(source_type):
    decision = policy.evaluate(record(source_type=source_type), now=NOW)
    assert decision.usable is False
    assert decision.reason == policy.REASON_SECONDARY_ONLY


def test_the_academic_year_must_match_the_question():
    current = record(academic_year="2026/27")
    assert policy.evaluate(current, now=NOW, academic_year="2026/27").usable is True
    mismatch = policy.evaluate(current, now=NOW, academic_year="2027/28")
    assert mismatch.usable is False
    assert mismatch.reason == policy.REASON_YEAR_MISMATCH

    unknown = policy.evaluate(record(), now=NOW, academic_year="2027/28")
    assert unknown.usable is False
    assert unknown.reason == policy.REASON_YEAR_UNKNOWN


def test_a_database_conflict_register_withholds_a_confidence_verified_record():
    """A second source disagreeing is a conflict even when the row still says VERIFIED."""
    from task2_helpers import cleanup, make_conflict, make_program, make_university, new_session

    db = new_session()
    university = programme = conflict = None
    try:
        university = make_university(db, slug="policy-conflict-uni")
        programme = make_program(db, university, slug="policy-conflict-programme")
        conflict = make_conflict(db, entity_type="program", entity_id=programme.id)
        db.commit()

        decision = policy.evaluate_record(db, programme)
        assert decision.usable is False
        assert decision.reason == policy.REASON_CONFLICTING

        from app.services import assistant

        assert assistant.search_programs(db, subject="Test Programme policy-conflict-programme") == []
    finally:
        cleanup(db, conflict, programme, university)
        db.close()


def test_the_public_serializer_reports_verified_unverified_and_conflict_statuses(client, api):
    from task2_helpers import cleanup, make_conflict, make_program, make_university, new_session

    db = new_session()
    university = verified = unverified = conflict = None
    try:
        university = make_university(db, slug="serializer-uni")
        verified = make_program(db, university, slug="serializer-verified")
        unverified = make_program(db, university, slug="serializer-unverified",
                                  confidence="NEEDS_REVIEW", verified_by="Awaiting review")
        db.commit()

        body = client.get(f"{api}/programs/serializer-verified").json()["program"]
        assert body["provenance"]["verified"] is True
        assert body["provenance"]["verification_reason"] == "OK"
        assert body["provenance"]["policy_version"] == policy.POLICY_VERSION

        body = client.get(f"{api}/programs/serializer-unverified").json()["program"]
        assert body["provenance"]["verified"] is False
        assert body["provenance"]["verification_reason"] == policy.REASON_NOT_VERIFIED
        assert body["provenance"]["verification_note"]

        conflict = make_conflict(db, entity_type="program", entity_id=verified.id)
        db.commit()
        body = client.get(f"{api}/programs/serializer-verified").json()["program"]
        assert body["provenance"]["verified"] is False
        assert body["provenance"]["verification_reason"] == policy.REASON_CONFLICTING
    finally:
        cleanup(db, conflict, unverified, verified, university)
        db.close()


def test_an_unverified_requirement_is_never_borrowed_from_a_verified_program(client, api):
    """A green programme badge does not verify the requirement row's numbers."""
    from app.models.education import ProgramRequirement
    from app.services import assistant

    from task2_helpers import cleanup, make_program, make_university, new_session, utc

    db = new_session()
    university = programme = requirement = None
    try:
        university = make_university(db, slug="requirement-uni")
        programme = make_program(db, university, slug="requirement-programme",
                                 name="Requirement Gating Programme")
        requirement = ProgramRequirement(
            program_id=programme.id, min_cgpa=3.91, ielts_overall=7.5,
            confidence="NEEDS_REVIEW", source_type="TIER_1_OFFICIAL_UNIVERSITY",
            source_url="https://requirement.example.edu/admissions",
            verified_at=utc(-1), verified_by="Awaiting review",
        )
        db.add(requirement)
        db.commit()

        rows = assistant.search_programs(db, subject="Requirement Gating Programme")
        assert rows, "the verified programme itself is still listed"
        row = rows[0]
        assert row["verified"] is True
        assert row["requirement_verified"] is False
        assert row["min_cgpa"] is None and row["ielts_overall"] is None

        body = client.get(f"{api}/programs/requirement-programme").json()["program"]
        assert body["requirement_summary"] == {}
        assert body["requirements"]["provenance"]["verified"] is False
        assert body["requirements"]["provenance"]["verification_reason"] == policy.REASON_NOT_VERIFIED
    finally:
        # The programme delete cascades its requirement; deleting both would double-delete.
        cleanup(db, programme, university)
        db.close()


def test_demo_chunks_never_surface_through_student_retrieval(db):
    """No passage built from demonstration data may be returned as verified evidence."""
    from app.services import retrieval
    from sqlalchemy import select

    retrieval.rebuild_index(db)
    assert db.query(KnowledgeChunk).count() > 0
    demo_titles = {c.title for c in db.scalars(
        select(KnowledgeChunk).where(KnowledgeChunk.provenance == "DEMO"))}
    assert demo_titles, "the seeded corpus is demonstration data and must be labelled as such"

    query = "artificial intelligence masters in the united states"
    hits = retrieval.search(db, query, limit=5)
    assert not (demo_titles & {hit["title"] for hit in hits}), \
        "demonstration passages must never be presented as verified records"
    assert all(hit["verified"] is True for hit in hits)

    # The admin curation view still sees them; that is how a verifier finds work to do.
    assert retrieval.search(db, query, limit=5, apply_policy=False)


def test_a_verified_record_reaches_retrieval_and_an_unverified_one_does_not(db):
    from app.services import retrieval

    from task2_helpers import cleanup, make_program, make_university, new_session

    own = new_session()
    university = verified = unverified = None
    try:
        university = make_university(own, slug="retrieval-uni")
        verified = make_program(own, university, slug="retrieval-verified",
                                name="Verified Acceptance Programme")
        unverified = make_program(own, university, slug="retrieval-unverified",
                                  name="Unverified Acceptance Programme", confidence="NEEDS_REVIEW",
                                  verified_by="Awaiting review")
        own.commit()

        retrieval.rebuild_index(own)
        hits = retrieval.search(own, "Verified Acceptance Programme", limit=10)
        assert hits, "a verified record must be retrievable"
        assert all(hit["verified"] is True for hit in hits)
        assert all("Unverified Acceptance Programme" != hit["title"] for hit in hits)
    finally:
        cleanup(own, unverified, verified, university)
        own.query(KnowledgeChunk).filter(KnowledgeChunk.slug.like("retrieval-%")).delete(
            synchronize_session=False)
        own.commit()
        own.close()


def test_an_unverified_record_never_appears_as_verified_in_an_assistant_answer(client, api, db):
    """End-to-end: a deliberately unverified programme must not reach the answer, data or sources."""
    from app.services import retrieval

    from task2_helpers import cleanup, make_program, make_university, new_session

    own = new_session()
    university = programme = None
    try:
        university = make_university(own, slug="assistant-unverified-uni")
        programme = make_program(own, university, slug="assistant-unverified-programme",
                                 name="Unverified Acceptance Programme",
                                 confidence="NEEDS_REVIEW", verified_by="Awaiting review",
                                 source_url="https://assistant-unverified.example.edu/programme")
        own.commit()
        retrieval.rebuild_index(own)

        res = client.post(f"{api}/assistant/ask", json={
            "question": "Is there a Master's in Unverified Acceptance Programme in the United States?",
            "session_id": "task2-unverified-path",
        })
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["domain"]["decision"] == "ALLOW"

        assert "assistant-unverified-programme" not in \
            str([p.get("slug") for p in body["data"].get("programs", [])])
        assert "https://assistant-unverified.example.edu/programme" not in \
            [s.get("url") for s in body["sources"]]
        assert "Unverified Acceptance Programme" not in body["answer"]
        for source in body["sources"]:
            assert source.get("confidence") == "VERIFIED"
    finally:
        cleanup(own, programme, university)
        own.query(KnowledgeChunk).filter(KnowledgeChunk.slug.like("assistant-unverified-%")).delete(
            synchronize_session=False)
        own.commit()
        own.close()
