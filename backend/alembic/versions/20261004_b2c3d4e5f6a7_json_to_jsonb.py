"""convert json columns to jsonb on PostgreSQL

PostgreSQL's `json` type stores the document as text and has no equality operator. Any
`SELECT DISTINCT` over a table containing one fails with:

    could not identify an equality operator for type json

The programme and university search endpoints both use DISTINCT (they join intakes and
scholarships and must not return duplicate rows), so on PostgreSQL every search 500'd while
working perfectly on SQLite, where the distinction does not exist.

`jsonb` is the parsed binary representation: equality and comparison work, and it can be GIN
indexed, which the array-ish columns here (subject_tags, target_countries, research_areas) will
want as the dataset grows.

PostgreSQL only - SQLite has a single JSON representation and needs no change.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-04
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _convert(target_type: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    source_type = "json" if target_type == "jsonb" else "jsonb"
    rows = bind.exec_driver_sql(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND data_type = %s
        ORDER BY table_name, column_name
        """,
        (source_type,),
    ).fetchall()

    for table_name, column_name in rows:
        op.execute(
            f'ALTER TABLE "{table_name}" '
            f'ALTER COLUMN "{column_name}" TYPE {target_type} '
            f'USING "{column_name}"::{target_type}'
        )


def upgrade() -> None:
    _convert("jsonb")


def downgrade() -> None:
    _convert("json")
