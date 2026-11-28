"""W7-T3: contact consent is explicit, affirmative and enforced by the server.

The product requirement is that a student is only contacted after they have actively agreed.
Enforcement therefore lives at the API boundary, not in the browser: a direct caller, an older
cached bundle, or a hand-written request must all be refused the same way.

Every applicable form is covered here:

    LeadForm           POST /leads              -> Lead
    Assessment         POST /leads              -> Lead
    AppointmentForm    POST /appointments       -> Appointment + Lead (side effect)
    EventRegister      POST /events/register    -> EventRegistration + Lead (side effect)
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from app.core.database import SessionLocal
from app.models.content import Event, EventRegistration
from app.models.crm import Appointment, Lead
from sqlalchemy import func, select

CONSENT_MSG = "we need your permission"


# --------------------------------------------------------------------- helpers
def session():
    return SessionLocal()


def count(model, **where) -> int:
    with session() as db:
        stmt = select(func.count()).select_from(model)
        for key, value in where.items():
            stmt = stmt.where(getattr(model, key) == value)
        return int(db.execute(stmt).scalar_one())


def latest(model, order_col):
    with session() as db:
        return db.execute(select(model).order_by(order_col.desc())).scalars().first()


def _future(days: int = 2) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def lead_payload(**over) -> dict:
    body = {"full_name": "Consent Probe", "phone": "+8801712300001"}
    body.update(over)
    return body


def appointment_payload(**over) -> dict:
    body = {
        "full_name": "Consent Probe", "phone": "+8801712300002",
        "scheduled_date": _future(), "scheduled_time": "10:00",
    }
    body.update(over)
    return body


def any_event_id() -> int:
    with session() as db:
        eid = db.execute(select(Event.id).order_by(Event.id)).scalars().first()
    assert eid is not None, "seed data must contain an event"
    return eid


def event_payload(**over) -> dict:
    body = {"event_id": any_event_id(), "full_name": "Consent Probe", "phone": "+8801712300003"}
    body.update(over)
    return body


# Path -> (endpoint, payload factory, table that must grow)
PATHS = [
    ("leads", "/leads", lead_payload),
    ("appointments", "/appointments", appointment_payload),
    ("events", "/events/register", event_payload),
]


# ------------------------------------------------- 1. affirmative consent works
@pytest.mark.parametrize("label,path,make", PATHS)
def test_affirmative_consent_is_accepted_and_persisted(client, api, label, path, make):
    res = client.post(f"{api}{path}", json=make(consent_contact=True))
    assert res.status_code == 200, res.text

    if label == "leads":
        row = latest(Lead, Lead.id)
        assert row.consent_contact is True
    elif label == "appointments":
        row = latest(Appointment, Appointment.id)
        assert row.consent_contact is True
        # The side-effect lead is a contact record too, and must carry the same decision.
        assert latest(Lead, Lead.id).consent_contact is True
    else:
        row = latest(EventRegistration, EventRegistration.id)
        assert row.consent_contact is True
        assert latest(Lead, Lead.id).consent_contact is True


# ------------------------------------- 2, 3, 4. missing / false / malformed fail
@pytest.mark.parametrize("label,path,make", PATHS)
@pytest.mark.parametrize("case,value", [
    ("missing", object()),
    ("false", False),
    ("null", None),
    ("string_true", "true"),
    ("int_one", 1),
    ("list", [True]),
    ("object", {"agreed": True}),
])
def test_consent_must_be_literally_true(client, api, label, path, make, case, value):
    payload = make()
    if case != "missing":
        payload["consent_contact"] = value

    leads_before = count(Lead, phone=payload["phone"])
    res = client.post(f"{api}{path}", json=payload)
    assert res.status_code == 422, f"{label}/{case} should be refused, got {res.status_code}"

    detail = str(res.json())
    # The refusal always names the field. The friendly sentence comes from our validator and so
    # is attached to an explicit false; a missing or malformed value is rejected by the type
    # system before it ever reaches that code path.
    assert "consent_contact" in detail, detail
    if case == "false":
        assert CONSENT_MSG in detail.lower(), detail

    # A refused submission must not create any contact record, including the side effects.
    assert count(Lead, phone=payload["phone"]) == leads_before, f"{label}/{case} created a lead"


@pytest.mark.parametrize("label,path,make", PATHS)
def test_a_refused_submission_creates_no_row_at_all(client, api, label, path, make):
    model = {"leads": Lead, "appointments": Appointment, "events": EventRegistration}[label]
    with session() as db:
        before = db.execute(select(func.count()).select_from(model)).scalar_one()
    res = client.post(f"{api}{path}", json=make())
    assert res.status_code == 422
    with session() as db:
        after = db.execute(select(func.count()).select_from(model)).scalar_one()
    assert after == before


# ------------------------------ 5. no permissive default anywhere in the schema
def test_the_model_does_not_default_consent_to_true():
    """A record created without asking must be 'unknown', never 'consented'."""
    with session() as db:
        row = Lead(reference=f"NO-CONSENT-{os.urandom(4).hex()}", full_name="No Consent",
                   phone="+8801712300009", stage="NEW")
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.consent_contact is None
        db.delete(row)
        db.commit()


def test_admin_created_lead_is_recorded_as_unknown_not_consented(client, api, auth):
    """Staff data entry (a walk-in, say) is not an affirmative act by the student."""
    res = client.post(f"{api}/admin/leads", headers=auth, json={
        "reference": f"ADMIN-{os.urandom(4).hex()}", "full_name": "Walk In",
        "phone": "+8801712300010", "stage": "NEW",
    })
    assert res.status_code == 200, res.text
    row = latest(Lead, Lead.id)
    assert row.consent_contact is None


# ------------------------------------- 6. marketing consent is separate/optional
def test_marketing_consent_is_separate_and_optional(client, api):
    """Contact consent must not imply marketing consent, and marketing must never be required."""
    res = client.post(f"{api}/profiles", json={
        "full_name": "Marketing Probe", "phone": "+8801712300011",
    })
    assert res.status_code == 200, res.text
    token = res.json()["token"]
    profile = client.get(f"{api}/profiles/{token}").json()
    assert profile["consent_marketing"] is False

    res = client.post(f"{api}/profiles", json={
        "full_name": "Marketing Yes", "phone": "+8801712300012", "consent_marketing": True,
    })
    assert res.status_code == 200
    assert client.get(f"{api}/profiles/{res.json()['token']}").json()["consent_marketing"] is True


def test_contact_consent_alone_does_not_grant_marketing(client, api):
    res = client.post(f"{api}/leads", json=lead_payload(consent_contact=True))
    assert res.status_code == 200
    row = latest(Lead, Lead.id)
    assert row.consent_contact is True
    # The Lead model has no marketing consent field, so nothing was silently granted.
    assert not hasattr(row, "consent_marketing")


# ---------------------------------------------------- 7. no bypass via request shape
def test_consent_cannot_be_smuggled_in_a_nested_or_query_parameter(client, api):
    # Nested inside the optional profile object.
    res = client.post(f"{api}/leads", json=lead_payload(
        profile={"full_name": "Nested", "consent_contact": True}))
    assert res.status_code == 422, res.text

    # As a query parameter instead of a body field.
    res = client.get(f"{api}/leads", params={"consent_contact": "true"})
    assert res.status_code in (404, 405)

    res = client.post(f"{api}/leads?consent_contact=true", json=lead_payload())
    assert res.status_code == 422, "query strings must not satisfy a body requirement"


def test_consent_cannot_be_smuggled_through_a_form_encoded_body(client, api):
    res = client.post(f"{api}/leads", data={"full_name": "Form", "phone": "+8801712300013",
                                            "consent_contact": "true"})
    assert res.status_code in (415, 422)


def test_the_unrelated_lead_validations_still_work(client, api):
    """Consent enforcement must not have replaced the other guards."""
    res = client.post(f"{api}/leads", json=lead_payload(consent_contact=True, unexpected_field=1))
    assert res.status_code == 200, "unknown top-level fields are ignored for leads (unchanged)"


# ------------------------------- 8. historical/unknown values are never rewritten
def test_unknown_historical_consent_is_not_converted_to_consented(client, api):
    from sqlalchemy import text

    with session() as db:
        db.execute(text(
            "insert into leads (reference, full_name, phone, source, stage, priority, utm, "
            "consent_contact, created_at, updated_at) values "
            # CURRENT_TIMESTAMP is spelled identically by SQLite and PostgreSQL, so this history
            # fixture works on either server rather than only on the one it was written for.
            "(:ref,'Historical Person','+8801712300014','WEBSITE','NEW','NORMAL','{}',NULL,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"), {"ref": f"HIST-{os.urandom(4).hex()}"})
        db.commit()

    # Unrelated activity must not touch it.
    client.post(f"{api}/leads", json=lead_payload(consent_contact=True))
    client.get(f"{api}/leads")

    with session() as db:
        remaining = db.execute(text(
            "select consent_contact from leads where full_name='Historical Person'")).scalars().all()
    assert remaining == [None], "a NULL historical value must stay NULL"


# ------------------------------------------------ 9. the DB really allows 'unknown'
def test_every_contact_table_can_store_unknown(client, api, db):
    from app.core.database import engine
    from sqlalchemy import inspect

    cols = {
        "leads": "consent_contact",
        "appointments": "consent_contact",
        "event_registrations": "consent_contact",
    }
    inspector = inspect(engine)
    for table, column in cols.items():
        present = {c["name"]: c for c in inspector.get_columns(table)}
        assert column in present, f"{table} has no {column}"
        assert present[column]["nullable"] is True, f"{table}.{column} must be nullable"
