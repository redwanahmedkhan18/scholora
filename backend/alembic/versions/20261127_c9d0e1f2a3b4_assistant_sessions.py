"""assistant sessions and verified conversation linkage (W4)

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-11-27 09:00:00.000000

Additive only. Existing ``assistant_conversations`` rows are preserved; their
``session_ref_id`` stays NULL because a client-supplied legacy ``session_id`` is not proof of
ownership and must never be backfilled as one. Downgrade removes only the objects this
revision created.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("sid", sa.String(length=64), nullable=False),
        sa.Column("credential_hash", sa.String(length=64), nullable=False),
        sa.Column("credential_generation", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("ownership", sa.String(length=20), nullable=False,
                  server_default="ANONYMOUS"),
        sa.Column("student_profile_id", sa.Integer(), nullable=True),
        sa.Column("profile_token_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("absolute_expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_reason", sa.String(length=60), nullable=True),
        sa.ForeignKeyConstraint(["student_profile_id"], ["student_profiles.id"],
                                name="fk_assistant_sessions_student_profile_id",
                                ondelete="RESTRICT"),
    )
    op.create_index("ix_assistant_sessions_sid", "assistant_sessions", ["sid"], unique=True)
    op.create_index("ix_assistant_sessions_credential_hash", "assistant_sessions",
                    ["credential_hash"], unique=True)
    op.create_index("ix_assistant_sessions_ownership", "assistant_sessions", ["ownership"])
    op.create_index("ix_assistant_sessions_student_profile_id", "assistant_sessions",
                    ["student_profile_id"])
    op.create_index("ix_assistant_sessions_absolute_expires_at", "assistant_sessions",
                    ["absolute_expires_at"])

    with op.batch_alter_table("assistant_conversations") as batch:
        batch.add_column(sa.Column("session_ref_id", sa.Integer(), nullable=True))
        batch.create_index("ix_assistant_conversations_session_ref_id", ["session_ref_id"])
        batch.create_foreign_key(
            "fk_assistant_conversations_session_ref_id",
            "assistant_sessions", ["session_ref_id"], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("assistant_conversations") as batch:
        batch.drop_constraint("fk_assistant_conversations_session_ref_id", type_="foreignkey")
        batch.drop_index("ix_assistant_conversations_session_ref_id")
        batch.drop_column("session_ref_id")

    op.drop_index("ix_assistant_sessions_absolute_expires_at", table_name="assistant_sessions")
    op.drop_index("ix_assistant_sessions_student_profile_id", table_name="assistant_sessions")
    op.drop_index("ix_assistant_sessions_ownership", table_name="assistant_sessions")
    op.drop_index("ix_assistant_sessions_credential_hash", table_name="assistant_sessions")
    op.drop_index("ix_assistant_sessions_sid", table_name="assistant_sessions")
    op.drop_table("assistant_sessions")
