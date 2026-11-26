"""mfa replay guard + revoke the published development TOTP secrets

Revision ID: a7b8c9d0e1f2
Revises: 3228d79ec167
Create Date: 2026-10-05 09:40:00.000000

Two related changes, both about the second factor actually being a secret:

1. ``users.mfa_last_used_step`` records the 30-second time-step of the last accepted TOTP
   code. Without it the same six digits stay valid for the whole drift window (~90 s), so a
   captured code could be replayed - the login endpoint returned 200 for the same code twice.

2. The development seed enrols ADMIN/SENIOR_COUNSELOR with fixed secrets that are checked
   into this repository and printed by ``scripts/mfa-code.sh``. Any deployed database seeded
   from it therefore has a *publicly known* second factor. Those enrolled factors are revoked
   here (secret and recovery codes cleared) so the account falls back to the mandatory-MFA
   enrolment flow, where the owner registers a secret only they hold.

The revocation is deliberately not reversible: restoring a published secret in a downgrade
would re-open the hole. ``downgrade`` drops the new column only. No user row is deleted and
no password is changed; only the compromised factor is removed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "3228d79ec167"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Kept in step with app/seed_data.py (DEV_MFA_SECRET_ADMIN / DEV_MFA_SECRET_SENIOR). They are
# literals here on purpose: a migration must describe the data it existed for, and must not
# change meaning if the seed file is edited later.
PUBLISHED_DEV_SECRETS = (
    "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
    "KRSXG5CTMVRXEZLUKRSXG5CTMVRXEZLU",
)


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_last_used_step", sa.Integer(), nullable=True))

    bind = op.get_bind()
    # mfa_recovery_codes is JSON on SQLite and JSONB on PostgreSQL; NULL is the one value that
    # is unambiguous in both (the application already reads it as `or []`).
    bind.execute(
        sa.text(
            "UPDATE users SET mfa_secret = NULL, mfa_confirmed_at = NULL, "
            "mfa_recovery_codes = NULL, mfa_last_used_step = NULL, failed_login_count = 0 "
            "WHERE mfa_secret IN :secrets"
        ).bindparams(sa.bindparam("secrets", expanding=True)),
        {"secrets": list(PUBLISHED_DEV_SECRETS)},
    )


def downgrade() -> None:
    # The revoked secrets are intentionally not restored - see the module docstring.
    op.drop_column("users", "mfa_last_used_step")
