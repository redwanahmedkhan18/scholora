"""Auth dependencies + RBAC (§91)."""
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models.crm import User

ROLE_HIERARCHY = {
    "ADMIN": 100, "SENIOR_COUNSELOR": 70, "DATA_MANAGER": 60,
    "APPLICATION_OFFICER": 55, "COUNSELOR": 50, "STUDENT": 10,
}


# A token minted for a privileged account that has not yet enrolled in MFA. It proves the
# password was correct, and nothing else: it unlocks only the enrolment endpoints. Without
# this scope the alternative is handing out a full session and *asking* the user to enrol,
# which is what the code used to do - policy said MFA was mandatory for ADMIN, and an ADMIN
# who never enrolled still received an unrestricted token.
MFA_ENROLMENT_SCOPE = "mfa_enrolment"


def _authenticate(authorization: Optional[str], db: Session) -> tuple[User, dict]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
    except Exception as exc:
        # Chain the cause for the log, but never echo the driver/token message to the caller.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc
    user = db.get(User, int(payload.get("sub", 0)))
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user, payload


def current_user(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> User:
    """A fully authenticated session. Enrolment-scoped tokens are refused here."""
    user, payload = _authenticate(authorization, db)
    if payload.get("scope") == MFA_ENROLMENT_SCOPE:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Multi-factor authentication must be set up before this account can be used.",
        )
    return user


def enrolling_user(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> User:
    """
    Accepts a normal session *or* an enrolment-scoped token.

    Used only by the MFA set-up endpoints, so someone locked out by the mandatory-MFA rule
    can still complete enrolment, and an already-signed-in user can rotate their secret.
    """
    user, _ = _authenticate(authorization, db)
    return user


def require_role(minimum: str = "COUNSELOR"):
    def _dep(user: User = Depends(current_user)) -> User:
        if ROLE_HIERARCHY.get(user.role, 0) < ROLE_HIERARCHY.get(minimum, 100):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user
    return _dep


def require_staff(user: User = Depends(current_user)) -> User:
    if user.role == "STUDENT":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Staff access required")
    return user
