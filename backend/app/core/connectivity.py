"""Connection URL handling and dependency diagnostics.

Everything here is deliberately safe to log: URLs are parsed into a small summary that
never contains a password, and failures are reported by exception class rather than by
the driver's message (which can echo hostnames or conninfo).

The production target is Supabase behind its IPv4 Session Pooler and Upstash over TLS.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import socket
import threading
import time
from typing import Any, Dict, Iterator, Optional
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


# ------------------------------------------------------------------- redaction
# Driver messages are useful, but psycopg can echo the DSN, a password-bearing URL or the
# keyword conninfo that produced the failure. These patterns remove those values before a
# message is logged. Only the connection shape (host, port, database, driver, SSL mode) is
# reported by the diagnostics below.
_URL_RE = re.compile(
    r"(?i)\b(?:postgresql(?:\+[a-z0-9_]+)?|postgres|redis|rediss)://[^\s\"']+"
)
_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/@:]+:[^\s/@]+@")
_KEY_VALUE_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|access[_-]?token|api[_-]?key|"
    r"authorization)\b\s*[=:]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;)]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_QUERY_VALUE_RE = re.compile(r"(?i)\b(password|passwd|pwd)=([^&\s]+)")


def sanitize_error_text(value: Any, limit: int = 1200) -> str:
    """Return a driver message with credentials and complete URLs removed.

    The function is deliberately conservative: an unrecognised token is better truncated
    than published. Connection URLs are replaced entirely, userinfo is stripped from any
    other URL, and key/value password forms are redacted.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    text = str(value)
    text = _URL_RE.sub("<database-url redacted>", text)
    text = _USERINFO_RE.sub(r"\1<redacted>@", text)
    text = _QUERY_VALUE_RE.sub(r"\1=<redacted>", text)
    text = _KEY_VALUE_RE.sub(r"\1=<redacted>", text)
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    if len(text) > limit:
        return f"{text[:limit]}…"
    return text


def _iter_exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """Walk ``__cause__``, ``__context__`` and SQLAlchemy's ``orig`` without looping."""
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        nxt = current.__cause__ or current.__context__
        if nxt is None:
            candidate = getattr(current, "orig", None)
            if isinstance(candidate, BaseException):
                nxt = candidate
        current = nxt if isinstance(nxt, BaseException) else None


def _describe_exception(exc: BaseException) -> Dict[str, Any]:
    detail: Dict[str, Any] = {
        "type": type(exc).__name__,
        "module": type(exc).__module__,
    }
    message = sanitize_error_text(exc)
    if message:
        detail["message"] = message

    # SQLAlchemy OperationalError carries a DB-API ``code`` and, for PostgreSQL, a
    # ``statement``/``params`` pair. Parameters are never safe to log.
    code = getattr(exc, "code", None)
    if code is not None:
        detail["sqlalchemy_code"] = sanitize_error_text(code, 200)
    statement = getattr(exc, "statement", None)
    if statement:
        detail["statement"] = sanitize_error_text(statement, 600)
    params = getattr(exc, "params", None)
    if params not in (None, (), [], {}):
        detail["parameters"] = "<redacted>"

    # psycopg 3 exposes SQLSTATE directly and richer fields on ``diag``.
    sqlstate = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    try:
        diag = getattr(exc, "diag", None)
    except Exception:  # noqa: BLE001 - some wrapped errors expose no diagnostics
        diag = None
    if not sqlstate and diag is not None:
        sqlstate = getattr(diag, "sqlstate", None)
    if sqlstate:
        detail["sqlstate"] = str(sqlstate)
    if diag is not None:
        for attr, key in (("severity", "severity"),
                          ("message_primary", "db_message"),
                          ("message_detail", "db_detail"),
                          ("message_hint", "db_hint")):
            raw = getattr(diag, attr, None)
            if raw:
                detail[key] = sanitize_error_text(raw, 600)
    return detail


def describe_database_error(exc: BaseException, raw: Optional[str] = None) -> Dict[str, Any]:
    """A complete, credential-free description of a database failure.

    It includes the exception chain (SQLAlchemy wrapper, psycopg cause), SQLSTATE when the
    driver provides it, and the connection shape taken from the URL that was actually used.
    It never includes a password, a full URL or bound query parameters.
    """
    summary = summarize_database_url(raw or settings.database_url)
    causes = [_describe_exception(item) for item in _iter_exception_chain(exc)]
    sqlstate = next((c["sqlstate"] for c in causes if c.get("sqlstate")), None)
    return {
        "error_type": type(exc).__name__,
        "error": sanitize_error_text(exc),
        "sqlstate": sqlstate,
        "connection": {
            "driver": summary.get("driver", ""),
            "host": summary.get("host", ""),
            "port": summary.get("port"),
            "database": summary.get("database", ""),
            "sslmode": summary.get("sslmode", ""),
            "is_supabase_direct": summary.get("is_supabase_direct", False),
            "is_supabase_pooler": summary.get("is_supabase_pooler", False),
            "pooler_username_ok": summary.get("pooler_username_ok"),
            "connect_timeout": int(settings.db_connect_timeout),
        },
        "causes": causes,
    }


def log_database_error(logger: logging.Logger, message: str,
                       exc: BaseException, raw: Optional[str] = None) -> Dict[str, Any]:
    """Log structured diagnostics at ERROR level and return the sanitized payload."""
    detail = describe_database_error(exc, raw)
    logger.error("%s %s", message, json.dumps(detail, default=str, sort_keys=True))
    return detail


def _endpoint(raw: Optional[str] = None) -> tuple[str, int]:
    url = make_url(normalize_database_url(raw or settings.database_url))
    return (url.host or "", int(url.port or 5432))


def _families(infos) -> list[str]:
    names: list[str] = []
    for info in infos:
        name = ("IPv6" if info[0] == socket.AF_INET6
                else "IPv4" if info[0] == socket.AF_INET else str(info[0]))
        if name not in names:
            names.append(name)
    return names


def probe_database_endpoint(raw: Optional[str] = None,
                            timeout: Optional[float] = None) -> Dict[str, Any]:
    """DNS and TCP probe of the configured database endpoint (no authentication).

    Authentication is intentionally separate: a TCP success does not prove the pooler
    username or password is right, and an auth failure does not mean the network is down.
    """
    summary = summarize_database_url(raw or settings.database_url)
    if not summary.get("driver", "").startswith("postgresql"):
        return {"probed": False, "reason": "not-postgresql"}

    timeout_s = float(timeout or settings.db_connect_timeout)
    report: Dict[str, Any] = {"probed": True, "timeout_s": timeout_s}
    try:
        host, port = _endpoint(raw)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        report["dns"] = {"ok": True, "families": _families(infos), "addresses": len(infos)}
    except Exception as exc:  # noqa: BLE001 - report the category, not the resolver text
        report["dns"] = {"ok": False, "error": type(exc).__name__}
        return report

    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            report["tcp"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        report["tcp"] = {"ok": False, "error": type(exc).__name__}
    report["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
    return report


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


def redis_summary(raw: str) -> Dict[str, Any]:
    """Redacted summary of a Redis URL *as it will actually be used*.

    ``summarize_redis_url`` on the raw value reports ``scheme: redis, tls: false`` for an
    Upstash ``redis://`` URL, because it describes what was configured rather than what
    the client will dial. The client upgrades Upstash to ``rediss://``, so a diagnostic
    built on the raw value tells an operator that TLS is off while every byte is in fact
    encrypted - the opposite of what they need to know. Normalise first.
    """
    clean = _clean(raw)
    if not clean:
        return summarize_redis_url("")
    try:
        return summarize_redis_url(normalize_redis_url(clean))
    except Exception:  # noqa: BLE001 - an unusable URL is reported, not raised
        return summarize_redis_url(clean)


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


def check_database(engine_obj=None, log_failure: bool = True,
                   include_diagnostics: bool = False) -> Dict[str, Any]:
    """Run a harmless ``SELECT 1`` and return a redacted status report.

    On failure the same sanitized exception chain that production logs receive is written
    to the log. ``include_diagnostics`` adds it to the return value for the CLI diagnostic
    only; the public readiness payload deliberately omits driver addresses and messages.
    A password or complete URL never reaches either.
    """
    from sqlalchemy import text

    summary = summarize_database_url(settings.database_url)
    started = time.monotonic()
    diagnostics: Optional[Dict[str, Any]] = None
    try:
        if engine_obj is None:
            from app.core.database import engine as engine_obj
        with engine_obj.connect() as connection:
            connection.execute(text("SELECT 1"))
        ok = True
        error = None
    except Exception as exc:  # noqa: BLE001 - the sanitized detail is reported instead
        ok = False
        error = type(exc).__name__
        diagnostics = describe_database_error(exc)
        if log_failure:
            log.error("database check failed: %s",
                      json.dumps(diagnostics, default=str, sort_keys=True))
    result: Dict[str, Any] = {
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
    if include_diagnostics and diagnostics is not None:
        result["diagnostics"] = diagnostics
    return result


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

    summary = redis_summary(raw)
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
