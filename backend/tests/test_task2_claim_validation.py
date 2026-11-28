"""W3 acceptance tests - factual claims must be supported by the retrieved evidence.

Covers supported claims, fabricated numbers with a real citation attached, categorical claims,
conflicting evidence, demo/unverified evidence, and the integration point in `ask()` where a
hostile model response must not reach the student.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.core import claim_validation as cv
from app.models.crm import KnowledgeChunk

from task2_helpers import cleanup, make_program, make_university, new_session


def _hit(title="MSc Computer Science", content=None, url="https://example.edu/course",
         verified=True, provenance=None):
    return {
        "title": title,
        "content": content or ("Annual tuition is USD 21,234. IELTS overall 6.5. "
                               "The application deadline is 1 July 2026."),
        "source_url": url,
        "url_path": url,
        "verified": verified,
        "provenance": provenance,
    }


def test_a_supported_factual_claim_passes():
    result = cv.validate("The MSc Computer Science costs USD 21,234 per year and requires IELTS 6.5.",
                         [_hit()], {})
    assert result.status == "VERIFIED"
    assert result.rejected_count == 0
    assert "21,234" in result.text


def test_a_fabricated_number_is_rejected_even_with_a_real_citation():
    """The citation is present and correct; the number is not in the source. Support, not URL."""
    result = cv.validate("The MSc Computer Science costs USD 9,000 per year.", [_hit()], {})
    assert result.status == "REJECTED"
    assert result.rejected_count == 1
    assert "9,000" not in result.text
    assert result.rejected[0].reason.startswith("UNSUPPORTED_NUMBER")


def test_a_categorical_claim_without_supporting_words_is_rejected():
    result = cv.validate("MOI is accepted for this programme.", [_hit()], {})
    assert result.rejected_count == 1
    assert result.rejected[0].reason == "UNSUPPORTED_CLAIM:moi"


def test_a_categorical_claim_is_kept_when_the_evidence_says_it():
    hit = _hit(content="MOI is accepted in place of IELTS for this programme.")
    result = cv.validate("MOI is accepted for this programme.", [hit], {})
    assert result.status == "VERIFIED"


def test_demo_evidence_can_never_support_a_fact():
    demo = _hit(content="Annual tuition is USD 9,000.", provenance="DEMO")
    result = cv.validate("The tuition is USD 9,000.", [demo], {})
    assert result.status == "REJECTED"
    assert "9,000" not in result.text


def test_unverified_evidence_can_never_support_a_fact():
    unverified = _hit(content="Annual tuition is USD 9,000.", verified=False)
    result = cv.validate("The tuition is USD 9,000.", [unverified], {})
    assert result.status == "REJECTED"


def test_evidence_without_a_number_cannot_support_a_number():
    hit = _hit(content="Tuition fees are published on the university website.")
    result = cv.validate("Tuition is USD 21,234 per year.", [hit], {})
    assert result.status == "REJECTED"


def test_conflicting_evidence_withholds_the_figure_rather_than_picking_one():
    first = _hit(title="MSc Data Science", content="Annual tuition is USD 21,234.")
    second = _hit(title="MSc Data Science", content="Annual tuition is USD 22,000.",
                  url="https://example.edu/other-page")
    result = cv.validate("The MSc Data Science costs USD 21,234 per year.", [first, second], {})
    assert result.status == "REJECTED"
    assert any(r.reason.startswith("CONFLICTING_EVIDENCE") for r in result.rejected)


def test_advice_and_questions_are_not_silenced():
    text = ("You should check the official page before applying. "
            "Would you like a counselor to confirm it?")
    result = cv.validate(text, [_hit()], {})
    assert result.status == "NO_CLAIMS"
    assert " ".join(result.text.split()) == " ".join(text.split())


def test_structured_tool_values_count_as_evidence_for_their_own_field():
    data = {"programs": [{
        "verified": True, "university": "Example University",
        "name": "MSc Computer Science", "tuition_annual": 21234.0, "currency": "USD",
        "ielts_overall": 6.5, "intakes": ["Fall 2026"],
    }]}
    result = cv.validate("Tuition is USD 21,234 and IELTS 6.5 is required.", [], data)
    assert result.status == "VERIFIED"


def test_no_evidence_at_all_rejects_a_factual_sentence():
    result = cv.validate("The tuition is USD 9,000.", [], {})
    assert result.status == "REJECTED"
    assert "9,000" not in result.text


# ----------------------------------------------------------------- integration
def _probe_programme(db):
    own = new_session()
    university = make_university(own, slug="claim-probe-uni", name="Claim Probe University")
    programme = make_program(own, university, slug="claim-probe-programme",
                             name="MSc Computer Science Claim Probe", tuition=21234.0)
    own.commit()
    from app.services import retrieval

    retrieval.rebuild_index(own)
    return own, university, programme


def _drop_probe(db, university, programme):
    cleanup(db, programme, university)
    db.query(KnowledgeChunk).filter(KnowledgeChunk.slug.like("claim-probe-%")).delete(
        synchronize_session=False)
    db.commit()


def test_a_hostile_model_cannot_insert_an_unsupported_fee(client, api, db, monkeypatch):
    from app.services import assistant_engine

    own, university, programme = _probe_programme(db)
    try:
        monkeypatch.setattr(assistant_engine, "_llm_available", lambda: True)
        monkeypatch.setattr(assistant_engine, "_call_llm", lambda *a, **k: SimpleNamespace(
            text=("The MSc Computer Science Claim Probe costs USD 9,000 per year. "
                  "You should check the official page before applying."),
            provider="hostile-test", model="hostile-1"))

        body = client.post(f"{api}/assistant/ask", json={
            "question": "What is the tuition for a Master's in computer science in the United States?",
            "session_id": "task2-w3-hostile",
        }).json()
        assert body["domain"]["decision"] == "ALLOW"
        assert "9,000" not in body["answer"]
        assert "check the official page" in body["answer"]
        assert body["claim_validation"]["claims_rejected"] >= 1
        assert body["claim_validation"]["status"] in ("QUALIFIED", "REJECTED")
        assert body["claim_validation"]["reasons"]
    finally:
        _drop_probe(own, university, programme)
        own.close()


def test_a_fully_fabricated_model_answer_falls_back_to_the_validated_template(client, api, db, monkeypatch):
    from app.services import assistant_engine

    own, university, programme = _probe_programme(db)
    try:
        monkeypatch.setattr(assistant_engine, "_llm_available", lambda: True)
        monkeypatch.setattr(assistant_engine, "_call_llm", lambda *a, **k: SimpleNamespace(
            text="The MSc Computer Science Claim Probe costs USD 9,000 per year.",
            provider="hostile-test", model="hostile-1"))

        body = client.post(f"{api}/assistant/ask", json={
            "question": "What is the tuition for a Master's in computer science in the United States?",
            "session_id": "task2-w3-fallback",
        }).json()
        assert "9,000" not in body["answer"]
        assert "21,234" in body["answer"], "the verified template value should be served instead"
        assert body["mode"] == "template"
        assert body["claim_validation"]["claims_rejected"] == 0
        assert body["claim_validation"]["reasons"] or body["mode"] == "template"
    finally:
        _drop_probe(own, university, programme)
        own.close()


def test_a_template_answer_from_verified_records_passes_claim_validation(client, api, db):
    own, university, programme = _probe_programme(db)
    try:
        body = client.post(f"{api}/assistant/ask", json={
            "question": "What is the tuition for a Master's in computer science in the United States?",
            "session_id": "task2-w3-template",
        }).json()
        assert "21,234" in body["answer"]
        assert body["claim_validation"]["status"] == "VERIFIED"
        assert body["claim_validation"]["claims_rejected"] == 0
    finally:
        _drop_probe(own, university, programme)
        own.close()
