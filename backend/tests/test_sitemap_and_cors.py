"""The sitemap feed and the CORS policy.

Two contracts are pinned here:

1. `/sitemap-data` must never turn a database outage into an empty-but-successful answer.
   The Next.js build treats a 404 as "this resource does not exist" and freezes that
   answer into a static page, so a throttled or unavailable database that answered
   `200 {"universities": []}` would publish a site that claims it has no content. A
   dependency failure is a 503. A genuinely empty table is a 200 with an empty list.
2. CORS must never pair a wildcard origin with credentials.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.models.education import Country, Program, University


API_PREFIX = "/api/v1"


# ------------------------------------------------------------------ normal operation
def test_sitemap_returns_every_documented_key(client):
    data = client.get(f"{API_PREFIX}/sitemap-data").json()
    for key in ("countries", "universities", "programs", "scholarships", "faculty",
                "faculties", "services", "success_stories", "events", "posts"):
        assert key in data, f"{key} missing from the sitemap contract"
        assert isinstance(data[key], list)


def test_sitemap_entries_have_slug_and_timestamp(client):
    data = client.get(f"{API_PREFIX}/sitemap-data").json()
    for key, rows in data.items():
        for row in rows:
            assert row.get("slug"), f"{key} row without a slug: {row}"
            # `lastmod` must be a parseable date; None is allowed and becomes "now".
            if row.get("updated") is not None:
                assert isinstance(row["updated"], str) and len(row["updated"]) >= 10


def test_sitemap_is_ordered_by_slug(client):
    """Stable ordering keeps an unchanged site's sitemap byte-identical between builds."""
    data = client.get(f"{API_PREFIX}/sitemap-data").json()
    for key, rows in data.items():
        slugs = [r["slug"] for r in rows]
        assert slugs == sorted(slugs), f"{key} is not ordered by slug"


def test_sitemap_omits_unpublished_content(client):
    """A draft must not become an indexable URL.

    Rather than depend on the seed data happening to contain a draft, one published post
    is temporarily unpublished and restored afterwards, so the assertion always runs.
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        post = db.query(BlogPost).filter(BlogPost.is_published.is_(True)).first()
        assert post is not None, "seed data should publish at least one post"
        slug = post.slug

        published = {p["slug"] for p in client.get(f"{API_PREFIX}/sitemap-data").json()["posts"]}
        assert slug in published

        post.is_published = False
        db.commit()
        try:
            draft = {p["slug"] for p in client.get(f"{API_PREFIX}/sitemap-data").json()["posts"]}
            assert slug not in draft, "an unpublished post leaked into the sitemap"
        finally:
            post.is_published = True
            db.commit()
    finally:
        db.close()


def test_sitemap_university_rows_carry_their_country(client):
    """The frontend builds /universities/{country}/{slug}; a null country is a broken URL."""
    data = client.get(f"{API_PREFIX}/sitemap-data").json()
    assert data["universities"], "seed data should publish universities"
    for row in data["universities"]:
        assert row["country"], f"university {row['slug']} has no country slug"


# ------------------------------------------------------------------ empty database
def test_sitemap_handles_empty_tables_as_success(client, monkeypatch):
    """No rows is a legitimate state (a fresh install) and must be a 200 with empty lists."""
    from sqlalchemy.orm import Session

    for model in (Country, University, Program):
        monkeypatch.setattr(
            Session, "execute",
            lambda self, statement, *a, **k: _EmptyResult(),
            raising=False,
        )
    res = client.get(f"{API_PREFIX}/sitemap-data")
    assert res.status_code == 200
    body = res.json()
    assert body["countries"] == [] and body["universities"] == [] and body["programs"] == []


class _EmptyResult:
    """Minimal stand-in for a SQLAlchemy Result that yields no rows."""

    def scalars(self):
        return self

    def unique(self):
        return self

    def all(self):
        return []


# ---------------------------------------------------------- database unavailability
def test_sitemap_reports_503_when_the_database_is_down(client, monkeypatch):
    """A connection failure must not look like an empty site, and must be retryable."""
    from sqlalchemy.orm import Session

    def boom(self, statement, *a, **k):
        raise OperationalError("SELECT 1", {}, Exception("connection is bad"))

    monkeypatch.setattr(Session, "execute", boom, raising=False)
    res = client.get(f"{API_PREFIX}/sitemap-data")

    assert res.status_code == 503, "a database outage must not be reported as success"
    assert res.headers.get("Retry-After"), "503 must tell the caller when to retry"
    body = res.text.lower()
    # No infrastructure detail may reach a public caller.
    for leak in ("psycopg", "supabase", "postgres", "5432", "traceback", "connection is bad"):
        assert leak not in body, f"error response leaked {leak!r}"


def test_sitemap_failure_is_not_cached_as_a_404(client, monkeypatch):
    """404 has a specific meaning to the frontend (notFound()); an outage must not use it."""
    from sqlalchemy.orm import Session

    def boom(self, statement, *a, **k):
        raise OperationalError("SELECT 1", {}, Exception("connection is bad"))

    monkeypatch.setattr(Session, "execute", boom, raising=False)
    assert client.get(f"{API_PREFIX}/sitemap-data").status_code != 404


# ------------------------------------------------------------------------- CORS policy
def test_cors_never_combines_wildcard_origin_with_credentials(monkeypatch):
    from app.core.config import Settings

    wildcard = Settings(cors_origins="*", public_site_url="https://scholara.example")
    assert wildcard.cors_allows_any_origin is True
    assert wildcard.cors_allow_credentials is False

    explicit = Settings(cors_origins="https://scholara.example",
                        public_site_url="https://scholara.example")
    assert explicit.cors_allows_any_origin is False
    assert explicit.cors_allow_credentials is True


def test_bare_hostname_cors_entry_is_upgraded_to_an_origin():
    """`CORS_ORIGINS=scholara.example` can never match a browser Origin header."""
    from app.core.config import Settings

    config = Settings(cors_origins="scholara.onrender.com, https://preview.example/",
                      public_site_url="https://www.scholara.example")
    assert config.cors_origin_list == [
        "https://scholara.onrender.com",
        "https://preview.example",
        "https://www.scholara.example",
    ]


def test_cors_allows_the_deployed_site_origin_without_listing_it_twice():
    from app.core.config import Settings

    config = Settings(cors_origins="https://scholara.example",
                      public_site_url="https://scholara.example/")
    assert config.cors_origin_list == ["https://scholara.example"]


def _preflight(client, origin: str):
    return client.options(
        f"{API_PREFIX}/settings",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type,accept",
        },
    )


def test_configured_origin_passes_preflight_and_foreign_origin_does_not(client):
    """Exercise the real middleware: the deployed site is allowed, strangers are not."""
    from app.core.config import settings

    if settings.cors_allows_any_origin:
        res = _preflight(client, "https://anything.example")
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") == "*"
        # A wildcard must never be accompanied by credentialed access.
        assert "access-control-allow-credentials" not in res.headers
        return

    allowed = settings.cors_origin_list[0]
    assert _preflight(client, allowed).status_code == 200
    assert _preflight(client, "https://evil.example").status_code == 400


def test_cors_exposes_retry_after_so_a_503_can_be_retried(client):
    from app.core.config import settings

    origin = "*" if settings.cors_allows_any_origin else settings.cors_origin_list[0]
    res = client.get(f"{API_PREFIX}/settings", headers={"Origin": origin})
    exposed = (res.headers.get("access-control-expose-headers") or "").lower()
    assert "retry-after" in exposed


from app.models.content import BlogPost  # noqa: E402  (used by the drafts test)
