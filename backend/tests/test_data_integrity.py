"""The rules the specification calls non-negotiable (§39, §40, §48, §66, §69).

These are the tests that matter most: they assert the platform cannot fabricate a fact,
cannot invent an admission probability and cannot leak an unsourced faculty email.
"""
from __future__ import annotations

import re

import pytest

from app.models.base import CONFIDENCE_STATES, ELIGIBILITY_STATES, SOURCE_TIERS

PROBABILITY_PATTERNS = [
    re.compile(r"\d{1,3}\s?%\s*(chance|probability|likelihood)", re.I),
    re.compile(r"(high|good|strong|low|poor)\s+chance\s+of\s+(admission|acceptance)", re.I),
    re.compile(r"guaranteed\s+(admission|offer|scholarship|visa)", re.I),
    re.compile(r"you\s+will\s+(get|be)\s+(admitted|accepted)", re.I),
]


def assert_no_probability_claims(blob: str) -> None:
    for pattern in PROBABILITY_PATTERNS:
        assert not pattern.search(blob), f"forbidden probability claim matched {pattern.pattern}"


# ----------------------------------------------------------- confidence contract
def test_every_published_university_carries_provenance(client, api):
    data = client.get(f"{api}/universities?page_size=50").json()
    assert data["items"], "seed data should expose universities"
    for u in data["items"]:
        prov = u.get("provenance")
        assert prov, f"{u['name']} is published without a provenance block"
        assert prov["confidence"] in CONFIDENCE_STATES
        assert prov["source_type"] in SOURCE_TIERS
        if prov["confidence"] == "VERIFIED":
            assert prov.get("source_url"), f"{u['name']} claims VERIFIED without a source URL"
            assert prov.get("verified_at"), f"{u['name']} claims VERIFIED without a verification date"


def test_program_detail_exposes_source_and_verification(client, api):
    slug = "university-of-texas-at-arlington-msc-artificial-intelligence"
    body = client.get(f"{api}/programs/{slug}").json()
    prov = body["program"]["provenance"]
    assert prov["confidence"] in CONFIDENCE_STATES
    assert prov["source_url"]
    assert prov["source_type"] in SOURCE_TIERS
    # Rankings carry their own provider-level provenance, never merged into one number.
    for ranking in body["program"]["university"].get("rankings", []):
        assert ranking["provider"] and ranking["edition_year"]


def test_conflicting_sources_are_surfaced_not_overwritten(client, api, auth):
    rows = client.get(f"{api}/admin/verifications?page_size=200", headers=auth).json()["items"]
    conflicting = [r for r in rows if r["status"] == "CONFLICTING"]
    assert conflicting, "the conflict register must retain conflicting rows"
    for row in conflicting:
        assert row["conflict_with"], "a conflict must explain which sources disagree"
        # The losing value is still stored — nothing is silently replaced.
        assert row["field_value"] is not None


# ------------------------------------------------------------ eligibility rules
def test_eligibility_returns_only_allowed_verdicts(client, api):
    res = client.post(f"{api}/eligibility/check", json={
        "profile": {"full_name": "T", "cgpa": 3.4, "cgpa_scale": 4.0, "ielts_overall": 6.5,
                    "highest_qualification": "Bachelor", "target_degree_level": "MASTER"},
        "program_slug": "university-of-texas-at-arlington-msc-artificial-intelligence",
    })
    assert res.status_code == 200
    body = res.json()["eligibility"]
    assert body["overall"] in ELIGIBILITY_STATES
    assert body["checks"], "a verdict must be backed by itemised checks"
    for check in body["checks"]:
        assert check["status"] in ELIGIBILITY_STATES
        assert check["criterion"]
    assert_no_probability_claims(res.text)


def test_eligibility_with_no_data_says_information_unavailable(client, api):
    """An empty profile must produce REQUIRES_REVIEW/INFORMATION_UNAVAILABLE, never a guess."""
    res = client.post(f"{api}/eligibility/check", json={
        "profile": {"full_name": "Unknown"},
        "program_slug": "university-of-texas-at-arlington-msc-artificial-intelligence",
    })
    assert res.status_code == 200
    body = res.json()["eligibility"]
    assert body["overall"] in ELIGIBILITY_STATES
    assert body["overall"] != "MEETS", "a profile with no data can never be judged as MEETS"


def test_matching_never_emits_a_probability(client, api):
    res = client.post(f"{api}/match/programs", json={
        "profile": {"full_name": "T", "cgpa": 3.5, "cgpa_scale": 4.0, "ielts_overall": 7.0,
                    "target_degree_level": "MASTER", "budget_per_year": 40000},
        "limit": 10,
    })
    assert res.status_code == 200
    assert_no_probability_claims(res.text)


def test_scholarship_language_is_criteria_based(client, api):
    res = client.post(f"{api}/match/scholarships", json={
        "profile": {"full_name": "T", "cgpa": 3.8, "cgpa_scale": 4.0, "ielts_overall": 7.5,
                    "target_degree_level": "MASTER"},
        "limit": 10,
    })
    assert res.status_code == 200
    text = res.text.lower()
    assert "you will receive" not in text
    assert "guaranteed scholarship" not in text
    assert_no_probability_claims(res.text)


# ------------------------------------------------------------------- faculty
def test_faculty_emails_are_labelled_and_never_inferred(client, api):
    data = client.get(f"{api}/faculty?page_size=50").json()
    allowed = {"OFFICIAL_UNIVERSITY_EMAIL", "PUBLIC_PROFESSIONAL_EMAIL", "UNAVAILABLE"}
    for person in data["items"]:
        status = person.get("email_status")
        assert status in allowed, f"{person['name']} has an unlabelled email status: {status}"
        if status == "UNAVAILABLE":
            assert not person.get("email"), "an UNAVAILABLE status must not carry an address"


# ------------------------------------------------------------------ assistant
def test_assistant_refuses_to_invent_unknown_universities(client, api):
    res = client.post(f"{api}/assistant/ask", json={
        "question": "What is the tuition at the Imaginary Institute of Nowhere in Atlantis?",
    })
    assert res.status_code == 200
    body = res.json()
    assert_no_probability_claims(body["answer"])
    # Either it is explicitly ungrounded, or it names only records we actually hold.
    if body["grounded"]:
        assert "imaginary institute" not in body["answer"].lower()


def test_assistant_never_predicts_admission(client, api):
    for question in [
        "What are my chances of getting into Oxford?",
        "Will I definitely get admitted with a 3.2 CGPA?",
        "Guarantee me a scholarship please",
    ]:
        body = client.post(f"{api}/assistant/ask", json={"question": question}).json()
        assert_no_probability_claims(body["answer"])


def test_assistant_logs_every_answer_for_audit(client, api, auth):
    client.post(f"{api}/assistant/ask", json={"question": "masters in data science in Canada"})
    rows = client.get(f"{api}/admin/assistant/conversations", headers=auth).json()
    assert rows["total"] >= 1
    latest = rows["items"][0]
    assert latest["question"]
    assert latest["answer"]
    assert "grounded" in latest


def test_assistant_escalates_commercial_questions(client, api):
    body = client.post(f"{api}/assistant/ask", json={"question": "What is your refund policy and fee structure?"}).json()
    assert body["escalated"] is True
    assert "counselor" in body["answer"].lower()


# ------------------------------------------------------------- public safety
@pytest.mark.parametrize("path", [
    "/universities?page_size=30", "/programs?page_size=30", "/scholarships?page_size=30",
    "/success-stories?page_size=30", "/faculty?page_size=30",
])
def test_no_public_listing_claims_a_probability(client, api, path):
    assert_no_probability_claims(client.get(f"{api}{path}").text)


def test_partnership_data_is_never_public(client, api):
    """Commercial partnership terms are internal-only (§44)."""
    body = client.get(f"{api}/universities/university-of-texas-at-arlington").text.lower()
    for leaked in ["commission", "agreement_expiry", "representative"]:
        assert leaked not in body


# --------------------------------------------------------------------------
# PostgreSQL type selection.
#
# `json` has no equality operator in PostgreSQL, so a single bare JSON column anywhere in a
# table is enough to make every SELECT DISTINCT against it fail at runtime - which is what took
# down /programs and /universities when the app moved off SQLite. The models must therefore
# compile to `jsonb`, never `json`.
#
# This test runs fine on SQLite because it compiles the DDL against the PostgreSQL dialect
# rather than executing it, so the whole class of bug is caught without a live Postgres.
# --------------------------------------------------------------------------

def test_every_json_column_compiles_to_jsonb_on_postgresql():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    from app.core.database import Base
    import app.models  # noqa: F401  (registers every table on Base.metadata)

    dialect = postgresql.dialect()
    offenders: list[str] = []

    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            try:
                rendered = column.type.compile(dialect=dialect).upper()
            except Exception:  # pragma: no cover - types with no PG rendering
                continue
            # "JSON" but not "JSONB" and not "JSONPATH" etc.
            if rendered == "JSON":
                offenders.append(f"{table.name}.{column.name}")

    assert not offenders, (
        "These columns compile to PostgreSQL `json`, which has no equality operator and will "
        "break any SELECT DISTINCT over their table. Use JSONType from app.models.base: "
        + ", ".join(offenders)
    )

    # And prove the mechanism works: a known JSON column really does render as JSONB.
    programs = Base.metadata.tables["programs"]
    assert "JSONB" in str(CreateTable(programs).compile(dialect=dialect)).upper()


def test_distinct_search_queries_are_dialect_safe():
    """The two endpoints that use DISTINCT must compile for PostgreSQL without error."""
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql

    from app.models.education import Program, University

    for model in (Program, University):
        stmt = select(model).distinct()
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        assert "SELECT DISTINCT" in compiled


# --------------------------------------------------------------------------
# Cost engine: currency correctness.
#
# Most programmes in the dataset are priced in GBP/EUR/CAD/AUD/NZD while Bangladeshi students
# state budgets in USD or BDT. The engine used to subtract the two directly, so a GBP 28,350
# programme was reported WITHIN_STATED_BUDGET against a USD 35,000 budget - roughly USD 36,000,
# i.e. over it. A wrong number about money is the worst kind of wrong number here.
# --------------------------------------------------------------------------

def test_budget_comparison_converts_between_currencies(db):
    from app.models.education import Program
    from app.services import costing, fx

    program = db.query(Program).filter(Program.currency == "GBP").first()
    if program is None:  # pragma: no cover - depends on seed mix
        import pytest

        pytest.skip("no GBP-priced programme in this dataset")

    budget_usd = 35000.0
    result = costing.estimate(program, budget_usd, "USD")

    assert result["currency"] == "GBP"
    assert result["budget_currency"] == "USD"
    # The budget must have been restated in the programme's currency before comparison.
    converted = result["budget_in_programme_currency"]
    assert converted == pytest_approx(fx.convert(budget_usd, "USD", "GBP"))
    assert converted < budget_usd  # GBP is stronger than USD, so the number must shrink

    # And the gap must be computed against the converted figure, not the raw one.
    assert result["budget_gap"] == round(result["estimated_annual_total"] - converted, 2)
    assert result["budget_conversion"]["from_currency"] == "USD"
    assert result["budget_conversion"]["rate_as_of"]


def pytest_approx(value):
    import pytest

    return pytest.approx(value, rel=1e-6)


def test_unknown_currency_pair_refuses_to_compare(db):
    from app.models.education import Program
    from app.services import costing

    program = db.query(Program).filter(Program.currency.isnot(None)).first()
    result = costing.estimate(program, 100000.0, "XYZ")  # not a real currency
    assert result["budget_status"] == "BUDGET_CURRENCY_MISMATCH"
    assert result["budget_gap"] is None
    assert "cannot be compared" in result["budget_note"]


def test_same_currency_comparison_is_unconverted(db):
    from app.models.education import Program
    from app.services import costing

    program = db.query(Program).filter(Program.currency == "USD").first()
    result = costing.estimate(program, 50000.0, "USD")
    assert "budget_conversion" not in result  # nothing to disclose
    assert result["budget_gap"] == round(result["estimated_annual_total"] - 50000.0, 2)


# --------------------------------------------------------------------------
# A verdict and its explanation must describe the same thing.
# --------------------------------------------------------------------------

def test_match_reason_detail_matches_its_own_status(db):
    from app.services import matching

    student = {
        "cgpa": 3.4,
        "cgpa_scale": 4.0,
        "ielts_overall": 6.5,
        "highest_qualification": "BACHELOR",
        "budget_per_year": 35000,
        "budget_currency": "USD",
    }
    payload = matching.match_programs(db, student, limit=10)
    results = payload["results"] if isinstance(payload, dict) else payload
    assert results

    # An INFORMATION_UNAVAILABLE verdict must never be explained by a sentence that asserts
    # the requirement was met - that pairing is how the old bug showed up.
    affirmative = ("meets the stated minimum", "meets ", "satisfies")
    for row in results:
        for reason in row["reasons"]:
            if reason["status"] == "INFORMATION_UNAVAILABLE":
                detail = reason["detail"].lower()
                assert not any(
                    detail.startswith(a) or f": {a}" in detail for a in affirmative
                ), f"{reason['factor']} says {reason['status']} but explains it with {reason['detail']!r}"


def test_budget_currency_options_exactly_match_what_the_cost_engine_can_convert(client, api):
    """
    The budget dropdown and the FX table must never drift. Offering a currency the engine
    cannot convert hands the student a budget question that then returns
    BUDGET_CURRENCY_MISMATCH instead of a verdict; omitting one the engine supports hides a
    valid option. Serving the list from fx.RATES makes the two impossible to desynchronise.
    """
    from app.services import fx
    served = [o["value"] for o in client.get(f"{api}/vocabularies").json()["budget_currencies"]]
    assert served == sorted(fx.RATES)
    # The rate vintage must travel with the list so the UI can disclose it.
    assert client.get(f"{api}/vocabularies").json()["budget_currency_rates_as_of"] == fx.RATES_AS_OF


def test_profile_vocabularies_are_served_not_guessed(client, api):
    from app.models.base import CGPA_SCALES, EDUCATION_LEVELS, ENGLISH_TESTS
    v = client.get(f"{api}/vocabularies").json()
    assert [o["value"] for o in v["education_levels"]] == list(EDUCATION_LEVELS)
    assert [o["value"] for o in v["cgpa_scales"]] == list(CGPA_SCALES)
    assert [o["value"] for o in v["english_tests"]] == list(ENGLISH_TESTS)
