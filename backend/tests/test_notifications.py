"""Outbound notification delivery (§60).

The WhatsApp branch is the only one that crosses from synchronous request/task code into an
async HTTP client. These tests pin the three behaviours that used to be wrong: delivery from
inside a running event loop, HTTP rejections being marked FAILED, and missing credentials
leaving the row QUEUED (never silently SENT).
"""
from __future__ import annotations

import asyncio

import app.models  # noqa: F401  (registers every table before create_all)
from app.core.config import settings
from app.core.database import Base
from app.models.crm import Notification
from app.services import notifications, whatsapp
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _note(channel: str = "WHATSAPP") -> Notification:
    return Notification(channel=channel, recipient="+8801700000000", trigger="LEAD_CREATED",
                        title="Hello", body="A counselor will contact you.", status="QUEUED")


def _configure_whatsapp(monkeypatch, send):
    monkeypatch.setattr(settings, "whatsapp_access_token", "test-token")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "test-phone-id")
    monkeypatch.setattr(whatsapp, "send_message", send)


def test_whatsapp_delivery_from_sync_code_marks_sent(monkeypatch):
    db = _session()
    sent: dict[str, str] = {}

    async def fake_send(to, body):
        sent["to"], sent["body"] = to, body
        return {"status": 200, "response": {}}

    _configure_whatsapp(monkeypatch, fake_send)
    note = _note()
    db.add(note)
    db.commit()

    notifications._deliver(db, note)

    assert note.status == "SENT"
    assert note.sent_at is not None
    assert sent["to"] == "+8801700000000"
    assert "A counselor will contact you." in sent["body"]
    db.close()


def test_whatsapp_delivery_inside_a_running_event_loop_still_sends(monkeypatch):
    """An async endpoint calling notification code must not die on asyncio.run()."""
    db = _session()

    async def fake_send(to, body):
        return {"status": 200, "response": {}}

    _configure_whatsapp(monkeypatch, fake_send)
    note = _note()
    db.add(note)
    db.commit()

    async def call_from_loop():
        notifications._deliver(db, note)

    asyncio.run(call_from_loop())

    assert note.status == "SENT", "delivery from an async endpoint must not be marked FAILED"
    assert note.sent_at is not None
    db.close()


def test_whatsapp_http_rejection_marks_failed(monkeypatch):
    db = _session()

    async def fake_send(to, body):
        return {"status": 401, "response": {"error": "invalid token"}}

    _configure_whatsapp(monkeypatch, fake_send)
    note = _note()
    db.add(note)
    db.commit()

    notifications._deliver(db, note)

    assert note.status == "FAILED"
    assert note.sent_at is None
    db.close()


def test_missing_credentials_leave_the_row_queued(monkeypatch):
    db = _session()
    monkeypatch.setattr(settings, "whatsapp_access_token", "")
    note = _note(channel="WHATSAPP")
    db.add(note)
    db.commit()

    notifications._deliver(db, note)

    assert note.status == "QUEUED", "an unconfigured transport must not claim delivery"
    assert note.sent_at is None
    db.close()


def test_website_notification_is_delivered_by_being_readable(monkeypatch):
    db = _session()
    note = _note(channel="WEBSITE")
    db.add(note)
    db.commit()

    notifications._deliver(db, note)

    assert note.status == "SENT"
    assert note.sent_at is not None
    db.close()
