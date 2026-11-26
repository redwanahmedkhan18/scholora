"""A fake SMTP server for tests: no sockets, no real mail, full visibility of the calls.

Shared by ``test_email.py`` (transport behaviour) and ``test_email_integration.py`` (the
security-notice workflow and the admin test action), so both assert against the same
recording of what the code actually did.
"""
from __future__ import annotations

import smtplib

from app.core.config import settings
from app.services import email as email_service


class FakeSMTP:
    """Stand-in for ``smtplib.SMTP`` / ``SMTP_SSL`` that records the call sequence.

    ``pending_behaviour`` holds exceptions to raise, one per attempt: ``send_message`` (or
    ``login``, if the first entry is an auth error) raises it, then it is consumed. Once the
    list is empty the connection succeeds, which is what makes retry assertions deterministic.
    """

    instances: list["FakeSMTP"] = []
    pending_behaviour: list[BaseException] = []

    def __init__(self, host, port, *args, **kwargs):
        self.host = host
        self.port = port
        self.timeout = kwargs.get("timeout")
        self.context = kwargs.get("context")
        self.calls: list[str] = []
        self.logged_in: tuple[str, str] | None = None
        self.messages: list = []
        # The same list object, so a raise on one attempt is not replayed on the next.
        self.behaviour: list[BaseException] = FakeSMTP.pending_behaviour
        self.noop_code = 250
        FakeSMTP.instances.append(self)

    def ehlo(self):
        self.calls.append("ehlo")
        return 250, b"ok"

    def starttls(self, context=None):
        self.calls.append("starttls")
        self.context = context
        return 220, b"ready to start TLS"

    def login(self, user, password):
        self.calls.append("login")
        if self.behaviour and isinstance(self.behaviour[0], smtplib.SMTPAuthenticationError):
            raise self.behaviour.pop(0)
        self.logged_in = (user, password)
        return 235, b"accepted"

    def send_message(self, message):
        self.calls.append("send_message")
        if self.behaviour:
            raise self.behaviour.pop(0)
        self.messages.append(message)
        return {}

    def noop(self):
        self.calls.append("noop")
        return self.noop_code, b"ok"

    def quit(self):
        self.calls.append("quit")
        return 221, b"bye"

    def close(self):
        self.calls.append("close")


def configure(monkeypatch, **overrides) -> type[FakeSMTP]:
    """Point settings at a working mailbox and install the fake transport."""
    FakeSMTP.instances = []
    FakeSMTP.pending_behaviour = []
    values = {
        "smtp_enabled": True,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "scholara.notifications@gmail.com",
        "smtp_username": "",
        "smtp_password": "abcd efgh ijkl mnop",
        "smtp_from_email": "",
        "smtp_from_name": "",
        "smtp_use_tls": True,
        "smtp_ssl": False,
        "smtp_timeout_seconds": 5.0,
        "smtp_max_retries": 1,
        "public_site_url": "https://scholara.com.bd",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(email_service, "RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    return FakeSMTP


def unconfigure(monkeypatch) -> None:
    """The state of a fresh environment: no mailbox at all."""
    for key, value in {
        "smtp_host": "", "smtp_user": "", "smtp_username": "", "smtp_password": "",
        "smtp_from_email": "", "smtp_enabled": True,
    }.items():
        monkeypatch.setattr(settings, key, value)
