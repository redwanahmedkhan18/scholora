"""Alembic environment wired to the application's own settings and metadata."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.database import Base
import app.models  # noqa: F401  (import for side effects: registers every table)

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


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
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
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


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
