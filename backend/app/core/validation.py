"""Canonicalisation for values the public website publishes.

The public header, footer, contact page, mobile navigation, WhatsApp deep links and the
Facebook links all render strings that an administrator types into the back office. Two
rules follow from that, and neither can live in the browser because the browser is not the
only writer:

1. **One stored form.** A phone number typed as ``01711 223344``, ``+880 1711-223344`` or
   ``+8801711223344`` is the same number and must produce the same ``tel:``/``wa.me`` link.
   Values are stored in E.164 and formatted for display on the way out.
2. **No dangerous strings reach a URL attribute.** ``facebook_page_url`` is rendered as an
   ``<a href>``, so ``javascript:alert(1)`` stored through the generic settings form is a
   stored XSS waiting for a click. Every URL field is parsed and scheme-checked here, in the
   API, before it is persisted.

Each helper raises ``ValueError`` with a message that is safe to show to the administrator.
"""
from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urlparse

from email_validator import EmailNotValidError, validate_email

# Characters people legitimately type in a phone number: digits, spaces, dashes, dots,
# parentheses and one leading plus. Country-aware punctuation ("(+880) 1711-223344") too.
_PHONE_ALLOWED = re.compile(r"^[+0-9\s\-().]+$")
_DIGITS = re.compile(r"\D+")

BD_COUNTRY_CODE = "880"

# Hosts that are unambiguously a Facebook *page*. Anything else - including look-alike
# domains such as facebook.com.evil.example - is rejected rather than trusted.
FACEBOOK_HOSTS = frozenset({
    "facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com",
    "fb.com", "www.fb.com", "fb.me", "www.fb.me", "messenger.com", "www.messenger.com",
})


def normalize_phone(raw: str, *, field: str = "Phone number") -> str:
    """Return E.164 (``+8801711223344``) or raise ``ValueError`` with a readable reason.

    A bare Bangladeshi national number (``01711223344``) is the form the office actually
    types; it is upgraded to ``+880...`` rather than rejected. Any other number must carry
    a country code, because guessing one would silently point ``tel:`` links at the wrong
    country.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > 32:
        raise ValueError(f"{field} is too long")
    if not _PHONE_ALLOWED.match(text) or text.count("+") > 1:
        raise ValueError(f"{field} may contain only digits, spaces, +, -, ( ) and .")
    digits = _DIGITS.sub("", text)
    if not digits:
        raise ValueError(f"{field} must contain digits")
    has_plus = "+" in text

    if not has_plus and not text.startswith("+"):
        # Local Bangladeshi mobile: 01XXXXXXXXX (11 digits) or 1XXXXXXXXX (10 digits).
        if digits.startswith("00"):
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) == 11:
            digits = BD_COUNTRY_CODE + digits[1:]
        elif len(digits) == 10 and digits.startswith("1"):
            digits = BD_COUNTRY_CODE + digits
        elif not digits.startswith(BD_COUNTRY_CODE):
            raise ValueError(
                f"{field} must include a country code, e.g. +8801711223344"
            )

    # E.164: 8-15 digits, first digit never 0.
    if not 8 <= len(digits) <= 15:
        raise ValueError(f"{field} must have 8-15 digits including the country code")
    if digits.startswith("0"):
        raise ValueError(f"{field} cannot start with 0 after the country code")
    return f"+{digits}"


def phone_display(e164: str) -> str:
    """Group an E.164 number for humans: ``+880 1711-223344``.

    Display only - links keep the unpunctuated E.164 form, because ``tel:`` and ``wa.me``
    must not contain spaces.
    """
    digits = _DIGITS.sub("", e164 or "")
    if not digits:
        return ""
    if digits.startswith(BD_COUNTRY_CODE) and len(digits) == 13:
        return f"+{BD_COUNTRY_CODE} {digits[3:7]}-{digits[7:]}"
    return f"+{digits}"


def whatsapp_number(e164: str) -> str:
    """The digits-only form ``wa.me`` requires (no plus, no separators)."""
    return _DIGITS.sub("", e164 or "")


def normalize_email(raw: str, *, field: str = "Email address") -> str:
    text = (raw or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > 200:
        raise ValueError(f"{field} is too long")
    try:
        # Deliberately no DNS lookup: a slow or blocked MX query must not fail a save.
        result = validate_email(text, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValueError(f"{field} is not a valid email address ({exc})") from exc
    return result.normalized.lower()


def normalize_https_url(raw: str, *, field: str, allow_http: bool = False) -> str:
    """A URL that is safe to place in an ``href``: absolute, https, no credentials.

    ``javascript:``, ``data:`` and protocol-relative values are rejected outright. The
    returned value is the parsed, re-assembled form so ``  HTTPS://x  `` cannot smuggle a
    different scheme through.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > 600:
        raise ValueError(f"{field} is too long")
    parsed = urlparse(text)
    scheme = parsed.scheme.lower()
    allowed = ("https", "http") if allow_http else ("https",)
    if scheme not in allowed:
        raise ValueError(f"{field} must start with https://")
    if not parsed.netloc or "." not in parsed.netloc:
        raise ValueError(f"{field} must be a full URL, e.g. https://www.facebook.com/yourpage")
    if parsed.username or parsed.password:
        raise ValueError(f"{field} must not contain credentials")
    return text


def normalize_facebook_url(raw: str, *, field: str = "Facebook page URL") -> str:
    text = normalize_https_url(raw, field=field)
    host = urlparse(text).netloc.lower().split("@")[-1]
    if host not in FACEBOOK_HOSTS:
        raise ValueError(
            f"{field} must be a facebook.com page link, e.g. https://www.facebook.com/yourpage"
        )
    if not urlparse(text).path.strip("/"):
        raise ValueError(f"{field} must point at a page, not the facebook.com home page")
    return text


def normalize_phone_list(values: Iterable[str], *, field: str) -> list[str]:
    """Validate every entry and drop duplicates, keeping the administrator's order."""
    out: list[str] = []
    for raw in values or []:
        if not (raw or "").strip():
            continue
        number = normalize_phone(str(raw), field=field)
        if number not in out:
            out.append(number)
    return out


def normalize_email_list(values: Iterable[str], *, field: str) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        if not (raw or "").strip():
            continue
        address = normalize_email(str(raw), field=field)
        if address not in out:
            out.append(address)
    return out
