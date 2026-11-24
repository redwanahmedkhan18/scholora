"""Deployment connectivity, URL normalisation and startup-safety tests.

These are unit tests: no test requires a live Supabase or Upstash endpoint. Live checks
belong in scripts/check_connectivity.py and must be run from Render.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI

from app.core import connectivity
from app.core.config import settings


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
