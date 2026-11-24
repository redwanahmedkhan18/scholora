"""Scholara API - International Education Consultancy Intelligence & Application Platform."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core import connectivity
from app.core.config import settings
from app.core.connectivity import summarize_database_url, summarize_redis_url
from app.core.database import engine
from app.core.ratelimit import RateLimitMiddleware
from app.api.v1.routers import (actions, admin, admin_extra, admin_platform,
                                 integrations, public, student)

import app.models  # noqa: F401  (register metadata)

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
        "database configured driver=%s host=%s port=%s sslmode=%s pooler=%s",
        db.get("driver"), db.get("host"), db.get("port"), db.get("sslmode"),
        db.get("is_supabase_pooler"),
    )
    if db.get("is_supabase_direct"):
        log.warning(
            "DATABASE_URL uses the Supabase direct host; on Render use the IPv4 Session "
            "Pooler URL from Supabase Dashboard -> Connect -> Session Pooler."
        )
    redis = summarize_redis_url(settings.redis_url) if settings.redis_url else None
    if redis:
        log.info("cache configured scheme=%s host=%s port=%s tls=%s",
                 redis.get("scheme"), redis.get("host"), redis.get("port"), redis.get("tls"))

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
    allow_origin_regex=r"https://.*\.e2b\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Internal server error", "path": str(request.url.path)})
