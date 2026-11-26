"""Outbound email: configuration, transport behaviour and the security-notice workflow.

Every test here runs against a fake SMTP server object, so the suite never opens a socket and
never sends a real message. The fake records the exact call sequence, which is how the TLS,
authentication and retry behaviour is asserted rather than assumed.
"""
from __future__ import annotations

import logging
import smtplib
import ssl

import pytest
from app.core.config import settings
from app.services import email as email_service
from app.services import email_templates

from smtp_fake import FakeSMTP  # noqa: F401 - fixtures `smtp`/`unconfigured` live in conftest

# --------------------------------------------------------------------------- configuration


def test_missing_settings_are_named_not_hidden(unconfigured):
    state = email_service.status()

    assert state["configured"] is False
    assert set(state["missing"]) == {"SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"}
    assert "SMTP_HOST" in state["reason"]
    assert state["verify_certificates"] is True


def test_smtp_username_is_accepted_as_an_alias(smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_user", "")
    monkeypatch.setattr(settings, "smtp_username", "alias@gmail.com")

    assert settings.smtp_login == "alias@gmail.com"
    assert email_service.configured() is True


def test_sender_defaults_to_the_authenticated_account(smtp):
    # Gmail rejects a From that is not the account that logged in, so the default must be it.
    assert settings.smtp_sender_email == "scholara.notifications@gmail.com"
    assert settings.smtp_sender_name == "Scholara"


def test_explicit_from_address_wins(smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_from_email", "hello@scholara.com.bd")
    monkeypatch.setattr(settings, "smtp_from_name", "Scholara Admissions")

    assert settings.smtp_sender_email == "hello@scholara.com.bd"
    assert settings.smtp_sender_name == "Scholara Admissions"


def test_invalid_port_is_rejected_rather_than_used(smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_port", 99999)

    state = email_service.status()

    assert state["port_valid"] is False
    assert state["configured"] is False
    assert "SMTP_PORT" in state["missing"]
    # And nothing is attempted, so the failure is loud instead of a mystery timeout.
    result = email_service.send("ops@example.org", "x", "y")
    assert result.ok is False
    assert "SMTP_PORT" in result.error


def test_plaintext_mode_is_refused(smtp, monkeypatch):
    # A mail client that silently speaks plaintext leaks the password on any shared network.
    monkeypatch.setattr(settings, "smtp_use_tls", False)
    monkeypatch.setattr(settings, "smtp_ssl", False)

    assert settings.smtp_missing == ["SMTP_USE_TLS"]
    assert email_service.send("ops@example.org", "x", "y").ok is False


def test_kill_switch_disables_delivery(smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_enabled", False)

    state = email_service.status()
    assert state["configured"] is False
    assert "switched off" in state["reason"]


def test_status_never_leaks_the_password_or_full_username(smtp):
    state = email_service.status()
    blob = repr(state)

    # The password must never appear. The From address is not a secret - it is on every
    # message the mailbox sends - but the *authenticated username* field is masked.
    assert "abcd efgh ijkl mnop" not in blob
    assert state["username"] == "s***s@gmail.com"


@pytest.mark.parametrize("address,expected", [
    ("scholara.notifications@gmail.com", "s***s@gmail.com"),
    ("ab@x.com", "a@x.com"),
    ("x@y.com", "x@y.com"),
    ("not-an-address", "***"),
    ("", ""),
])
def test_mask_email(address, expected):
    assert email_service.mask_email(address) == expected


# --------------------------------------------------------------------------- transport


def test_starttls_with_verified_context_is_used(smtp):
    result = email_service.send("ops@example.org", "Hello", "Body text")

    assert result.ok is True
    server = smtp.instances[-1]
    assert server.calls[:4] == ["ehlo", "starttls", "ehlo", "login"]
    assert isinstance(server.context, ssl.SSLContext)
    # check_hostname + CERT_REQUIRED is what "we verify certificates" actually means.
    assert server.context.check_hostname is True
    assert server.context.verify_mode == ssl.CERT_REQUIRED
    assert server.logged_in == ("scholara.notifications@gmail.com", "abcd efgh ijkl mnop")
    assert server.timeout == 5.0


def test_message_has_sender_identity_text_and_html(smtp):
    rendered = email_templates.security_notice(
        "Two-factor authentication changed", "Two-factor authentication was turned on.")
    result = email_service.send("ops@example.org", rendered.subject, rendered.text, rendered.html)

    assert result.ok is True
    message = smtp.instances[-1].messages[0]
    assert message["From"] == "Scholara <scholara.notifications@gmail.com>"
    assert message["To"] == "ops@example.org"
    assert message["Message-ID"]
    assert "Two-factor authentication was turned on." in message.get_body(
        preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "Two-factor authentication was turned on." in html
    assert "scholara.com.bd" in html


def test_plain_text_part_always_exists(smtp):
    email_service.send("ops@example.org", "Subject", "Only text")

    message = smtp.instances[-1].messages[0]
    assert message.get_body(preferencelist=("plain",)) is not None
    assert message.get_body(preferencelist=("html",)) is None


def test_no_message_is_sent_when_unconfigured(unconfigured):
    # The old behaviour reported success for every channel with no transport behind it.
    result = email_service.send("ops@example.org", "Subject", "Body")

    assert result.ok is False
    assert result.detail.get("skipped") is True
    assert result.attempts == 0


def test_authentication_failure_is_permanent_and_not_retried(smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_max_retries", 3)
    smtp.pending_behaviour = [smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")]

    result = email_service.send("ops@example.org", "Subject", "Body")

    assert result.ok is False
    assert result.attempts == 1
    assert result.detail.get("permanent") is True
    assert "App Password" in result.error        # tells the operator what to fix


def test_connection_failure_is_retried_then_reported(smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_max_retries", 2)

    def refuse(*args, **kwargs):
        raise smtplib.SMTPServerDisconnected("connection reset")

    monkeypatch.setattr(email_service.smtplib, "SMTP", refuse)

    result = email_service.send("ops@example.org", "Subject", "Body")

    assert result.ok is False
    assert result.attempts == 3                 # 1 + 2 retries
    assert result.detail.get("exhausted") is True


def test_transient_failure_recovers_on_a_later_attempt(smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_max_retries", 2)
    smtp.pending_behaviour = [smtplib.SMTPServerDisconnected("blip")]

    result = email_service.send("ops@example.org", "Subject", "Body")

    assert result.ok is True
    assert result.attempts == 2


def test_timeout_is_classified_as_transient(smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_max_retries", 1)

    def slow(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(email_service.smtplib, "SMTP", slow)

    result = email_service.send("ops@example.org", "Subject", "Body")

    assert result.ok is False
    assert result.attempts == 2
    assert "timed out" in result.error.lower()


def test_refused_sender_is_permanent_and_says_why(smtp):
    smtp.pending_behaviour = [smtplib.SMTPSenderRefused(550, b"5.7.1 From address not allowed",
                                                       "scholara.notifications@gmail.com")]

    result = email_service.send("ops@example.org", "Subject", "Body")

    assert result.ok is False
    assert result.detail.get("permanent") is True
    assert "From address" in result.error


def test_server_data_rejection_5xx_is_permanent(smtp):
    smtp.pending_behaviour = [smtplib.SMTPDataError(554, b"5.7.1 message rejected")]

    result = email_service.send("ops@example.org", "Subject", "Body")

    assert result.ok is False
    assert result.detail.get("permanent") is True


def test_tls_verification_failure_is_never_retried_or_bypassed(smtp, monkeypatch):
    def bad_cert(*args, **kwargs):
        raise ssl.SSLCertVerificationError("certificate verify failed: hostname mismatch")

    monkeypatch.setattr(email_service.ssl, "create_default_context", bad_cert)

    result = email_service.send("ops@example.org", "Subject", "Body")

    assert result.ok is False
    assert result.attempts == 1
    assert "TLS" in result.error or "certificate" in result.error.lower()


def test_password_never_reaches_the_log(smtp, monkeypatch, caplog):
    monkeypatch.setattr(settings, "smtp_max_retries", 0)
    smtp.pending_behaviour = [smtplib.SMTPServerDisconnected(
        "auth failed for abcd efgh ijkl mnop and scholara.notifications@gmail.com")]

    with caplog.at_level(logging.DEBUG):
        email_service.send("ops@example.org", "Subject", "Body")

    blob = caplog.text
    assert "abcd efgh ijkl mnop" not in blob
    assert "scholara.notifications@gmail.com" not in blob


def test_verify_connection_authenticates_without_sending(smtp):
    result = email_service.verify_connection()

    assert result.ok is True
    server = smtp.instances[-1]
    assert "login" in server.calls
    assert "send_message" not in server.calls
    assert "quit" in server.calls


def test_verify_connection_reports_a_bad_password(smtp):
    smtp.pending_behaviour = [smtplib.SMTPAuthenticationError(535, b"nope")]

    result = email_service.verify_connection()

    assert result.ok is False
    assert "App Password" in result.error


# --------------------------------------------------------------------------- templates


def test_templates_never_contain_credentials():
    rendered = email_templates.security_notice("Two-factor reset", "MFA was reset by an operator.")

    for needle in email_templates.FORBIDDEN_SUBSTRINGS:
        assert needle.lower() not in rendered.text.lower()
        assert needle.lower() not in rendered.html.lower()


def test_template_refuses_to_render_a_secret():
    with pytest.raises(ValueError):
        email_templates.security_notice("Enrolled", "Your otpauth:// secret is JBSWY3DPEHPK3PXP")


def test_html_is_escaped():
    rendered = email_templates.security_notice("Notice", "<script>alert(1)</script>")

    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html


def test_action_url_is_absolute_in_the_rendered_email(smtp):
    rendered = email_templates.security_notice("Notice", "Body", action_url="/admin/security")

    assert "https://scholara.com.bd/admin/security" in rendered.text
    assert 'href="https://scholara.com.bd/admin/security"' in rendered.html


# ------------------------------------------------------- settings saved in the admin screen
# `smtp_login`, `smtp_sender_email` and `smtp_configured` are *properties*, and a property runs
# on the base Settings object: `self.smtp_host` there is the environment value, never the
# database override. A mailbox entered in Admin -> Configuration was therefore stored, shown as
# saved, and ignored - the service still reported "Not configured". These tests drive the real
# override path (the same one the admin form uses) and would fail if that regressed.
SMTP_OVERRIDES = {
    "smtp_host": "smtp.gmail.com",
    "smtp_port": "587",
    "smtp_user": "notifications@gmail.com",
    "smtp_password": "abcd efgh ijkl mnop",
    "smtp_use_tls": True,
}


def _apply_overrides(client, api, auth, values):
    from app.core import runtime

    res = client.put(f"{api}/admin/config", json={"values": values}, headers=auth)
    assert res.status_code == 200, res.text
    runtime.invalidate()


def test_smtp_saved_in_the_admin_screen_actually_configures_email(client, api, auth):
    from app.core import runtime

    assert email_service.configured() is False          # nothing in the environment
    try:
        _apply_overrides(client, api, auth, SMTP_OVERRIDES)

        assert settings.smtp_login == SMTP_OVERRIDES["smtp_user"]
        assert settings.smtp_sender_email == SMTP_OVERRIDES["smtp_user"]
        assert settings.smtp_sender_name == "Scholara"
        assert settings.smtp_missing == []
        assert settings.smtp_configured is True
        assert email_service.configured() is True
        assert email_service.status()["security"] == "starttls"
    finally:
        _apply_overrides(client, api, auth, {k: "" for k in SMTP_OVERRIDES})
        runtime.invalidate()
    assert email_service.configured() is False


def test_an_overridden_port_typo_is_refused_before_it_is_saved(client, api, auth):
    from app.core import runtime

    try:
        _apply_overrides(client, api, auth, SMTP_OVERRIDES)
        res = client.put(f"{api}/admin/config", json={"values": {"smtp_port": "99999"}},
                         headers=auth)
        # Refused at the boundary, so an operator cannot save a mailbox that can never work.
        assert res.status_code == 400
        assert "65535" in res.json()["detail"]
        runtime.invalidate()
        assert email_service.configured() is True      # the good value is still in force
    finally:
        _apply_overrides(client, api, auth, {k: "" for k in SMTP_OVERRIDES})
        runtime.invalidate()


def test_a_password_saved_in_the_admin_screen_is_never_readable_back(client, api, auth):
    try:
        _apply_overrides(client, api, auth, SMTP_OVERRIDES)
        listing = client.get(f"{api}/admin/config", headers=auth).text
        assert SMTP_OVERRIDES["smtp_password"] not in listing
        assert "abcd efgh ijkl mnop" not in listing
        assert "abcd efgh ijkl mnop" not in str(email_service.status())
    finally:
        _apply_overrides(client, api, auth, {k: "" for k in SMTP_OVERRIDES})
