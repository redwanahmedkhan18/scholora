"""drop dead mfa_enabled flag

Revision ID: 7f8c667c163f
Revises: dfb40f6534de
Create Date: 2026-10-04 02:12:05.020431
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7f8c667c163f'
down_revision: Union[str, None] = 'dfb40f6534de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `mfa_enabled` was written once by the seed and read by nothing. Real enrolment state is
    # mfa_secret + mfa_confirmed_at, so the flag could claim an account was protected while it
    # had no secret - which is exactly what the seeded administrator looked like.
    op.drop_column("users", "mfa_enabled")


def downgrade() -> None:
    op.add_column("users", sa.Column("mfa_enabled", sa.BOOLEAN(), nullable=False,
                                     server_default=sa.false()))
    # Restore a value consistent with real enrolment state rather than a blanket false.
    op.execute("UPDATE users SET mfa_enabled = (mfa_confirmed_at IS NOT NULL)")
