"""Test fixtures: an isolated SQLite database, a seeded app and an authenticated client.

Nothing here touches the development database. Each session gets its own temporary file,
seeded with the same data the real app ships with, so tests exercise realistic rows.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------------------
# The PostgreSQL contract
#
# The concurrency, locking and migration guarantees are only provable on PostgreSQL, and those
# tests skip themselves when no server is configured - which is right locally and wrong in a job
# whose entire purpose is to run them. A skipped test looks identical to a passing one in a CI
# summary, so the skip has to be made into a failure somewhere. That is what this switch does:
#
#   SCHOLARA_REQUIRE_PG_TESTS=1
#
# With it set, the session refuses to run unless a PostgreSQL URL exists, refuses to run if no
# PostgreSQL test was collected, and fails the run if any of them was skipped. Without it (the
# default, and what SQLite contributors get) the tests skip and say so.
def _required(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


_REQUIRE_PG = _required("SCHOLARA_REQUIRE_PG_TESTS")
_REQUIRE_REDIS = _required("SCHOLARA_REQUIRE_REDIS_TESTS")
_MARKERS = {"pg": _REQUIRE_PG, "redis_live": _REQUIRE_REDIS}
_SKIPPED_REQUIRED: list[str] = []

TMP_DIR = Path(tempfile.mkdtemp(prefix="scholara-tests-"))
DB_PATH = TMP_DIR / "test.db"

# Must be set before app.core.config is first imported.
#
# The default is a throwaway SQLite file, which is what the suite normally uses. Setting
# ``SCHOLARA_TEST_DATABASE_URL`` runs the same suite against a real server instead - the only way
# to exercise row locks, SKIP LOCKED, unique-violation behaviour and JSONB comparison, none of
# which SQLite has. The value must point at a disposable database; nothing in the suite guards
# against a non-disposable target, so the caller owns that decision.
_TEST_DATABASE_URL = os.environ.get("SCHOLARA_TEST_DATABASE_URL", "").strip()
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL or f"sqlite:///{DB_PATH}"
os.environ["STORAGE_DIR"] = str(TMP_DIR / "private")
os.environ["RATE_LIMIT_ENABLED"] = "false"   # per-test limits are asserted explicitly
os.environ["SCHEDULER_ENABLED"] = "false"    # no background thread during tests
os.environ["ENVIRONMENT"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
# The suite authenticates as the seeded admin with a fixed password. A developer's real
# backend/.env overrides ADMIN_EMAIL/ADMIN_PASSWORD (it holds their production admin), so
# seeding and the login helper disagreed and every admin test failed with a 401. Pin both
# here so the suite is hermetic and never depends on, or touches, a real account.
os.environ["ADMIN_EMAIL"] = "admin@scholara.com.bd"
os.environ["ADMIN_PASSWORD"] = "Admin@12345"
# Never let a developer's real backend/.env make the suite talk to production Upstash.
os.environ["REDIS_URL"] = ""
os.environ["CELERY_BROKER_URL"] = ""
os.environ["CELERY_RESULT_BACKEND"] = ""
# Third-party credentials too: a developer's .env holds real Meta/Meta-pixel/LLM keys, and
# tests that assert "no credentials configured" (e.g. CAPI degrading to LOGGED_LOCALLY)
# silently stopped testing that behaviour. Every test that needs a provider patches
# settings directly, so nothing is lost by starting from an empty external config.
for _external_credential in (
    "META_PIXEL_ID", "META_CAPI_ACCESS_TOKEN", "FACEBOOK_PAGE_ID",
    "FACEBOOK_PAGE_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_APP_SECRET", "XAI_API_KEY", "MISTRAL_API_KEY", "GEMINI_API_KEY",
    "GA4_MEASUREMENT_ID", "GOOGLE_ADS_CONVERSION_ID", "S3_ACCESS_KEY", "S3_SECRET_KEY",
    # SMTP too: a developer's real Gmail App Password in backend/.env must never be used by a
    # test run, and tests that assert "not configured" must actually see an empty mailbox.
    "SMTP_HOST", "SMTP_USER", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL",
):
    os.environ[_external_credential] = ""

import app.models  # noqa: E402,F401  (register metadata first)
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402  (aliased: `app` is also the package name)
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    # Drop first. The default SQLite file is fresh, but ``SCHOLARA_TEST_DATABASE_URL`` points at a
    # database that survives between runs, and a run that was interrupted never reached its own
    # teardown. Leftover tables then make ``create_all`` a no-op and the re-seed allocates ids from
    # the *continued* sequence, so any test keyed to a particular row id fails on the next run -
    # which is exactly how tests/test_w7_read_audit.py started failing intermittently. Dropping
    # first makes every run start from the same schema regardless of what the last one did.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    from app.core.config import settings

    if engine.dialect.name == "postgresql":
        # ``create_all`` builds the models, but ``embedding_vec`` is added by a migration
        # (pgvector), not declared on the model. Mirror it here so a suite run against a real
        # server exercises the same schema the migration produces instead of failing on a column
        # the product's own migrations would have created.
        from sqlalchemy import text as _text

        with engine.begin() as conn:
            # The concurrency suite builds its own schema; a leftover one from an interrupted run
            # would carry rows into the next run's assertions.
            conn.execute(_text("DROP SCHEMA IF EXISTS pgcamp CASCADE"))
            conn.execute(_text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(_text(
                f"ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS "
                f"embedding_vec vector({int(settings.embedding_dim)})"))
    from app import seed

    seed.run(reset=True)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="session")
def client():
    with TestClient(fastapi_app) as c:
        yield c


@pytest.fixture
def smtp(monkeypatch):
    """A working mailbox backed by the fake SMTP server (no socket is ever opened)."""
    from smtp_fake import configure
    return configure(monkeypatch)


@pytest.fixture
def unconfigured(monkeypatch):
    """No mailbox configured - the default state of a fresh environment."""
    from smtp_fake import unconfigure
    unconfigure(monkeypatch)


@pytest.fixture(scope="session")
def api() -> str:
    from app.core.config import settings

    return settings.api_v1_prefix


@pytest.fixture(scope="session")
def admin_token(client, api) -> str:
    """
    A full ADMIN session. ADMIN is in MFA_REQUIRED_ROLES, so the password alone no longer
    returns a token - the login reports mfa_required and the challenge must be answered with
    a code derived from the seeded development secret.
    """
    from app.core import mfa
    from app.seed_data import DEV_MFA_SECRET_ADMIN

    creds = {"email": "admin@scholara.com.bd", "password": "Admin@12345"}
    res = client.post(f"{api}/admin/auth/login", json=creds)
    assert res.status_code == 200, res.text
    body = res.json()

    if body.get("access_token") and not body.get("mfa_enrolment_required"):
        return body["access_token"]

    assert body.get("mfa_required") is True, body
    challenge = body.get("challenge_token")
    assert challenge, body
    res = client.post(f"{api}/admin/auth/mfa/challenge",
                      json={"challenge_token": challenge,
                            "code": mfa.current_code(DEV_MFA_SECRET_ADMIN)})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


@pytest.fixture(scope="session")
def auth(admin_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def student_token(client, api) -> str:
    """A real student profile created through the public API."""
    res = client.post(f"{api}/profiles", json={
        "full_name": "Test Student", "email": "test.student@example.com", "phone": "+8801711111111",
        "highest_qualification": "Bachelor", "major": "Computer Science", "cgpa": 3.4,
        "cgpa_scale": 4.0, "ielts_overall": 6.5, "target_degree_level": "MASTER",
        "target_discipline": "Computer Science", "budget_per_year": 25000,
        "preferred_countries": ["United States"],
    })
    assert res.status_code == 200, res.text
    return res.json()["token"]


@pytest.fixture(scope="session")
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ------------------------------------------------------------- PostgreSQL enforcement hooks
def pytest_collection_modifyitems(config, items):
    """Fail loudly, before any test runs, when a required live-service run cannot happen."""
    urls = {"pg": "SCHOLARA_PG_URL", "redis_live": "SCHOLARA_REDIS_URL"}
    # Only requirements whose tests are actually part of *this* run are enforced. A job that
    # requires Redis should not fail a step that collects one PostgreSQL module, and a job that
    # requires PostgreSQL should not fail because a Redis module was filtered out. What matters
    # is that a required service's tests, when present, never skip for want of a URL.
    for marker in [m for m, on in _MARKERS.items() if on]:
        if not [item for item in items if marker in item.keywords]:
            continue
        if not os.environ.get(urls[marker], "").strip():
            raise pytest.UsageError(
                f"{marker} tests are part of this run but {urls[marker]} is empty. They would "
                "have been skipped, which is exactly what this run exists to prevent.")


def pytest_runtest_logreport(report):
    """Record any required live-service test that skipped itself anyway.

    Every phase is inspected, not just setup: a marker (`skipif`) skips during setup, but a
    fixture or a test body calling ``pytest.skip`` reports the skip during call or teardown, and
    that is the case a naive setup-only check misses.
    """
    if not report.skipped:
        return
    keywords = getattr(report, "keywords", {})
    if any(_MARKERS.get(marker) and marker in keywords for marker in _MARKERS):
        if report.nodeid not in _SKIPPED_REQUIRED:
            _SKIPPED_REQUIRED.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    """Turn a silent skip into a failed job."""
    if not _SKIPPED_REQUIRED:
        return
    session.exitstatus = 1
    print("\n" + "=" * 78)
    print("A live service was required for this run, but these tests were SKIPPED:")
    for nodeid in _SKIPPED_REQUIRED:
        print(f"  - {nodeid}")
    print("A job that verifies a service may not pass by skipping its tests for it.")
    print("=" * 78)
