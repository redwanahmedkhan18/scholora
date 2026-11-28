"""catalogue changes - version history for factual catalogue fields (W13)

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-11-30 10:00:00.000000

Adds one table. Nothing existing is altered, so the migration is additive and reversible: a
downgrade drops the table and leaves every catalogue row, verification record and audit entry
exactly as it was.

Why a new table rather than a column on ``data_verifications``: that register holds the *current*
provenance of a field and keeps one row per field. History needs one row per *change*, with both
values, a per-entity version sequence and an explicit review state - a different cardinality, and
overloading the register would make "what did this field say last year" unanswerable while
destroying the constraint that keeps the register readable.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalogue_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(length=60), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(length=80), nullable=False),
        sa.Column("previous_value", sa.String(length=600), nullable=True),
        sa.Column("new_value", sa.String(length=600), nullable=True),
        sa.Column("classification", sa.String(length=20), nullable=False,
                  server_default="MATERIAL"),
        sa.Column("source_url", sa.String(length=600), nullable=True),
        sa.Column("source_type", sa.String(length=60), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(), nullable=True),
        sa.Column("actor", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("decided_by", sa.String(length=160), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("import_batches.id"), nullable=True),
        sa.Column("verification_id", sa.Integer(), sa.ForeignKey("data_verifications.id"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_catalogue_changes_entity_type", "catalogue_changes", ["entity_type"])
    op.create_index("ix_catalogue_changes_entity_id", "catalogue_changes", ["entity_id"])
    op.create_index("ix_catalogue_changes_field_name", "catalogue_changes", ["field_name"])
    op.create_index("ix_catalogue_changes_classification", "catalogue_changes",
                    ["classification"])
    op.create_index("ix_catalogue_changes_status", "catalogue_changes", ["status"])
    op.create_index("ix_catalogue_changes_batch_id", "catalogue_changes", ["batch_id"])
    # The review queue reads pending rows newest-first, and history reads one entity in sequence.
    op.create_index("ix_catalogue_changes_pending", "catalogue_changes",
                    ["status", "entity_type", "entity_id"])


def downgrade() -> None:
    op.drop_index("ix_catalogue_changes_pending", table_name="catalogue_changes")
    op.drop_index("ix_catalogue_changes_batch_id", table_name="catalogue_changes")
    op.drop_index("ix_catalogue_changes_status", table_name="catalogue_changes")
    op.drop_index("ix_catalogue_changes_classification", table_name="catalogue_changes")
    op.drop_index("ix_catalogue_changes_field_name", table_name="catalogue_changes")
    op.drop_index("ix_catalogue_changes_entity_id", table_name="catalogue_changes")
    op.drop_index("ix_catalogue_changes_entity_type", table_name="catalogue_changes")
    op.drop_table("catalogue_changes")
