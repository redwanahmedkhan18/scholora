"""pgvector embedding column + HNSW index

Adds the dense-retrieval column the assistant ranks on. PostgreSQL only: SQLite has no vector
type, and on SQLite the existing JSON ``embedding`` column plus a Python cosine is used instead,
so this migration is a no-op there and the application works either way.

The column width comes from ``settings.embedding_dim`` (default 1024, matching mistral-embed and
the local fallback vectoriser). Every vector in the index must be the same width, so changing
that setting later means a new migration and a reindex.

Revision ID: a1b2c3d4e5f6
Revises: dc186cc99406
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "dc186cc99406"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return  # SQLite keeps the JSON column and ranks in Python

    from app.core.config import settings

    dim = int(settings.embedding_dim)

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(f"ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS embedding_vec vector({dim})")

    # HNSW gives good recall at low latency and, unlike IVFFlat, needs no training pass - which
    # matters because the index is rebuilt from scratch every time the corpus is reindexed.
    # Cosine ops, because the vectors are L2-normalised.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding_vec "
        "ON knowledge_chunks USING hnsw (embedding_vec vector_cosine_ops)"
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_vec")
    op.execute("ALTER TABLE knowledge_chunks DROP COLUMN IF EXISTS embedding_vec")
