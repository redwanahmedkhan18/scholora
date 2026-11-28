"""F-5 — the analytics request boundary has a deliberate length, in code and in the column.

`conversion_` + an unbounded `event_name` was written into `analytics_events.name`, which is
`VARCHAR(80)` on PostgreSQL and unconstrained on SQLite. The result was a 200 in the suite and a
**500** in production - a client error reported as a server fault, which is how real faults get
lost among mistyped ones. The same hole existed on `/track`, which stores a caller-supplied name in
the same column.

The boundary is *derived*, not chosen: the column is 80 characters, the relay prefixes 11, so the
relay's `event_name` gets 69. Both live in `app.models.crm.ANALYTICS_EVENT_NAME_MAX` and are used by
the column, the request schemas and the tests, so the three cannot drift apart. Invalid input is
refused with 422 and nothing is persisted - never truncated, because a shortened name is a different
event.
"""
from __future__ import annotations

import pytest
from app.core.config import settings
from app.models.crm import ANALYTICS_EVENT_NAME_MAX, AnalyticsEvent
from sqlalchemy import select

from task2_helpers import new_session

CONVERSION_MAX = ANALYTICS_EVENT_NAME_MAX - len("conversion_")


def _internal() -> dict:
    return {"X-Internal-Token": (settings.internal_api_token or "").strip()}


def _events(db, prefix: str) -> list[AnalyticsEvent]:
    db.expire_all()
    return list(db.execute(select(AnalyticsEvent).where(
        AnalyticsEvent.name.like(f"{prefix}%"))).scalars())


def _clear(db, *prefixes: str) -> None:
    for prefix in prefixes:
        for row in _events(db, prefix):
            db.delete(row)
    db.commit()


# =====================================================================
# 1. the conversion relay
# =====================================================================
def test_the_bound_is_derived_from_the_column_that_stores_it():
    assert ANALYTICS_EVENT_NAME_MAX == 80
    assert CONVERSION_MAX == ANALYTICS_EVENT_NAME_MAX - len("conversion_")
    # The composed name is what PostgreSQL constrains, so it must fit by construction.
    assert len("conversion_") + CONVERSION_MAX == ANALYTICS_EVENT_NAME_MAX


@pytest.mark.parametrize("name", ["", " "])
def test_an_empty_event_name_is_refused(client, api, name):
    res = client.post(f"{api}/marketing/conversion", headers=_internal(),
                      json={"event_name": name})
    assert res.status_code == 422, res.text


def test_the_maximum_length_is_accepted_and_stored_intact(client, api):
    name = "L" + "x" * (CONVERSION_MAX - 1)
    db = new_session()
    try:
        res = client.post(f"{api}/marketing/conversion", headers=_internal(),
                          json={"event_name": name})
        assert res.status_code in (200, 201), res.text
        stored = _events(db, "conversion_")
        assert any(row.name == f"conversion_{name.lower()}" for row in stored), \
            "the whole name must be stored, not a prefix of it"
        assert all(len(row.name) <= ANALYTICS_EVENT_NAME_MAX for row in stored)
    finally:
        _clear(db, "conversion_")
        db.close()


def test_one_character_beyond_the_maximum_is_a_client_error(client, api):
    name = "L" + "x" * CONVERSION_MAX
    db = new_session()
    try:
        before = len(_events(db, "conversion_"))
        res = client.post(f"{api}/marketing/conversion", headers=_internal(),
                          json={"event_name": name})
        assert res.status_code == 422, (
            "one character past the column must be refused, not truncated and not a 500")
        assert "event_name" in res.text
        assert len(_events(db, "conversion_")) == before, "nothing may be persisted"
    finally:
        _clear(db, "conversion_")
        db.close()


@pytest.mark.parametrize("length", [CONVERSION_MAX + 1, 200, 5000])
def test_much_longer_names_are_refused_without_a_server_error(client, api, length):
    db = new_session()
    try:
        before = len(_events(db, "conversion_"))
        res = client.post(f"{api}/marketing/conversion", headers=_internal(),
                          json={"event_name": "A" * length})
        assert res.status_code == 422, res.text
        assert len(_events(db, "conversion_")) == before
    finally:
        _clear(db, "conversion_")
        db.close()


@pytest.mark.parametrize("payload", [
    {"event_name": "Lead<script>"},
    {"event_name": "Lead With Spaces"},
    {"event_name": "Lead\nDrop"},
    {"event_name": "Lead;DROP"},
    {"event_name": "Purchase"},
    {"event_name": "CompleteRegistration"},
    {"event_name": "SubmitApplication"},
    {"event_name": "Schedule"},
    {"event_name": "Contact"},
    {"event_name": "ViewContent"},
    {"event_name": "custom_event-2"},
])
def test_recognised_names_are_accepted_and_shapes_that_are_not_names_are_refused(
        client, api, payload):
    db = new_session()
    try:
        res = client.post(f"{api}/marketing/conversion", headers=_internal(), json=payload)
        if " " in payload["event_name"] or any(c in payload["event_name"] for c in "<\n;"):
            assert res.status_code == 422, res.text
        else:
            assert res.status_code in (200, 201), res.text
    finally:
        _clear(db, "conversion_")
        db.close()


def test_unicode_and_control_characters_are_refused(client, api):
    for name in ["Лид", "成約", "Lead\u0000", "Lead\u200b", "  "]:
        res = client.post(f"{api}/marketing/conversion", headers=_internal(),
                          json={"event_name": name})
        assert res.status_code == 422, f"{name!r} -> {res.status_code}"


def test_a_missing_event_name_falls_back_to_the_documented_default(client, api):
    db = new_session()
    try:
        res = client.post(f"{api}/marketing/conversion", headers=_internal(), json={})
        assert res.status_code in (200, 201), res.text
        assert any(row.name == "conversion_lead" for row in _events(db, "conversion_"))
    finally:
        _clear(db, "conversion_")
        db.close()


@pytest.mark.parametrize("value", [None, 12345, True, ["Lead"], {"name": "Lead"}])
def test_wrong_types_are_refused(client, api, value):
    res = client.post(f"{api}/marketing/conversion", headers=_internal(),
                      json={"event_name": value})
    assert res.status_code == 422, res.text


def test_null_event_name_is_refused(client, api):
    db = new_session()
    try:
        res = client.post(f"{api}/marketing/conversion", headers=_internal(),
                          json={"event_name": None})
        assert res.status_code == 422, res.text
        assert not _events(db, "conversion_")
    finally:
        _clear(db, "conversion_")
        db.close()


# --------------------------------------------------------------------- currency
@pytest.mark.parametrize("code", ["BDT", "usd", "EUR"])
def test_a_lowercase_or_padded_currency_is_normalised(client, api, code):
    db = new_session()
    try:
        res = client.post(f"{api}/marketing/conversion", headers=_internal(),
                          json={"event_name": "Lead", "currency": code})
        assert res.status_code in (200, 201), res.text
        row = _events(db, "conversion_")[0]
        assert str(row.meta["currency"]).isupper()
        assert len(str(row.meta["currency"])) == 3
    finally:
        _clear(db, "conversion_")
        db.close()


@pytest.mark.parametrize("code", ["", "US", "USDD", "123", "$", "U S", " gbp ", "B\u0147T", None, 42])
def test_an_invalid_currency_is_refused(client, api, code):
    db = new_session()
    try:
        res = client.post(f"{api}/marketing/conversion", headers=_internal(),
                          json={"event_name": "Lead", "currency": code})
        assert res.status_code == 422, f"{code!r} -> {res.status_code}"
        assert not _events(db, "conversion_")
    finally:
        _clear(db, "conversion_")
        db.close()


# =====================================================================
# 2. the other writer of the same column
# =====================================================================
def test_the_track_endpoint_has_the_same_bound(client, api):
    db = new_session()
    try:
        ok = client.post(f"{api}/track", json={"name": "e" * ANALYTICS_EVENT_NAME_MAX,
                                               "path": "/programs"})
        assert ok.status_code == 200, ok.text
        too_long = client.post(f"{api}/track", json={"name": "e" * (ANALYTICS_EVENT_NAME_MAX + 1),
                                                     "path": "/programs"})
        assert too_long.status_code == 422, (
            "the ingestion path had the same 500 and must share the same boundary")
        for bad in ["", "has spaces", "with;semicolon", "<script>", "Лид"]:
            res = client.post(f"{api}/track", json={"name": bad, "path": "/programs"})
            assert res.status_code == 422, f"{bad!r} -> {res.status_code}"
    finally:
        _clear(db, "e", "conversion_")
        db.close()


def test_malformed_track_payload_types_are_refused(client, api):
    for payload in [{"name": 5}, {"name": None}, {"name": ["x"]}, {"path": 5, "name": "ok"}]:
        res = client.post(f"{api}/track", json=payload)
        assert res.status_code == 422, f"{payload} -> {res.status_code}"


# =====================================================================
# 3. the boundary is enforced at the model too, so a bypass fails the same way on both backends
# =====================================================================
def test_the_model_refuses_an_over_long_name_whatever_wrote_it():
    """A writer that skips the request schema gets a clear refusal, not a backend-dependent rule.

    On PostgreSQL this was a `StringDataRightTruncation` 500; on SQLite the same value was stored.
    Both must now refuse, and neither may truncate: a shortened event name is a different event.
    """
    db = new_session()
    try:
        row = AnalyticsEvent(name="x" * ANALYTICS_EVENT_NAME_MAX)
        assert row.name == "x" * ANALYTICS_EVENT_NAME_MAX

        with pytest.raises(ValueError) as excinfo:
            AnalyticsEvent(name="x" * (ANALYTICS_EVENT_NAME_MAX + 1))
        assert "limited to 80" in str(excinfo.value)

        stored = AnalyticsEvent(name="ok_event")
        db.add(stored)
        db.flush()
        with pytest.raises(ValueError):
            stored.name = "y" * (ANALYTICS_EVENT_NAME_MAX + 50)
        assert stored.name == "ok_event", "a refused assignment must not change the row"
        db.rollback()
    finally:
        db.close()
