"""W6 acceptance tests - retrieval scoping and intent rewrite.

Part 1: the scope contract itself. One interpretation of a question, model-specific adapters
built from the same destination primitives, and the states that must never degrade into "no
filter".
"""
from __future__ import annotations

import pytest
from app.models.education import Country
from app.services import destinations, fx, scoping
from sqlalchemy import select

from task2_helpers import cleanup, new_session


def _country(db, *, iso_code=None, iso2=None):
    stmt = select(Country)
    if iso_code:
        stmt = stmt.where(Country.iso_code == iso_code)
    else:
        stmt = stmt.where(Country.iso2 == iso2)
    row = db.execute(stmt).scalars().first()
    assert row is not None, f"the catalogue must contain {iso_code or iso2}"
    return row


def _ids(db, clause):
    from app.models.education import University

    return set(db.execute(select(University.id).where(clause)).scalars())


# =====================================================================
# destination resolution and the region hierarchy
# =====================================================================
def test_a_country_scope_contains_its_regions_and_a_region_scope_contains_only_itself():
    db = new_session()
    try:
        _country(db, iso2="GB")
        scotland = _country(db, iso_code="GB-SCT")

        uk_scope = scoping.resolve_scope(db, context={"country": "United Kingdom"})
        scotland_scope = scoping.resolve_scope(db, context={"country": "Scotland"})

        assert uk_scope.ok and scotland_scope.ok
        assert uk_scope.country_ids and scotland.id in uk_scope.country_ids, (
            "a country scope is its family, so the UK includes Scotland")
        assert uk_scope.region_ids == ()
        assert scotland_scope.region_ids == (scotland.id,), (
            "a region is served by region, not by its sovereign's country ids")
        assert scotland_scope.country_ids == ()
    finally:
        db.close()


def test_the_scope_adapter_agrees_with_the_destination_helper_it_replaces():
    """No competing hierarchy: for a single destination the ids are exactly ``university_scope``'s."""
    db = new_session()
    try:
        for token in ("United Kingdom", "Scotland", "United States"):
            scope = scoping.resolve_scope(db, context={"country": token})
            assert scope.ok
            ours = _ids(db, scoping.university_clause(db, scope))
            theirs = _ids(db, destinations.university_scope(db, scope.destination))
            assert ours == theirs, f"scope adapter diverged from university_scope for {token!r}"

            assert theirs, f"{token!r} must have institutions for this comparison to mean anything"
    finally:
        db.close()


def test_a_region_request_never_yields_the_rest_of_the_sovereign_country():
    db = new_session()
    try:
        uk_ids = _ids(db, scoping.university_clause(
            db, scoping.resolve_scope(db, context={"country": "United Kingdom"})))
        scotland_ids = _ids(db, scoping.university_clause(
            db, scoping.resolve_scope(db, context={"country": "Scotland"})))
        assert scotland_ids, "Scotland must have institutions in the catalogue"
        assert scotland_ids < uk_ids, "Scotland must be a strict subset of the United Kingdom"
    finally:
        db.close()


def test_an_unknown_destination_is_unsupported_and_never_unrestricted():
    db = new_session()
    try:
        scope = scoping.resolve_scope(db, context={"country": "Atlantis"})
        assert scope.status == scoping.UNSUPPORTED
        assert scope.reasons == (scoping.DESTINATION_UNKNOWN,)
        assert scope.clarify_slot == "country"
        assert not scope.ok
        # The adapter must not answer "everything" for a destination nobody could resolve.
        assert scoping.university_clause(db, scope) is None
        assert scoping.chunk_scope_ids(db, scope) is None
    finally:
        db.close()


def test_an_excluded_destination_cannot_be_reached_through_the_adapter():
    db = new_session()
    try:
        from app.models.education import University

        uk = _country(db, iso2="GB")
        scotland = _country(db, iso_code="GB-SCT")
        scope = scoping.resolve_scope(
            db, context={"country": "United Kingdom", "excluded_countries": ["Scotland"]})

        ids = _ids(db, scoping.university_clause(db, scope))
        uk_all = _ids(db, destinations.university_scope(db, uk))
        scotland_all = _ids(db, destinations.university_scope(db, scotland))

        assert uk_all, "the catalogue must have UK institutions for this test to mean anything"
        assert scotland_all, "and Scottish ones"
        assert ids, "excluding one region must not empty the whole country"
        assert ids == uk_all - scotland_all, (
            "the exclusion removes exactly the excluded region's institutions")
        remaining = list(db.execute(select(University).where(
            scoping.university_clause(db, scope))).scalars())
        assert all(u.region_id != scotland.id for u in remaining)
    finally:
        db.close()


# =====================================================================
# degree, subject, year, budget
# =====================================================================
@pytest.mark.parametrize("raw,expected", [
    ("MASTER", "MASTER"), ("Masters", "MASTER"), ("master's", "MASTER"),
    ("BACHELOR", "BACHELOR"), ("PhD", "PHD"), ("Ph.D.", None),
])
def test_degree_normalisation_accepts_catalogue_levels_only(raw, expected):
    assert scoping.normalize_degree(raw) == expected


def test_an_unsupported_degree_is_rejected_rather_than_dropped():
    db = new_session()
    try:
        scope = scoping.resolve_scope(db, context={"degree_level": "diploma"})
        assert scope.status == scoping.UNSUPPORTED
        assert scope.reasons == (scoping.DEGREE_UNSUPPORTED,)
        assert not scope.ok
    finally:
        db.close()


@pytest.mark.parametrize("text,amount,currency", [
    ("universities under 25 lakh taka", 2_500_000.0, "BDT"),
    ("tuition below $20,000", 20_000.0, "USD"),
    ("something within 15k gbp", 15_000.0, "GBP"),
    ("my budget is 30000", 30_000.0, "USD"),
    ("can afford 2 crore inr", 20_000_000.0, "INR"),
])
def test_a_stated_budget_is_read_with_its_own_currency(text, amount, currency):
    assert scoping.parse_budget(text) == (amount, currency)


@pytest.mark.parametrize("text", [
    "IELTS 6.5 and CGPA 3.5 please",
    "universities in Canada",
    "I have 3 years of work experience",
    "show me master's programs",
])
def test_non_budget_numbers_never_become_a_budget(text):
    assert scoping.parse_budget(text) == (None, None)


def test_a_budget_is_never_compared_across_currencies():
    """20,000 GBP is about 25,300 USD. A raw comparison would exclude a 22,000 USD programme."""
    db = new_session()
    try:
        scope = scoping.resolve_scope(db, context={}, question="universities under 20000 gbp")
        assert scope.budget_amount == 20_000.0 and scope.budget_currency == "GBP"

        converted = fx.convert(20_000.0, "GBP", "USD")
        assert converted is not None and converted > 25_000

        assert scoping.tuition_within_budget(scope, 22_000.0, "USD") is True, (
            "22,000 USD is inside a 20,000 GBP budget once converted")
        assert not (22_000.0 <= scope.budget_amount), (
            "the raw comparison this test exists to prevent would have excluded it")
        assert scoping.tuition_within_budget(scope, 40_000.0, "USD") is False
    finally:
        db.close()


def test_a_currency_without_a_rate_excludes_rather_than_guesses():
    db = new_session()
    try:
        scope = scoping.resolve_scope(db, context={}, question="universities under 20000 usd")
        assert scope.budget_amount == 20_000.0
        # No rate for the programme's currency: the programme cannot be shown to be within
        # budget, so it is excluded. Comparing the numbers anyway would be a false claim.
        assert fx.supported("XYZ") is False
        assert scoping.tuition_within_budget(scope, 100.0, "XYZ") is False
        assert scoping.tuition_within_budget(scope, None, "USD") is False
    finally:
        db.close()


def test_no_budget_means_no_budget_filter():
    db = new_session()
    try:
        scope = scoping.resolve_scope(db, context={}, question="universities in Canada")
        assert scope.budget_amount is None
        assert scoping.tuition_within_budget(scope, 999_999.0, "XYZ") is True
    finally:
        db.close()


def test_an_explicit_year_becomes_a_hard_constraint_on_intakes():
    """An empty year set is a real answer, never "no filter"."""
    db = new_session()
    try:
        from task2_helpers import make_intake, make_program, make_university

        university = make_university(db, slug="w6-year-university")
        stated = make_program(db, university, slug="w6-year-stated")
        unstated = make_program(db, university, slug="w6-year-unstated")
        make_intake(db, stated, label="Fall 2099", year=2099)
        db.commit()
        try:
            matching = scoping.resolve_scope(db, question="masters starting in 2099")
            assert matching.academic_year is not None
            ids = scoping.program_year_ids(db, matching)
            assert ids is not None
            assert stated.id in ids, "a programme stating the year qualifies"
            assert unstated.id not in ids, "missing year data can never satisfy a mandatory year"

            empty = scoping.resolve_scope(db, question="masters starting in 2088")
            assert scoping.program_year_ids(db, empty) == (), (
                "nothing in that year must be an empty set, not None")

            none_requested = scoping.resolve_scope(db, question="masters in the USA")
            assert scoping.program_year_ids(db, none_requested) is None, (
                "no year requested must not invent a year filter")
        finally:
            cleanup(db, unstated, stated, university)
    finally:
        db.close()


# =====================================================================
# provenance and trust
# =====================================================================
def test_the_current_request_wins_over_a_stale_sentence_guess_but_not_the_reverse():
    """A context value (server-owned, already resolved by W5) leads the sentence guess."""
    db = new_session()
    try:
        scope = scoping.resolve_scope(db, context={"country": "Canada"},
                                      question="what about the UK?")
        assert scope.destination is not None
        assert scope.destination.name == "Canada", (
            "the carried constraint is the student's own stated scope")
    finally:
        db.close()


def test_a_question_alone_never_invents_a_dimension_it_did_not_name():
    db = new_session()
    try:
        scope = scoping.resolve_scope(db, context={}, question="show me master's programs")
        assert scope.destination is None
        assert scope.subject is None
        assert scope.budget_amount is None
        assert scope.academic_year is None
        assert scope.ok, "an ordinary broad question is allowed to be broad"
        assert scoping.university_clause(db, scope) is None
    finally:
        db.close()


def test_scope_states_are_distinguishable():
    db = new_session()
    try:
        assert scoping.resolve_scope(db, context={}).status == scoping.RESOLVED
        assert scoping.resolve_scope(db, context={"country": "Nowhere"}).status == scoping.UNSUPPORTED
        assert scoping.RESOLVED != scoping.NEEDS_CLARIFICATION != scoping.UNSUPPORTED
        assert scoping.CONTRADICTORY not in (scoping.RESOLVED, scoping.UNSUPPORTED)
    finally:
        db.close()


# =====================================================================
# subject matching: no substring false positives
# =====================================================================
@pytest.mark.parametrize("subject,hay,expected", [
    ("public health", "Master of Public Health", True),
    ("public health", "MSc Nursing Practice", False),
    ("art", "Earth Science BSc", False),
    ("art", "Master of Arts", True),
    ("computer science", "Computer Sciences MSc", True),
    ("data science", "Data Analytics MSc", False),
    ("", "anything", True),
])
def test_subject_matching_is_word_based(subject, hay, expected):
    assert scoping.subject_matches(subject, hay) is expected


def test_subject_matching_ignores_empty_haystacks():
    assert scoping.subject_matches("physics", None, "") is False
    assert scoping.subject_matches("physics", "Physics") is True


def test_excluding_a_region_also_excludes_institutions_tagged_with_the_region():
    """An institution may carry a region row as its country; the exclusion must still apply."""
    from app.models.education import University

    db = new_session()
    created = None
    try:
        scotland = _country(db, iso_code="GB-SCT")
        created = University(name="W6 Region Tagged", slug="w6-region-tagged",
                             country_id=scotland.id, city="Glasgow",
                             overview="Test-only institution.", confidence="VERIFIED",
                             source_type="TIER_1_OFFICIAL_UNIVERSITY",
                             source_url="https://w6-region-tagged.example.edu",
                             verified_at=None, review_due_at=None, verified_by="W6")
        db.add(created)
        db.commit()

        scope = scoping.resolve_scope(
            db, context={"country": "United Kingdom", "excluded_countries": ["Scotland"]})
        ids = _ids(db, scoping.university_clause(db, scope))
        assert created.id not in ids
        assert scoping.university_in_scope(
            db, scope,
            db.execute(select(University).where(University.id == created.id)).scalars().first()
        ) is False
    finally:
        cleanup(db, created)
        db.close()
