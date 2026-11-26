"""TOTP multi-factor authentication for staff accounts (RFC 6238).

Implemented directly on hmac/hashlib so the platform gains no dependency for a
120-line algorithm. Compatible with Google Authenticator, Authy, 1Password and
Microsoft Authenticator.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
PERIOD = 30
WINDOW = 1  # accept the previous/next step to tolerate clock drift


def generate_secret() -> str:
    """A fresh base32 shared secret (160 bits, the RFC 4226 recommendation)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("utf-8").rstrip("=")


def decode_secret(secret_b32: str | None) -> bytes | None:
    """The raw key behind a base32 secret, or None if it is not a usable secret.

    A stored secret normally comes from :func:`generate_secret`, but the column is plain text:
    a hand-edited row, a half-finished restore or a truncated copy can leave something that is
    not base32 at all. ``base64.b32decode`` raises ``binascii.Error`` on those, which used to
    escape a login attempt as a 500. Malformed input is *never* accepted here - it decodes or it
    is refused - and nothing about the value is logged or echoed.
    """
    if not isinstance(secret_b32, str) or not secret_b32:
        return None
    compact = secret_b32.strip().replace(" ", "")
    if not compact:
        return None
    padding = "=" * (-len(compact) % 8)
    try:
        key = base64.b32decode(compact.upper() + padding, casefold=True)
    except (binascii.Error, ValueError):
        return None
    # b32decode happily returns b"" for "=======", which HMAC would accept as a real key.
    return key or None


def _hotp_from_key(key: bytes, counter: int) -> str:
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**DIGITS)).zfill(DIGITS)


def _hotp(secret_b32: str, counter: int) -> str:
    key = decode_secret(secret_b32)
    if key is None:
        # Deliberately carries no part of the secret.
        raise ValueError("mfa secret is not valid base32")
    return _hotp_from_key(key, counter)


def current_code(secret_b32: str, at: float | None = None) -> str:
    return _hotp(secret_b32, int((at or time.time()) // PERIOD))


def match_step(secret_b32: str, code: str, at: float | None = None) -> int | None:
    """The 30-second time-step a code belongs to, or None if it is not valid now.

    The step, not just a boolean, is what makes a code single-use: callers persist the last
    accepted step and refuse anything at or below it. RFC 6238 §5.2 requires exactly this -
    without it a code stays usable for the whole period it is on screen *and* both drift
    windows, which is a replay window of up to 90 seconds per captured code.
    """
    if not secret_b32 or not code:
        return None
    key = decode_secret(secret_b32)
    if key is None:
        # An unusable secret cannot authenticate anybody; a bad code is a 401, not a 500.
        return None
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    counter = int((at or time.time()) // PERIOD)
    for drift in range(-WINDOW, WINDOW + 1):
        if hmac.compare_digest(_hotp_from_key(key, counter + drift), code):
            return counter + drift
    return None


def verify(secret_b32: str, code: str, at: float | None = None,
           minimum_step: int | None = None) -> bool:
    """Constant-time check across the drift window, optionally rejecting used steps."""
    step = match_step(secret_b32, code, at)
    if step is None:
        return False
    if minimum_step is not None and step <= minimum_step:
        return False
    return True


def provisioning_uri(secret_b32: str, account: str, issuer: str = "Scholara") -> str:
    """otpauth:// URI — render as a QR code, or let the user type the secret."""
    label = quote(f"{issuer}:{account}")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"
    )


def qr_data_uri(otpauth_url: str) -> str | None:
    """Base64 SVG data URI for the provisioning QR code, or None if it cannot be drawn.

    Returned as a data URI, not inline markup: an ``<img src="data:image/svg+xml;base64,...">"
    cannot execute script, whereas injecting the SVG into the DOM with innerHTML could.
    A missing QR library degrades to the manual key rather than failing enrolment.
    """
    try:
        import segno  # imported lazily so the rest of the module works without it

        buf = io.BytesIO()
        segno.make(otpauth_url, error="m").save(buf, kind="svg", scale=4, border=2,
                                                 dark="#0b1220", light="#ffffff")
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/svg+xml;base64,{encoded}"
    except Exception:      # pragma: no cover - depends on an optional wheel being present
        return None


# --------------------------------------------------------------- recovery codes
def generate_recovery_codes(count: int = 8) -> list[str]:
    """Single-use codes for when the authenticator device is lost."""
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    """Keyed hash of a recovery code.

    Plain SHA-256 was used originally. A recovery code carries only 48 bits of entropy, so an
    unkeyed digest of one is brute-forceable offline by anyone who obtains the database dump.
    Keying the hash with SECRET_KEY (which lives in the environment, not the database) makes a
    stolen table useless on its own. The legacy unkeyed form is still *verified* so existing
    users are not silently locked out, but every newly issued code uses the keyed form.
    """
    normalized = code.strip().lower().replace(" ", "")
    key = _recovery_key()
    if key:
        digest = hmac.new(key, normalized.encode(), hashlib.sha256).hexdigest()
        return f"hmac-sha256:{digest}"
    return hashlib.sha256(normalized.encode()).hexdigest()


def _recovery_key() -> bytes:
    try:
        from app.core.config import settings
        return (settings.secret_key or "").encode()
    except Exception:            # pragma: no cover - config always importable in practice
        return b""


def _legacy_hash(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


def check_recovery_code(code: str, hashed_list: list[str]) -> str | None:
    """Return the matching stored hash so the caller can burn it, or None."""
    stored_values = [s for s in (hashed_list or []) if isinstance(s, str)]
    candidates = [hash_recovery_code(code), _legacy_hash(code)]
    for stored in stored_values:
        for candidate in candidates:
            if hmac.compare_digest(stored, candidate):
                return stored
    return None


def issue_recovery_codes(count: int = 8) -> tuple[list[str], list[str]]:
    """Return (plaintext_codes, hashed_codes). Only the hashes are ever stored."""
    plain = generate_recovery_codes(count)
    return plain, [hash_recovery_code(c) for c in plain]


def recovery_code_count(hashed_list: list[str] | None) -> int:
    return len(hashed_list or [])


def consume_recovery_code(hashed_list: list[str], code: str) -> tuple[list[str], bool]:
    """Burn a recovery code. Returns (remaining_hashes, was_valid)."""
    match = check_recovery_code(code, hashed_list)
    if not match:
        return list(hashed_list or []), False
    return [h for h in (hashed_list or []) if h != match], True
