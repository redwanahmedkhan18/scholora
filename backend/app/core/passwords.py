"""The one staff-password policy, shared by the HTTP API and the operator CLI.

Two writers can set a staff password: the back office (``POST/PUT /admin/{resource}``) and
``python -m app.mgmt_admin``. When the two disagree, the weaker one wins - which is how a
published demo password survives a "hardened" deployment - so the rule lives in exactly one
place and both call it.

The published-password rule matters more than the length rule. A password that appears in this
repository (the seed data, README, the old login screen) is not a secret: anyone who can read
the source can use it, and an account still holding one is not protected by it. The login
endpoint refuses those passwords in production (``admin.py``); this module stops them from being
stored in the first place.
"""
from __future__ import annotations

from typing import Optional

from app.core.config import settings

MIN_PASSWORD_LENGTH = 12


def password_policy_error(password: str) -> Optional[str]:
    """Return a message that is safe to show to the operator, or ``None`` if usable.

    Deliberately returns the reason instead of raising: the CLI wants ``SystemExit``, the API
    wants a 422, and the tests want to assert the message without catching an exception type.
    """
    value = password if isinstance(password, str) else str(password or "")
    if len(value) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    # Exact match, the same comparison the production login refusal makes: this is the list of
    # strings that ship with the source, not a strength heuristic.
    if value in settings.published_password_list:
        return "That password is published in this repository. Choose another one."
    return None
