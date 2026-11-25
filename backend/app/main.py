"""Scholara API - International Education Consultancy Intelligence & Application Platform."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError

# Register every model on the shared metadata before the app is created. Imported under a
# private alias so `app` here is unambiguously the FastAPI instance below, not the package
# (the module/instance name collision broke both mypy's view and reader intuition).
import app.models as _models  # noqa: F401
from app.api.v1.routers import actions, admin, admin_extra, admin_platform, integrations, public, student
from app.core import connectivity
from app.core.config import settings
from app.core.connectivity import log_database_error, redis_summary, summarize_database_url
from app.core.database import engine
from app.core.ratelimit import RateLimitMiddleware

# Uvicorn configures only its own loggers. Everything under `app.*` propagates to a root
# logger with no handler, so `log.info(...)` fell through to logging.lastResort and was
# dropped at WARNING level - the redacted startup summary that is supposed to confirm
# `pooler=True` never appeared in Render's logs at all. Install a handler only when
# nothing has configured logging yet, so an embedding process (a Celery worker, a test
# harness) keeps its own setup.
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s:     %(name)s - %(message)s")

log = logging.getLogger(__name__)

DESCRIPTION = """
Source-backed education intelligence API powering the public website, the student platform,
the counselor CRM and the WhatsApp assistant from a single education database.

* Verified data contract - every student-facing fact carries source, source tier and verification date.
* Deterministic eligibility - MEETS / PARTIALLY_MEETS / DOES_NOT_MEET / REQUIRES_REVIEW / INFORMATION_UNAVAILABLE.
* No admission-probability claims are ever produced.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = summarize_database_url(settings.database_url)
    log.info(
        "database configured driver=%s host=%s port=%s sslmode=%s pooler=%s "
        "connect_timeout=%ss pool_size=%s max_overflow=%s",
        db.get("driver"), db.get("host"), db.get("port"), db.get("sslmode"),
        db.get("is_supabase_pooler"), settings.db_connect_timeout,
        settings.db_pool_size, settings.db_max_overflow,
    )
    if db.get("is_supabase_direct"):
        log.warning(
            "DATABASE_URL uses the Supabase direct host; on Render use the IPv4 Session "
            "Pooler URL from Supabase Dashboard -> Connect -> Session Pooler."
        )
    if db.get("is_supabase_pooler") and db.get("pooler_username_ok") is False:
        log.error(
            "DATABASE_URL uses the Supabase Session Pooler but the username is missing "
            "the project suffix. Use postgres.<project-ref>, not bare postgres."
        )
    redis = redis_summary(settings.redis_url) if settings.redis_url else None
    if redis:
        log.info("cache configured scheme=%s host=%s port=%s tls=%s",
                 redis.get("scheme"), redis.get("host"), redis.get("port"), redis.get("tls"))

    if settings.cors_allows_any_origin:
        if settings.is_production:
            log.warning(
                "CORS_ORIGINS is '*' in production: any website may call this API from a "
                "browser. Set CORS_ORIGINS to the deployed frontend origin "
                "(https://<site>) plus any preview domains you trust."
            )
    else:
        log.info("cors origins configured: %s", ", ".join(settings.cors_origin_list))

    from app.services import tasks

    tasks.start_scheduler()
    try:
        yield
    finally:
        tasks.stop_scheduler()
        engine.dispose()


app = FastAPI(title=f"{settings.app_name} API", version="1.0.0", description=DESCRIPTION,
              docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json",
              lifespan=lifespan)

app.add_middleware(RateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # Opt-in only. The previous hardcoded r"https://.*\.e2b\.app" let any sandbox on
    # the internet call the API from a browser, in production as well.
    allow_origin_regex=(settings.cors_origin_regex or None),
    # Credentials are disabled whenever every origin is allowed: Starlette would
    # otherwise echo the caller's Origin and allow credentialed requests from any site.
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Internal-Token"],
    expose_headers=["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    max_age=600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith(f"{settings.api_v1_prefix}/documents/"):
        response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/api/health", tags=["system"])
@app.get("/api/health/live", tags=["system"])
def health():
    """Liveness only: the process is running. Never touches the database or Redis."""
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@app.get("/api/health/ready", tags=["system"])
def readiness(response: Response):
    """Readiness: required dependencies must answer, or this returns 503."""
    report = connectivity.readiness_report()
    response.status_code = 200 if report["ready"] else 503
    return report


app.include_router(public.router, prefix=f"{settings.api_v1_prefix}")
app.include_router(actions.router, prefix=f"{settings.api_v1_prefix}")
app.include_router(student.router, prefix=f"{settings.api_v1_prefix}")
app.include_router(integrations.router, prefix=f"{settings.api_v1_prefix}")
# admin_extra and admin_platform first: their concrete paths must win over admin.py's
# /{resource} catch-all, which would otherwise swallow /config, /import/* and /retrieval/*.
app.include_router(admin_extra.router, prefix=f"{settings.api_v1_prefix}/admin")
app.include_router(admin_platform.router, prefix=f"{settings.api_v1_prefix}/admin")
app.include_router(admin.router, prefix=f"{settings.api_v1_prefix}/admin")


@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
@app.exception_handler(SATimeoutError)
async def database_unavailable(request: Request,
                               exc: OperationalError | InterfaceError | SATimeoutError):
    """A connection-class database outage is retryable (503).

    Only connection failures (a refused/unreachable database, a dropped connection, or a
    pool-checkout timeout) are translated here. Data errors such as ``IntegrityError`` or
    ``ProgrammingError`` are bugs or bad input, not a temporary outage, and must reach the
    generic 500 handler rather than telling a client to retry.
    """
    log_database_error(log, f"database failure on {request.url.path}", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "Database temporarily unavailable", "path": str(request.url.path)},
        headers={"Retry-After": "30"},
    )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Internal server error", "path": str(request.url.path)})
