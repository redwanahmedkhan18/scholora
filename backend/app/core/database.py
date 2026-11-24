"""SQLAlchemy engine/session. SQLite for zero-config dev, PostgreSQL in production."""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)

if settings.database_url.startswith("sqlite"):
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
    finally:
        db.close()
