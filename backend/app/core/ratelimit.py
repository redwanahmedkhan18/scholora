"""Fixed-window rate limiting (§security).

Applied as middleware so every route is covered, with tighter buckets on the
endpoints that cost money or leak information when abused: lead capture,
appointment booking, the assistant, and admin login.
"""
from __future__ import annotations

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
    Rule(re.compile(r"/api/v1/brochures/generate$"), 20, 3600, ("POST",), "brochure"),
    Rule(re.compile(r"/api/v1/(match|eligibility)/"), 60, 3600, ("POST",), "engines"),
    Rule(re.compile(r"/api/v1/documents/"), 40, 3600, ("POST", "PUT", "DELETE"), "documents"),
    # An admin-triggered SMTP test sends real mail (and burns the Gmail daily quota), so it gets
    # its own small bucket: a stolen session must not be able to use the mailbox as a weapon.
    Rule(re.compile(r"/api/v1/admin/config/test/"), 10, 300, ("POST",), "config_test"),
    Rule(re.compile(r"/api/v1/track$"), 600, 3600, ("POST",), "track"),
    Rule(re.compile(r"/api/v1/admin/"), 600, 60, name="admin"),
    Rule(re.compile(r"/api/v1/"), 300, 60, name="public"),
]


def client_key(request: Request) -> str:
    """Prefer the proxy-forwarded client IP; fall back to the socket peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else "unknown"


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
