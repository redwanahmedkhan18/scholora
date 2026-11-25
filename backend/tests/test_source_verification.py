"""
Source-level verification.

The tests that matter here are about restraint: what the checker must refuse to conclude.
A link checker that is merely enthusiastic will condemn good citations, and a verification
system that lets a machine sign off defeats the entire provenance model.
"""
from __future__ import annotations

import pytest
from app.services import source_verification as sv


# ------------------------------------------------------------------ classification
def test_only_404_and_410_mean_the_page_is_gone():
    assert sv._classify(404)[0] == sv.BROKEN
    assert sv._classify(410)[0] == sv.BROKEN


def test_a_refusal_is_not_a_dead_link():
    """
    The "la Caixa" fellowship page - a real, current, hand-verified citation - answers
    403 to an unrecognised user-agent. Calling that BROKEN would tell a verifier to
    delete correct data, and would condemn every well-defended institutional site.
    """
    for status in (401, 403, 406, 429):
        state, note = sv._classify(status)
        assert state == sv.BLOCKED, f"{status} must not be reported as a dead link"
        assert "by hand" in note


def test_a_server_error_is_transient_not_fatal():
    for status in (500, 502, 503):
        assert sv._classify(status)[0] == sv.UNREACHABLE


def test_success_and_redirects_are_ok():
    for status in (200, 204, 301, 302, 308):
        assert sv._classify(status)[0] == sv.OK


def test_an_unreachable_check_is_never_reported_as_broken():
    """No network egress must not mean 'every citation on the platform is dead'."""
    result = sv.check_url("https://this-host-does-not-resolve-zzz9.invalid")
    assert result["state"] == sv.UNREACHABLE
    assert result["http_status"] is None
    assert "could not check" in result["note"]


# ------------------------------------------------------------------- attribution
def test_verification_requires_a_named_human(db):
    """VERIFIED is a claim somebody has to own. There is no system default."""
    for anonymous in ("", "   ", None):
        with pytest.raises(ValueError, match="verifier name is required"):
            sv.verify_source(db, source_url="https://example.org/a", verifier=anonymous)


def test_verifying_an_uncited_url_is_an_error(db):
    with pytest.raises(ValueError, match="no claims cite"):
        sv.verify_source(db, source_url="https://nothing-cites-this.example",
                         verifier="A Person")


def test_the_queue_groups_claims_by_page(db):
    """
    The point of the grouping: thousands of field claims resolve to a few dozen pages, so
    a verifier opens a page once and clears everything that cites it.
    """
    from app.models.crm import DataVerification

    url = "https://example.org/fees-page"
    for field in ("tuition_annual", "living_cost_annual", "application_fee"):
        db.add(DataVerification(entity_type="program", entity_id=1, field_name=field,
                                field_value="1", source_url=url,
                                source_type="TIER_1_OFFICIAL_UNIVERSITY",
                                status="NEEDS_REVIEW"))
    db.commit()

    entry = next(e for e in sv.source_queue(db, limit=200) if e["source_url"] == url)
    assert entry["claims"] == 3
    assert entry["pending"] == 3
    assert entry["link_state"] == sv.UNCHECKED, "never checked must not look like a pass"

    done = sv.verify_source(db, source_url=url, verifier="Nusrat Jahan")
    assert done["verified"] == 3

    entry = next(e for e in sv.source_queue(db, limit=200) if e["source_url"] == url)
    assert entry["pending"] == 0 and entry["verified"] == 3
    assert all(r.verified_by == "Nusrat Jahan"
               for r in db.query(DataVerification).filter_by(source_url=url).all())


# -------------------------------------------------------------------- readiness
def test_readiness_never_returns_a_secret(db):
    """
    The checklist exists to say what is missing. If it ever echoed a token back it would
    turn an operations screen into a credential leak.
    """
    from app.core.config import get_settings
    from app.services import readiness

    settings = get_settings()
    secrets = [str(getattr(settings, k, "") or "")
               for spec in readiness.REQUIREMENTS for k in spec["settings"]]
    secrets = [s for s in secrets if len(s) > 6]

    blob = repr(readiness.report(db))
    for secret in secrets:
        assert secret not in blob, "a configured credential was echoed back"


def test_readiness_states_what_still_works_without_each_integration(db):
    """A missing credential must never be presented as a broken platform."""
    from app.services import readiness
    for item in readiness.report(db)["items"]:
        assert item["degrades_to"], f"{item['label']} does not say what happens without it"
        assert item["where"], f"{item['label']} does not say where to get it"
        assert item["status"] in {"READY", "MISSING", "OPTIONAL"}


def test_one_llm_key_is_enough(db):
    """Three providers are fallbacks for each other, not three separate requirements."""
    from app.services import readiness
    spec = next(s for s in readiness.REQUIREMENTS if s["key"] == "llm")
    assert spec.get("satisfied_by_any") is True
    assert len(spec["settings"]) == 3
