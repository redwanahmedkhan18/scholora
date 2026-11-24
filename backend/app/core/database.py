"""SQLAlchemy engine/session. SQLite for zero-config dev, Supabase PostgreSQL in production.

The engine is created once at import time and never per request. PostgreSQL connections
use psycopg 3, a bounded pool, pre-ping, recycling and an explicit connect timeout. SSL is
kept on for Supabase hosts (``sslmode=require`` unless the URL says otherwise).
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings
from app.core.connectivity import (
    assert_safe_database_url,
    database_connect_args,
    normalize_database_url,
    summarize_database_url,
)

log = logging.getLogger(__name__)

database_url = normalize_database_url(settings.database_url)
assert_safe_database_url(database_url, settings.environment)

_database_summary = summarize_database_url(database_url)
if _database_summary.get("is_supabase_direct"):
    log.warning(
        "database: DATABASE_URL uses a Supabase direct host (%s). Render may only reach "
        "its IPv6 address; use the Session Pooler URL from Supabase Dashboard -> Connect.",
        _database_summary.get("host"),
    )

if database_url.startswith("sqlite"):
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
        hide_parameters=True,
    )
else:
    engine = create_engine(
        database_url,
        connect_args=database_connect_args(database_url),
        pool_pre_ping=True,
        pool_recycle=int(settings.db_pool_recycle),
        pool_size=int(settings.db_pool_size),
        max_overflow=int(settings.db_max_overflow),
        pool_timeout=int(settings.db_pool_timeout),
        pool_use_lifo=True,
        hide_parameters=True,
    )

if database_url.startswith("sqlite"):
    # SQLite ships with foreign key enforcement OFF. Leaving it off means the test suite
    # happily accepts writes that PostgreSQL rejects - exactly how a staff-deletion bug
    # (orphaning audit_logs.actor_id) reached production-shaped code unnoticed. Turning it
    # on per connection makes dev and CI behave like the real database.
    @event.listens_for(engine, "connect")
    def _sqlite_enforce_foreign_keys(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
