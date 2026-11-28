"""full-length verification evidence

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-12-01 09:00:00.000000

``data_verifications.field_value`` was ``VARCHAR(400)``, and every writer clipped the value to fit
it. The derivation compares that recorded value with the value actually stored on the record, so a
claim about anything longer than 400 characters - a programme ``overview``, a serialised
``other_fees`` map - recorded a prefix, never matched, and left the record permanently
un-promotable. The reviewer was told the field was approved while the record was silently withheld
(C2-10).

Widening the column is the only fix that keeps the comparison honest: evidence must be the value
it attests to. A larger fixed limit was rejected because the authoritative columns it describes
are themselves unbounded (``Text``), so any finite cap re-creates the same defect at a different
threshold.

Upgrade is a pure widening: no data is rewritten and no existing value is touched.

Downgrade narrows the column back, which cannot preserve evidence longer than 400 characters. It
therefore **refuses to run while such rows exist** rather than truncating the evidence a trust
decision was taken on. Withdraw or re-register those claims first; the message names how many rows
are involved.

SQLite stores no length for a ``VARCHAR`` and enforces none, so the type change is a no-op there -
the column already accepts any length. PostgreSQL is altered directly.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None

PREVIOUS_LIMIT = 400


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        # SQLite has no length enforcement to lift and no ALTER COLUMN TYPE; the stored values are
        # already whatever length they were written at.
        return
    op.alter_column(
        "data_verifications",
        "field_value",
        type_=sa.Text(),
        existing_type=sa.String(PREVIOUS_LIMIT),
        existing_nullable=True,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    connection = op.get_bind()
    oversized = connection.execute(
        sa.text(
            "SELECT count(*) FROM data_verifications "
            "WHERE field_value IS NOT NULL AND length(field_value) > :limit"
        ),
        {"limit": PREVIOUS_LIMIT},
    ).scalar_one()
    if oversized:
        raise RuntimeError(
            f"{oversized} verification claim(s) hold evidence longer than {PREVIOUS_LIMIT} "
            "characters. Narrowing the column would truncate the evidence a trust decision was "
            "taken on, so this downgrade refuses. Withdraw or re-register those claims first."
        )
    op.alter_column(
        "data_verifications",
        "field_value",
        type_=sa.String(PREVIOUS_LIMIT),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
