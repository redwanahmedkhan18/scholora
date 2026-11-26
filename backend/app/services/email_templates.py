"""Email rendering: subject, plain text and branded HTML for one notification.

Kept apart from the transport so a template can be unit-tested without touching SMTP, and so
the "what is safe to put in an email" decision lives in one readable file.

Two rules are enforced here rather than left to each caller:

* **No credentials, ever.** These templates render a notification title and body - never a
  password, a TOTP secret, a recovery code, a session token or a reset link with a token in
  the query string. `assert_safe()` is used by the tests to prove that.
* **A plain-text part always exists.** The HTML is the nicer view; the text is the message.
"""
from __future__ import annotations

import html as html_module
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings

#: Substrings that must never appear in an outgoing message body. Used by tests and by
#: `assert_safe` below, which is a cheap last line of defence against a careless future caller.
FORBIDDEN_SUBSTRINGS = ("mfa_secret", "recovery_code", "password_hash", "Bearer ", "otpauth://")


@dataclass
class RenderedEmail:
    subject: str
    text: str
    html: str


def site_url() -> str:
    return (settings.public_site_url or "https://scholara.com.bd").rstrip("/")


def _escape(value: str) -> str:
    return html_module.escape(value or "", quote=True)


def assert_safe(*chunks: str) -> None:
    """Raise if a rendered message contains something that looks like a credential.

    A developer who one day interpolates a TOTP secret into a notification gets a loud
    failure in the test suite instead of a security incident in a mailbox.
    """
    blob = "\n".join(c for c in chunks if c).lower()
    for needle in FORBIDDEN_SUBSTRINGS:
        if needle.lower() in blob:
            raise ValueError(f"refusing to send an email containing {needle!r}")


def security_notice(title: str, body: str, *, action_url: Optional[str] = None,
                    account: Optional[str] = None) -> RenderedEmail:
    """A security change on the recipient's account (two-factor switched on/off/reset).

    Deliberately sparse: the change, the account it affects, when, and what to do if it was
    not them. No codes, no device fingerprints, no "click here to reverse this" link that an
    attacker could replay.
    """
    brand = settings.app_name or "Scholara"
    base = site_url()
    account_line = f"Account: {account}" if account else ""
    help_line = (
        "If you did not make this change, contact the Scholara team immediately and change "
        "your password."
    )
    subject = f"{brand}: {title}"

    lines = [f"{brand} security notice", "", title]
    if body:
        lines += ["", body]
    if account_line:
        lines.append(account_line)
    lines += ["", help_line]
    if action_url:
        lines += ["", f"Open your account: {base}{action_url if action_url.startswith('/') else '/' + action_url}"]
    lines += ["", f"— {brand}", base]
    text = "\n".join(lines)

    rows = "".join(
        f'<p style="margin:0 0 14px;font-size:15px;line-height:1.6;color:#1f2937;">{_escape(part)}</p>'
        for part in [body] if part
    )
    button = ""
    if action_url:
        href = base + (action_url if action_url.startswith("/") else "/" + action_url)
        button = (
            f'<p style="margin:22px 0 0;"><a href="{_escape(href)}" '
            'style="display:inline-block;background:#0f3d8a;color:#ffffff;text-decoration:none;'
            'padding:12px 22px;border-radius:10px;font-weight:700;font-size:14px;">'
            "Open your account</a></p>"
        )

    html = f"""<!doctype html>
<html lang="en"><body style="margin:0;padding:0;background:#f1f5f9;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="max-width:560px;background:#ffffff;border-radius:16px;overflow:hidden;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
        <tr><td style="background:#0f3d8a;padding:20px 24px;color:#ffffff;">
          <span style="font-size:18px;font-weight:800;letter-spacing:-0.01em;">{_escape(brand)}</span>
          <span style="font-size:11px;text-transform:uppercase;letter-spacing:0.14em;color:#bfdbfe;display:block;margin-top:2px;">Security notice</span>
        </td></tr>
        <tr><td style="padding:24px;">
          <h1 style="margin:0 0 14px;font-size:19px;line-height:1.3;color:#0f172a;">{_escape(title)}</h1>
          {rows}
          {f'<p style="margin:0 0 14px;font-size:13px;color:#475569;">{_escape(account_line)}</p>' if account_line else ''}
          {button}
          <p style="margin:22px 0 0;padding-top:16px;border-top:1px solid #e2e8f0;font-size:13px;line-height:1.6;color:#475569;">
            {_escape(help_line)}
          </p>
        </td></tr>
        <tr><td style="padding:16px 24px;background:#f8fafc;font-size:12px;color:#64748b;">
          <a href="{_escape(base)}" style="color:#0f3d8a;text-decoration:none;">{_escape(base)}</a>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

    assert_safe(subject, text, html)
    return RenderedEmail(subject=subject, text=text, html=html)


def test_message(*, to: str, triggered_by: str) -> RenderedEmail:
    """The message an administrator sends to themselves to prove SMTP works.

    It says who asked for it and to which mailbox, so a stray test email is self-explaining.
    """
    brand = settings.app_name or "Scholara"
    subject = f"{brand}: SMTP test message"
    text = (
        f"This is a test message from {brand}.\n\n"
        f"Requested by: {triggered_by}\n"
        f"Delivered to: {to}\n\n"
        "If you can read this, the Gmail SMTP configuration is working: the server "
        "accepted the connection, the credentials and the message.\n\n"
        f"— {brand}"
    )
    html = f"""<!doctype html>
<html lang="en"><body style="margin:0;background:#f1f5f9;padding:24px 12px;
  font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:14px;padding:24px;">
    <h1 style="margin:0 0 12px;font-size:18px;color:#0f172a;">{_escape(brand)} SMTP test</h1>
    <p style="font-size:14px;line-height:1.6;color:#334155;margin:0 0 12px;">
      If you can read this, the SMTP configuration is working: the server accepted the
      connection, the credentials and the message.
    </p>
    <p style="font-size:13px;color:#64748b;margin:0;">
      Requested by {_escape(triggered_by)} · delivered to {_escape(to)}
    </p>
  </div>
</body></html>"""
    assert_safe(subject, text, html)
    return RenderedEmail(subject=subject, text=text, html=html)
