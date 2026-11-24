"""Test fixtures: an isolated SQLite database, a seeded app and an authenticated client.

Nothing here touches the development database. Each session gets its own temporary file,
seeded with the same data the real app ships with, so tests exercise realistic rows.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

TMP_DIR = Path(tempfile.mkdtemp(prefix="scholara-tests-"))
DB_PATH = TMP_DIR / "test.db"

# Must be set before app.core.config is first imported.
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["STORAGE_DIR"] = str(TMP_DIR / "private")
os.environ["RATE_LIMIT_ENABLED"] = "false"   # per-test limits are asserted explicitly
os.environ["SCHEDULER_ENABLED"] = "false"    # no background thread during tests
os.environ["ENVIRONMENT"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
# Never let a developer's real backend/.env make the suite talk to production Upstash.
os.environ["REDIS_URL"] = ""
os.environ["CELERY_BROKER_URL"] = ""
os.environ["CELERY_RESULT_BACKEND"] = ""

from fastapi.testclient import TestClient  # noqa: E402

import app.models  # noqa: E402,F401  (register metadata first)
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402  (aliased: `app` is also the package name)


@pytest.fixture(scope="session", autouse=True)
def _database():
    Base.metadata.create_all(engine)
    from app import seed

    seed.run(reset=True)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="session")
def client():
    with TestClient(fastapi_app) as c:
        yield c


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
    res = client.post(f"{api}/admin/auth/mfa/challenge",
                      json={**creds, "code": mfa.current_code(DEV_MFA_SECRET_ADMIN)})
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
