"""Connection URL handling and dependency diagnostics.

Everything here is deliberately safe to log: URLs are parsed into a small summary that
never contains a password, and failures are reported by exception class rather than by
the driver's message (which can echo hostnames or conninfo).

The production target is Supabase behind its IPv4 Session Pooler and Upstash over TLS.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.engine import make_url

from app.core.config import settings

log = logging.getLogger(__name__)

PRODUCTION_ENVIRONMENTS = {"production", "prod"}


# --------------------------------------------------------------------------- helpers
def _clean(raw: str) -> str:
    """Trim whitespace and the stray quotes that copy/paste from dashboards produces."""
    return (raw or "").strip().strip('"').strip("'").strip()


def _is_production(environment: Optional[str] = None) -> bool:
    return (environment or settings.environment).strip().lower() in PRODUCTION_ENVIRONMENTS


def mask_host(host: str) -> str:
    """Keep enough of a hostname to identify it without publishing the project ref."""
    host = (host or "").strip()
    if not host:
        return ""
    if host in {"localhost", "127.0.0.1", "::1"}:
        return host
    if host.endswith(".pooler.supabase.com"):
        return "….pooler.supabase.com"
    if host.endswith(".supabase.co"):
        return "db.….supabase.co"
    if len(host) <= 16:
        return host
    return f"{host[:4]}…{host[-8:]}"


def is_supabase_direct_host(host: str) -> bool:
    host = (host or "").lower()
    return host.endswith(".supabase.co") and ".pooler.supabase.com" not in host


def is_supabase_pooler_host(host: str) -> bool:
    return (host or "").lower().endswith(".pooler.supabase.com")


# --------------------------------------------------------------------------- PostgreSQL
def normalize_database_url(raw: str) -> str:
    """Return a SQLAlchemy URL that always uses psycopg 3 for PostgreSQL.

    ``postgres://`` and ``postgresql://`` are valid URLs but SQLAlchemy maps them to
    psycopg2, which is not installed. Supabase hosts also get ``sslmode=require`` unless
    the URL already specifies a mode, so TLS is never silently dropped.
    """
    raw = _clean(raw)
    if not raw:
        return ""

    try:
        url = make_url(raw)
    except Exception as exc:
        raise ValueError("DATABASE_URL is not a valid SQLAlchemy URL") from exc

    if url.drivername in {"postgres", "postgresql", "postgresql+psycopg2"}:
        url = url.set(drivername="postgresql+psycopg")

    if url.drivername.startswith("postgresql"):
        host = url.host or ""
        if (is_supabase_direct_host(host) or is_supabase_pooler_host(host)) and "sslmode" not in url.query:
            url = url.set(query={**url.query, "sslmode": "require"})

    return url.render_as_string(hide_password=False)


def summarize_database_url(raw: str) -> Dict[str, Any]:
    """A redacted description of the configured database connection."""
    raw = _clean(raw)
    if not raw:
        return {
            "configured": False, "driver": "", "host": "", "port": None,
            "database": "", "sslmode": "", "username_present": False,
            "is_supabase_direct": False, "is_supabase_pooler": False,
            "pooler_username_ok": None,
        }

    try:
        url = make_url(normalize_database_url(raw))
    except Exception:
        return {
            "configured": True, "driver": "invalid", "host": "", "port": None,
            "database": "", "sslmode": "", "username_present": False,
            "is_supabase_direct": False, "is_supabase_pooler": False,
            "pooler_username_ok": None,
        }

    host = url.host or ""
    pooler = is_supabase_pooler_host(host)
    username = url.username or ""
    return {
        "configured": True,
        "driver": url.drivername,
        "host": mask_host(host),
        "port": url.port or (5432 if url.drivername.startswith("postgresql") else None),
        "database": url.database or "",
        "sslmode": url.query.get("sslmode", ""),
        "username_present": bool(username),
        "is_supabase_direct": is_supabase_direct_host(host),
        "is_supabase_pooler": pooler,
        # Session Pooler usernames are `postgres.<project-ref>`; a bare `postgres`
        # authenticates against the pooler's default project and is a common mistake.
        "pooler_username_ok": ("." in username) if pooler else None,
    }


def assert_safe_database_url(raw: str, environment: Optional[str] = None) -> None:
    """Refuse production on SQLite and reject URLs without a supported driver."""
    raw = _clean(raw)
    if not raw:
        raise RuntimeError("DATABASE_URL is empty. Set the Supabase Session Pooler URL.")
    if raw.startswith("sqlite"):
        if _is_production(environment):
            raise RuntimeError(
                "Refusing to start production on SQLite. Set DATABASE_URL to the "
                "Supabase Session Pooler connection string."
            )
        return
    if not raw.startswith(("postgresql+psycopg://", "postgresql://", "postgres://")):
        raise RuntimeError(
            "DATABASE_URL must use the PostgreSQL psycopg driver "
            "(postgresql+psycopg://…)."
        )


def database_connect_args(raw: Optional[str] = None) -> Dict[str, Any]:
    """psycopg connection options for the configured database URL."""
    url = normalize_database_url(raw or settings.database_url)
    if not url.startswith("postgres"):
        return {}
    summary = summarize_database_url(url)
    args: Dict[str, Any] = {
        "connect_timeout": int(settings.db_connect_timeout),
        "application_name": "scholara",
    }
    sslmode = (summary.get("sslmode") or settings.db_sslmode or "").strip()
    if sslmode:
        args["sslmode"] = sslmode
    return args


# --------------------------------------------------------------------------- Redis
def normalize_redis_url(raw: str) -> str:
    """Return a redis-py TCP URL, upgrading Upstash's ``redis://`` to TLS.

    Upstash requires TLS on its TCP endpoint. Dashboard snippets occasionally omit the
    extra ``s`` or arrive wrapped in quotes; both are corrected here rather than disabling
    certificate verification.
    """
    raw = _clean(raw)
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        raise ValueError("Upstash REST URLs are not Redis TCP URLs; use rediss://…")

    try:
        parts = urlsplit(raw)
    except Exception as exc:
        raise ValueError("REDIS_URL is not a valid URL") from exc

    if parts.scheme not in {"redis", "rediss"}:
        raise ValueError("Redis URL must start with redis:// or rediss://")

    scheme = parts.scheme
    if scheme == "redis" and (parts.hostname or "").lower().endswith(".upstash.io"):
        scheme = "rediss"

    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))


def summarize_redis_url(raw: str) -> Dict[str, Any]:
    """A redacted description of a Redis URL."""
    raw = _clean(raw)
    if not raw:
        return {"configured": False, "scheme": "", "tls": False, "host": "", "port": None, "db": ""}
    try:
        parts = urlsplit(raw)
    except Exception:
        return {"configured": True, "scheme": "invalid", "tls": False, "host": "", "port": None, "db": ""}
    return {
        "configured": True,
        "scheme": parts.scheme,
        "tls": parts.scheme == "rediss",
        "host": mask_host(parts.hostname or ""),
        "port": parts.port or 6379,
        "db": parts.path or "/",
    }


def redis_connect_kwargs(raw: str, timeout: Optional[float] = None) -> Dict[str, Any]:
    """redis-py keyword arguments with explicit timeouts and TLS verification."""
    url = normalize_redis_url(raw)
    parts = urlsplit(url)
    connect_timeout = float(timeout or settings.redis_socket_connect_timeout)
    kwargs: Dict[str, Any] = {
        "decode_responses": True,
        "socket_connect_timeout": connect_timeout,
        "socket_timeout": float(timeout or settings.redis_socket_timeout),
        "health_check_interval": int(settings.redis_health_check_interval),
        "retry_on_timeout": True,
    }
    if parts.scheme == "rediss":
        kwargs["ssl_cert_reqs"] = "required"
    return kwargs


# --------------------------------------------------------------------------- probes
_probe_lock = threading.Lock()
_probe_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
PROBE_TTL_SECONDS = 5.0


def _cached_probe(key: str, producer) -> Dict[str, Any]:
    now = time.monotonic()
    with _probe_lock:
        hit = _probe_cache.get(key)
        if hit and now - hit[0] < PROBE_TTL_SECONDS:
            return dict(hit[1])
    result = producer()
    with _probe_lock:
        _probe_cache[key] = (now, result)
    return dict(result)


def _probe_key(label: str, url: str) -> str:
    return f"{label}:{hashlib.sha256(url.encode('utf-8')).hexdigest()}"


def check_database(engine_obj=None) -> Dict[str, Any]:
    """Run a harmless ``SELECT 1`` and return a redacted status report."""
    from sqlalchemy import text

    summary = summarize_database_url(settings.database_url)
    started = time.monotonic()
    try:
        if engine_obj is None:
            from app.core.database import engine as engine_obj
        with engine_obj.connect() as connection:
            connection.execute(text("SELECT 1"))
        ok = True
        error = None
    except Exception as exc:  # noqa: BLE001 - category only, never the driver message
        ok = False
        error = type(exc).__name__
    return {
        "ok": ok,
        "configured": summary.get("configured", False),
        "driver": summary.get("driver", ""),
        "host": summary.get("host", ""),
        "port": summary.get("port"),
        "database": summary.get("database", ""),
        "sslmode": summary.get("sslmode", ""),
        "is_supabase_direct": summary.get("is_supabase_direct", False),
        "is_supabase_pooler": summary.get("is_supabase_pooler", False),
        "pooler_username_ok": summary.get("pooler_username_ok"),
        "error": error,
        "latency_ms": round((time.monotonic() - started) * 1000, 1),
    }


def check_redis_url(raw: str, label: str, required: bool = False,
                    timeout: Optional[float] = None) -> Dict[str, Any]:
    """Ping one Redis TCP endpoint. Never reports the URL or password."""
    if not _clean(raw):
        return {
            "ok": not required,
            "configured": False,
            "required": required,
            "label": label,
            "scheme": "",
            "tls": False,
            "host": "",
            "port": None,
            "error": None if not required else "NotConfigured",
            "latency_ms": 0.0,
        }

    summary = summarize_redis_url(raw)
    key = _probe_key(label, _clean(raw))

    def probe() -> Dict[str, Any]:
        started = time.monotonic()
        try:
            import redis as redis_lib
        except Exception as exc:  # noqa: BLE001
            return {**summary, "ok": False, "required": required, "label": label,
                    "error": type(exc).__name__, "latency_ms": 0.0}

        client = None
        try:
            url = normalize_redis_url(raw)
            client = redis_lib.from_url(url, **redis_connect_kwargs(url, timeout=timeout))
            client.ping()
            ok = True
            error = None
        except Exception as exc:  # noqa: BLE001
            ok = False
            error = type(exc).__name__
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
        return {
            **summary,
            "ok": ok,
            "required": required,
            "label": label,
            "error": error,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
        }

    return _cached_probe(key, probe)


def readiness_report() -> Dict[str, Any]:
    """Database + Redis + Celery-broker readiness, with production requirements enforced."""
    production = _is_production()
    database = check_database()
    cache = check_redis_url(
        settings.redis_url, "cache",
        required=bool(_clean(settings.redis_url)) or production,
    )
    broker = check_redis_url(
        settings.celery_broker_url, "celery_broker",
        required=bool(_clean(settings.celery_broker_url)) or production,
    )
    ready = bool(database["ok"] and cache["ok"] and broker["ok"])
    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "environment": settings.environment,
        "checks": {
            "database": database,
            "cache": cache,
            "celery_broker": broker,
        },
    }
