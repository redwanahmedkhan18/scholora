"""W11 acceptance tests - SSRF and URL-ingestion controls.

All requests are mocked. These tests prove the guard refuses private and metadata targets,
pins the connection to the validated IP, revalidates every redirect, enforces content type,
size and timeouts, and that the real extraction and citation-check paths use it.
"""
from __future__ import annotations

from typing import Dict, List

import httpx
import pytest
from app.core import net_guard as ng
from app.services import extraction, source_verification


def resolver_for(mapping: Dict[str, List[str]]):
    def _resolve(host: str, port: int) -> List[str]:
        if host in mapping:
            return mapping[host]
        return ["93.184.216.34"]

    return _resolve


PUBLIC = resolver_for({"example.edu": ["93.184.216.34"]})


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.parametrize("url,code", [
    ("file:///etc/passwd", "UNSUPPORTED_SCHEME"),
    ("ftp://example.edu/x", "UNSUPPORTED_SCHEME"),
    ("javascript:alert(1)", "UNSUPPORTED_SCHEME"),
    ("https://user:pass@example.edu/x", "CREDENTIALS_IN_URL"),
    ("http://localhost/x", "BLOCKED_HOST"),
    ("http://metadata.google.internal/computeMetadata/v1/", "BLOCKED_HOST"),
    ("http://printer.local/x", "BLOCKED_HOST"),
    ("http://127.0.0.1/x", "PRIVATE_ADDRESS"),
    ("http://127.0.0.1:8080/x", "DISALLOWED_PORT"),
    ("http://10.0.0.1/x", "PRIVATE_ADDRESS"),
    ("http://169.254.169.254/latest/meta-data/", "PRIVATE_ADDRESS"),
    ("http://[::1]/x", "PRIVATE_ADDRESS"),
    ("http://0.0.0.0/x", "PRIVATE_ADDRESS"),
    ("http://100.64.0.1/x", "PRIVATE_ADDRESS"),
    ("http://[::ffff:127.0.0.1]/x", "PRIVATE_ADDRESS"),
    ("http://example.edu:22/x", "DISALLOWED_PORT"),
    ("http://example.edu:99999/x", "INVALID_PORT"),
    ("http:///x", "INVALID_URL"),
    ("not a url", "UNSUPPORTED_SCHEME"),
])
def test_unsafe_urls_are_refused(url, code):
    with pytest.raises(ng.UrlSafetyError) as caught:
        ng.validate_url(url, resolver=PUBLIC)
    assert caught.value.code == code


def test_a_hostname_resolving_to_a_private_address_is_refused():
    private = resolver_for({"evil.example": ["10.1.2.3"]})
    with pytest.raises(ng.UrlSafetyError, match="PRIVATE_ADDRESS"):
        ng.validate_url("https://evil.example/x", resolver=private)


def test_a_mixed_public_and_private_answer_is_refused():
    """One private answer is enough; a round-robin attacker must not win the race."""
    mixed = resolver_for({"mixed.example": ["93.184.216.34", "192.168.1.10"]})
    with pytest.raises(ng.UrlSafetyError, match="PRIVATE_ADDRESS"):
        ng.validate_url("https://mixed.example/x", resolver=mixed)


def test_dns_failure_is_reported_as_dns_error():
    def failing(host, port):
        raise ng.UrlSafetyError("DNS_ERROR", "gaierror")

    with pytest.raises(ng.UrlSafetyError, match="DNS_ERROR"):
        ng.validate_url("https://does-not-resolve.example/x", resolver=failing)


def test_a_valid_public_url_is_pinned_to_its_validated_ip():
    target = ng.validate_url("https://example.edu/courses?x=1", resolver=PUBLIC)
    assert target.ip == "93.184.216.34"
    assert target.host == "example.edu"
    assert target.pinned_url == "https://93.184.216.34/courses?x=1"


def test_fetch_connects_to_the_pinned_ip_and_preserves_the_host_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["url"] = str(request.url)
        seen["host_header"] = request.headers.get("host")
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>ok</p>")

    response = ng.fetch("https://example.edu/study", resolver=PUBLIC,
                        transport=_transport(handler), allowed_content_types=("text/html",))
    assert response.status_code == 200
    assert "ok" in response.text
    assert seen["host"] == "93.184.216.34", "the connection must go to the validated IP"
    assert "example.edu" not in seen["url"]
    assert seen["host_header"] == "example.edu"


def test_every_redirect_hop_is_revalidated_and_private_ones_are_blocked():
    calls: List[str] = []

    def resolver(host: str, port: int) -> List[str]:
        calls.append(host)
        if len(calls) == 1:
            return ["93.184.216.34"]
        return ["10.9.8.7"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://rebind.example/next"})

    with pytest.raises(ng.UrlSafetyError) as caught:
        ng.fetch("https://rebind.example/start", resolver=resolver, transport=_transport(handler))
    assert caught.value.code == "PRIVATE_ADDRESS"
    assert len(calls) >= 2, "the redirect destination must be resolved again"


def test_an_https_downgrade_redirect_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://example.edu/plain"})

    with pytest.raises(ng.UrlSafetyError, match="UNSAFE_REDIRECT"):
        ng.fetch("https://example.edu/start", resolver=PUBLIC, transport=_transport(handler))


def test_a_redirect_to_a_non_http_scheme_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    with pytest.raises(ng.UrlSafetyError, match="UNSUPPORTED_SCHEME"):
        ng.fetch("https://example.edu/start", resolver=PUBLIC, transport=_transport(handler))


def test_redirect_budget_is_enforced():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/loop"})

    with pytest.raises(ng.UrlSafetyError, match="REDIRECT_LIMIT"):
        ng.fetch("https://example.edu/start", resolver=PUBLIC, transport=_transport(handler),
                 max_redirects=2)


def test_a_disallowed_content_type_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/x-msdownload"},
                              content=b"binary")

    with pytest.raises(ng.UrlSafetyError, match="CONTENT_TYPE"):
        ng.fetch("https://example.edu/x", resolver=PUBLIC, transport=_transport(handler),
                 allowed_content_types=("text/html",))


def test_a_missing_content_type_is_refused_when_a_type_is_required():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<p>ok</p>")

    with pytest.raises(ng.UrlSafetyError, match="CONTENT_TYPE"):
        ng.fetch("https://example.edu/x", resolver=PUBLIC, transport=_transport(handler),
                 allowed_content_types=("text/html",))


def test_response_size_limit_is_enforced():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 500)

    with pytest.raises(ng.UrlSafetyError, match="RESPONSE_TOO_LARGE"):
        ng.fetch("https://example.edu/x", resolver=PUBLIC, transport=_transport(handler),
                 max_bytes=100, allowed_content_types=("text/html",))


def test_a_timeout_is_reported_without_a_raw_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(ng.UrlSafetyError, match="TIMEOUT"):
        ng.fetch("https://example.edu/x", resolver=PUBLIC, transport=_transport(handler))


def test_a_blocked_url_never_reaches_the_transport():
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200)

    with pytest.raises(ng.UrlSafetyError, match="PRIVATE_ADDRESS"):
        ng.fetch("http://169.254.169.254/latest/meta-data/", transport=_transport(handler))
    assert called["n"] == 0


def test_log_safe_host_never_includes_the_query_or_credentials():
    assert ng.safe_host("https://example.edu/path?token=secret") == "example.edu"
    assert "secret" not in ng.safe_host("https://example.edu/path?token=secret")


# --------------------------------------------------------------- real paths
def test_extraction_refuses_a_private_url_without_network():
    # The URL is an IP literal, so validation refuses it before any socket or DNS call.
    page = extraction.fetch_page("http://127.0.0.1/admin")
    assert page.status.startswith("BLOCKED_")
    assert page.status == "BLOCKED_PRIVATE_ADDRESS"


def test_extraction_run_reports_a_blocked_root_page(db):
    out = extraction.run(db, name="Nonexistent Institute", country="",
                         website="http://10.0.0.5/", use_llm=False)
    assert out["resolution"]["status"] == "RESOLVED"
    assert out["pages"] and out["pages"][0]["status"].startswith("BLOCKED_")
    assert out["findings"] == []


def test_source_check_reports_a_private_citation_as_blocked_not_broken():
    result = source_verification.check_url("http://127.0.0.1/private")
    assert result["state"] == source_verification.BLOCKED
    assert result["http_status"] is None
    assert "URL safety policy" in result["note"]


def test_source_check_still_reports_dns_failure_as_unreachable():
    result = source_verification.check_url("https://this-host-does-not-resolve-zzz9.invalid")
    assert result["state"] == source_verification.UNREACHABLE
    assert "could not check" in result["note"]
