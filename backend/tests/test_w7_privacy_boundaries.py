"""W7-T1: one server-side privacy boundary for client-supplied paths, URLs and UTM data.

A portal link is a bearer capability. The client gate in ``frontend/src/lib/track.ts`` is the
first layer; these tests pin the second, authoritative layer: ``app.core.privacy`` applied at
every persistence boundary that accepts a location from the browser.

Nothing here may weaken campaign attribution: ``utm_campaign`` still resolves ``campaign_id``.
"""
from __future__ import annotations

from app.core.privacy import safe_analytics_path, safe_source_url, scrub_analytics_value
from app.models.content import AdCampaign
from app.models.crm import AnalyticsEvent, Lead
from sqlalchemy import select

SECRET = "w7-secret-capability-8f21"


# --------------------------------------------------------------- unit: helpers
def test_safe_source_url_reduces_full_urls_to_a_path():
    assert safe_source_url("https://scholara.test/programs?utm_source=x#top") == "/programs"
    assert safe_source_url("http://scholara.test/a/b?e=1") == "/a/b"
    assert safe_source_url("/a/b?x=1#y") == "/a/b"


def test_safe_source_url_drops_private_portal_paths_however_they_are_dressed():
    for value in (
        f"/my/{SECRET}",
        f"/my/{SECRET}?utm_source=x",
        f"https://scholara.test/my/{SECRET}",
        f"https://scholara.test/my/{SECRET}?email=a@b.c",
        f"//scholara.test/my/{SECRET}",
        "/my",
    ):
        assert safe_source_url(value) is None, value


def test_safe_source_url_rejects_non_paths_and_junk_without_raising():
    for value in (None, "", "   ", 42, "javascript:alert(1)", "not-a-path", "ftp://h/a", "?only=query"):
        assert safe_source_url(value) is None, value


def test_safe_source_url_treats_every_case_of_the_portal_prefix_as_private():
    # The URL boundary must not depend on Next.js route case-sensitivity.
    for value in (f"/MY/{SECRET}", f"/My/{SECRET}", "/MY", f"https://scholara.test/MY/{SECRET}"):
        assert safe_source_url(value) is None, value


def test_safe_source_url_strips_a_portal_segment_from_an_otherwise_public_path():
    assert safe_source_url(f"/programs/my/{SECRET}") == "/programs/my"


def test_safe_source_url_keeps_a_long_url_within_the_column_budget():
    long_url = "https://scholara.test/programs?" + "&".join(f"utm_content={i}" for i in range(60))
    safe = safe_source_url(long_url)
    assert safe == "/programs"
    assert len(safe) < 300


def test_safe_analytics_path_retains_the_w4_semantics_exactly():
    assert safe_analytics_path(f"/my/{SECRET}") is None
    assert safe_analytics_path("/my") is None
    assert safe_analytics_path("/programs?utm_source=x#top") == "/programs"
    assert safe_analytics_path("") is None
    assert safe_analytics_path(None) is None
    # Deliberately unchanged from W4: this helper backs /track, whose behaviour is frozen.
    assert safe_analytics_path(f"/MY/{SECRET}") == f"/MY/{SECRET}"


def test_scrub_analytics_value_is_narrow_and_structural():
    scrubbed = scrub_analytics_value({
        "utm_campaign": "usa_fall_2027",
        "landing_path": f"/my/{SECRET}",
        "nested": {"ref": f"https://site/my/{SECRET}"},
        "list": [f"/my/{SECRET}", "keep-me"],
    })
    assert SECRET not in repr(scrubbed)
    assert scrubbed["utm_campaign"] == "usa_fall_2027"
    assert scrubbed["list"][1] == "keep-me"
    assert scrubbed["nested"]["ref"] == "https://site/my"


# ------------------------------------------------------------ /api/v1/leads
def test_lead_private_path_never_reaches_the_crm_or_analytics(client, api, db):
    res = client.post(f"{api}/leads", json={"consent_contact": True,
        "full_name": "Privacy Probe", "phone": "+8801700000001",
        "page_path": f"/my/{SECRET}",
        "utm": {"utm_source": "x", "landing_path": f"/my/{SECRET}"},
    })
    assert res.status_code == 200, res.text
    lead = db.execute(select(Lead).order_by(Lead.id.desc())).scalars().first()
    event = db.execute(select(AnalyticsEvent).order_by(AnalyticsEvent.id.desc())).scalars().first()
    assert lead.page_path is None
    assert event.path is None
    assert event.name == "lead_created"
    assert SECRET not in repr(lead.utm) and SECRET not in repr(event.utm)
    assert SECRET not in repr(event.meta)


def test_lead_full_url_page_path_is_reduced_to_a_path(client, api, db):
    res = client.post(f"{api}/leads", json={"consent_contact": True,
        "full_name": "URL Probe", "phone": "+8801700000002",
        "page_path": f"https://scholara.test/my/{SECRET}?email=a%40b.c",
    })
    assert res.status_code == 200, res.text
    lead = db.execute(select(Lead).order_by(Lead.id.desc())).scalars().first()
    assert lead.page_path is None
    assert SECRET not in repr(lead.page_path)


def test_lead_public_landing_and_campaign_attribution_are_preserved(client, api, db):
    campaign = db.execute(
        select(AdCampaign).where(AdCampaign.utm_campaign == "usa_fall_2027")
    ).scalars().first()
    assert campaign is not None, "seeded campaign missing; the attribution assertion is vacuous"

    res = client.post(f"{api}/leads", json={"consent_contact": True,
        "full_name": "Attribution Probe", "phone": "+8801700000003",
        "page_path": "/programs?utm_source=facebook&utm_campaign=usa_fall_2027#top",
        "utm": {"utm_source": "facebook", "utm_medium": "cpc",
                "utm_campaign": "usa_fall_2027", "landing_path": "/programs"},
    })
    assert res.status_code == 200, res.text
    lead = db.execute(select(Lead).order_by(Lead.id.desc())).scalars().first()
    event = db.execute(select(AnalyticsEvent).order_by(AnalyticsEvent.id.desc())).scalars().first()

    assert lead.page_path == "/programs", "query string and fragment must be gone"
    assert event.path == "/programs"
    # Attribution must survive the boundary untouched.
    assert lead.utm["utm_campaign"] == "usa_fall_2027"
    assert lead.utm["utm_source"] == "facebook"
    assert lead.utm["landing_path"] == "/programs"
    assert lead.campaign_id == campaign.id, "utm_campaign must still resolve the campaign"


def test_lead_utm_scrub_is_case_insensitive_at_the_new_boundary(client, api, db):
    res = client.post(f"{api}/leads", json={"consent_contact": True,
        "full_name": "Case Probe", "phone": "+8801700000005",
        "utm": {"utm_campaign": "usa_fall_2027", "landing_path": f"/MY/{SECRET}"},
    })
    assert res.status_code == 200, res.text
    lead = db.execute(select(Lead).order_by(Lead.id.desc())).scalars().first()
    assert SECRET not in repr(lead.utm)
    assert lead.utm["utm_campaign"] == "usa_fall_2027"


def test_scrub_analytics_value_default_is_the_frozen_track_behaviour():
    frozen = scrub_analytics_value(f"/MY/{SECRET}")
    assert frozen == f"/MY/{SECRET}", "/track semantics must not change"
    assert SECRET not in repr(scrub_analytics_value(f"/MY/{SECRET}", ignore_case=True))


def test_lead_nested_utm_portal_paths_are_scrubbed_without_losing_keys(client, api, db):
    res = client.post(f"{api}/leads", json={"consent_contact": True,
        "full_name": "Nested Probe", "phone": "+8801700000004",
        "utm": {"utm_campaign": "uk_january_intake",
                "landing_path": f"/my/{SECRET}",
                "extra": {"ref": f"https://site/my/{SECRET}"}},
    })
    assert res.status_code == 200, res.text
    lead = db.execute(select(Lead).order_by(Lead.id.desc())).scalars().first()
    assert SECRET not in repr(lead.utm)
    assert lead.utm["utm_campaign"] == "uk_january_intake"
    assert "extra" in lead.utm and "ref" in lead.utm["extra"]


# -------------------------------------------------- /api/v1/marketing/conversion
def test_conversion_source_url_is_scrubbed_before_persistence(client, api, db):
    res = client.post(f"{api}/marketing/conversion", json={
        "event_name": "Lead",
        "event_source_url": f"https://scholara.test/my/{SECRET}?email=probe@example.com",
    })
    assert res.status_code == 200, res.text
    event = db.execute(select(AnalyticsEvent).order_by(AnalyticsEvent.id.desc())).scalars().first()
    assert event.name.startswith("conversion")
    assert event.path is None
    assert SECRET not in repr(event.path)


def test_conversion_public_url_is_reduced_to_a_path(client, api, db):
    res = client.post(f"{api}/marketing/conversion", json={
        "event_name": "Purchase", "event_source_url": "https://scholara.test/programs?utm_source=fb",
    })
    assert res.status_code == 200, res.text
    event = db.execute(select(AnalyticsEvent).order_by(AnalyticsEvent.id.desc())).scalars().first()
    assert event.path == "/programs"


# -------------------------------------------------------------- /track regression
def test_track_endpoint_behaviour_is_unchanged_after_the_helper_move(client, api, db):
    client.post(f"{api}/track", json={"name": "page_view", "path": f"/my/{SECRET}"})
    private_row = db.execute(select(AnalyticsEvent).order_by(AnalyticsEvent.id.desc())).scalars().first()
    assert private_row.path is None

    client.post(f"{api}/track", json={"name": "page_view", "path": "/programs?utm_source=x#top"})
    public_row = db.execute(select(AnalyticsEvent).order_by(AnalyticsEvent.id.desc())).scalars().first()
    assert public_row.path == "/programs"


def test_conversion_never_relays_a_raw_or_private_url_to_meta(client, api, monkeypatch):
    """P3: what leaves the platform must not carry a query string or a portal capability."""
    import httpx
    from app.core.config import settings

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
            captured["payload"] = json
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(settings, "meta_capi_access_token", "test-capi-token", raising=False)
    monkeypatch.setattr(settings, "meta_pixel_id", "1234567890", raising=False)

    res = client.post(f"{api}/marketing/conversion", json={
        "event_name": "Lead",
        "event_source_url": f"https://scholara.test/my/{SECRET}?email=probe@example.com",
        "email": "probe@example.com",
        "phone": "+8801700000009",
    })
    assert res.status_code == 200, res.text
    assert captured.get("payload"), "the vendor call did not happen; the assertion would be vacuous"

    data = captured["payload"]["data"][0]
    assert "event_source_url" not in data, "a private/query-bearing URL must not be relayed"
    blob = repr(captured["payload"])
    assert SECRET not in blob
    assert "probe@example.com" not in blob and "+8801700000009" not in blob
    # Only hashed identifiers may identify the person.
    assert set(data["user_data"]) <= {"em", "ph"}
    assert len(data["user_data"]["em"]) == 64


def test_conversion_relays_only_a_clean_path_for_a_public_url(client, api, monkeypatch):
    import httpx
    from app.core.config import settings

    captured: dict = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, params=None, json=None):
            captured["payload"] = json
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(settings, "meta_capi_access_token", "test-capi-token", raising=False)
    monkeypatch.setattr(settings, "meta_pixel_id", "1234567890", raising=False)

    res = client.post(f"{api}/marketing/conversion", json={
        "event_name": "Lead", "event_source_url": "https://scholara.test/programs?utm_source=fb&email=a@b.c",
    })
    assert res.status_code == 200, res.text
    assert captured["payload"]["data"][0]["event_source_url"] == "/programs"


# ------------------------------------------------- /api/v1/appointments (Finding 1)
# The booking form is public and unauthenticated, so its ``utm`` is client-supplied like the
# lead form's. These pin the same boundary on that second write path: before the fix the raw
# dictionary was persisted verbatim into ``Lead.utm``.
def _book(client, api, **overrides):
    from datetime import date, timedelta
    body = {"full_name": "Booking Probe", "phone": "+8801700000099",
            "scheduled_date": (date.today() + timedelta(days=4)).isoformat(),
            "scheduled_time": "11:00", "consent_contact": True}
    body.update(overrides)
    return client.post(f"{api}/appointments", json=body)


def test_appointment_private_landing_path_never_reaches_the_lead(client, api, db):
    res = _book(client, api, utm={"utm_source": "fb", "landing_path": f"/my/{SECRET}"})
    assert res.status_code == 200, res.text
    lead = db.execute(select(Lead).order_by(Lead.id.desc())).scalars().first()
    assert SECRET not in repr(lead.utm), "a portal capability reached Lead.utm through a booking"
    assert lead.utm["landing_path"] == "/my"
    assert lead.utm["utm_source"] == "fb", "ordinary attribution must survive"


def test_appointment_nested_url_carrying_the_token_is_scrubbed(client, api, db):
    res = _book(client, api, utm={"utm_campaign": "uk_january_intake",
                                  "extra": {"ref": f"https://site.test/my/{SECRET}"}})
    assert res.status_code == 200, res.text
    lead = db.execute(select(Lead).order_by(Lead.id.desc())).scalars().first()
    assert SECRET not in repr(lead.utm)
    assert "extra" in lead.utm and "ref" in lead.utm["extra"], "structure must be preserved"
    assert lead.utm["utm_campaign"] == "uk_january_intake"


def test_appointment_utm_scrub_is_case_insensitive(client, api, db):
    res = _book(client, api, utm={"landing_path": f"/MY/{SECRET}"})
    assert res.status_code == 200, res.text
    lead = db.execute(select(Lead).order_by(Lead.id.desc())).scalars().first()
    assert SECRET not in repr(lead.utm), "/MY/ must be treated like /my/"


def test_appointment_campaign_attribution_uses_the_sanitised_dictionary(client, api, db):
    campaign = db.execute(
        select(AdCampaign).where(AdCampaign.utm_campaign == "usa_fall_2027")
    ).scalars().first()
    assert campaign is not None, "seeded campaign missing; this assertion would be vacuous"

    res = _book(client, api, utm={"utm_source": "facebook", "utm_medium": "cpc",
                                  "utm_campaign": "usa_fall_2027",
                                  "landing_path": f"/my/{SECRET}"})
    assert res.status_code == 200, res.text
    lead = db.execute(select(Lead).order_by(Lead.id.desc())).scalars().first()
    assert lead.campaign_id == campaign.id, "attribution must still resolve after scrubbing"
    assert SECRET not in repr(lead.utm)


def test_appointment_still_creates_its_records_and_accepts_consent(client, api, db):
    from app.models.crm import Appointment
    res = _book(client, api, utm={"utm_source": "fb"})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "REQUESTED"
    appt = db.execute(select(Appointment).order_by(Appointment.id.desc())).scalars().first()
    lead = db.execute(select(Lead).order_by(Lead.id.desc())).scalars().first()
    assert appt.consent_contact is True
    assert lead.consent_contact is True, "the lead must carry the same affirmative decision"


def test_appointment_without_affirmative_consent_is_still_refused(client, api):
    for bad in (None, False, "true"):
        res = _book(client, api, consent_contact=bad, utm={"utm_source": "fb"})
        assert res.status_code == 422, f"consent={bad!r} must be refused, got {res.status_code}"
