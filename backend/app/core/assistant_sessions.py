"""W4 - secure, database-backed assistant sessions.

A browser credential is an opaque 384-bit random string. Only a keyed hash of it is stored,
so a database dump cannot be replayed as a session. The public session id used for database
relationships is a *different* random value and is never sufficient to authenticate.

Ownership is explicit:

* ``ANONYMOUS``  - no profile, no student data, ever.
* ``PROFILE``    - bound to exactly one ``student_profiles`` row after the authoritative
  public-token lookup succeeded. A profile-bound session can never become anonymous, and the
  foreign key is ``ON DELETE RESTRICT`` so deleting a profile cannot silently downgrade it.

Both credential hashing and CSRF derivation use separate domain-separated keys derived from
``ASSISTANT_SESSION_SECRET`` (falling back to ``SECRET_KEY`` outside production). The staff JWT
is a different credential and is never accepted here.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.crm import AssistantSession, StudentProfile

ANONYMOUS = "ANONYMOUS"
PROFILE = "PROFILE"
OWNERSHIPS = (ANONYMOUS, PROFILE)

COOKIE_NAME = "scholara_assistant"
COOKIE_PATH = "/api/v1/assistant"
CSRF_HEADER = "X-CSRF-Token"

CREDENTIAL_BYTES = 48          # 384 bits of entropy
SESSION_ID_BYTES = 32          # non-secret database/audit reference
DEFAULT_IDLE_MINUTES = 60
DEFAULT_ABSOLUTE_HOURS = 720   # 30 days

_DEV_SECRET = "dev-secret-change-me"


class SessionError(Exception):
    """A refused session operation. ``code`` is stable and safe to return to a client."""

    def __init__(self, code: str, detail: str = "", status: int = 400) -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code
        self.status = status


class RotationConflict(SessionError):
    def __init__(self) -> None:
        super().__init__("SESSION_CONFLICT",
                         "The session changed while this request was in flight.", 409)


# ----------------------------------------------------------------------- time
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _store_time(value: Optional[datetime]) -> Optional[datetime]:
    """Store naive UTC. The existing schema uses ``DateTime`` without a timezone, and SQLite
    returns naive values regardless; comparisons are always done in aware UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _read_time(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ------------------------------------------------------------------- secrets
def _base_secret() -> bytes:
    explicit = (getattr(settings, "assistant_session_secret", "") or "").strip()
    if settings.is_production:
        # Production needs its own strong key. Falling back to SECRET_KEY would couple two
        # credential systems and made it possible to run with no session secret at all.
        from app.core.config import INSECURE_SECRETS
        if not explicit or explicit.lower() in INSECURE_SECRETS or len(explicit) < 32:
            raise SessionError("SESSION_KEY_UNCONFIGURED",
                               "Assistant sessions are not configured on this server.", 503)
        return explicit.encode("utf-8")
    if explicit:
        return explicit.encode("utf-8")
    base = (settings.secret_key or "").strip()
    return (base or _DEV_SECRET).encode("utf-8")


def _pepper(purpose: bytes) -> bytes:
    return hmac.new(_base_secret(), b"scholara.assistant-session.v1:" + purpose,
                    hashlib.sha256).digest()


def hash_credential(raw: str) -> str:
    mac = hmac.new(_pepper(b"credential"), (raw or "").encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()


def profile_fingerprint(profile_token: Optional[str]) -> Optional[str]:
    """Keyed, non-reversible fingerprint of a portal profile token.

    Stored on a profile-bound session so every ask can prove the browser is still presenting
    the same profile. It is not an authorization credential and cannot be reversed into the
    portal token.
    """
    if not profile_token:
        return None
    mac = hmac.new(_pepper(b"profile-token"), profile_token.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()


def matches_profile(row: AssistantSession, profile_token: Optional[str]) -> bool:
    """Whether the supplied portal token is the one this session was bound to."""
    if row.ownership != PROFILE:
        return False
    expected = row.profile_token_fingerprint or ""
    supplied = profile_fingerprint(profile_token) or ""
    return bool(expected) and hmac.compare_digest(expected, supplied)


def _raw_credential() -> str:
    return secrets.token_urlsafe(CREDENTIAL_BYTES)


def _raw_sid() -> str:
    return secrets.token_urlsafe(SESSION_ID_BYTES)


def _rowcount(result: object) -> int:
    """SQLAlchemy typing leaves ``rowcount`` off the generic Result; the DBAPI result has it."""
    return int(getattr(result, "rowcount", 0) or 0)


# ------------------------------------------------------------------ lifecycle
def idle_limit() -> timedelta:
    minutes = int(getattr(settings, "assistant_session_idle_minutes", DEFAULT_IDLE_MINUTES) or 0)
    return timedelta(minutes=max(1, minutes))


def absolute_limit() -> timedelta:
    hours = int(getattr(settings, "assistant_session_absolute_hours", DEFAULT_ABSOLUTE_HOURS) or 0)
    return timedelta(hours=max(1, hours))


def _is_usable(row: AssistantSession, now: datetime) -> bool:
    if row.revoked_at is not None:
        return False
    absolute = _read_time(row.absolute_expires_at)
    if absolute is None or absolute <= now:
        return False
    last_seen = _read_time(row.last_seen_at) or _read_time(row.created_at)
    if last_seen is None or last_seen + idle_limit() <= now:
        return False
    if row.ownership == PROFILE and (not row.student_profile_id
                                     or not row.profile_token_fingerprint):
        return False
    if row.ownership == ANONYMOUS and row.student_profile_id is not None:
        return False
    return True


def issue(db: Session, *, profile_token: Optional[str] = None,
          now: Optional[datetime] = None) -> Tuple[str, AssistantSession]:
    """Create a session. Returns ``(raw_credential, row)``; the raw value goes to a cookie only."""
    now = now or now_utc()
    profile: Optional[StudentProfile] = None
    if profile_token:
        profile = db.execute(
            select(StudentProfile).where(StudentProfile.public_token == profile_token)
        ).scalars().first()
        if profile is None:
            raise SessionError("PROFILE_NOT_FOUND", "That profile link is not valid.", 400)

    credential = _raw_credential()
    row = AssistantSession(
        sid=_raw_sid(),
        credential_hash=hash_credential(credential),
        credential_generation=0,
        ownership=PROFILE if profile else ANONYMOUS,
        student_profile_id=profile.id if profile else None,
        profile_token_fingerprint=profile_fingerprint(profile_token) if profile else None,
        created_at=_store_time(now),
        last_seen_at=_store_time(now),
        absolute_expires_at=_store_time(now + absolute_limit()),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return credential, row


def resolve(db: Session, raw: Optional[str], *, now: Optional[datetime] = None) -> Optional[AssistantSession]:
    """Return the live session for a raw credential, or ``None``. Never raises."""
    if not raw or len(raw) > 256:
        return None
    now = now or now_utc()
    try:
        digest = hash_credential(raw)
        row = db.execute(
            select(AssistantSession).where(AssistantSession.credential_hash == digest)
        ).scalars().first()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None
    if row is None:
        return None
    # Hash equality is already exact, but compare in constant time as well: this is the one
    # place a credential is checked, and it costs nothing.
    if not hmac.compare_digest(row.credential_hash or "", digest):
        return None
    if not _is_usable(row, now):
        return None
    return row


def touch(db: Session, row: AssistantSession, *, now: Optional[datetime] = None) -> None:
    """Record activity without extending the absolute deadline."""
    now = now or now_utc()
    try:
        db.execute(
            update(AssistantSession)
            .where(AssistantSession.id == row.id,
                   AssistantSession.revoked_at.is_(None),
                   AssistantSession.credential_generation == row.credential_generation)
            .values(last_seen_at=_store_time(now))
        )
        db.commit()
    except Exception:
        db.rollback()


def rotate(db: Session, row: AssistantSession, *,
           now: Optional[datetime] = None) -> Tuple[str, AssistantSession]:
    """Atomically replace the credential. A stale or expired session cannot be revived."""
    now = now or now_utc()
    credential = _raw_credential()
    generation = int(row.credential_generation or 0) + 1
    result = db.execute(
        update(AssistantSession)
        .where(AssistantSession.id == row.id,
               AssistantSession.credential_hash == row.credential_hash,
               AssistantSession.credential_generation == row.credential_generation,
               AssistantSession.revoked_at.is_(None),
               AssistantSession.absolute_expires_at > _store_time(now))
        .values(credential_hash=hash_credential(credential),
                credential_generation=generation,
                last_seen_at=_store_time(now))
    )
    if _rowcount(result) != 1:
        db.rollback()
        raise RotationConflict()
    db.commit()
    db.refresh(row)
    if not _is_usable(row, now):
        # Defensive: the conditional update makes this unreachable, but never hand out a
        # credential whose row is not actually usable.
        db.rollback()
        raise RotationConflict()
    return credential, row


def bind_profile(db: Session, row: AssistantSession, profile_token: str, *,
                 now: Optional[datetime] = None) -> Tuple[str, AssistantSession]:
    """Explicitly turn an anonymous session into a profile-bound one, rotating the credential."""
    if row.ownership != ANONYMOUS:
        raise SessionError("SESSION_ALREADY_BOUND",
                           "This session is already bound to a student.", 409)
    now = now or now_utc()
    profile = db.execute(
        select(StudentProfile).where(StudentProfile.public_token == profile_token)
    ).scalars().first()
    if profile is None:
        raise SessionError("PROFILE_NOT_FOUND", "That profile link is not valid.", 400)

    credential = _raw_credential()
    generation = int(row.credential_generation or 0) + 1
    result = db.execute(
        update(AssistantSession)
        .where(AssistantSession.id == row.id,
               AssistantSession.ownership == ANONYMOUS,
               AssistantSession.credential_hash == row.credential_hash,
               AssistantSession.credential_generation == row.credential_generation,
               AssistantSession.revoked_at.is_(None),
               AssistantSession.absolute_expires_at > _store_time(now))
        .values(ownership=PROFILE,
                student_profile_id=profile.id,
                profile_token_fingerprint=profile_fingerprint(profile_token),
                credential_hash=hash_credential(credential),
                credential_generation=generation,
                last_seen_at=_store_time(now),
                # W5: anonymous constraints must not follow the student into a bound session.
                context_json=None,
                context_revision=AssistantSession.context_revision + 1)
    )
    if _rowcount(result) != 1:
        db.rollback()
        raise RotationConflict()
    db.commit()
    db.refresh(row)
    return credential, row


def switch_profile(db: Session, row: AssistantSession, profile_token: str, *,
                   now: Optional[datetime] = None) -> Tuple[Optional[str], AssistantSession]:
    """Explicitly move a profile-bound session to the currently presented profile.

    A matching token is a no-op (the common page-refresh path). A different verified token
    rotates the credential and the binding atomically, so Student A's credential cannot be
    reused for Student B. An invalid token is refused and the caller revokes the stale
    session.
    """
    if row.ownership != PROFILE:
        raise SessionError("SESSION_NOT_BOUND", "This session is not profile-bound.", 409)
    now = now or now_utc()
    profile = db.execute(
        select(StudentProfile).where(StudentProfile.public_token == profile_token)
    ).scalars().first()
    if profile is None:
        raise SessionError("PROFILE_NOT_FOUND", "That profile link is not valid.", 400)
    if matches_profile(row, profile_token):
        return None, row

    credential = _raw_credential()
    generation = int(row.credential_generation or 0) + 1
    result = db.execute(
        update(AssistantSession)
        .where(AssistantSession.id == row.id,
               AssistantSession.ownership == PROFILE,
               AssistantSession.credential_hash == row.credential_hash,
               AssistantSession.credential_generation == row.credential_generation,
               AssistantSession.revoked_at.is_(None),
               AssistantSession.absolute_expires_at > _store_time(now))
        .values(student_profile_id=profile.id,
                profile_token_fingerprint=profile_fingerprint(profile_token),
                credential_hash=hash_credential(credential),
                credential_generation=generation,
                last_seen_at=_store_time(now),
                # W5: a different profile must never inherit the previous student's context.
                context_json=None,
                context_revision=AssistantSession.context_revision + 1)
    )
    if _rowcount(result) != 1:
        db.rollback()
        raise RotationConflict()
    db.commit()
    db.refresh(row)
    return credential, row


def revalidate(db: Session, row: AssistantSession, *, generation: int,
               profile_id: Optional[int], now: Optional[datetime] = None) -> bool:
    """Re-read the row after a long generation and confirm it is still the same session.

    Rotation, rebinding or revocation during generation makes the answer belong to a
    superseded identity. This refreshes the row in the existing request transaction; it does
    not lock it for the duration of generation.
    """
    try:
        db.refresh(row)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False
    if int(row.credential_generation or 0) != int(generation):
        return False
    if row.ownership == PROFILE:
        if not profile_id or row.student_profile_id != profile_id:
            return False
    elif row.ownership == ANONYMOUS:
        if profile_id is not None:
            return False
    else:
        return False
    return _is_usable(row, now or now_utc())


def revoke(db: Session, row: AssistantSession, reason: str = "USER_REVOKED") -> bool:
    """Revoke exactly once. Restart-safe because the state is in the database."""
    result = db.execute(
        update(AssistantSession)
        .where(AssistantSession.id == row.id,
               AssistantSession.revoked_at.is_(None))
        .values(revoked_at=_store_time(now_utc()),
                revoked_reason=(reason or "USER_REVOKED")[:60],
                # W5: revocation also discards the stored conversation context.
                context_json=None,
                context_revision=AssistantSession.context_revision + 1)
    )
    db.commit()
    return _rowcount(result) == 1


def revoke_by_sid(db: Session, sid: str, reason: str = "ADMIN_REVOKED") -> bool:
    row = db.execute(select(AssistantSession).where(AssistantSession.sid == sid)).scalars().first()
    if row is None:
        return False
    return revoke(db, row, reason)


def revoke_for_profile(db: Session, student_profile_id: int,
                       reason: str = "PROFILE_ERASED", *, commit: bool = True) -> int:
    """Revoke every session belonging to a profile before erasure.

    ``commit=False`` lets the caller keep the revocation inside its own transaction, so a
    failure later in an erasure rolls the revocation back with everything else instead of
    leaving a half-applied result behind. Existing callers keep the commit-immediately default.
    """
    if not student_profile_id:
        return 0
    result = db.execute(
        update(AssistantSession)
        .where(AssistantSession.student_profile_id == student_profile_id,
               AssistantSession.revoked_at.is_(None))
        .values(revoked_at=_store_time(now_utc()),
                revoked_reason=reason[:60])
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return _rowcount(result)


# ---------------------------------------------------------------------- CSRF
def csrf_token(row: AssistantSession) -> str:
    """A per-session CSRF token derived from the server key and the credential generation.

    It is not an authentication credential by itself; it only proves the caller can read the
    session state from the same origin.
    """
    message = f"{row.sid}:{int(row.credential_generation or 0)}".encode("utf-8")
    mac = hmac.new(_pepper(b"csrf"), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")[:43]


def verify_csrf(row: AssistantSession, token: Optional[str]) -> bool:
    if not token:
        return False
    expected = csrf_token(row).encode("ascii")
    provided = (token or "").strip().encode("ascii", errors="ignore")
    return hmac.compare_digest(expected, provided)


# -------------------------------------------------------------------- cookies
def cookie_max_age(row: AssistantSession) -> int:
    expiry = _read_time(row.absolute_expires_at) or (now_utc() + absolute_limit())
    return max(1, int((expiry - now_utc()).total_seconds()))


def set_cookie(response: Response, credential: str, row: AssistantSession) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=credential,
        max_age=cookie_max_age(row),
        path=COOKIE_PATH,
        httponly=True,
        secure=bool(settings.is_production),
        samesite="lax",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path=COOKIE_PATH,
                           secure=bool(settings.is_production), httponly=True,
                           samesite="lax")


def public_state(row: AssistantSession) -> dict:
    """Non-sensitive state. Never includes the credential or its hash."""
    created = _read_time(row.created_at)
    seen = _read_time(row.last_seen_at)
    expires = _read_time(row.absolute_expires_at)
    return {
        "sid": row.sid,
        "ownership": row.ownership,
        "profile_id": row.student_profile_id,
        "created_at": created.isoformat() if created else None,
        "last_seen_at": seen.isoformat() if seen else None,
        "expires_at": expires.isoformat() if expires else None,
        "generation": int(row.credential_generation or 0),
    }
