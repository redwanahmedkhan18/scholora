"""Deployment connectivity, URL normalisation and startup-safety tests.

These are unit tests: no test requires a live Supabase or Upstash endpoint. Live checks
belong in scripts/check_connectivity.py and must be run from Render.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from app.core import connectivity
from app.core.config import settings
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

DIRECT_SUPABASE = (
    "postgresql://postgres:p%40ssword@db.abcdefghijklmnop.supabase.co:5432/postgres"
)
POOLER_SUPABASE = (
    "postgresql://postgres.abcdefghijklmnop:p%40ssword@"
    "aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
)


def test_normalize_database_url_uses_psycopg_and_ssl_for_supabase():
    out = connectivity.normalize_database_url(DIRECT_SUPABASE)
    assert out.startswith("postgresql+psycopg://")
    assert "sslmode=require" in out
    assert "p%40ssword" in out


def test_normalize_database_url_preserves_existing_sslmode():
    raw = "postgres://user:p%40ss@db.example.com:5432/app?sslmode=verify-full"
    out = connectivity.normalize_database_url(raw)
    assert out.startswith("postgresql+psycopg://")
    assert out.count("sslmode=") == 1
    assert "sslmode=verify-full" in out


def test_normalize_database_url_leaves_sqlite_untouched():
    raw = "sqlite:///./data/scholara.db"
    assert connectivity.normalize_database_url(raw) == raw


def test_summarize_database_url_never_leaks_the_password_or_project_ref():
    summary = connectivity.summarize_database_url(DIRECT_SUPABASE)
    rendered = json.dumps(summary)
    assert "p@ssword" not in rendered
    assert "p%40ssword" not in rendered
    assert "abcdefghijklmnop" not in rendered
    assert summary["driver"] == "postgresql+psycopg"
    assert summary["port"] == 5432
    assert summary["sslmode"] == "require"
    assert summary["is_supabase_direct"] is True
    assert summary["is_supabase_pooler"] is False


def test_summarize_pooler_detects_username_format():
    good = connectivity.summarize_database_url(POOLER_SUPABASE)
    assert good["is_supabase_pooler"] is True
    assert good["pooler_username_ok"] is True

    bare = connectivity.summarize_database_url(
        "postgresql://postgres:pw@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
    )
    assert bare["pooler_username_ok"] is False


def test_production_refuses_sqlite_but_development_allows_it():
    with pytest.raises(RuntimeError):
        connectivity.assert_safe_database_url("sqlite:///./data/scholara.db", "production")
    connectivity.assert_safe_database_url("sqlite:///./data/scholara.db", "development")


def test_database_connect_args_include_timeout_and_ssl():
    args = connectivity.database_connect_args(DIRECT_SUPABASE)
    assert args["sslmode"] == "require"
    assert args["connect_timeout"] == settings.db_connect_timeout
    assert args["application_name"] == "scholara"


def test_confirmed_session_pooler_configuration_is_recognised():
    """The exact production shape: aws-1, port 5432, session-pooler username, TLS."""
    url = (
        "postgresql+psycopg://postgres.tfuznncjdupjxdgptroj:p%40ssword@"
        "aws-1-ap-south-1.pooler.supabase.com:5432/postgres?sslmode=require"
    )
    summary = connectivity.summarize_database_url(url)
    assert summary["driver"] == "postgresql+psycopg"
    assert summary["port"] == 5432
    assert summary["database"] == "postgres"
    assert summary["sslmode"] == "require"
    assert summary["is_supabase_pooler"] is True
    assert summary["is_supabase_direct"] is False
    assert summary["pooler_username_ok"] is True
    # The password is decoded by URL parsing and must never appear in the summary.
    assert "p%40ssword" not in json.dumps(summary)
    assert "p@ssword" not in json.dumps(summary)
    assert "tfuznncjdupjxdgptroj" not in json.dumps(summary)


def test_pooler_with_a_bare_postgres_username_is_reported_as_wrong():
    summary = connectivity.summarize_database_url(
        "postgresql://postgres:secret@aws-1-ap-south-1.pooler.supabase.com:5432/postgres"
    )
    assert summary["is_supabase_pooler"] is True
    assert summary["pooler_username_ok"] is False


def test_check_database_succeeds_with_a_real_engine():
    """A genuine connection round-trip (SQLite is the test suite's engine, not production)."""
    engine = create_engine("sqlite://")
    try:
        result = connectivity.check_database(engine, log_failure=False)
    finally:
        engine.dispose()
    assert result["ok"] is True
    assert result["error"] is None


class _FakeDiag:
    sqlstate = "28P01"
    severity = "FATAL"
    message_primary = "password authentication failed for user"
    message_detail = None
    message_hint = None


class _FakePsycopgError(Exception):
    sqlstate = "28P01"
    diag = _FakeDiag()


def _operational_error(message: str, sqlstate: str = "28P01") -> OperationalError:
    orig = _FakePsycopgError(message)
    orig.sqlstate = sqlstate
    orig.diag = _FakeDiag()
    orig.diag.sqlstate = sqlstate
    return OperationalError("SELECT 1", {}, orig)


def test_database_failure_includes_sanitized_type_message_and_sqlstate():
    secret = "p@ssword-super-secret"
    url = f"postgresql://postgres:{secret}@db.abcdefghijklmnop.supabase.co:5432/postgres"
    exc = _operational_error(
        "connection failed: password=super-secret "
        f"url={url} token=abc123"
    )
    detail = connectivity.describe_database_error(exc, url)

    assert detail["error_type"] == "OperationalError"
    assert detail["sqlstate"] == "28P01"
    assert detail["connection"]["driver"] == "postgresql+psycopg"
    assert detail["connection"]["port"] == 5432
    assert detail["connection"]["database"] == "postgres"
    assert detail["connection"]["sslmode"] == "require"
    assert detail["connection"]["connect_timeout"] == settings.db_connect_timeout
    assert detail["connection"]["is_supabase_pooler"] is False

    # The SQLAlchemy wrapper and the psycopg cause are both present.
    assert [c["type"] for c in detail["causes"]][:2] == ["OperationalError", "_FakePsycopgError"]
    assert any(c.get("db_message") for c in detail["causes"])

    rendered = json.dumps(detail)
    for leak in (secret, "super-secret", "abc123", "postgresql://"):
        assert leak not in rendered, f"diagnostics leaked {leak!r}"


def test_authentication_failure_is_classified_without_exposing_credentials(caplog):
    secret = "auth-secret"
    exc = _operational_error(
        f"FATAL: password authentication failed; password={secret}",
        sqlstate="28P01",
    )
    logger = logging.getLogger("scholara.test.db")
    with caplog.at_level(logging.ERROR, logger=logger.name):
        payload = connectivity.log_database_error(logger, "auth failed", exc)

    assert payload["sqlstate"] == "28P01"
    assert payload["error_type"] == "OperationalError"
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "OperationalError" in logs
    assert "28P01" in logs
    assert secret not in logs
    assert "password=<redacted>" in logs


def test_probe_database_endpoint_reports_dns_and_tcp_without_network(monkeypatch):
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(connectivity.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(connectivity.socket, "create_connection", lambda *a, **k: FakeSocket())

    report = connectivity.probe_database_endpoint(POOLER_SUPABASE, timeout=1)
    assert report["probed"] is True
    assert report["dns"] == {"ok": True, "families": ["IPv4"], "addresses": 1}
    assert report["tcp"] == {"ok": True}
    assert "p%40ssword" not in json.dumps(report)


def test_probe_database_endpoint_is_skipped_for_sqlite():
    report = connectivity.probe_database_endpoint("sqlite:///./data/scholara.db")
    assert report == {"probed": False, "reason": "not-postgresql"}


def test_postgres_engine_uses_pre_ping_and_a_bounded_pool():
    from app.core.database import engine

    # The test suite uses SQLite, but the pool reliability flags are configured on every
    # engine. Production values are asserted separately from Settings below.
    assert engine.pool._pre_ping is True
    assert settings.db_pool_size >= 1
    assert settings.db_max_overflow >= 0
    assert settings.db_pool_timeout >= 1
    assert settings.db_pool_recycle >= 0


def test_normalize_redis_url_upgrades_upstash_to_tls_and_strips_quotes():
    raw = '"redis://default:p%40ss@example.upstash.io:6379/0"'
    out = connectivity.normalize_redis_url(raw)
    assert out.startswith("rediss://")
    summary = connectivity.summarize_redis_url(out)
    assert summary["tls"] is True
    assert "p%40ss" not in json.dumps(summary)
    assert "example" not in json.dumps(summary)


def test_normalize_redis_url_rejects_rest_urls():
    with pytest.raises(ValueError):
        connectivity.normalize_redis_url("https://example.upstash.io")


def test_redis_connect_kwargs_require_certificate_validation():
    kwargs = connectivity.redis_connect_kwargs("rediss://default:pw@example.upstash.io:6379/0")
    assert kwargs["ssl_cert_reqs"] == "required"
    assert kwargs["socket_connect_timeout"] == settings.redis_socket_connect_timeout


def test_missing_redis_is_skipped_or_required():
    optional = connectivity.check_redis_url("", "cache", required=False)
    assert optional["ok"] is True
    assert optional["configured"] is False

    required = connectivity.check_redis_url("", "cache", required=True)
    assert required["ok"] is False
    assert required["error"] == "NotConfigured"


def test_invalid_redis_url_reports_category_not_url():
    result = connectivity.check_redis_url("https://secret-host.upstash.io", "cache", required=True)
    assert result["ok"] is False
    assert result["error"] == "ValueError"
    assert "secret-host" not in json.dumps(result)


def test_database_failure_reports_only_exception_category(monkeypatch):
    class BrokenEngine:
        def connect(self):
            raise RuntimeError("password=super-secret")

    monkeypatch.setattr("app.core.database.engine", BrokenEngine())
    result = connectivity.check_database()
    assert result["ok"] is False
    assert result["error"] == "RuntimeError"
    assert "super-secret" not in json.dumps(result)


def test_production_readiness_requires_redis_and_broker(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "celery_broker_url", "")
    monkeypatch.setattr(connectivity, "check_database", lambda: {"ok": True})

    report = connectivity.readiness_report()
    assert report["ready"] is False
    assert report["checks"]["cache"]["error"] == "NotConfigured"
    assert report["checks"]["celery_broker"]["error"] == "NotConfigured"


def test_readiness_report_requires_database(monkeypatch):
    monkeypatch.setattr(
        connectivity, "check_database",
        lambda: {"ok": False, "error": "OperationalError", "configured": True},
    )
    monkeypatch.setattr(
        connectivity, "check_redis_url",
        lambda *a, **k: {"ok": True, "configured": False, "error": None},
    )
    report = connectivity.readiness_report()
    assert report["ready"] is False
    assert report["status"] == "not_ready"


def test_health_liveness_and_readiness(client):
    live = client.get("/api/health/live")
    assert live.status_code == 200
    assert live.json()["status"] == "ok"

    ready = client.get("/api/health/ready")
    assert ready.status_code == 200
    assert ready.json()["ready"] is True


def test_readiness_returns_503_when_database_is_down(client, monkeypatch):
    monkeypatch.setattr(
        connectivity, "check_database",
        lambda: {"ok": False, "error": "OperationalError", "configured": True},
    )
    res = client.get("/api/health/ready")
    assert res.status_code == 503
    assert res.json()["ready"] is False


def test_lifespan_does_not_create_schema(monkeypatch):
    from app.core.database import Base
    from app.main import lifespan

    called = []
    monkeypatch.setattr(Base.metadata, "create_all", lambda *a, **k: called.append(True))

    async def run():
        async with lifespan(FastAPI()):
            pass

    asyncio.run(run())
    assert called == []


def test_celery_app_builds_and_discovers_tasks():
    from app.services.tasks import JOBS, build_celery_app

    app = build_celery_app("redis://default:pw@example.upstash.io:6379/0", "")
    assert app.main == "scholara"
    assert app.conf.task_ignore_result is True
    assert app.conf.broker_connection_retry_on_startup is True
    assert app.conf.broker_url.startswith("rediss://")
    for name in JOBS:
        assert f"scholara.{name}" in app.tasks


def test_production_disables_the_in_process_scheduler(monkeypatch):
    from app.services import tasks

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "allow_in_process_scheduler_in_production", False)
    assert tasks.scheduler_mode() == "disabled"
    assert tasks.start_scheduler() is False


def test_alembic_has_a_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    script = ScriptDirectory.from_config(Config(str(ini)))
    assert len(script.get_heads()) == 1


def test_only_connection_errors_map_to_a_retryable_503():
    """Integrity/programming errors are bad input or bugs, not a retryable outage."""
    from app.main import app
    from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
    from sqlalchemy.exc import TimeoutError as SATimeoutError

    assert OperationalError in app.exception_handlers
    assert InterfaceError in app.exception_handlers
    assert SATimeoutError in app.exception_handlers
    assert IntegrityError not in app.exception_handlers


def test_database_unavailable_handler_is_generic_and_retryable():
    from app.main import database_unavailable
    from sqlalchemy.exc import OperationalError
    from starlette.requests import Request

    scope = {
        "type": "http", "method": "GET", "path": "/api/v1/public/sitemap-data",
        "headers": [], "query_string": b"", "scheme": "http",
        "server": ("testserver", 80), "client": ("127.0.0.1", 1234), "root_path": "",
    }
    exc = OperationalError("SELECT 1", {}, Exception("connection is bad password=leaky-secret"))
    response = asyncio.run(database_unavailable(Request(scope), exc))

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    body = response.body.decode()
    assert "Database temporarily unavailable" in body
    assert "leaky-secret" not in body
    assert "password" not in body
