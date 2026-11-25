"""
Tests for live site extraction.

The ones that matter here are the refusals: the extractor declining to guess a domain,
and the grounding check throwing away a fabricated quote. Those two behaviours are the
entire safety argument for letting this feature exist, so they are tested directly
rather than inferred from a happy path.
"""
from __future__ import annotations

import pytest
from app.services import extraction as X

FAKE_FEES_HTML = """
<html><head><title>Fees and funding - Example University</title></head><body>
<nav><a href="/study/">Study</a> <a href="/news/">News</a></nav>
<p>Tuition fees for international students on taught master's programmes are
EUR 18,500 per year for the 2026/27 academic year.</p>
<p>Applicants must hold IELTS 6.5 overall with no band below 6.0.</p>
<p>research, postgraduate, programme, course, example, archaeology, phd, per annum,</p>
<p>The application deadline for September entry is 1 July 2026.</p>
</body></html>
"""

FAKE_HOME_HTML = """
<html><head><title>Example University</title></head><body>
<a href="/study/">Study with us</a>
<a href="/study/fees/">Fees and funding</a>
<a href="/news/latest">Latest news</a>
<a href="https://other-site.example.org/x">External</a>
</body></html>
"""


def _page(url, html):
    return X.Page(url, "OK", X.page_title(html), X.strip_html(html), html)


# --------------------------------------------------------------------- domain safety
def test_it_refuses_to_guess_a_domain_for_an_unknown_university(db):
    """
    The failure mode this prevents: inventing www.<name>.edu, reading a completely
    different institution's site, and reporting its fees with full confidence.
    """
    r = X.resolve(db, name="Nonexistent Institute of Technology", country="Ireland")
    assert r.status == "NOT_IN_CATALOGUE"
    assert r.website == ""
    assert "will not guess" in r.message


def test_an_operator_supplied_website_is_accepted(db):
    r = X.resolve(db, name="Nonexistent Institute", country="",
                  website="https://example.edu/")
    assert r.status == "RESOLVED"
    assert r.website == "https://example.edu/"


def test_a_catalogue_match_uses_the_verified_website(db):
    """Whatever the fixture holds - the point is the website comes from the record."""
    from app.models.education import University
    known = db.query(University).filter(University.website != "").first()
    if known is None:
        pytest.skip("fixture has no university with a website")
    r = X.resolve(db, name=known.name, country="")
    assert r.status == "RESOLVED"
    assert r.website == known.website


def test_run_stops_cleanly_when_it_cannot_resolve(db):
    out = X.run(db, name="Totally Unknown University", country="Narnia", use_llm=False)
    assert out["resolution"]["status"] == "NOT_IN_CATALOGUE"
    assert out["findings"] == []
    assert out["pages"] == []


# ------------------------------------------------------------------------- grounding
def test_grounding_discards_a_quote_that_is_not_on_the_page():
    """
    The load-bearing guardrail. A model that invents a tuition figure must not be able
    to get it past this, no matter how plausible the sentence looks.
    """
    pages = [_page("https://example.edu/study/fees/", FAKE_FEES_HTML)]
    fabricated = [{"intent": "tuition", "method": "llm", "score": 10,
                   "source_url": "https://example.edu/study/fees/",
                   "quote": "Tuition fees for international students are EUR 9,000 per year."}]
    kept, dropped = X.ground(fabricated, pages)
    assert kept == []
    assert dropped == 1


def test_grounding_keeps_a_real_quote():
    pages = [_page("https://example.edu/study/fees/", FAKE_FEES_HTML)]
    real = [{"intent": "tuition", "method": "llm", "score": 10,
             "source_url": "https://example.edu/study/fees/",
             "quote": "Applicants must hold IELTS 6.5 overall with no band below 6.0."}]
    kept, dropped = X.ground(real, pages)
    assert len(kept) == 1 and dropped == 0


def test_grounding_repoints_a_quote_attributed_to_the_wrong_page():
    """Right text, wrong URL is a citation bug, not a fabrication. Fix it, keep it."""
    pages = [_page("https://example.edu/", FAKE_HOME_HTML),
             _page("https://example.edu/study/fees/", FAKE_FEES_HTML)]
    misattributed = [{"intent": "tuition", "method": "llm", "score": 10,
                      "source_url": "https://example.edu/",
                      "quote": "Applicants must hold IELTS 6.5 overall with no band below 6.0."}]
    kept, dropped = X.ground(misattributed, pages)
    assert dropped == 0
    assert kept[0]["source_url"] == "https://example.edu/study/fees/"
    assert "attribution corrected" in kept[0]["note"]


# -------------------------------------------------------------------- quality filters
def test_a_keyword_blob_is_not_a_finding():
    """
    Grounding proves a quote is real; it says nothing about whether it is worth
    reading. Tag clouds match the tuition vocabulary perfectly and inform nobody.
    """
    assert not X.looks_like_prose(
        "research, postgraduate, programme, course, nui galway, archaeology, phd, per annum,")
    assert X.looks_like_prose(
        "Tuition fees for international students are EUR 18,500 per year.")


def test_cost_does_not_match_costume():
    assert not X.has_word("the lir academy costume level 1", "cost")
    assert X.has_word("the total cost is high", "cost")


def test_deterministic_extraction_finds_the_figures():
    pages = [_page("https://example.edu/study/fees/", FAKE_FEES_HTML)]
    found = X.deterministic_findings(pages, ["tuition", "entry_requirements"], "fees and ielts")
    quotes = " ".join(f["quote"] for f in found)
    assert "18,500" in quotes
    assert "IELTS 6.5" in quotes
    assert "archaeology" not in quotes          # the keyword blob stayed out


def test_number_hungry_intents_reject_sentences_with_no_number():
    pages = [_page("https://example.edu/x", "<html><body><p>Tuition fees are payable "
                   "by all students at the start of term.</p></body></html>")]
    assert X.deterministic_findings(pages, ["tuition"], "") == []


# ----------------------------------------------------------------------- intent logic
@pytest.mark.parametrize("instruction,expected", [
    ("how much is the tuition?", "tuition"),
    ("what IELTS score do I need", "entry_requirements"),
    ("when is the application deadline", "deadlines"),
    ("are there any scholarships", "scholarships"),
])
def test_instructions_map_to_intents(instruction, expected):
    assert expected in X.classify_instruction(instruction)


def test_a_vague_instruction_still_gets_a_sensible_default():
    assert X.classify_instruction("tell me about this place") == list(X.DEFAULT_INTENTS)


# ------------------------------------------------------------------------- link logic
def test_only_same_host_links_are_followed():
    got = X.links(FAKE_HOME_HTML, "https://example.edu/")
    hosts = {u.split("/")[2] for u, _ in got}
    assert hosts == {"example.edu"}


def test_hub_links_outrank_news():
    """
    Real university sites bury fees two clicks deep behind a generic hub, so hubs have
    to be worth following even though they answer nothing themselves.
    """
    assert X.hub_score("https://example.edu/study/", "Study with us") > \
           X.hub_score("https://example.edu/news/latest", "Latest news")


def test_the_api_route_requires_a_data_manager(client):
    assert client.post("/api/v1/admin/extraction/run",
                       json={"name": "x"}).status_code in (401, 403)
