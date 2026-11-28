"""W6 acceptance tests - retrieval scoping, part 2.

Every surface that returns records must apply the same scope, in the query, before ranking and
before truncation. These tests build deliberately awkward catalogues (many out-of-scope rows,
a handful of in-scope ones, limits smaller than the noise) so an implementation that filters
*after* fetching cannot pass.
"""
from __future__ import annotations

from app.models.crm import KnowledgeChunk
from app.models.education import (
    Country,
    Professor,
    Program,
    Scholarship,
    University,
)
from app.services import assistant, matching, retrieval, scoping
from sqlalchemy import event, select

from task2_helpers import cleanup, new_session, utc


def _country(db, *, iso_code=None, iso2=None):
    stmt = select(Country)
    stmt = stmt.where(Country.iso_code == iso_code) if iso_code else stmt.where(Country.iso2 == iso2)
    row = db.execute(stmt).scalars().first()
    assert row is not None
    return row


def _university(db, *, slug, country, region=None, name=None):
    row = University(
        name=name or f"W6 {slug}", slug=slug, country_id=country.id,
        region_id=region.id if region is not None else None, city="Testville",
        overview="Test-only institution created by the W6 acceptance suite.",
        confidence="VERIFIED", source_type="TIER_1_OFFICIAL_UNIVERSITY",
        source_url=f"https://{slug}.example.edu/official",
        verified_at=utc(-1), review_due_at=utc(90), verified_by="W6 Acceptance Verifier",
    )
    db.add(row)
    db.flush()
    return row


def _program(db, university, *, slug, name=None, degree="MASTER", discipline="Computer Science",
             tuition=20000.0, currency="USD"):
    row = Program(
        university_id=university.id, name=name or f"W6 Programme {slug}", slug=slug,
        degree_level=degree, award="MSc", discipline=discipline, currency=currency,
        tuition_annual=tuition, overview="Test-only programme created by the W6 suite.",
        is_published=True, confidence="VERIFIED", source_type="TIER_1_OFFICIAL_UNIVERSITY",
        source_url=f"https://example.edu/{slug}", verified_at=utc(-1), review_due_at=utc(90),
        verified_by="W6 Acceptance Verifier",
    )
    db.add(row)
    db.flush()
    return row


def _scholarship(db, *, slug, country=None, university=None, degree_levels=None, disciplines=None):
    row = Scholarship(
        name=f"W6 Award {slug}", slug=slug, provider="W6 Test Provider",
        country_id=country.id if country is not None else None,
        university_id=university.id if university is not None else None,
        degree_levels=degree_levels if degree_levels is not None else [],
        disciplines=disciplines if disciplines is not None else [],
        amount_type="PERCENTAGE", amount_value=25.0, currency="USD",
        eligibility_summary="Test-only award.", is_published=True, confidence="VERIFIED",
        source_type="TIER_1_OFFICIAL_UNIVERSITY", source_url=f"https://example.edu/a/{slug}",
        verified_at=utc(-1), review_due_at=utc(90), verified_by="W6 Acceptance Verifier",
    )
    db.add(row)
    db.flush()
    return row


def _professor(db, university, *, slug, interests):
    row = Professor(
        university_id=university.id, full_name=f"Prof {slug}", slug=slug,
        title="Professor", research_interests=list(interests), research_keywords=list(interests),
        confidence="VERIFIED", source_type="TIER_1_OFFICIAL_UNIVERSITY",
        source_url=f"https://example.edu/p/{slug}", verified_at=utc(-1), review_due_at=utc(90),
        verified_by="W6 Acceptance Verifier",
    )
    db.add(row)
    db.flush()
    return row


# =====================================================================
# assistant tools: predicates before ranking and limits
# =====================================================================
def test_programmes_are_filtered_in_the_query_not_after_the_limit():
    """30 out-of-scope programmes are inserted first, with the lowest ids and the lowest limit.

    If the destination filter ran after a ``limit * 4`` fetch - the shape this workstream
    replaced - the two in-scope programmes would never be seen.
    """
    db = new_session()
    made = []
    try:
        canada = _country(db, iso2="CA")
        us = _country(db, iso2="US")
        noise_uni = _university(db, slug="w6-noise-uni", country=us)
        canada_uni = _university(db, slug="w6-canada-uni", country=canada)
        for i in range(30):
            made.append(_program(db, noise_uni, slug=f"w6-noise-{i}"))
        wanted = [_program(db, canada_uni, slug=f"w6-canada-{i}") for i in range(2)]
        db.commit()

        scope = scoping.resolve_scope(db, context={"country": "Canada"})
        rows = assistant.search_programs(db, limit=2, scope=scope)
        assert len(rows) == 2
        assert {r["country"] for r in rows} == {"Canada"}
        assert {r["slug"] for r in rows} == {p.slug for p in wanted}

        # And the same query without the scope sees far more, so the assertion above is not
        # passing simply because the catalogue is tiny.
        wide = assistant.search_programs(db, limit=40)
        assert len(wide) >= 32
    finally:
        cleanup(db, *made, db.execute(select(University).where(
            University.slug == "w6-noise-uni")).scalars().first(),
                db.execute(select(University).where(
                    University.slug == "w6-canada-uni")).scalars().first())
        db.close()


def test_a_degree_constraint_is_applied_in_the_query():
    db = new_session()
    made = []
    try:
        us = _country(db, iso2="US")
        uni = _university(db, slug="w6-degree-uni", country=us)
        for i in range(6):
            made.append(_program(db, uni, slug=f"w6-bachelor-{i}", degree="BACHELOR",
                                 name=f"W6 Degree Probe Bachelor {i}"))
        wanted = _program(db, uni, slug="w6-master-degree", degree="MASTER",
                          name="W6 Degree Probe Master")
        db.commit()

        scope = scoping.resolve_scope(db, context={"degree_level": "Master's"})
        rows = assistant.search_programs(db, limit=5, scope=scope, subject="w6 degree probe")
        assert [r["slug"] for r in rows] == [wanted.slug]
    finally:
        cleanup(db, *made, db.execute(select(University).where(
            University.slug == "w6-degree-uni")).scalars().first())
        db.close()


def test_an_unknown_destination_returns_nothing_from_every_tool():
    db = new_session()
    try:
        scope = scoping.resolve_scope(db, context={"country": "Atlantis"})
        assert scope.ok is False
        assert assistant.search_programs(db, scope=scope) == []
        assert assistant.search_universities(db, scope=scope) == []
        assert assistant.search_scholarships(db, scope=scope) == []
        assert assistant.search_faculty(db, ["anything"], scope=scope) == []
        assert retrieval.search(db, "anything at all", scope=scope) == []
    finally:
        db.close()


def test_scholarship_degree_filter_runs_before_the_limit():
    """The old code fetched ``limit * 4`` rows and only then removed wrong-level awards."""
    db = new_session()
    made = []
    try:
        us = _country(db, iso2="US")
        for i in range(10):
            made.append(_scholarship(db, slug=f"w6-phd-award-{i}", country=us,
                                     degree_levels=["PHD"]))
        wanted = [_scholarship(db, slug=f"w6-master-award-{i}", country=us,
                               degree_levels=["MASTER"] * 1) for i in range(2)]
        db.commit()

        rows = assistant.search_scholarships(db, limit=2, degree_level="MASTER")
        assert [r["name"] for r in rows] == [s.name for s in wanted]
    finally:
        cleanup(db, *made, *[db.execute(select(Scholarship).where(
            Scholarship.slug == f"w6-master-award-{i}")).scalars().first() for i in range(2)])
        db.close()


def test_scholarship_destination_uses_the_same_family_semantics():
    db = new_session()
    made = []
    try:
        uk = _country(db, iso2="GB")
        scotland = _country(db, iso_code="GB-SCT")
        canada = _country(db, iso2="CA")
        glasgow = _university(db, slug="w6-glasgow-uni", country=uk, region=scotland)
        made.append(_scholarship(db, slug="w6-uk-award", country=uk))
        made.append(_scholarship(db, slug="w6-scotland-award", university=glasgow))
        made.append(_scholarship(db, slug="w6-canada-award", country=canada))
        db.commit()

        uk_names = {r["name"] for r in assistant.search_scholarships(
            db, limit=20, scope=scoping.resolve_scope(db, context={"country": "United Kingdom"}))}
        assert "W6 Award w6-uk-award" in uk_names
        assert "W6 Award w6-scotland-award" in uk_names, (
            "an award attached to a Scottish institution is a UK award")

        scotland_names = {r["name"] for r in assistant.search_scholarships(
            db, limit=20, scope=scoping.resolve_scope(db, context={"country": "Scotland"}))}
        assert "W6 Award w6-scotland-award" in scotland_names
        assert "W6 Award w6-uk-award" not in scotland_names, (
            "a Scotland request must not return a UK-wide award")
        assert "W6 Award w6-canada-award" not in scotland_names
    finally:
        cleanup(db, *made, db.execute(select(University).where(
            University.slug == "w6-glasgow-uni")).scalars().first())
        db.close()


def test_faculty_for_a_region_is_not_widened_to_the_sovereign():
    """Scotland used to be resolved through ``sovereign_of`` and returned the whole UK."""
    db = new_session()
    made = []
    try:
        uk = _country(db, iso2="GB")
        scotland = _country(db, iso_code="GB-SCT")
        english = _university(db, slug="w6-english-uni", country=uk)
        scottish = _university(db, slug="w6-scottish-uni", country=uk, region=scotland)
        made.append(_professor(db, english, slug="w6-english-prof", interests=["astrobiology"]))
        made.append(_professor(db, scottish, slug="w6-scottish-prof", interests=["astrobiology"]))
        db.commit()

        uk_rows = assistant.search_faculty(db, ["astrobiology"], limit=10,
                                          scope=scoping.resolve_scope(db, context={"country": "United Kingdom"}))
        assert {"Prof w6-english-prof", "Prof w6-scottish-prof"} <= {r["name"] for r in uk_rows}

        scotland_rows = assistant.search_faculty(
            db, ["astrobiology"], limit=10,
            scope=scoping.resolve_scope(db, context={"country": "Scotland"}))
        names = {r["name"] for r in scotland_rows}
        assert "Prof w6-scottish-prof" in names
        assert "Prof w6-english-prof" not in names, "a Scotland request must stay inside Scotland"
    finally:
        cleanup(db, *made, db.execute(select(University).where(
            University.slug == "w6-english-uni")).scalars().first(),
                db.execute(select(University).where(
                    University.slug == "w6-scottish-uni")).scalars().first())
        db.close()


def test_the_structured_answer_never_falls_back_for_an_unresolved_scope(monkeypatch):
    from app.services import assistant as tools

    called: list[str] = []
    for name in ("search_programs", "search_universities", "search_scholarships", "search_faculty"):
        real = getattr(tools, name)
        monkeypatch.setattr(tools, name, lambda *a, _n=name, _r=real, **k: (
            called.append(_n), _r(*a, **k))[1])

    db = new_session()
    try:
        result = tools.answer(db, "show me master's programs", {"country": "Atlantis"})
        assert result["data"] == {}
        assert result["grounded"] is False
        assert result["scope"]["status"] == scoping.UNSUPPORTED
        assert called == [], "no retrieval may run for a scope that could not be resolved"
    finally:
        db.close()


# =====================================================================
# RAG: filtered before scoring and before top-k
# =====================================================================
def _reset_chunks(db):
    db.query(KnowledgeChunk).delete()
    db.commit()


def test_rag_drops_out_of_scope_chunks_before_ranking():
    """The out-of-scope passage matches the query better than the in-scope one.

    Without a scope it is the top hit. With a Canada scope it must not appear at all - not
    ranked lower, absent - because the filter runs in the query.
    """
    db = new_session()
    made = []
    try:
        canada = _country(db, iso2="CA")
        us = _country(db, iso2="US")
        canada_uni = _university(db, slug="w6-rag-canada", country=canada,
                                 name="Maple Institute Astronomy")
        us_uni = _university(db, slug="w6-rag-us", country=us,
                             name="Prairie Astronomy Institute")
        made.append(_program(db, canada_uni, slug="w6-rag-canada-prog",
                             name="Astronomy Observation Programme",
                             discipline="Astronomy"))
        made.append(_program(db, us_uni, slug="w6-rag-us-prog",
                             name="Astronomy Observation Astronomy Astronomy",
                             discipline="Astronomy"))
        db.commit()
        _reset_chunks(db)
        retrieval.rebuild_index(db)

        query = "astronomy observation astronomy astronomy"
        unscoped = retrieval.search(db, query, limit=5)
        assert unscoped, "the corpus must contain both passages"
        assert unscoped[0]["entity_type"] == "program"
        assert "w6-rag-us-prog" == unscoped[0].get("slug"), (
            "the out-of-scope passage is the strongest match, which is the point of this test")

        scoped = retrieval.search(db, query, limit=5,
                                  scope=scoping.resolve_scope(db, context={"country": "Canada"}))
        assert scoped, "the in-scope passage must still be retrievable"
        assert all(h["entity_type"] != "program" or h["slug"] != "w6-rag-us-prog"
                   for h in scoped), "an out-of-scope passage must not be ranked at all"
    finally:
        _reset_chunks(db)
        cleanup(db, *made, db.execute(select(University).where(
            University.slug == "w6-rag-canada")).scalars().first(),
                db.execute(select(University).where(
                    University.slug == "w6-rag-us")).scalars().first())
        retrieval.rebuild_index(db)
        db.close()


def test_rag_does_not_treat_missing_entity_metadata_as_globally_applicable():
    db = new_session()
    try:
        _reset_chunks(db)
        term = "orbitalmechanicsprobe"
        db.add(KnowledgeChunk(
            entity_type="mystery", entity_id=999001, slug="w6-rag-mystery",
            title="Orbital mechanics probe", content=f"{term} {term} {term}",
            lexical_vector=retrieval.lexical_vector(f"{term} {term} {term}"),
            embedding=[], token_count=3, confidence="VERIFIED",
            source_type="TIER_1_OFFICIAL_UNIVERSITY",
            source_url="https://example.edu/mystery", verified_at=utc(-1),
        ))
        db.commit()

        assert retrieval.search(db, term, limit=5), "the chunk is retrievable without a scope"
        assert retrieval.search(db, term, limit=5, scope=scoping.resolve_scope(
            db, context={"country": "Canada"})) == [], (
            "an unknown entity type is out of scope, not global")
    finally:
        _reset_chunks(db)
        retrieval.rebuild_index(db)
        db.close()


def test_rag_applies_the_year_constraint_too():
    db = new_session()
    made = []
    try:
        from task2_helpers import make_intake

        us = _country(db, iso2="US")
        uni = _university(db, slug="w6-rag-year-uni", country=us)
        stated = _program(db, uni, slug="w6-rag-year-stated", name="Heliophysics Stated Probe",
                          discipline="Physics")
        made.append(_program(db, uni, slug="w6-rag-year-unstated",
                             name="Heliophysics Unstated Probe", discipline="Physics"))
        make_intake(db, stated, label="Fall 2099", year=2099)
        db.commit()
        _reset_chunks(db)
        retrieval.rebuild_index(db)

        query = "heliophysics probe"
        scope = scoping.resolve_scope(db, context={"country": "United States"},
                                      question="starting in 2099")
        hits = retrieval.search(db, query, limit=10, scope=scope)
        slugs = {h.get("slug") for h in hits if h["entity_type"] == "program"}
        assert "w6-rag-year-stated" in slugs
        assert "w6-rag-year-unstated" not in slugs, (
            "a passage that cannot satisfy the requested year is not in scope")
    finally:
        _reset_chunks(db)
        cleanup(db, *made, db.execute(select(University).where(
            University.slug == "w6-rag-year-uni")).scalars().first())
        retrieval.rebuild_index(db)
        db.close()


# =====================================================================
# matching services
# =====================================================================
def test_subject_is_hard_and_alternatives_are_kept_out_of_the_ranking():
    """A nursing programme is never ranked as a match for a public-health request.

    The destination is pinned to a small country so the alternative bucket is deterministic and
    the test cannot pass by accident on a large pool.
    """
    db = new_session()
    made = []
    try:
        hungary = _country(db, iso_code="HU")
        uni = _university(db, slug="w6-subject-uni", country=hungary)
        wanted = _program(db, uni, slug="w6-public-health", discipline="Public Health",
                          name="Master of Public Health")
        for i in range(2):
            made.append(_program(db, uni, slug=f"w6-nursing-{i}", discipline="Nursing",
                                 name=f"MSc Nursing Practice {i}"))
        db.commit()

        scope = scoping.scope_from_destinations(db, [hungary], degree_level="MASTER")
        student = {"target_discipline": "Public Health", "target_degree_level": "MASTER"}
        result = matching.match_programs(db, student, {}, limit=10, scope=scope)

        slugs = [r["program"].slug for r in result["results"]]
        assert wanted.slug in slugs
        assert not any(s.startswith("w6-nursing-") for s in slugs), (
            "a nursing programme is not an exact match for a public health request")

        alternatives = {p.slug for p in result["alternatives"]}
        assert any(s.startswith("w6-nursing-") for s in alternatives), (
            "near misses are offered separately rather than hidden: " + repr(alternatives))
        assert not (alternatives & set(slugs)), "alternatives never enter the exact-match list"
    finally:
        cleanup(db, *made, db.execute(select(University).where(
            University.slug == "w6-subject-uni")).scalars().first())
        db.close()


def test_matching_a_region_returns_its_institutions_not_nothing_and_not_the_country():
    db = new_session()
    made = []
    try:
        uk = _country(db, iso2="GB")
        scotland = _country(db, iso_code="GB-SCT")
        scottish = _university(db, slug="w6-match-scot-uni", country=uk, region=scotland)
        english = _university(db, slug="w6-match-eng-uni", country=uk)
        made.append(_program(db, scottish, slug="w6-match-scot-prog", name="Scottish Probe MSc",
                             discipline="Data Science"))
        made.append(_program(db, english, slug="w6-match-eng-prog", name="English Probe MSc",
                             discipline="Data Science"))
        db.commit()

        scope = scoping.scope_from_destinations(db, [scotland], degree_level="MASTER")
        result = matching.match_programs(db, {"target_degree_level": "MASTER"}, {},
                                         limit=10, scope=scope)
        slugs = [r["program"].slug for r in result["results"]]
        assert "w6-match-scot-prog" in slugs, "a region filter must find its own institutions"
        assert "w6-match-eng-prog" not in slugs
    finally:
        cleanup(db, *made, db.execute(select(University).where(
            University.slug == "w6-match-scot-uni")).scalars().first(),
                db.execute(select(University).where(
                    University.slug == "w6-match-eng-uni")).scalars().first())
        db.close()


def test_matching_scholarships_honours_a_stated_destination():
    db = new_session()
    made = []
    try:
        canada = _country(db, iso2="CA")
        us = _country(db, iso2="US")
        made.append(_scholarship(db, slug="w6-ms-canada", country=canada))
        made.append(_scholarship(db, slug="w6-ms-us", country=us))
        db.commit()

        scope = scoping.scope_from_destinations(db, [canada])
        rows = matching.match_scholarships(db, {"target_degree_level": "MASTER"},
                                           limit=50, scope=scope)
        slugs = {r["scholarship"].slug for r in rows}
        assert "w6-ms-canada" in slugs
        assert "w6-ms-us" not in slugs
    finally:
        cleanup(db, *made)
        db.close()


# =====================================================================
# evidence that the filter is in the SQL that is actually executed
# =====================================================================
def test_the_destination_filter_is_in_the_executed_sql():
    from app.core.database import engine

    captured: list[str] = []

    def _listen(conn, cursor, statement, parameters, context, executemany):
        captured.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", _listen)
    db = new_session()
    try:
        scope = scoping.resolve_scope(db, context={"country": "Canada"})
        assistant.search_programs(db, limit=2, scope=scope)
    finally:
        event.remove(engine, "before_cursor_execute", _listen)
        db.close()

    selects = [s.lower() for s in captured if s.strip().lower().startswith("select")]
    assert any("from programs" in s and "where" in s for s in selects), captured
    assert any("limit" in s for s in selects), captured
    # A programme query that restricts the destination must restrict it in the database, by
    # joining the institution - not by fetching everything and trimming in Python.
    assert any("from programs join universities" in s and "country_id in" in s for s in selects), (
        "the destination predicate must reach the database: " + repr(captured[-3:]))


def test_a_subject_substring_false_positive_is_not_returned_as_a_match():
    """'art' must not match 'Earth Science'; a substring test does exactly that."""
    db = new_session()
    made = []
    try:
        hungary = _country(db, iso_code="HU")
        uni = _university(db, slug="w6-substr-uni", country=hungary)
        made.append(_program(db, uni, slug="w6-earth", discipline="Earth Science",
                             name="Earth Science BSc"))
        arts = _program(db, uni, slug="w6-arts", discipline="Fine Art",
                        name="Master of Fine Art")
        db.commit()

        scope = scoping.resolve_scope(db, context={"country": "Hungary", "subject": "art"})
        rows = assistant.search_programs(db, limit=10, scope=scope)
        slugs = {r["slug"] for r in rows}
        assert arts.slug in slugs
        assert "w6-earth" not in slugs, "'art' is not a whole word of 'Earth Science'"
    finally:
        cleanup(db, *made, db.execute(select(University).where(
            University.slug == "w6-substr-uni")).scalars().first())
        db.close()


def test_a_tuition_answer_compares_the_budget_in_its_own_currency():
    """get_tuition used to pass no currency, so a BDT budget was read as USD."""
    from app.services import fx

    from task2_helpers import cleanup as t2_cleanup
    from task2_helpers import make_program, make_university

    db = new_session()
    university = programme = None
    try:
        university = make_university(db, slug="w6-tuition-uni")
        programme = make_program(db, university, slug="w6-tuition-prog", tuition=30_000.0)
        db.commit()

        total_usd = assistant.get_tuition(db, programme.id)["estimated_annual_total"]
        assert total_usd > 30_000, "the annual total includes living costs"
        bdt = fx.convert(total_usd, "USD", "BDT")
        assert bdt is not None

        # A budget stated in BDT, comfortably above the converted total.
        within = assistant.get_tuition(db, programme.id, budget=bdt * 1.2, currency="BDT")
        assert within["budget_status"] == "WITHIN_STATED_BUDGET", (
            "reading a BDT budget as USD would call this over budget")
        assert within["budget_currency"] == "BDT", "the stated currency is preserved, not rewritten"
        assert within["stated_budget"] == round(bdt * 1.2, 2), "the original amount is preserved"
        assert within["budget_conversion"], "a converted comparison must carry its provenance"

        # The same number means different money: as USD it covers the total, as BDT it is about
        # one hundredth of it. An engine that ignored the currency would answer identically.
        as_usd = assistant.get_tuition(db, programme.id, budget=total_usd, currency="USD")
        assert as_usd["budget_status"] == "WITHIN_STATED_BUDGET"
        as_bdt = assistant.get_tuition(db, programme.id, budget=total_usd, currency="BDT")
        assert as_bdt["budget_status"] == "ABOVE_STATED_BUDGET"
    finally:
        t2_cleanup(db, programme, university)
        db.close()


def test_an_empty_destination_list_is_unsupported_rather_than_unrestricted():
    db = new_session()
    try:
        scope = scoping.scope_from_destinations(db, [])
        assert scope.status == scoping.UNSUPPORTED
        assert not scope.ok
        assert scoping.university_clause(db, scope) is None
    finally:
        db.close()


def test_a_caller_supplied_year_reaches_the_query_predicate():
    """A year passed to the tool, not parsed from a sentence, is still a hard constraint."""
    from app.core import academic_year as ay
    from app.core.database import engine

    from task2_helpers import make_intake

    captured: list[str] = []

    def _listen(conn, cursor, statement, parameters, context, executemany):
        captured.append(" ".join(statement.split()).lower())

    db = new_session()
    made = []
    try:
        hungary = _country(db, iso_code="HU")
        uni = _university(db, slug="w6-year-param-uni", country=hungary)
        stated = _program(db, uni, slug="w6-year-param-stated", name="W6 Year Param Probe Stated")
        made.append(_program(db, uni, slug="w6-year-param-unstated",
                             name="W6 Year Param Probe Unstated"))
        make_intake(db, stated, label="Fall 2099", year=2099)
        db.commit()

        event.listen(engine, "before_cursor_execute", _listen)
        try:
            rows = assistant.search_programs(db, subject="w6 year param probe",
                                             academic_year=ay.parse("2099"))
        finally:
            event.remove(engine, "before_cursor_execute", _listen)

        assert {r["slug"] for r in rows} == {"w6-year-param-stated"}
        assert any("from programs" in s and "programs.id in" in s for s in captured), (
            "the year must narrow the query itself: " + repr(captured[:5]))
    finally:
        cleanup(db, *made, db.execute(select(University).where(
            University.slug == "w6-year-param-uni")).scalars().first())
        db.close()


def test_legacy_country_ids_get_the_region_rule_too():
    """A caller that passes raw country ids must not fall back to the country-only rule."""
    db = new_session()
    made = []
    try:
        uk = _country(db, iso2="GB")
        scotland = _country(db, iso_code="GB-SCT")
        scottish = _university(db, slug="w6-legacy-scot-uni", country=uk, region=scotland)
        made.append(_program(db, scottish, slug="w6-legacy-scot-prog",
                             name="W6 Legacy Scotland Probe", discipline="Data Science"))
        db.commit()

        result = matching.match_programs(
            db, {"target_degree_level": "MASTER"},
            {"country_ids": [scotland.id], "subject": "W6 Legacy Scotland Probe"}, limit=10)
        slugs = [r["program"].slug for r in result["results"]]
        assert "w6-legacy-scot-prog" in slugs, (
            "a region id must find its institutions, not an empty set")
    finally:
        cleanup(db, *made, db.execute(select(University).where(
            University.slug == "w6-legacy-scot-uni")).scalars().first())
        db.close()
