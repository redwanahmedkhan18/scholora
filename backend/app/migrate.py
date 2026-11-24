"""Idempotent schema reconciliation for SQLite dev databases.

Alembic owns migrations in production (see ``alembic/``). For the bundled SQLite file this
script does the same job without a migration graph: it creates any missing tables and adds
any missing columns, so an existing database picks up new model fields without being wiped.

    python3 -m app.migrate
"""
from __future__ import annotations

from sqlalchemy import inspect, text

import app.models  # noqa: F401  (register all metadata)
from app.core.config import settings
from app.core.database import Base, engine

# SQLite ALTER TABLE cannot add a column with a non-constant default, so defaults are
# applied as a follow-up UPDATE instead.
BACKFILL: dict[str, dict[str, str]] = {
    "users": {"failed_login_count": "0", "mfa_recovery_codes": "'[]'"},
    "notifications": {"severity": "'INFO'"},
}


def _sql_type(column) -> str:
    try:
        return column.type.compile(dialect=engine.dialect)
    except Exception:
        return "TEXT"


def run() -> dict[str, list[str]]:
    if settings.is_postgres or settings.is_production:
        raise SystemExit(
            "app.migrate is for SQLite development only. "
            "Production schema changes must use: alembic upgrade head"
        )
    created: list[str] = []
    altered: list[str] = []

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    # 1. Create tables that do not exist at all.
    for name, table in Base.metadata.tables.items():
        if name not in existing_tables:
            table.create(engine)
            created.append(name)

    # 2. Add missing columns to tables that already exist.
    inspector = inspect(engine)
    with engine.begin() as conn:
        for name, table in Base.metadata.tables.items():
            if name in created:
                continue
            present = {c["name"] for c in inspector.get_columns(name)}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl = f'ALTER TABLE "{name}" ADD COLUMN "{column.name}" {_sql_type(column)}'
                if not column.nullable and column.default is None and not column.primary_key:
                    ddl += " NULL"  # existing rows have no value; keep the ALTER legal
                conn.execute(text(ddl))
                altered.append(f"{name}.{column.name}")

                default = BACKFILL.get(name, {}).get(column.name)
                if default:
                    conn.execute(text(
                        f'UPDATE "{name}" SET "{column.name}" = {default} '
                        f'WHERE "{column.name}" IS NULL'
                    ))

    return {"created_tables": created, "added_columns": altered}


if __name__ == "__main__":
    result = run()
    print(f"Tables created : {len(result['created_tables'])}")
    for name in result["created_tables"]:
        print(f"  + {name}")
    print(f"Columns added  : {len(result['added_columns'])}")
    for name in result["added_columns"]:
        print(f"  + {name}")
    if not result["created_tables"] and not result["added_columns"]:
        print("Schema already up to date.")
