"""Outbound email: one authenticated SMTP transport for the whole application.

There was no email delivery before this module. `notifications._deliver` handled the staff
inbox and WhatsApp and then left anything else QUEUED for ever, so a security notification was
recorded as delivered when nothing had been sent. This service is the missing transport, and
it is the only place in the codebase that opens an SMTP connection.

Design decisions, all of which the tests pin down:

* **STARTTLS with verification.** Port 587 + ``starttls()`` is the Gmail recommendation, and the
  TLS context comes from ``ssl.create_default_context()`` - certificates are checked and the
  hostname is verified. There is no configuration switch that turns that off.
* **Honest reporting.** ``send()`` returns a result, and that result says what the *server*
  said. A message counts as delivered only after ``send_message`` is accepted; an
  unconfigured mailbox raises ``EmailNotConfigured`` instead of reporting success.
* **Retry only what a retry can fix.** A dropped connection, a timeout or a 4xx greeting is
  retried with a short backoff. A rejected password, a refused sender, a 5xx at DATA or a
  missing setting is permanent: retrying it would just burn the account's sending quota and
  bury the real cause in the log.
* **No secrets in logs.** Recipients are masked, the password is never formatted into a
  message, and exception text is scrubbed before it is logged or returned.
"""
from __future__ import annotations

import logging
import smtplib
import socket
import ssl
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any, Dict, List, Optional

from app.core.config import settings

log = logging.getLogger(__name__)

#: SMTP status codes that mean "try again later" rather than "this will never work".
TRANSIENT_CODES = frozenset({421, 450, 451, 452})

#: How long to wait before attempt 2, 3, ... Short: a login is not a background job, and the
#: caller (a request) is waiting. Anything longer belongs in a queue we do not have.
RETRY_BACKOFF_SECONDS = (0.5, 1.5)


class EmailError(RuntimeError):
    """Base class for every delivery failure this module raises."""


class EmailNotConfigured(EmailError):
    """No usable SMTP settings. Never retried - there is nothing to connect to."""


class EmailAuthError(EmailError):
    """The server rejected the credentials. Permanent until the password is fixed."""


class EmailTransientError(EmailError):
    """A network-level or 4xx failure that a later attempt may survive."""


class EmailPermanentError(EmailError):
    """The server refused the message itself (sender, recipient, content)."""


@dataclass
class DeliveryResult:
    """What happened, in a form safe to log, store and return to an administrator."""

    ok: bool
    attempts: int = 0
    error: Optional[str] = None
    message_id: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "attempts": self.attempts,
            "error": self.error,
            "message_id": self.message_id,
            **self.detail,
        }


# --------------------------------------------------------------------------- diagnostics
def mask_email(address: Optional[str]) -> str:
    """``scholara.notifications@gmail.com`` -> ``s***s@gmail.com``.

    Enough for an operator to see *which* mailbox is configured, not enough to be a secret.
    """
    value = (address or "").strip()
    if "@" not in value:
        return "***" if value else ""
    local, _, domain = value.partition("@")
    if len(local) <= 2:
        head = local[:1] or "*"
    else:
        head = f"{local[0]}***{local[-1]}"
    return f"{head}@{domain}"


def missing_settings() -> List[str]:
    """Names of the settings that still need a value (never their contents)."""
    return list(settings.smtp_missing)


def configured() -> bool:
    return bool(settings.smtp_configured)


def status() -> Dict[str, Any]:
    """Safe configuration diagnostics for the admin UI, the CLI and the startup log.

    Contains no password, no full username and no connection string. ``reason`` explains why
    email is off, so "nothing is being sent" is never a mystery.
    """
    missing = missing_settings()
    reason = ""
    if not settings.smtp_enabled:
        reason = "Email delivery is switched off (SMTP_ENABLED=false)."
    elif missing:
        reason = f"Not configured: set {', '.join(missing)}."
    return {
        "configured": configured(),
        "enabled": bool(settings.smtp_enabled),
        "reason": reason,
        "missing": missing,
        "host": settings.smtp_host or None,
        "port": settings.smtp_port,
        "port_valid": settings.smtp_port_is_valid,
        "security": "ssl" if settings.smtp_ssl else ("starttls" if settings.smtp_use_tls else "none"),
        "username": mask_email(settings.smtp_login) or None,
        "from_address": settings.smtp_sender_email or None,
        "from_name": settings.smtp_sender_name,
        "timeout_seconds": settings.smtp_timeout_seconds,
        "max_retries": settings.smtp_max_retries,
        "verify_certificates": True,
    }


# --------------------------------------------------------------------------- composition
def build_message(to: str, subject: str, text: str, html: Optional[str] = None,
                  *, reply_to: Optional[str] = None) -> EmailMessage:
    """Assemble a multipart message with a plain-text part always present.

    Plain text is not optional: it is what a screen reader, a plain-text client and the
    spam filters all read first.
    """
    sender_email = settings.smtp_sender_email
    sender_name = settings.smtp_sender_name
    if not sender_email:
        raise EmailNotConfigured("No sender address is configured (SMTP_FROM_EMAIL).")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((sender_name, sender_email))
    message["To"] = to
    message["Message-ID"] = make_msgid(domain=sender_email.split("@")[-1] or None)
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(text)
    if html:
        # Same content, richer rendering. Clients that prefer text keep the text part.
        message.add_alternative(html, subtype="html")
    return message


def _scrub(text: Any) -> str:
    """Remove anything credential-shaped from text that is about to be logged or returned."""
    value = str(text or "")
    password = settings.smtp_password or ""
    if password and password in value:
        value = value.replace(password, "***")
    login = settings.smtp_login or ""
    if login and login in value:
        value = value.replace(login, mask_email(login))
    return value[:400]


# --------------------------------------------------------------------------- transport
def _server() -> smtplib.SMTP:
    """Open and authenticate a connection. Raises a classified EmailError on failure.

    Kept separate from ``send`` so the admin "test connection" action can prove the
    credentials without sending a message anywhere.
    """
    host = (settings.smtp_host or "").strip()
    if not host:
        raise EmailNotConfigured("SMTP_HOST is not set.")
    if not settings.smtp_port_is_valid:
        raise EmailNotConfigured(f"SMTP_PORT {settings.smtp_port!r} is not a valid port.")
    login = settings.smtp_login
    password = settings.smtp_password
    if not login or not password:
        raise EmailNotConfigured("SMTP_USER and SMTP_PASSWORD are both required.")
    if not (settings.smtp_use_tls or settings.smtp_ssl):
        # Refusing is the point: a mail client that silently speaks plaintext would be a
        # credential leak on any shared network.
        raise EmailNotConfigured("Neither STARTTLS nor SSL is enabled; refusing to send credentials in clear text.")

    timeout = max(1.0, float(settings.smtp_timeout_seconds or 10.0))
    try:
        context = ssl.create_default_context()      # verifies chain + hostname, always
    except ssl.SSLError as exc:                     # pragma: no cover - no CA store at all
        raise EmailPermanentError(_scrub(f"TLS setup failed: {exc}")) from exc

    try:
        if settings.smtp_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, int(settings.smtp_port),
                                                    timeout=timeout, context=context)
        else:
            server = smtplib.SMTP(host, int(settings.smtp_port), timeout=timeout)
            server.ehlo()
            code, response = server.starttls(context=context)
            if code != 220:
                server.close()
                raise EmailTransientError(f"STARTTLS was refused ({code}).")
            server.ehlo()
    except (socket.timeout, TimeoutError) as exc:
        raise EmailTransientError(f"Timed out connecting to {host}:{settings.smtp_port}.") from exc
    except ssl.SSLError as exc:
        # A certificate problem is a configuration/infrastructure fault, not a blip: retrying
        # would hammer the server and still fail. Never bypassed, only reported.
        # Must come before OSError, which SSLError also subclasses.
        raise EmailPermanentError(_scrub(f"TLS verification failed: {exc}")) from exc
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, OSError) as exc:
        raise EmailTransientError(_scrub(f"Could not connect to {host}: {exc}")) from exc

    try:
        server.login(login, password)
    except smtplib.SMTPAuthenticationError:
        server.close()
        # 535 for a Gmail App Password that was revoked, mistyped, or is the account password.
        raise EmailAuthError(
            "The mail server rejected the credentials. For Gmail, SMTP_PASSWORD must be a "
            "16-character App Password (Google Account -> Security -> 2-Step Verification -> "
            "App passwords), not the account password."
        ) from None
    except (smtplib.SMTPServerDisconnected, smtplib.SMTPException, OSError) as exc:
        server.close()
        raise EmailTransientError(_scrub(f"Authentication failed: {exc}")) from exc
    return server


def verify_connection() -> DeliveryResult:
    """Prove the configuration works without sending anything (admin test action)."""
    started = time.monotonic()
    try:
        server = _server()
    except EmailError as exc:
        return DeliveryResult(ok=False, attempts=1, error=_scrub(exc),
                              detail={"ms": round((time.monotonic() - started) * 1000)})
    try:
        code, _ = server.noop()
    except (smtplib.SMTPException, OSError):
        code = 0
    finally:
        _quit(server)
    return DeliveryResult(
        ok=code in (250, 200, 0), attempts=1,
        detail={"ms": round((time.monotonic() - started) * 1000),
                "message": "Authenticated SMTP session established."},
    )


def _quit(server: Any) -> None:
    try:
        server.quit()
    except Exception:                                    # noqa: BLE001 - closing must not raise
        try:
            server.close()
        except Exception:                                # noqa: BLE001
            pass


def _classify_send_error(exc: Exception) -> EmailError:
    """Map what ``send_message`` raised onto retryable / not."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return EmailAuthError("The mail server rejected the credentials.")
    if isinstance(exc, smtplib.SMTPSenderRefused):
        # Gmail replies 550 when From is not the authenticated account or one of its aliases.
        return EmailPermanentError(
            _scrub(f"The server rejected the sender address: {exc.smtp_code} {exc.smtp_error!r}. "
                   "For Gmail the From address must be the authenticated account or an alias of it.")
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return EmailPermanentError(_scrub(f"Every recipient was refused: {exc.recipients}"))
    if isinstance(exc, smtplib.SMTPDataError):
        code = getattr(exc, "smtp_code", 0)
        return (EmailTransientError if code in TRANSIENT_CODES else EmailPermanentError)(
            _scrub(f"The server rejected the message body: {code} {getattr(exc, 'smtp_error', b'')}"))
    if isinstance(exc, ssl.SSLError):
        # Checked before OSError: SSLCertVerificationError subclasses both, and a certificate
        # problem is never transient.
        return EmailPermanentError(_scrub(f"TLS problem: {exc}"))
    if isinstance(exc, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                        socket.timeout, TimeoutError, OSError)):
        return EmailTransientError(_scrub(f"Connection problem: {exc}"))
    return EmailPermanentError(_scrub(f"{type(exc).__name__}: {exc}"))


def send(to: str, subject: str, text: str, html: Optional[str] = None,
         *, reply_to: Optional[str] = None) -> DeliveryResult:
    """Send one message, retrying only transient failures.

    Returns a ``DeliveryResult``; raises nothing for a delivery failure. The caller decides
    what to do with a failure, and every caller in this project records it rather than
    claiming success.
    """
    if not configured():
        reason = status()["reason"] or "Email delivery is not configured."
        log.info("email: not sending to %s - %s", mask_email(to), reason)
        return DeliveryResult(ok=False, attempts=0, error=reason, detail={"skipped": True})

    message = build_message(to, subject, text, html, reply_to=reply_to)
    attempts_allowed = 1 + max(0, int(settings.smtp_max_retries or 0))
    last_error = "unknown error"
    attempts = 0

    for attempt in range(1, attempts_allowed + 1):
        attempts = attempt
        server = None
        try:
            server = _server()
            server.send_message(message)
            _quit(server)
            log.info("email: delivered %r to %s on attempt %d", subject[:80], mask_email(to), attempt)
            return DeliveryResult(ok=True, attempts=attempt, message_id=message["Message-ID"],
                                  detail={"to": mask_email(to)})
        except EmailNotConfigured as exc:
            # A settings problem cannot be fixed by trying again.
            log.warning("email: configuration error - %s", _scrub(exc))
            return DeliveryResult(ok=False, attempts=attempt, error=_scrub(exc),
                                  detail={"not_configured": True})
        except EmailError as exc:
            last_error = _scrub(exc)
            if server is not None:
                _quit(server)
            if isinstance(exc, (EmailAuthError, EmailPermanentError)):
                log.warning("email: permanent failure to %s - %s", mask_email(to), last_error)
                return DeliveryResult(ok=False, attempts=attempt, error=last_error,
                                      detail={"permanent": True})
        except Exception as exc:                            # noqa: BLE001
            classified = _classify_send_error(exc)
            last_error = _scrub(classified)
            if server is not None:
                _quit(server)
            if isinstance(classified, (EmailAuthError, EmailPermanentError)):
                log.warning("email: permanent failure to %s - %s", mask_email(to), last_error)
                return DeliveryResult(ok=False, attempts=attempt, error=last_error,
                                      detail={"permanent": True})

        if attempt < attempts_allowed:
            delay = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
            log.info("email: attempt %d failed (%s); retrying in %.1fs",
                     attempt, last_error, delay)
            time.sleep(delay)

    log.warning("email: gave up after %d attempts to %s - %s", attempts, mask_email(to), last_error)
    return DeliveryResult(ok=False, attempts=attempts, error=last_error,
                          detail={"exhausted": True})
