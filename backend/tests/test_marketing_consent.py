"""M-1 - the conversion relay cannot become an open, consent-blind channel to Meta.

Before this campaign ``POST /api/v1/marketing/conversion`` was anonymous, accepted any e-mail and
phone, hashed them and sent them to the Conversions API with the platform's own token. That made
the platform a relay for third-party identifiers, and it disclosed addresses to an advertising
platform on the strength of a consent ("contact me about my enquiry") that does not cover
ad-platform matching.

The tests below pin the replacement contract:

* the route is server-to-server (the internal token), so an arbitrary client cannot push events;
* **no** personal identifier is accepted, so none can be relayed - raw or hashed;
* a lead reference is resolved server-side and must exist, so an event cannot be attributed to a
  record we do not hold;
* consent is irrelevant to identifiers precisely because no identifier is forwarded - the tests
  prove a revoked, absent or NULL consent cannot leak one either;
* vendor failures report a bare status, never the URL (which carries the access token) or an
  identifier.
"""
from __future__ import annotations

import json
import logging

import httpx
import pytest
from app.core import ratelimit
from app.core.config import settings
from app.models.crm import Lead


def _internal() -> dict:
    return {"X-Internal-Token": (settings.internal_api_token or "").strip()}


def _lead(db, *, consent, email="Lead.One@Example.COM", phone="+8801712345678"):
    row = Lead(full_name="Relay Lead", phone=phone, email=email,
               reference=f"RLY-{consent}-{email[:3]}", consent_contact=consent, source="WEBSITE")
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def db():
    from task2_helpers import new_session

    session = new_session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def wire(monkeypatch):
    """Capture what the handler would put on the wire, without contacting Meta."""
    captured: dict = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"events_received": 1}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, params=None, json=None):
            captured["url"] = url
            captured["params"] = params
            captured["payload"] = json
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(settings, "meta_capi_access_token", "test-capi-token", raising=False)
    monkeypatch.setattr(settings, "meta_pixel_id", "1234567890", raising=False)
    monkeypatch.setattr(settings, "meta_graph_base_url", "https://graph.example.invalid",
                        raising=False)
    return captured


# =====================================================================
# 1. authorization: an anonymous client cannot push events
# =====================================================================
def test_an_anonymous_client_cannot_relay_a_conversion(client, api, wire, db):
    from app.models.crm import AnalyticsEvent

    before = db.query(AnalyticsEvent).count()
    for headers in ({}, {"X-Internal-Token": "wrong-token"}, {"X-Internal-Token": ""}):
        res = client.post(f"{api}/marketing/conversion", headers=headers,
                          json={"event_name": "Lead", "value": 12})
        assert res.status_code == 403, (headers, res.text)
    assert "payload" not in wire, "an unauthenticated call must never reach the vendor"
    # Authorization runs before the body: an anonymous caller cannot even write an analytics row.
    db.expire_all()
    assert db.query(AnalyticsEvent).count() == before, "an unauthorised call wrote a row"


def test_an_empty_configured_token_authorizes_nobody(client, api, wire, monkeypatch):
    """An unset secret must not be satisfied by an unset header."""
    monkeypatch.setattr(settings, "internal_api_token", "", raising=False)
    res = client.post(f"{api}/marketing/conversion", headers={"X-Internal-Token": ""},
                      json={"event_name": "Lead"})
    assert res.status_code == 403
    assert "payload" not in wire


# =====================================================================
# 2. no personal identifier is accepted, so none can be relayed
# =====================================================================
@pytest.mark.parametrize("payload", [
    {"event_name": "Lead", "email": "someone@example.com"},
    {"event_name": "Lead", "phone": "+8801711111111"},
    {"event_name": "Lead", "email": "someone@example.com", "phone": "+8801711111111"},
])
def test_a_payload_with_an_identifier_is_refused_before_anything_happens(client, api, wire,
                                                                       db, payload):
    res = client.post(f"{api}/marketing/conversion", headers=_internal(), json=payload)
    assert res.status_code == 422, res.text
    # FastAPI names the offending field, which is what makes the refusal actionable.
    assert "email" in res.text or "phone" in res.text
    assert "payload" not in wire, "an identifier-bearing request must not reach the vendor"


def test_the_wire_payload_carries_no_identifier_and_no_user_data(client, api, wire, db):
    res = client.post(f"{api}/marketing/conversion", headers=_internal(), json={
        "event_name": "Lead", "event_source_url": "https://scholara.test/contact?email=a@b.c",
        "value": 0, "currency": "BDT"})
    assert res.status_code == 200, res.text
    body = wire["payload"]["data"][0]
    assert "user_data" not in body
    blob = json.dumps(wire["payload"])
    for needle in ("@", "+880", "email=", "a@b.c"):
        assert needle not in blob, f"the wire payload contains {needle!r}"
    # The landing page is still reduced to a clean path.
    assert body["event_source_url"] == "/contact"


# =====================================================================
# 3. consent cannot be bypassed, withdrawn into, or inferred
# =====================================================================
@pytest.mark.parametrize("consent", [True, False, None])
def test_consent_state_never_enables_identifier_forwarding(client, api, wire, db, consent):
    """Whatever the record says, no identifier reaches Meta - the endpoint has none to send."""
    lead = _lead(db, consent=consent)
    try:
        res = client.post(f"{api}/marketing/conversion", headers=_internal(), json={
            "event_name": "Lead", "lead_reference": lead.reference, "value": 500})
        assert res.status_code == 200, res.text
        body = wire["payload"]["data"][0]
        assert "user_data" not in body
        blob = json.dumps(wire["payload"]).lower()
        assert lead.email.lower() not in blob and lead.phone not in blob
        assert lead.reference.lower() not in blob, "our reference is not Meta's business"
    finally:
        db.query(Lead).filter(Lead.id == lead.id).delete()
        db.commit()


def test_a_revoked_consent_cannot_be_followed_by_a_forwarded_identifier(client, api, wire, db):
    """Withdrawal is honoured by construction: there is no identifier path to withdraw into."""
    lead = _lead(db, consent=True)
    try:
        client.post(f"{api}/marketing/conversion", headers=_internal(),
                    json={"event_name": "Lead", "lead_reference": lead.reference})
        lead.consent_contact = False
        db.commit()
        client.post(f"{api}/marketing/conversion", headers=_internal(),
                    json={"event_name": "Lead", "lead_reference": lead.reference})
        for payload in (wire["payload"],):
            assert "user_data" not in payload["data"][0]
    finally:
        db.query(Lead).filter(Lead.id == lead.id).delete()
        db.commit()


def test_a_client_cannot_substitute_another_leads_identifier(client, api, wire, db):
    """The classic relay attack: name somebody else's record and supply their address."""
    lead = _lead(db, consent=True, email="victim@example.com", phone="+8801999999999")
    try:
        res = client.post(f"{api}/marketing/conversion", headers=_internal(), json={
            "event_name": "Lead", "lead_reference": lead.reference,
            "email": "victim@example.com"})
        assert res.status_code == 422, "an identifier is not accepted alongside a reference"
        assert "payload" not in wire
    finally:
        db.query(Lead).filter(Lead.id == lead.id).delete()
        db.commit()


def test_an_unknown_lead_reference_is_refused(client, api, wire, db):
    res = client.post(f"{api}/marketing/conversion", headers=_internal(),
                      json={"event_name": "Lead", "lead_reference": "RLY-does-not-exist"})
    assert res.status_code == 422, res.text
    assert "payload" not in wire, "an event for a record we do not hold is not forwarded"


# =====================================================================
# 4. rate limiting is dedicated to this endpoint
# =====================================================================
def test_the_conversion_route_has_its_own_rate_limit_rule():
    """Independent of the 300/60s catch-all, so guessing the token is throttled on its own."""
    rule = ratelimit.match_rule("/api/v1/marketing/conversion", "POST")
    assert rule is not None and rule.name == "conversion"
    assert rule.limit == 60 and rule.window == 3600
    catch_all = ratelimit.match_rule("/api/v1/something-else", "POST")
    assert catch_all is not None and catch_all.name == "public"
    assert rule.limit < catch_all.limit, "the dedicated bucket must be the stricter one"


def test_the_dedicated_limit_returns_429_before_the_catch_all_would(client, api, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True, raising=False)
    statuses = []
    for _ in range(62):
        res = client.post(f"{api}/marketing/conversion", json={"event_name": "Lead"})
        statuses.append(res.status_code)
    assert 403 in statuses, "unauthenticated calls are refused"
    assert 429 in statuses, "the dedicated bucket must eventually throttle"
    assert statuses.count(403) <= 60, "the rule allows 60 per hour, not the catch-all's 300"
    assert statuses.index(429) < 62


# =====================================================================
# 5. failure handling never leaks the token or an identifier
# =====================================================================
def test_a_vendor_failure_does_not_echo_the_token_or_an_identifier(client, api, monkeypatch,
                                                                   caplog, db):
    """httpx errors quote the request URL, and that URL carries the access token."""
    class _Boom(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, params=None, json=None):
            raise httpx.ConnectError(
                f"connection failed for {url}?access_token={params.get('access_token')}",
                request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    monkeypatch.setattr(settings, "meta_capi_access_token", "SECRET-CAPI-TOKEN-123",
                        raising=False)
    monkeypatch.setattr(settings, "meta_pixel_id", "1234567890", raising=False)

    with caplog.at_level(logging.DEBUG):
        res = client.post(f"{api}/marketing/conversion", headers=_internal(),
                          json={"event_name": "Lead", "value": 0})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "RELAY_FAILED"
    assert "SECRET-CAPI-TOKEN-123" not in res.text
    assert "SECRET-CAPI-TOKEN-123" not in caplog.text, "the access token must not be logged"
    assert "@" not in res.text
