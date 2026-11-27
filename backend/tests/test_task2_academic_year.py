"""W10 academic-year integration acceptance tests.

The policy already knew how to check a year; these tests prove the year is extracted from the
student's words, normalised, carried through retrieval and the assistant tools, and actually
filters records that belong to a different intake. No year in the question must mean no
constraint, not a guessed one.
"""
from __future__ import annotations

import pytest
from app.core import academic_year as ay
from app.core import verification_policy as policy
from app.models.crm import KnowledgeChunk

from task2_helpers import cleanup, make_intake, make_program, make_university, new_session


@pytest.mark.parametrize("text,years", [
    ("What is the tuition for the 2026 intake?", {2026}),
    ("Requirements for Fall 2026", {2026}),
    ("Is September 2026 entry still open?", {2026}),
    ("Fees for the academic year 2026/27", {2026, 2027}),
    ("What about 2026-2027 scholarships?", {2026, 2027}),
    ("Living costs for 2026", {2026}),
    ("2026 academic year deadlines", {2026}),
    ("Either 2026 or 2027 works", {2026, 2027}),
    ("Apply by 2027", {2027}),
])
def test_supported_year_expressions_are_normalised(text, years):
    parsed = ay.parse(text)
    assert parsed is not None
    assert set(parsed.years) == years


@pytest.mark.parametrize("text", [
    "IELTS 6.5 and a 3.2 CGPA",
    "What is the tuition in Canada?",
    "Tell me about scholarships",
    "",
])
def test_questions_without_a_year_are_not_given_one(text):
    assert ay.parse(text) is None


def test_a_year_range_matches_both_intakes_but_not_a_third_year():
    record = _valid_record(academic_year="2026/27")
    assert policy.evaluate(record, now=ay_datetime(), academic_year="Fall 2026").usable
    assert policy.evaluate(record, now=ay_datetime(), academic_year="Spring 2027").usable
    late = policy.evaluate(record, now=ay_datetime(), academic_year="2028")
    assert late.usable is False
    # The year check is evaluated after the provenance checks; make the record otherwise valid.
    assert late.reason in (policy.REASON_YEAR_MISMATCH, policy.REASON_NOT_VERIFIED)


def ay_datetime():
    from datetime import datetime, timezone

    return datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def _valid_record(**overrides):
    from datetime import timedelta

    now = ay_datetime()
    row = {
        "confidence": "VERIFIED",
        "verified_at": now - timedelta(days=10),
        "source_url": "https://www.university.edu/fees",
        "source_type": "TIER_1_OFFICIAL_UNIVERSITY",
        "review_due_at": now + timedelta(days=100),
        "verified_by": "Acceptance Test Verifier",
        "academic_year": "2026/27",
    }
    row.update(overrides)
    return row


def test_matching_and_mismatched_academic_years_are_enforced_centrally():
    now = ay_datetime()
    assert policy.evaluate(_valid_record(), now=now, academic_year="2026/27").usable
    mismatch = policy.evaluate(_valid_record(), now=now, academic_year="2028/29")
    assert mismatch.usable is False
    assert mismatch.reason == policy.REASON_YEAR_MISMATCH
    unknown = policy.evaluate(_valid_record(academic_year=None), now=now,
                              academic_year="2026/27")
    assert unknown.usable is False
    assert unknown.reason == policy.REASON_YEAR_UNKNOWN


def test_intake_year_field_and_label_both_count():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    intake = {"confidence": "VERIFIED", "verified_at": now, "source_url": "https://x.edu",
              "source_type": "TIER_1_OFFICIAL_UNIVERSITY", "review_due_at": None,
              "verified_by": "A Verifier", "year": 2027, "label": "Spring 2027"}
    assert policy.evaluate(intake, now=now, academic_year="Spring 2027").usable
    assert policy.evaluate(intake, now=now, academic_year="Fall 2026").usable is False


def test_programme_year_comes_from_its_intakes(db):
    own = new_session()
    university = programme = None
    try:
        university = make_university(own, slug="year-policy-uni")
        programme = make_program(own, university, slug="year-policy-programme")
        make_intake(own, programme, label="Fall 2026", year=2026)
        make_intake(own, programme, label="Spring 2027", year=2027)
        own.commit()

        assert policy.evaluate_record(own, programme, academic_year=ay.parse("2026")).usable
        assert policy.evaluate_record(own, programme, academic_year=ay.parse("2027")).usable
        mismatch = policy.evaluate_record(own, programme, academic_year=ay.parse("2028"))
        assert mismatch.usable is False
        assert mismatch.reason == policy.REASON_YEAR_MISMATCH
    finally:
        cleanup(own, programme, university)
        own.close()


def test_tools_filter_records_and_intakes_by_the_requested_year(db):
    from app.services import assistant

    own = new_session()
    university = programme = no_year = None
    try:
        university = make_university(own, slug="year-tools-uni")
        programme = make_program(own, university, slug="year-tools-programme",
                                 name="MSc Computer Science Year Tools Probe")
        make_intake(own, programme, label="Fall 2026", year=2026)
        make_intake(own, programme, label="Spring 2027", year=2027)
        no_year = make_program(own, university, slug="year-tools-no-year",
                               name="MSc Computer Science No Year Probe")
        own.commit()

        rows_2026 = assistant.search_programs(
            own, subject="Year Tools Probe", academic_year=ay.parse("2026"))
        by_slug = {r["slug"]: r for r in rows_2026}
        assert "year-tools-programme" in by_slug
        assert "year-tools-no-year" not in by_slug, "a programme with no year cannot satisfy a year query"
        assert by_slug["year-tools-programme"]["intakes"] == ["Fall 2026"]

        rows_2028 = assistant.search_programs(
            own, subject="Year Tools Probe", academic_year=ay.parse("2028"))
        assert "year-tools-programme" not in {r["slug"] for r in rows_2028}

        intakes = assistant.get_intakes(own, programme.id, academic_year=ay.parse("2026"))
        assert [i["label"] for i in intakes] == ["Fall 2026"]
        assert assistant.get_intakes(own, programme.id,
                                     academic_year=ay.parse("2028")) == []
    finally:
        cleanup(own, no_year, programme, university)
        own.close()


def test_retrieval_respects_the_requested_year(db):
    from app.services import retrieval

    own = new_session()
    university = programme = None
    try:
        university = make_university(own, slug="year-retrieval-uni")
        programme = make_program(own, university, slug="year-retrieval-programme",
                                 name="MSc Computer Science Retrieval Year Probe")
        make_intake(own, programme, label="Fall 2026", year=2026)
        own.commit()
        retrieval.rebuild_index(own)

        query = "Computer Science Retrieval Year Probe"
        assert retrieval.search(own, query, academic_year=ay.parse("2026"))
        assert retrieval.search(own, query, academic_year=ay.parse("2028")) == []
        assert retrieval.search(own, query), "no year in the query means no year constraint"
    finally:
        cleanup(own, programme, university)
        own.query(KnowledgeChunk).filter(
            KnowledgeChunk.slug.like("year-retrieval-%")).delete(synchronize_session=False)
        own.commit()
        own.close()


def test_the_assistant_extracts_the_year_and_reports_it(client, api, db):
    from app.services import retrieval

    own = new_session()
    university = programme = None
    try:
        university = make_university(own, slug="year-assistant-uni")
        programme = make_program(own, university, slug="year-assistant-programme",
                                 name="MSc Computer Science Assistant Year Probe")
        make_intake(own, programme, label="Fall 2026", year=2026)
        own.commit()
        retrieval.rebuild_index(own)

        body = client.post(f"{api}/assistant/ask", json={
            "question": "Is there a Master's in computer science for the 2026 intake in the United States?",
            "session_id": "task2-academic-year-match",
        }).json()
        assert body["domain"]["decision"] == "ALLOW"
        assert body["academic_year"] == {"label": "2026", "years": [2026]}
        assert body["policy"]["academic_year"] == "2026"
        assert "year-assistant-programme" in {
            p["slug"] for p in body["data"].get("programs", [])}
        assert body["data"]["programs"][0]["intakes"] == ["Fall 2026"]

        other = client.post(f"{api}/assistant/ask", json={
            "question": "Is there a Master's in computer science for the 2028 intake in the United States?",
            "session_id": "task2-academic-year-mismatch",
        }).json()
        assert other["academic_year"] == {"label": "2028", "years": [2028]}
        assert "year-assistant-programme" not in {
            p["slug"] for p in other["data"].get("programs", [])}

        none = client.post(f"{api}/assistant/ask", json={
            "question": "Is there a Master's in computer science in the United States?",
            "session_id": "task2-academic-year-absent",
        }).json()
        assert none["academic_year"] is None
        assert none["policy"]["academic_year"] is None
    finally:
        cleanup(own, programme, university)
        own.query(KnowledgeChunk).filter(
            KnowledgeChunk.slug.like("year-assistant-%")).delete(synchronize_session=False)
        own.commit()
        own.close()


def test_an_ambiguous_expression_is_treated_as_a_set_not_a_guess():
    parsed = ay.parse("2026 or 2027 intake")
    assert parsed is not None
    assert set(parsed.years) == {2026, 2027}
    assert parsed.matches([2027])
    assert not parsed.matches([2025])


def test_two_different_academic_year_ranges_do_not_satisfy_each_other():
    """Regression: 2026/27 and 2027/28 both contain calendar 2027 but are different years."""
    record = _valid_record(academic_year="2026/27")
    now = ay_datetime()

    adjacent = policy.evaluate(record, now=now, academic_year="2027/28")
    assert adjacent.usable is False
    assert adjacent.reason == policy.REASON_YEAR_MISMATCH

    assert policy.evaluate(record, now=now, academic_year="2026/27").usable
    # A plain intake year still matches the range that contains it.
    assert policy.evaluate(record, now=now, academic_year="Spring 2027").usable
    assert ay.same_academic_year(ay.parse("2026/27"), {"academic_year": "2026/27"})
    assert not ay.same_academic_year(ay.parse("2027/28"), {"academic_year": "2026/27"})
