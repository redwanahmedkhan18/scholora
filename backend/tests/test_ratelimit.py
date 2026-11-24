"""Rate limiting and caching, exercised directly against their units.

The HTTP suite disables the middleware so that hundreds of assertions do not trip it; this
module tests the limiter itself plus one live middleware pass through a throwaway app.
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import ratelimit
from app.core.cache import cache
from app.core.config import settings


@pytest.fixture(autouse=True)
def _limiter_on():
    """conftest disables limiting for the HTTP suite; this module needs it live."""
    previous = settings.rate_limit_enabled
    settings.rate_limit_enabled = True
    yield
    settings.rate_limit_enabled = previous


def _reset_cache() -> None:
    """Drop everything the limiter and cache hold between tests."""
    cache.invalidate_prefix("")


# ------------------------------------------------------------------- rules
def test_rules_are_ordered_most_specific_first():
    """The first match wins, so the catch-all must sit last."""
    assert ratelimit.RULES[-1].name == "public"
    assert ratelimit.match_rule("/api/v1/admin/auth/login", "POST").name == "login"
    assert ratelimit.match_rule("/api/v1/admin/leads", "GET").name == "admin"


def test_login_rule_is_strictest():
    login = ratelimit.match_rule("/api/v1/admin/auth/login", "POST")
    browse = ratelimit.match_rule("/api/v1/countries", "GET")
    assert login is not None and browse is not None
    assert login.limit < browse.limit, "login must be far stricter than public browsing"


def test_write_endpoints_are_limited_per_hour():
    leads = ratelimit.match_rule("/api/v1/leads", "POST")
    assert leads is not None
    assert leads.window >= 3600
    assert leads.limit <= 10


def test_read_only_requests_skip_the_write_rules():
    """A GET on /leads is an admin read, not a lead submission."""
    assert ratelimit.match_rule("/api/v1/leads", "GET").name != "leads"


# -------------------------------------------------------------- middleware
def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ratelimit.RateLimitMiddleware)

    @app.post("/api/v1/leads")
    def leads():
        return {"ok": True}

    @app.get("/api/v1/countries")
    def countries():
        return []

    return app


def test_middleware_emits_headers_and_eventually_429():
    _reset_cache()
    client = TestClient(_app())

    first = client.get("/api/v1/countries")
    assert first.status_code == 200
    assert int(first.headers["X-RateLimit-Limit"]) > 0
    assert int(first.headers["X-RateLimit-Remaining"]) >= 0

    rule = ratelimit.match_rule("/api/v1/leads", "POST")
    statuses = [client.post("/api/v1/leads").status_code for _ in range(rule.limit + 3)]
    assert statuses[0] == 200
    assert 429 in statuses, "the limiter must eventually refuse"

    blocked = client.post("/api/v1/leads")
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["retry_after_seconds"] > 0
    assert blocked.headers.get("Retry-After")


def test_separate_clients_have_separate_budgets():
    _reset_cache()
    client = TestClient(_app())
    rule = ratelimit.match_rule("/api/v1/leads", "POST")

    for _ in range(rule.limit + 2):
        client.post("/api/v1/leads", headers={"X-Forwarded-For": "10.0.0.1"})
    assert client.post("/api/v1/leads", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 429
    # A different caller is unaffected.
    assert client.post("/api/v1/leads", headers={"X-Forwarded-For": "10.0.0.2"}).status_code == 200


# ------------------------------------------------------------------- cache
def test_cache_json_roundtrip_and_expiry():
    _reset_cache()
    cache.set_json("k", {"v": 1}, ttl=60)
    assert cache.get_json("k") == {"v": 1}

    cache.set("short", "x", ttl=1)
    time.sleep(1.2)
    assert cache.get("short") is None

    cache.delete("k")
    assert cache.get_json("k") is None


def test_cache_counter_increments_and_reports_ttl():
    _reset_cache()
    counts = [cache.incr_with_ttl("counter", 30)[0] for _ in range(5)]
    assert counts == [1, 2, 3, 4, 5]
    _, remaining = cache.incr_with_ttl("counter", 30)
    assert 0 < remaining <= 30


def test_cache_invalidates_by_prefix():
    _reset_cache()
    for i in range(3):
        cache.set_json(f"homepage:{i}", {"i": i}, ttl=60)
    cache.set_json("other:1", {"x": 1}, ttl=60)
    assert cache.invalidate_prefix("homepage:") == 3
    assert cache.get_json("homepage:0") is None
    assert cache.get_json("other:1") == {"x": 1}


def test_cache_works_without_redis():
    """Redis is optional — the in-process fallback must behave identically."""
    _reset_cache()
    cache.set_json("fallback", [1, 2, 3], ttl=30)
    assert cache.get_json("fallback") == [1, 2, 3]
    assert cache.backend in {"redis", "memory"}


# ------------------------------------------------- first-party render traffic
def test_internal_token_exempts_server_side_rendering():
    """
    The website's own server rendering must never be throttled. A build issues hundreds of
    requests in a burst; a 429 there used to be indistinguishable from a 404 downstream and
    froze a "not found" page for a university that exists.
    """
    _reset_cache()
    client = TestClient(_app())
    rule = ratelimit.match_rule("/api/v1/leads", "POST")
    headers = {"X-Internal-Token": settings.internal_api_token}

    statuses = {client.post("/api/v1/leads", headers=headers).status_code
                for _ in range(rule.limit * 3)}
    assert statuses == {200}, "first-party rendering must never be refused"


def test_a_wrong_internal_token_is_still_limited():
    """The exemption is a shared secret, not an opt-out anyone can claim."""
    _reset_cache()
    client = TestClient(_app())
    rule = ratelimit.match_rule("/api/v1/leads", "POST")

    statuses = [client.post("/api/v1/leads", headers={"X-Internal-Token": "guessed"}).status_code
                for _ in range(rule.limit + 3)]
    assert 429 in statuses


def test_exemption_is_off_when_no_token_is_configured():
    """An empty token must not mean 'any empty header matches'."""
    _reset_cache()
    previous = settings.internal_api_token
    settings.internal_api_token = ""
    try:
        client = TestClient(_app())
        rule = ratelimit.match_rule("/api/v1/leads", "POST")
        statuses = [client.post("/api/v1/leads", headers={"X-Internal-Token": ""}).status_code
                    for _ in range(rule.limit + 3)]
        assert 429 in statuses
    finally:
        settings.internal_api_token = previous
