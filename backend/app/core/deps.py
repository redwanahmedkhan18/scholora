"""Auth dependencies + RBAC (§91)."""
import hmac
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
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

# A token that proves the password was correct and nothing else. It is the handle the login
# screen holds between the two steps, so the second step does not have to resend the password
# and cannot be replayed as a session.
MFA_CHALLENGE_SCOPE = "mfa_challenge"

# Every token that is *not* a session. Anything in this set must be refused by
# current_user/require_* - the failure mode of forgetting one was a half-authenticated token
# reaching the whole back office.
NON_SESSION_SCOPES = frozenset({MFA_ENROLMENT_SCOPE, MFA_CHALLENGE_SCOPE})


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
    """A fully authenticated session. Any scoped (non-session) token is refused here."""
    user, payload = _authenticate(authorization, db)
    scope = payload.get("scope")
    if scope in NON_SESSION_SCOPES:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Multi-factor authentication must be completed before this account can be used.",
        )
    return user


def enrolling_user(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> User:
    """
    Accepts a normal session *or* an enrolment-scoped token.

    Used only by the MFA set-up endpoints, so someone locked out by the mandatory-MFA rule
    can still complete enrolment, and an already-signed-in user can rotate their secret.
    A *challenge* token is refused: it has not seen a second factor yet.
    """
    user, payload = _authenticate(authorization, db)
    if payload.get("scope") == MFA_CHALLENGE_SCOPE:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Complete the authentication challenge before changing two-factor settings.",
        )
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


INTERNAL_TOKEN_HEADER = "x-internal-token"


def require_internal_caller(request: Request) -> None:
    """Authorize a server-to-server call with the shared internal token.

    Some endpoints exist for our own Next.js server (or an operator's runner), not for a browser:
    the rate-limit exemption and the marketing conversion relay are both in this group. A browser
    never holds the token - it reaches the API through the Next.js proxy, which does not forward
    the header - so requiring it turns "any client on the internet" into "the deployment".

    Fails closed on every axis: an unset token authorizes nobody (an empty secret must never be
    the same string as an empty header), a missing header is refused, and the comparison is
    constant-time so the header cannot be guessed a character at a time. The published development
    default is refused at startup in production (see ``Settings.check_production``), so this
    dependency can never be satisfied by a value that is in the repository.
    """
    expected = (settings.internal_api_token or "").strip()
    supplied = (request.headers.get(INTERNAL_TOKEN_HEADER) or "").strip()
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This endpoint is server-to-server only; send the internal token.",
        )
