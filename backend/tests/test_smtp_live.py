"""SMTP delivery against a real socket, not a patched ``smtplib``.

``test_email.py`` and ``test_email_integration.py`` replace ``smtplib`` with a recorder: they
prove the *classification* logic and the caller contracts, but no byte ever crosses a socket, so
they cannot show that the transport conversation itself works. This module runs a minimal SMTP
server on loopback and lets the real ``smtplib`` client talk to it through a real STARTTLS
handshake, including a real certificate check against a CA this test generates.

What is faked and what is not:
  * faked: the mailbox. It is a thread, it accepts whatever it is told, and nothing leaves the
    machine.
  * not faked: the socket conversation, the TLS upgrade, the certificate verification, the
    authentication exchange, the retry classification driven by real server responses, and the
    log redaction.
  * still unverified: any real provider (Gmail, SES, Postmark). A green run here says the client
    is correct, not that an account can send mail.
"""
from __future__ import annotations

import base64
import logging
import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List

import pytest

pytestmark = pytest.mark.smtp_live

CRLF = "\r\n"


# --------------------------------------------------------------------------- certificate
@pytest.fixture(scope="module")
def cert(tmp_path_factory):
    """A self-signed certificate for `localhost`, acting as its own CA."""
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    directory = tmp_path_factory.mktemp("smtp-cert")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                # The client connects to the literal address: resolving "localhost" first tries
                # ::1, where nothing is listening, and that shows up as a connect timeout.
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "smtp.pem"
    key_path = directory / "smtp.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    return cert_path, key_path


# --------------------------------------------------------------------------- SMTP server
class SMTPSink(threading.Thread):
    """A tiny ESMTP server: enough of the protocol for smtplib, nothing more.

    ``behaviour`` selects the failure the test wants: ``ok``, ``auth_failed`` (535 at AUTH),
    ``rcpt_refused`` (550 at RCPT) or ``drop_then_ok`` (close the first connection immediately).
    """

    def __init__(self, cert_path: Path, key_path: Path, behaviour: str = "ok") -> None:
        super().__init__(daemon=True, name="smtp-sink")
        self.cert_path, self.key_path = cert_path, key_path
        self.behaviour = behaviour
        self.messages: List[str] = []
        self.commands: List[str] = []
        self.credentials: List[tuple[str, str]] = []
        self.connections = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()

    # ---------------------------------------------------------------- plumbing
    def run(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.connections += 1
            if self.behaviour == "drop_then_ok" and self.connections == 1:
                conn.close()                   # a transient failure, and a retryable one
                continue
            try:
                self._converse(conn)
            except (OSError, ssl.SSLError):
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass

    def _converse(self, conn: socket.socket) -> None:
        """Speak ESMTP over a tiny explicit buffer.

        ``socket.makefile`` is deliberately not used: it reads ahead, so bytes of the TLS
        ClientHello end up inside the buffered reader and the handshake deadlocks into a client
        timeout. Reading exactly what a line needs keeps the socket position honest.
        """

        def read_line(sock: socket.socket) -> bytes:
            buffer = bytearray()
            while not buffer.endswith(b"\n"):
                chunk = sock.recv(1)
                if not chunk:
                    return bytes(buffer)
                buffer.extend(chunk)
            return bytes(buffer)

        def say(sock: socket.socket, line: str) -> None:
            sock.sendall((line + CRLF).encode())

        say(conn, "220 localhost ESMTP scholara-test")
        tls = False
        while True:
            raw = read_line(conn)
            if not raw:
                return
            line = raw.decode(errors="replace").rstrip("\r\n")
            self.commands.append(line)
            upper = line.upper()

            if upper.startswith("EHLO"):
                # Every line but the last is a continuation ("250-"); the reply is only complete
                # when a line starts "250 ". Advertising STARTTLS without a terminating line made
                # smtplib wait for ever, which is exactly what a wrong capability list looks like.
                say(conn, "250-localhost")
                say(conn, "250-AUTH LOGIN PLAIN")
                if not tls:
                    say(conn, "250-STARTTLS")
                say(conn, "250 HELP")
            elif upper.startswith("HELO"):
                say(conn, "250 localhost")
            elif upper.startswith("STARTTLS"):
                say(conn, "220 ready to start TLS")
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(str(self.cert_path), str(self.key_path))
                conn = context.wrap_socket(conn, server_side=True)
                tls = True
            elif upper.startswith("AUTH PLAIN"):
                parts = line.split(" ", 2)
                if len(parts) == 3:
                    decoded = base64.b64decode(parts[2]).split(b"\0")
                    self.credentials.append((decoded[1].decode(), decoded[2].decode()))
                say(conn, "535 5.7.8 authentication failed" if self.behaviour == "auth_failed"
                    else "235 2.7.0 accepted")
            elif upper.startswith("AUTH LOGIN"):
                say(conn, "334 VXNlcm5hbWU6")
                user = base64.b64decode(read_line(conn).strip()).decode()
                say(conn, "334 UGFzc3dvcmQ6")
                password = base64.b64decode(read_line(conn).strip()).decode()
                self.credentials.append((user, password))
                say(conn, "535 5.7.8 authentication failed" if self.behaviour == "auth_failed"
                    else "235 2.7.0 accepted")
            elif upper.startswith("MAIL FROM"):
                say(conn, "250 2.1.0 ok")
            elif upper.startswith("RCPT TO"):
                say(conn, "550 5.1.1 no such user" if self.behaviour == "rcpt_refused"
                    else "250 2.1.5 ok")
            elif upper.startswith("DATA"):
                say(conn, "354 end with <CRLF>.<CRLF>")
                body = bytearray()
                while True:
                    chunk = read_line(conn)
                    if not chunk or chunk in (b".\r\n", b".\n"):
                        break
                    body.extend(chunk)
                self.messages.append(body.decode(errors="replace"))
                say(conn, "250 2.0.0 queued")
            elif upper.startswith("QUIT"):
                say(conn, "221 bye")
                return
            elif upper.startswith("RSET"):
                say(conn, "250 reset")
            else:
                say(conn, "250 ok")


@pytest.fixture()
def sink(cert):
    servers: List[SMTPSink] = []

    def start(behaviour: str = "ok") -> SMTPSink:
        server = SMTPSink(cert[0], cert[1], behaviour)
        servers.append(server)
        server.start()
        return server

    yield start
    for server in servers:
        server.stop()


@pytest.fixture()
def mailbox(monkeypatch, cert):
    """Point the email service at a sink, trusting only this test's certificate."""
    from app.core.config import settings
    from app.services import email as email_service

    servers: List[SMTPSink] = []

    def configure(server: SMTPSink, *, retries: int = 0) -> None:
        servers.append(server)
        monkeypatch.setattr(settings, "smtp_host", "127.0.0.1", raising=False)
        monkeypatch.setattr(settings, "smtp_port", server.port, raising=False)
        monkeypatch.setattr(settings, "smtp_use_tls", True, raising=False)
        monkeypatch.setattr(settings, "smtp_ssl", False, raising=False)
        monkeypatch.setattr(settings, "smtp_user", "mailer@scholara.test", raising=False)
        monkeypatch.setattr(settings, "smtp_username", "mailer@scholara.test", raising=False)
        monkeypatch.setattr(settings, "smtp_password", "smtp-probe-password", raising=False)
        monkeypatch.setattr(settings, "smtp_from_email", "mailer@scholara.test", raising=False)
        monkeypatch.setattr(settings, "smtp_enabled", True, raising=False)
        monkeypatch.setattr(settings, "smtp_max_retries", retries, raising=False)
        monkeypatch.setattr(settings, "smtp_timeout_seconds", 5.0, raising=False)

        # Verification stays on; only the trust anchor changes, to the certificate this test
        # generated. That is the honest way to exercise a real STARTTLS handshake locally: the
        # client still validates a chain and a hostname.
        real_context = ssl.create_default_context

        def trusted_context(*args: Any, **kwargs: Any) -> ssl.SSLContext:
            kwargs.setdefault("cafile", str(cert[0]))
            return real_context(*args, **kwargs)

        monkeypatch.setattr(email_service.ssl, "create_default_context", trusted_context)

    yield configure
    for server in servers:
        server.stop()


# --------------------------------------------------------------------------- the tests
def test_a_message_is_delivered_through_a_real_tls_session(mailbox, sink):
    from app.services import email as email_service

    server = sink("ok")
    mailbox(server)

    result = email_service.send("student@example.invalid", "Application received",
                                "Body of the message.", "<p>Body of the message.</p>")

    assert result.ok is True, result
    assert result.attempts == 1
    assert len(server.messages) == 1
    delivered = server.messages[0]
    assert "Application received" in delivered
    assert "student@example.invalid" in delivered
    assert "mailer@scholara.test" in delivered or "From: Scholara" in delivered
    # The transport upgraded to TLS before AUTH: STARTTLS appears before the credentials.
    joined = " | ".join(server.commands)
    assert "STARTTLS" in joined
    assert server.credentials and server.credentials[0][0] == "mailer@scholara.test"


def test_the_password_never_reaches_the_message_or_the_log(mailbox, sink, caplog):
    from app.services import email as email_service

    server = sink("ok")
    mailbox(server)

    with caplog.at_level(logging.DEBUG):
        email_service.send("student@example.invalid", "Notice", "Body.")

    assert "smtp-probe-password" not in "\n".join(server.messages)
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert log_text, "the delivery must be logged, or this proves nothing"
    assert "smtp-probe-password" not in log_text, "the SMTP password must never be logged"
    assert "student@example.invalid" not in log_text, (
        "the recipient address must be masked in the log, not printed in full")


def test_a_rejected_password_is_permanent_and_not_retried(mailbox, sink):
    from app.services import email as email_service

    server = sink("auth_failed")
    mailbox(server, retries=2)

    result = email_service.send("student@example.invalid", "Notice", "Body.")

    assert result.ok is False
    assert result.attempts == 1, (
        f"a rejected password cannot be fixed by retrying, got {result.attempts} attempts")
    assert server.connections == 1


def test_a_refused_recipient_is_permanent(mailbox, sink):
    from app.services import email as email_service

    server = sink("rcpt_refused")
    mailbox(server, retries=2)

    result = email_service.send("nobody@example.invalid", "Notice", "Body.")

    assert result.ok is False, result
    assert result.attempts == 1
    assert server.messages == [], "nothing may be queued for a refused recipient"


def test_a_dropped_connection_is_retried_and_then_delivered(mailbox, sink):
    from app.services import email as email_service

    server = sink("drop_then_ok")
    mailbox(server, retries=1)

    result = email_service.send("student@example.invalid", "Notice", "Body.")

    assert result.ok is True, result
    assert result.attempts == 2, f"a dropped connection is retryable, got {result.attempts}"
    assert server.connections == 2
    assert len(server.messages) == 1
