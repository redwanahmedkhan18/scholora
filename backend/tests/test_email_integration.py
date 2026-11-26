"""Email in the real workflows: the MFA security notice, the admin test action and the CLI.

These tests run against the application itself (Temporary SQLite, seeded) with a fake SMTP
server, so what is asserted is the behaviour a user would get:

* a two-factor change still succeeds when the mailbox is broken - and says so;
* exactly one email is produced per event, and it does not appear twice in the inbox;
* a delivery failure is recorded, never reported as success;
* the test action is ADMIN-only, cannot be aimed at somebody else's address, and sends
  nothing when asked only to verify.
"""
from __future__ import annotations

import uuid

import pytest
from app.core import mfa
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.crm import Notification, User

from smtp_fake import FakeSMTP, configure, unconfigure

PASSWORD = "Counselor@Test-Password-1"


def _make_counselor(email: str) -> User:
    """A fresh staff account. MFA is not yet enrolled, so the enrolment flow can be driven."""
    db = SessionLocal()
    try:
        user = User(full_name="Email Test Counselor", email=email, role="COUNSELOR",
                    password_hash=hash_password(PASSWORD), is_active=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user
    finally:
        db.close()


def _notifications(email: str) -> list[Notification]:
    db = SessionLocal()
    try:
        rows = (db.query(Notification)
                .filter(Notification.recipient == email)
                .order_by(Notification.id).all())
        for row in rows:
            db.expunge(row)
        return rows
    finally:
        db.close()


@pytest.fixture
def counselor(client, api):
    """A logged-in counselor, plus the token that enrolment is allowed to use."""
    email = f"counselor-{uuid.uuid4().hex[:8]}@example.org"
    _make_counselor(email)
    res = client.post(f"{api}/admin/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    body = res.json()
    token = body.get("access_token")
    assert token, body
    return {"email": email, "headers": {"Authorization": f"Bearer {token}"}, "token": token}


def _enrol_and_confirm(client, api, counselor) -> dict:
    """Enrol an authenticator and confirm it. Returns the confirm response (with a session)."""
    res = client.post(f"{api}/admin/auth/mfa/enroll", headers=counselor["headers"])
    assert res.status_code == 200, res.text
    secret = res.json()["secret"]
    res = client.post(f"{api}/admin/auth/mfa/confirm",
                      json={"code": mfa.current_code(secret)}, headers=counselor["headers"])
    assert res.status_code == 200, res.text
    return res.json()


# --------------------------------------------------------------------------- MFA workflow


def test_enabling_mfa_emails_the_account_holder(client, api, counselor, monkeypatch):
    configure(monkeypatch)
    _enrol_and_confirm(client, api, counselor)

    rows = _notifications(counselor["email"])
    inbox = [r for r in rows if r.channel != "EMAIL"]
    mail = [r for r in rows if r.channel == "EMAIL"]

    assert len(inbox) == 1 and inbox[0].trigger == "MFA_CHANGED"
    assert inbox[0].status == "SENT"
    assert len(mail) == 1, rows
    assert mail[0].status == "SENT", mail[0].status
    assert mail[0].sent_at is not None
    # One message actually left the building, addressed to the account holder, with branding.
    delivered = FakeSMTP.instances[-1].messages
    assert len(delivered) == 1
    assert delivered[0]["To"] == counselor["email"]
    body = delivered[0].get_body(preferencelist=("plain",)).get_content()
    assert "Two-factor authentication was turned on" in body
    assert "Scholara" in body


def test_email_copy_does_not_duplicate_the_inbox_item(client, api, counselor, monkeypatch):
    configure(monkeypatch)
    before = {n.id for n in _notifications(counselor["email"])}
    confirmed = _enrol_and_confirm(client, api, counselor)
    # The confirm response hands back a real session; the same TOTP step cannot be replayed.
    headers = {"Authorization": f"Bearer {confirmed['access_token']}"}

    # The inbox endpoint lists this user's rows *and* broadcast rows (user_id IS NULL), and the
    # database is shared by every module in the session, so a global count would depend on what ran
    # before. Measure what this event produced instead, then confirm the endpoint exposes it once.
    produced = [n for n in _notifications(counselor["email"]) if n.id not in before]
    inbox_rows = [n for n in produced if n.channel != "EMAIL"]
    mail_rows = [n for n in produced if n.channel == "EMAIL"]
    assert len(inbox_rows) == 1 and inbox_rows[0].title == "Two-factor authentication changed", produced
    assert len(mail_rows) == 1, produced        # the email copy is a delivery record, not a second item

    inbox = client.get(f"{api}/admin/notifications/inbox", headers=headers).json()
    shown = [i for i in inbox["items"] if i["id"] == inbox_rows[0].id]
    assert len(shown) == 1, inbox               # the event appears in the inbox exactly once
    assert inbox["unread"] == 1, inbox          # the email copy must not inflate the badge


def test_an_unconfigured_mailbox_leaves_the_notice_queued_and_the_change_applied(
        client, api, counselor, monkeypatch):
    unconfigure(monkeypatch)
    _enrol_and_confirm(client, api, counselor)

    rows = _notifications(counselor["email"])
    mail = [r for r in rows if r.channel == "EMAIL"]
    assert len(mail) == 1
    assert mail[0].status == "QUEUED"          # honest: nothing was accepted by a server
    assert mail[0].sent_at is None
    assert "[not sent:" in (mail[0].body or "")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == counselor["email"]).one()
        assert user.mfa_confirmed_at is not None      # the security change itself went through
    finally:
        db.close()


def test_a_broken_mailbox_does_not_fail_the_security_change(client, api, counselor, monkeypatch):
    import smtplib

    configure(monkeypatch)
    FakeSMTP.pending_behaviour = [smtplib.SMTPAuthenticationError(535, b"bad app password")]

    _enrol_and_confirm(client, api, counselor)          # must not raise / 500

    mail = [r for r in _notifications(counselor["email"]) if r.channel == "EMAIL"]
    assert len(mail) == 1
    assert mail[0].status == "FAILED"
    assert mail[0].sent_at is None
    # The reason is on the record, not only in the log, so an operator can act on it.
    assert "App Password" in (mail[0].body or "")


# --------------------------------------------------------------------------- admin test action


def test_admin_smtp_test_reports_missing_configuration(client, api, auth, monkeypatch):
    unconfigure(monkeypatch)

    res = client.post(f"{api}/admin/config/test/smtp", headers=auth)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is False
    assert "SMTP_HOST" in body["detail"]


def test_admin_smtp_test_verifies_without_sending(client, api, auth, monkeypatch):
    configure(monkeypatch)

    res = client.post(f"{api}/admin/config/test/smtp", headers=auth)

    body = res.json()
    assert body["ok"] is True, body
    assert "Connected to smtp.gmail.com:587" in body["detail"]
    assert "send_message" not in FakeSMTP.instances[-1].calls


def test_admin_smtp_test_sends_to_the_callers_own_address(client, api, auth, monkeypatch):
    configure(monkeypatch)

    res = client.post(f"{api}/admin/config/test/smtp", headers=auth,
                      json={"to": "admin@scholara.com.bd"})

    body = res.json()
    assert body["ok"] is True, body
    assert body["attempts"] == 1
    assert FakeSMTP.instances[-1].messages[0]["To"] == "admin@scholara.com.bd"


def test_admin_smtp_test_cannot_mail_a_third_party(client, api, auth, monkeypatch):
    configure(monkeypatch)

    res = client.post(f"{api}/admin/config/test/smtp", headers=auth,
                      json={"to": "victim@example.org"})

    body = res.json()
    assert body["ok"] is False
    assert "your own account" in body["detail"]
    assert FakeSMTP.instances == []            # no connection was even opened


def test_smtp_test_requires_authentication(client, api):
    res = client.post(f"{api}/admin/config/test/smtp")
    assert res.status_code in (401, 403), res.status_code


def test_smtp_test_is_audited(client, api, auth, monkeypatch):
    from app.models.crm import AuditLog

    configure(monkeypatch)
    client.post(f"{api}/admin/config/test/smtp", headers=auth)

    db = SessionLocal()
    try:
        row = (db.query(AuditLog).filter(AuditLog.action == "TEST_INTEGRATION")
               .order_by(AuditLog.id.desc()).first())
        assert row is not None and row.detail.get("target") == "smtp"
    finally:
        db.close()


# --------------------------------------------------------------------------- CLI


def test_cli_reports_status_and_exit_code(monkeypatch, capsys):
    from app import mgmt_admin

    unconfigure(monkeypatch)
    assert mgmt_admin.main(["--email-status"]) == 1
    out = capsys.readouterr().out
    assert "configured : no" in out
    assert "SMTP_HOST" in out


def test_cli_sends_a_test_message_without_leaking_the_password(monkeypatch, capsys):
    from app import mgmt_admin

    configure(monkeypatch)
    assert mgmt_admin.main(["--send-test-email", "ops@example.org"]) == 0
    out = capsys.readouterr().out
    assert "Accepted by the server for o***s@example.org" in out
    assert "abcd efgh ijkl mnop" not in out


def test_cli_reset_of_mfa_emails_the_account_holder(monkeypatch, capsys):
    from app import mgmt_admin

    email = f"cli-reset-{uuid.uuid4().hex[:8]}@example.org"
    _make_counselor(email)
    configure(monkeypatch)

    assert mgmt_admin.main(["--reset-mfa", email]) == 0

    mail = [r for r in _notifications(email) if r.channel == "EMAIL"]
    assert len(mail) == 1
    assert mail[0].trigger == "MFA_RESET"
    assert mail[0].status == "SENT"
    assert mail[0].sent_at is not None
