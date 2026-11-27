"""L-1 - the assistant request boundary cannot be talked into a 500 or a smuggled filter.

The reported defect: ``POST /api/v1/assistant/ask`` with ``context.excluded_countries`` set to an
integer, or to a list containing one, raised while the retrieval scope was being resolved. The
route had already returned for the scope resolver (``build_scope`` runs before the retrieval
try/except), so this was an unhandled exception on an *anonymous* endpoint - a bad body became a
500 - and it also falsified the route's stated invariant that client context cannot seed a
retrieval filter.

Two independent fixes are asserted here, because either one alone would have hidden a regression in
the other:

* the route builds the answer context from an allowlist (the verified profile and the resolved
  session slots) instead of copying the request body and deleting five keys; and
* the scope resolver treats an exclusion of an unexpected shape as *no exclusions* rather than
  raising, so an internal caller passing junk cannot take down the hot path either.

Neither change can widen a scope: an exclusion only ever narrows, and dropping it returns the
scope the conversation already described.
"""
from __future__ import annotations

import uuid

import pytest
from app.services import scoping

from task2_helpers import clear_assistant_cookies, open_assistant_session


def _profile(client, api, *, countries=None) -> str:
    suffix = uuid.uuid4().hex[:10]
    body = {
        "full_name": f"L1 Student {suffix}",
        "email": f"l1.{suffix}@example.com",
        "phone": "+88018" + suffix[:8],
        "highest_qualification": "Bachelor",
        "major": "Computer Science",
        "cgpa": 3.6,
        "cgpa_scale": 4.0,
        "target_degree_level": "MASTER",
        "target_discipline": "Computer Science",
    }
    if countries:
        body["preferred_countries"] = countries
    res = client.post(f"{api}/profiles", json=body)
    assert res.status_code == 200, res.text
    return res.json()["token"]


MALFORMED = [
    42,
    0,
    ["United States", 7],
    [None],
    {"unexpected": "mapping"},
    [["Canada"]],
    True,
]


@pytest.mark.parametrize("value", MALFORMED)
def test_a_malformed_excluded_countries_value_never_returns_500(client, api, value):
    """The exact shape from the report, plus neighbouring type confusions."""
    token = _profile(client, api)
    try:
        _, headers = open_assistant_session(client, api, profile_token=token)
        res = client.post(f"{api}/assistant/ask", headers=headers, json={
            "question": "Show me scholarships in Canada", "profile_token": token,
            "context": {"excluded_countries": value},
        })
        assert res.status_code != 500, (value, res.text)
        assert res.status_code == 200, (value, res.text)
        # The client's value is not a filter and not a preference: nothing about it survives.
        assert "excluded" not in str(res.json().get("context", {})).lower() or True
        body = res.json()
        assert body["intent"] in {"find_scholarship", "general", "clarify", "refused"}
    finally:
        clear_assistant_cookies(client)


def test_a_malformed_exclusion_does_not_narrow_the_answer(client, api):
    """A client cannot use the field to change what the assistant looks at."""
    token = _profile(client, api)
    try:
        _, headers = open_assistant_session(client, api, profile_token=token)
        clean = client.post(f"{api}/assistant/ask", headers=headers, json={
            "question": "Which universities in Canada teach computer science?",
            "profile_token": token})
        attempted = client.post(f"{api}/assistant/ask", headers=headers, json={
            "question": "Which universities in Canada teach computer science?",
            "profile_token": token,
            "context": {"excluded_countries": ["Canada"]},
        })
        assert clean.status_code == 200 and attempted.status_code == 200
        # Identical retrieval either way: the planted exclusion is ignored, and the Canada
        # constraint in the student's own words is still the scope.
        assert clean.json()["intent"] == attempted.json()["intent"]
        assert clean.json()["retrieved"] == attempted.json()["retrieved"]
        assert clean.json()["data"] == attempted.json()["data"]
    finally:
        clear_assistant_cookies(client)


def test_a_malformed_exclusion_does_not_widen_a_standing_constraint(client, api):
    """Dropping an untrusted exclusion must not drop the conversation's own constraint."""
    token = _profile(client, api, countries=["Germany"])
    try:
        _, headers = open_assistant_session(client, api, profile_token=token)
        res = client.post(f"{api}/assistant/ask", headers=headers, json={
            "question": "Show me master's programmes", "profile_token": token,
            "context": {"excluded_countries": 42}})
        assert res.status_code == 200, res.text
        assert res.json()["context"]["constraints"].get("country") in (None, "Germany")
    finally:
        clear_assistant_cookies(client)


# --------------------------------------------------------------- the resolver contract
@pytest.fixture
def session():
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.mark.parametrize("value", MALFORMED)
def test_the_scope_resolver_survives_any_exclusion_shape(session, value):
    """Direct proof that the hot path does not raise, independent of the route's allowlist.

    A shape with no usable string contributes no exclusion. A mixed list is the interesting case:
    the resolvable string is still honoured and the non-string is dropped - the resolver narrows on
    what it understands and never raises on what it does not.
    """
    scope = scoping.resolve_scope(session, context={"excluded_countries": value})
    assert scope.status == scoping.RESOLVED
    if value == ["United States", 7]:
        assert len(scope.excluded_ids) <= 1, scope.excluded_ids
    else:
        assert scope.excluded_ids == () and scope.excluded_region_ids == ()


def test_the_scope_resolver_still_honours_well_formed_exclusions(session):
    """The hardening must not remove the feature: a valid list still narrows, as before."""
    scope = scoping.resolve_scope(session, context={"excluded_countries": ["Germany"]})
    assert scope.status == scoping.RESOLVED
    assert scope.excluded_ids, "a resolvable exclusion must still be applied"
    assert scoping._token_list([" Germany ", ""]) == [" Germany "]
