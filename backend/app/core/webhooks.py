"""Inbound webhook signature validation (§security).

Meta (WhatsApp Cloud API, Messenger, Instagram) signs every delivery with
``X-Hub-Signature-256: sha256=<hmac>`` over the **raw** request body using the app
secret. Verifying against a re-serialised body is a classic mistake, so callers must
pass the exact bytes FastAPI received.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, Request, status

from app.core.config import settings

MAX_SKEW_SECONDS = 300


def verify_meta_signature(raw_body: bytes, header_value: str | None, app_secret: str | None = None) -> bool:
    secret = (app_secret or settings.whatsapp_app_secret or "").strip()
    if not secret:
        # No secret configured. In production that is a misconfiguration, so refuse outright.
        # Locally we accept only *unsigned* calls (the simulator); a signature we cannot
        # verify is always rejected, otherwise a forged header would sail through dev.
        return settings.environment != "production" and not header_value
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value.split("=", 1)[1].strip())


async def require_meta_signature(request: Request) -> bytes:
    """FastAPI dependency: returns the raw body once the signature checks out."""
    raw = await request.body()
    header = request.headers.get("x-hub-signature-256")
    if not verify_meta_signature(raw, header):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")
    return raw


def verify_generic_signature(
    raw_body: bytes, signature: str | None, timestamp: str | None, secret: str
) -> bool:
    """HMAC-SHA256 over ``timestamp.body`` with replay protection."""
    if not signature or not timestamp or not secret:
        return False
    try:
        skew = abs(time.time() - float(timestamp))
    except ValueError:
        return False
    if skew > MAX_SKEW_SECONDS:
        return False
    payload = f"{timestamp}.".encode() + raw_body
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def sign_outbound(raw_body: bytes, secret: str) -> dict[str, str]:
    """Produce headers for webhooks we send (partner integrations)."""
    timestamp = str(int(time.time()))
    payload = f"{timestamp}.".encode() + raw_body
    return {
        "X-Scholara-Timestamp": timestamp,
        "X-Scholara-Signature": hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest(),
    }
