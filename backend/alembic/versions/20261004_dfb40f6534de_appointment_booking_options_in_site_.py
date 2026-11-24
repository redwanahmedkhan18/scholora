"""appointment booking options in site settings

Moves the public booking form's service list, mode list, time slots and minimum lead time out
of the frontend (where they were hard-coded literals) and into admin-editable settings.

Hand-corrected after autogenerate. Two things it got wrong and that must not be reinstated:

1. It wanted to DROP `knowledge_chunks.embedding_vec` and its HNSW index. That column is created
   by migration a1b2c3d4e5f6 in raw SQL and is deliberately not declared on the SQLAlchemy model
   (there is no portable vector type), so autogenerate cannot see it and assumes it is stale.
   Dropping it would destroy the retrieval index. Any future autogenerate will suggest this
   again - always delete those two lines.
2. It emitted `nullable=False` with no server default, which fails against the existing
   singleton settings row. Defaults are supplied below and backfilled.

Revision ID: dfb40f6534de
Revises: b2c3d4e5f6a7
Create Date: 2026-10-04
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "dfb40f6534de"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

DEFAULT_SERVICE_TYPES = [
    {"value": "PROFILE_ASSESSMENT", "label": "Profile assessment"},
    {"value": "UNIVERSITY_SELECTION", "label": "University selection"},
    {"value": "APPLICATION_SUPPORT", "label": "Application support"},
    {"value": "SCHOLARSHIP_GUIDANCE", "label": "Scholarship guidance"},
    {"value": "VISA_GUIDANCE", "label": "Visa guidance"},
    {"value": "PHD_SUPERVISION", "label": "PhD and supervisor strategy"},
    {"value": "DOCUMENT_REVIEW", "label": "Document review"},
    {"value": "OTHER", "label": "Something else"},
]
DEFAULT_MODES = [
    {"value": "OFFLINE", "label": "At the Uttara office"},
    {"value": "ONLINE", "label": "Online (video call)"},
    {"value": "PHONE", "label": "Phone call"},
]
DEFAULT_SLOTS = ["10:00", "11:00", "12:00", "13:00", "15:00", "16:00", "17:00", "18:00"]


def upgrade() -> None:
    op.add_column("site_settings", sa.Column("appointment_service_types", JSON_TYPE,
                                             nullable=False, server_default="[]"))
    op.add_column("site_settings", sa.Column("appointment_modes", JSON_TYPE,
                                             nullable=False, server_default="[]"))
    op.add_column("site_settings", sa.Column("appointment_slots", JSON_TYPE,
                                             nullable=False, server_default="[]"))
    op.add_column("site_settings", sa.Column("appointment_lead_days", sa.Integer(),
                                             nullable=False, server_default="1"))

    # Backfill the singleton row with the values the frontend used to hard-code, so the booking
    # form behaves identically the moment this lands and the admin can edit from there.
    # A JSON string parameter will not implicitly become jsonb on PostgreSQL, so cast it.
    # SQLite's JSON column takes the string as-is, hence the dialect split.
    cast = "CAST(:{name} AS jsonb)" if op.get_bind().dialect.name == "postgresql" else ":{name}"
    op.execute(
        sa.text(
            "UPDATE site_settings SET "
            f"appointment_service_types = {cast.format(name='svc')}, "
            f"appointment_modes = {cast.format(name='modes')}, "
            f"appointment_slots = {cast.format(name='slots')}"
        ).bindparams(
            svc=json.dumps(DEFAULT_SERVICE_TYPES),
            modes=json.dumps(DEFAULT_MODES),
            slots=json.dumps(DEFAULT_SLOTS),
        )
    )


def downgrade() -> None:
    op.drop_column("site_settings", "appointment_lead_days")
    op.drop_column("site_settings", "appointment_slots")
    op.drop_column("site_settings", "appointment_modes")
    op.drop_column("site_settings", "appointment_service_types")
