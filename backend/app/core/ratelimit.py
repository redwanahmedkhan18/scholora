"""Fixed-window rate limiting (§security).

Applied as middleware so every route is covered, with tighter buckets on the
endpoints that cost money or leak information when abused: lead capture,
appointment booking, the assistant, and admin login.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
from dataclasses import dataclass
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.cache import cache
from app.core.config import settings


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern[str]
    limit: int
    window: int
    methods: tuple[str, ...] = ("POST", "PUT", "PATCH", "DELETE", "GET")
    name: str = ""


# Ordered: the first match wins.
RULES: list[Rule] = [
    Rule(re.compile(r"/api/v1/admin/auth/login$"), 10, 300, ("POST",), "login"),
    # A lost-device recovery attempt is the most attractive endpoint to guess at: it accepts a
    # password *and* a recovery code, so it gets a tighter bucket than the rest of the flow.
    Rule(re.compile(r"/api/v1/admin/auth/mfa/recover$"), 5, 900, ("POST",), "mfa_recover"),
    Rule(re.compile(r"/api/v1/admin/auth/mfa/"), 10, 300, ("POST",), "mfa"),
    Rule(re.compile(r"/api/v1/leads$"), 8, 3600, ("POST",), "leads"),
    Rule(re.compile(r"/api/v1/appointments$"), 6, 3600, ("POST",), "appointments"),
    Rule(re.compile(r"/api/v1/events/register$"), 10, 3600, ("POST",), "event_register"),
    Rule(re.compile(r"/api/v1/assistant/ask$"), 30, 3600, ("POST",), "assistant"),
    # Session issuance is anonymous and writes a row; normal questions use the separate
    # rule above, so this cannot throttle conversation.
    Rule(re.compile(r"/api/v1/assistant/session$"), 10, 60, ("POST",), "assistant_session"),
    Rule(re.compile(r"/api/v1/brochures/generate$"), 20, 3600, ("POST",), "brochure"),
    Rule(re.compile(r"/api/v1/(match|eligibility)/"), 60, 3600, ("POST",), "engines"),
    Rule(re.compile(r"/api/v1/documents/"), 40, 3600, ("POST", "PUT", "DELETE"), "documents"),
    # An admin-triggered SMTP test sends real mail (and burns the Gmail daily quota), so it gets
    # its own small bucket: a stolen session must not be able to use the mailbox as a weapon.
    Rule(re.compile(r"/api/v1/admin/config/test/"), 10, 300, ("POST",), "config_test"),
    Rule(re.compile(r"/api/v1/track$"), 600, 3600, ("POST",), "track"),
    # The conversion relay is server-to-server (it requires the internal token), so this is not
    # the only control on it - but a dedicated bucket keeps any attempt to guess that token, or a
    # misconfigured caller looping, away from the 300/60s public budget. First match wins, so it
    # must sit above the catch-all.
    Rule(re.compile(r"/api/v1/marketing/conversion$"), 60, 3600, ("POST",), "conversion"),
    Rule(re.compile(r"/api/v1/admin/"), 600, 60, name="admin"),
    Rule(re.compile(r"/api/v1/"), 300, 60, name="public"),
]


def _address(value: str):
    try:
        return ipaddress.ip_address((value or "").strip().strip("[]"))
    except ValueError:
        return None


def client_key(request: Request) -> str:
    """The rate-limit identity: direct peer by default, forwarded only through a trusted proxy.

    ``X-Forwarded-For`` is client-controlled. It is honoured only when the immediate ASGI
    peer is inside ``TRUSTED_PROXIES``. The chain is walked from the trusted connection side
    and stops at the first untrusted hop, so a leftmost value cannot be spoofed. ``X-Real-IP``
    and ``Forwarded`` are ignored: there is no separate trust policy for them.
    """
    peer = request.client.host if request.client else "unknown"
    if not (settings.trusted_proxies or "").strip():
        return peer
    try:
        networks = settings.trusted_proxy_networks
    except Exception:
        # Startup validation rejects this; at request time fail closed to the direct peer.
        return peer
    peer_ip = _address(peer)
    if peer_ip is None or not any(peer_ip in net for net in networks):
        return peer

    values = request.headers.getlist("x-forwarded-for")
    # Duplicate headers make the chain ambiguous; only a single proxy-provided header is trusted.
    if len(values) != 1 or not values[0].strip():
        return peer
    forwarded = values[0]
    hops = [part.strip() for part in forwarded.split(",")]
    if not hops or any(not hop for hop in hops):
        return peer
    parsed = [_address(hop) for hop in hops]
    if any(address is None for address in parsed):
        return peer

    current = peer_ip
    for candidate in reversed(parsed):
        if not any(current in net for net in networks):
            break
        current = candidate
    return str(current)


def match_rule(path: str, method: str) -> Optional[Rule]:
    for rule in RULES:
        if method in rule.methods and rule.pattern.search(path):
            return rule
    return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        # The website's server-side rendering calls this API hundreds of times during a
        # build. Throttling it does not protect anything - it is first-party traffic - and
        # a 429 mid-build silently bakes a 404 page for a real university. A shared secret
        # exempts it. Public traffic never carries this header: the browser talks to the
        # Next.js proxy, which does not forward it.
        token = (settings.internal_api_token or "").strip()
        if token and secrets.compare_digest(
                (request.headers.get("x-internal-token") or "").strip(), token):
            return await call_next(request)

        rule = match_rule(path, request.method)
        if rule is None:
            return await call_next(request)

        key = f"rl:{rule.name}:{client_key(request)}"
        count, remaining_ttl = cache.incr_with_ttl(key, rule.window)

        if count > rule.limit:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please wait a moment and try again.",
                    "retry_after_seconds": remaining_ttl,
                },
                headers={
                    "Retry-After": str(remaining_ttl),
                    "X-RateLimit-Limit": str(rule.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(rule.limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, rule.limit - count))
        return response
