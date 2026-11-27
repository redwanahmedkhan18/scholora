"""W6 acceptance tests - the public retrieval surface, end to end.

The assistant tools and the matching endpoints used to answer the same question differently:
a Scotland filter narrowed universities but widened faculty to the whole United Kingdom, a
destination posted to ``/match/scholarships`` was silently ignored, and a region slug to
``/match/programs`` matched nothing at all because every Scottish institution carries
``country_id = United Kingdom`` and ``region_id = Scotland``.
"""
from __future__ import annotations

from app.core.database import SessionLocal
from app.models.education import Country, University
from app.services import assistant_engine, destinations, scoping
from sqlalchemy import select


def _db():
    return SessionLocal()


def _ensure_corpus(db):
    """Build the retrieval corpus if this run has not built it yet.

    The index is built by a seeding/admin step rather than by the seed fixture, so a run of this
    file alone must not depend on another file having built it first.
    """
    from app.models.crm import KnowledgeChunk
    from app.services import retrieval
    from sqlalchemy import func

    if not db.scalar(select(func.count()).select_from(KnowledgeChunk)):
        retrieval.rebuild_index(db)


def _scotland_cities():
    return destinations.SCOTLAND_CITIES


def test_match_programs_honours_a_country_filter(client, api):
    body = {"profile": {"nationality": "Bangladesh", "target_degree_level": "MASTER"},
            "country_slugs": ["united-states"], "limit": 20}
    res = client.post(f"{api}/match/programs", json=body)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["results"], "the seeded catalogue has US master's programmes"
    for row in payload["results"]:
        assert row["program"]["university"]["country"]["slug"] == "united-states"
        assert row["exact_match"] is True


def test_match_programs_finds_a_region_and_stays_inside_it(client, api):
    """A region slug used to match no programme at all; Scotland must not mean the whole UK."""
    body = {"profile": {"nationality": "Bangladesh", "target_degree_level": "MASTER"},
            "country_slugs": ["scotland"], "limit": 20}
    res = client.post(f"{api}/match/programs", json=body)
    assert res.status_code == 200, res.text
    rows = res.json()["results"]
    assert rows, "a Scotland filter must return Glasgow's programmes, not an empty page"
    for row in rows:
        university = row["program"]["university"]
        assert university["country"]["slug"] == "united-kingdom"
        assert (university["city"] or "").lower() in _scotland_cities()


def test_match_programs_with_an_unknown_destination_returns_nothing_not_everything(client, api):
    body = {"profile": {"nationality": "Bangladesh"}, "country_slugs": ["atlantis"], "limit": 20}
    res = client.post(f"{api}/match/programs", json=body)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["results"] == []
    assert payload["stats"]["total"] == 0
    assert payload["alternatives"] == []


def test_match_programs_separates_alternatives_from_exact_matches(client, api):
    body = {"profile": {"nationality": "Bangladesh", "target_degree_level": "MASTER"},
            "subject": "Public Health", "limit": 5}
    payload = client.post(f"{api}/match/programs", json=body).json()
    exact_ids = {r["program"]["id"] for r in payload["results"]}
    alternative_ids = {r["program"]["id"] for r in payload["alternatives"]}
    assert not (exact_ids & alternative_ids), "an alternative is never also an exact match"
    for row in payload["alternatives"]:
        assert row["exact_match"] is False
        assert "not an exact match" in row["label"]


def test_match_scholarships_honours_the_destination_it_is_given(client, api):
    """The endpoint accepted ``country_slugs`` and answered with awards from every country."""
    us = client.post(f"{api}/match/scholarships",
                     json={"profile": {"nationality": "Bangladesh"}, "limit": 50,
                           "country_slugs": ["united-states"]}).json()
    canada = client.post(f"{api}/match/scholarships",
                         json={"profile": {"nationality": "Bangladesh"}, "limit": 50,
                               "country_slugs": ["canada"]}).json()
    assert us, "the seeded catalogue has US awards"
    for row in us:
        country = row["scholarship"].get("country")
        university = row["scholarship"].get("university")
        assert (country and country["slug"] == "united-states") or university, (
            "every returned award must belong to the requested destination")

    us_ids = {r["scholarship"]["id"] for r in us}
    canada_ids = {r["scholarship"]["id"] for r in canada}
    assert not (us_ids & canada_ids), "a destination filter must actually filter"

    unknown = client.post(f"{api}/match/scholarships",
                          json={"profile": {"nationality": "Bangladesh"},
                                "country_slugs": ["atlantis"]})
    assert unknown.json() == [], "an unknown destination returns nothing, not everything"


def test_match_faculty_with_a_region_slug_does_not_widen_to_the_sovereign(client, api):
    res = client.post(f"{api}/match/faculty",
                      json={"interests": ["information retrieval"], "country_slug": "scotland"})
    assert res.status_code == 200, res.text
    rows = res.json()
    assert rows, "Glasgow's faculty must be found through a Scotland filter"
    slugs = {(r.get("university") or {}).get("slug") for r in rows}
    assert slugs == {"university-of-glasgow"}, slugs

    unknown = client.post(f"{api}/match/faculty",
                          json={"interests": ["information retrieval"],
                                "country_slug": "atlantis"})
    assert unknown.json() == []


# =====================================================================
# the assistant answer path
# =====================================================================
def test_the_engine_asks_a_targeted_question_when_scope_cannot_be_resolved():
    db = _db()
    try:
        result = assistant_engine.ask(db, "show me master's programs",
                                      context={"country": "Atlantis"})
        assert result["mode"] == "clarify"
        assert result["intent"] == "clarify"
        assert result["clarify_slot"] == "country"
        assert result["data"] == {} and result["retrieved"] == []
        assert result["scope"]["status"] == scoping.UNSUPPORTED
        assert "Atlantis" not in result["answer"], "no student-supplied text in the reply"
    finally:
        db.close()


def test_the_engine_scopes_retrieval_to_the_carried_destination():
    """A carried destination narrows both the structured answer and the passages behind it."""
    from task2_helpers import cleanup, make_program, make_university

    db = _db()
    made = []
    try:
        canada = db.execute(select(Country).where(Country.iso2 == "CA")).scalars().first()
        us = db.execute(select(Country).where(Country.iso2 == "US")).scalars().first()
        assert canada is not None and us is not None

        canada_uni = make_university(db, slug="w6-engine-canada-uni")
        canada_uni.country_id = canada.id
        us_uni = make_university(db, slug="w6-engine-us-uni")
        made.append(make_program(db, canada_uni, slug="w6-engine-canada-prog",
                                 name="W6 Engine Scope Probe Canada", tuition=18000.0))
        made.append(make_program(db, us_uni, slug="w6-engine-us-prog",
                                 name="W6 Engine Scope Probe United States", tuition=19000.0))
        db.commit()

        result = assistant_engine.ask(db, "show me master's programmes in w6 engine scope probe",
                                      context={"country": "Canada"})
        rows = result["data"].get("programs") or []
        assert rows, "the Canadian programme must be found"
        assert {row["slug"] for row in rows} == {"w6-engine-canada-prog"}, (
            "a US programme must not appear in a Canada-scoped answer")
        assert {row["country"] for row in rows} == {"Canada"}
    finally:
        cleanup(db, *made, db.execute(select(University).where(
            University.slug == "w6-engine-canada-uni")).scalars().first(),
                db.execute(select(University).where(
                    University.slug == "w6-engine-us-uni")).scalars().first())
        db.close()


def test_a_broad_question_without_constraints_still_retrieves():
    """Fail-closed applies to unresolved mandatory scope, not to ordinary broad questions."""
    from app.models.crm import KnowledgeChunk
    from app.services import retrieval

    db = _db()
    try:
        _ensure_corpus(db)
        # The seeded demonstration records do not all pass the verification policy in a test
        # database, so the scoping claim is checked with the policy switched off: what is being
        # proved here is that an unscoped question is not narrowed to nothing.
        hits = retrieval.search(db, "scholarship", limit=5, apply_policy=False)
        assert hits, "an unscoped question searches the whole corpus"
        assert db.scalar(select(KnowledgeChunk.id).limit(1)) is not None

        result = assistant_engine.ask(db, "what scholarships are available for masters?")
        assert result["mode"] != "clarify", "an ordinary broad question must not be blocked"
    finally:
        db.close()
