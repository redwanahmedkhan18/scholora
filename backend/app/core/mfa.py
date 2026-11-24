"""TOTP multi-factor authentication for staff accounts (RFC 6238).

Implemented directly on hmac/hashlib so the platform gains no dependency for a
120-line algorithm. Compatible with Google Authenticator, Authy, 1Password and
Microsoft Authenticator.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
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


def _hotp(secret_b32: str, counter: int) -> str:
    padding = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + padding, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**DIGITS)).zfill(DIGITS)


def current_code(secret_b32: str, at: float | None = None) -> str:
    return _hotp(secret_b32, int((at or time.time()) // PERIOD))


def verify(secret_b32: str, code: str, at: float | None = None) -> bool:
    """Constant-time check across the drift window."""
    if not secret_b32 or not code:
        return False
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return False
    counter = int((at or time.time()) // PERIOD)
    for drift in range(-WINDOW, WINDOW + 1):
        if hmac.compare_digest(_hotp(secret_b32, counter + drift), code):
            return True
    return False


def provisioning_uri(secret_b32: str, account: str, issuer: str = "Scholara") -> str:
    """otpauth:// URI — render as a QR code, or let the user type the secret."""
    label = quote(f"{issuer}:{account}")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"
    )


# --------------------------------------------------------------- recovery codes
def generate_recovery_codes(count: int = 8) -> list[str]:
    """Single-use codes for when the authenticator device is lost."""
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


def check_recovery_code(code: str, hashed_list: list[str]) -> str | None:
    """Return the matching hash so the caller can burn it, or None."""
    target = hash_recovery_code(code)
    for stored in hashed_list or []:
        if hmac.compare_digest(stored, target):
            return stored
    return None


def issue_recovery_codes(count: int = 8) -> tuple[list[str], list[str]]:
    """Return (plaintext_codes, hashed_codes). Only the hashes are ever stored."""
    plain = generate_recovery_codes(count)
    return plain, [hash_recovery_code(c) for c in plain]


def consume_recovery_code(hashed_list: list[str], code: str) -> tuple[list[str], bool]:
    """Burn a recovery code. Returns (remaining_hashes, was_valid)."""
    match = check_recovery_code(code, hashed_list)
    if not match:
        return list(hashed_list or []), False
    return [h for h in (hashed_list or []) if h != match], True
