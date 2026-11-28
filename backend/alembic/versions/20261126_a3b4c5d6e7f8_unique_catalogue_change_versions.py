"""unique catalogue change versions

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-11-26 14:10:00.000000

A version number is what makes "the third change to this fee" expressible, so two rows may not
share one. The sequence is now assigned under a transaction-scoped advisory lock, but a lock only
protects writers that take it: a raw insert, a restored dump or a future code path could still
duplicate a version. The index makes the guarantee structural instead of procedural.

Rows written before the lock existed may already contain duplicates. They are renumbered to a
dense 1..n sequence per (entity, field) *by id order* - which is the true chronological order,
because ids are assigned by the insert - so the history keeps its real sequence rather than
losing rows to the constraint. Downgrade drops the index only; it does not attempt to restore the
old duplicated numbers, because those numbers were wrong.

Both statements are plain SQL, supported identically by PostgreSQL and SQLite.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None

_RENUMBER = """
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY entity_type, entity_id, field_name ORDER BY id
           ) AS rn
    FROM catalogue_changes
)
UPDATE catalogue_changes
SET version = ranked.rn
FROM ranked
WHERE catalogue_changes.id = ranked.id
  AND catalogue_changes.version <> ranked.rn
"""


def upgrade() -> None:
    op.execute(sa.text(_RENUMBER))
    op.create_index(
        "uq_catalogue_changes_version",
        "catalogue_changes",
        ["entity_type", "entity_id", "field_name", "version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_catalogue_changes_version", table_name="catalogue_changes")
