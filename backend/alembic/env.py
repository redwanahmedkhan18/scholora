"""Alembic environment wired to the application's own settings and metadata."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.core.config import settings
from app.core.connectivity import (
    assert_safe_database_url,
    database_connect_args,
    normalize_database_url,
)
from app.core.database import Base
import app.models  # noqa: F401  (import for side effects: registers every table)

config = context.config
database_url = normalize_database_url(settings.database_url)
assert_safe_database_url(database_url, settings.environment)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

#: Serialises migrations across Render pre-deploy commands, one-off jobs and a manual
#: Render Shell run. PostgreSQL session advisory locks are released automatically if the
#: process dies.
MIGRATION_LOCK_KEY = 0x53434F4C  # "SCOL"


# Objects that exist in the database but deliberately not on the SQLAlchemy models.
# `knowledge_chunks.embedding_vec` is a pgvector column created in raw SQL by migration
# a1b2c3d4e5f6, together with its HNSW index, because there is no portable column type for
# it. Autogenerate cannot see it on the model and therefore proposes dropping both on every
# single run - which would silently destroy the retrieval index. Filter them out so a
# generated migration is safe to apply without hand-editing.
INVISIBLE_TO_AUTOGENERATE = {
    ("column", "knowledge_chunks", "embedding_vec"),
    ("index", "knowledge_chunks", "ix_knowledge_chunks_embedding_vec"),
}


def include_object(object_, name, type_, reflected, compare_to):
    table = getattr(getattr(object_, "table", None), "name", None)
    if (type_, table, name) in INVISIBLE_TO_AUTOGENERATE:
        return False
    if type_ == "index" and name and "embedding_vec" in name:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        database_url,
        connect_args=database_connect_args(database_url),
        poolclass=pool.NullPool,
        pool_pre_ping=True,
    )
    try:
        with connectable.connect() as connection:
            is_postgres = connection.dialect.name == "postgresql"
            if is_postgres:
                connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
                connection.commit()
            try:
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                    compare_type=True,
                    include_object=include_object,
                    # SQLite cannot ALTER most things in place; batch mode rewrites the table.
                    render_as_batch=connection.dialect.name == "sqlite",
                )
                with context.begin_transaction():
                    context.run_migrations()
            finally:
                if is_postgres:
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
                    connection.commit()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
