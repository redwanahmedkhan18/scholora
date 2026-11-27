"""Change detection, version history and staleness for catalogue facts (W13).

Three questions this module answers, and one it refuses to:

1. *Did this field really change?* A re-import that only reorders whitespace is not a change, and
   recording it would bury the real ones. ``classify`` compares normalized values and, for the
   fields a student acts on, also asks whether the move is plausible.
2. *What did it say before?* ``record`` writes the superseded value, the new one, both sources and
   a per-entity sequence number before anything is presented as current.
3. *Is this still current?* ``staleness`` reports the verification window the policy uses, and says
   "unknown" when no threshold is configured rather than inventing one.

The refusal: nothing here decides that data is true. A change is *material*, not *approved*, and a
record being recent is not evidence of anything. Approval is a human act in the W12 review path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, cast

from sqlalchemy import func, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core import verification_policy as policy
from app.core.config import settings
from app.models.crm import CatalogueChange

# ---------------------------------------------------------------- classification
NO_CHANGE = "NO_CHANGE"
FORMAT_ONLY = "FORMAT_ONLY"
MATERIAL = "MATERIAL"
SUSPICIOUS = "SUSPICIOUS"

#: Fields whose value a student could act on, and where a large swing is worth a second look.
MONEY_FIELDS = frozenset({"tuition_annual", "tuition_total", "amount_value", "deposit",
                          "application_fee", "living_cost_annual", "insurance_annual"})
SCORE_FIELDS = frozenset({"min_cgpa", "ielts_overall", "ielts_min", "min_ielts", "cgpa_scale"})
DATE_FIELDS = frozenset({"application_deadline", "deadline", "application_opens", "class_start"})
TIME_SENSITIVE_FIELDS = MONEY_FIELDS | SCORE_FIELDS | DATE_FIELDS | frozenset({
    "intakes", "degree_levels", "eligible_nationalities", "duration_months", "year", "label",
})

#: Plausibility envelopes. These bound *arithmetic impossibilities*, not product policy: a CGPA of
#: 47 out of 4 is a data error, not a strict university. A move outside them is flagged for review,
#: never silently accepted and never silently dropped.
_CGPA_MAX = {"4": 4.0, "5": 5.0, "10": 10.0, "100": 100.0}
_IELTS_RANGE = (0.0, 9.0)


def _norm(value: Any) -> str:
    """Whitespace/case/punctuation-insensitive form used only to detect *formatting* changes."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text.replace("\u2013", "-").replace("\u2014", "-")


def _as_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def classify(field: str, previous: Any, new: Any) -> str:
    """NO_CHANGE, FORMAT_ONLY, MATERIAL or SUSPICIOUS for one field.

    Conservative by design: an unrecognised shape is MATERIAL, because calling a real change
    "harmless" is the error that loses students' trust, while calling a harmless change "material"
    only costs a reviewer a glance.
    """
    if previous is None and new is None:
        return NO_CHANGE
    if _norm(previous) == _norm(new):
        return NO_CHANGE
    # Same value with different spacing/punctuation/case only.
    if re.sub(r"[^a-z0-9]+", "", _norm(previous)) == re.sub(r"[^a-z0-9]+", "", _norm(new)):
        return FORMAT_ONLY

    if field in MONEY_FIELDS:
        old_money, new_money = _as_number(previous), _as_number(new)
        if old_money is not None and new_money is not None:
            if new_money < 0:
                return SUSPICIOUS
            # A threefold swing in a fee is either a currency mix-up, a different cost period or a
            # typo. It is never presented as a quiet update.
            if old_money > 0 and (new_money > old_money * 3 or new_money < old_money / 3):
                return SUSPICIOUS
        return MATERIAL

    if field in SCORE_FIELDS:
        new_score = _as_number(new)
        if field in ("ielts_overall", "min_ielts", "ielts_min"):
            if new_score is not None and not (_IELTS_RANGE[0] <= new_score <= _IELTS_RANGE[1]):
                return SUSPICIOUS
        elif new_score is not None and new_score < 0:
            return SUSPICIOUS
        return MATERIAL

    if field in DATE_FIELDS:
        old_date, new_date = _as_date(previous), _as_date(new)
        # A deadline that moves backwards by more than a year is either a different intake or a
        # typo; either way a human should look.
        if old_date is not None and new_date is not None:
            if new_date < old_date - timedelta(days=400):
                return SUSPICIOUS
        return MATERIAL

    if field == "year":
        new_year = _as_number(new)
        if new_year is not None and not (1990 <= new_year <= 2100):
            return SUSPICIOUS
        return MATERIAL

    if field in ("degree_level",):
        from app.models.base import DEGREE_LEVELS

        if new is not None and str(new).upper() not in DEGREE_LEVELS:
            return SUSPICIOUS
        return MATERIAL

    if field in ("currency",):
        from app.services import fx

        if new is not None and not fx.supported(str(new)):
            return SUSPICIOUS
        return MATERIAL

    return MATERIAL


# ---------------------------------------------------------------- version recording
def _sequence_lock(db: Session, entity_type: str, entity_id: int, field_name: str) -> None:
    """Serialise version assignment for one field, for the rest of this transaction.

    ``max(version) + 1`` is a read-modify-write: two concurrent writers each read the same
    maximum and both insert it. Under PostgreSQL two workers produced versions ``[1, 2, 1]``
    (proved by ``tests/test_pg_concurrency.py``) - a silently corrupted history. A
    transaction-scoped advisory lock makes the pair atomic without a new table, without blocking
    other fields, and without changing the schema. It is released by the server when the
    transaction ends, including on a crash.

    SQLite has no advisory locks and serialises writers anyway, so this is a no-op there - stated
    plainly rather than implied.
    """
    try:
        if db.get_bind().dialect.name != "postgresql":
            return
    except Exception:            # noqa: BLE001 - detached session: nothing to serialise
        return
    key = f"catalogue_changes:{entity_type}:{entity_id}:{field_name}"
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})


def _next_version(db: Session, entity_type: str, entity_id: int,
                  field_name: str) -> int:
    highest = db.scalar(
        select(func.max(CatalogueChange.version)).where(
            CatalogueChange.entity_type == entity_type,
            CatalogueChange.entity_id == entity_id,
            CatalogueChange.field_name == field_name,
        )
    )
    return int(highest or 0) + 1


def record(db: Session, *, entity_type: str, entity_id: int, field_name: str,
           previous: Any, new: Any, source_url: Optional[str] = None,
           source_type: Optional[str] = None, retrieved_at: Optional[datetime] = None,
           actor: Optional[str] = None, batch_id: Optional[int] = None,
           verification_id: Optional[int] = None,
           classification: Optional[str] = None) -> Optional[CatalogueChange]:
    """Store a change worth keeping. Returns ``None`` for a non-change.

    Formatting-only and no-op changes are not stored: history that is mostly noise is history
    nobody reads, and every stored row here implies a reviewer may need to look at it.
    """
    kind = classification or classify(field_name, previous, new)
    if kind in (NO_CHANGE, FORMAT_ONLY):
        return None
    _sequence_lock(db, entity_type, entity_id, field_name)
    row = CatalogueChange(
        entity_type=entity_type,
        entity_id=entity_id,
        field_name=field_name,
        previous_value=_clip(previous),
        new_value=_clip(new),
        classification=kind,
        source_url=source_url,
        source_type=source_type,
        retrieved_at=retrieved_at,
        actor=(actor or None),
        status="PENDING",
        version=_next_version(db, entity_type, entity_id, field_name),
        batch_id=batch_id,
        verification_id=verification_id,
    )
    db.add(row)
    db.flush()
    return row


def _clip(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = value.isoformat() if isinstance(value, datetime) else str(value)
    return text[:600]


def pending_for(db: Session, entity_type: str, entity_id: int) -> List[CatalogueChange]:
    """Unresolved material changes for one entity, oldest first."""
    return list(db.execute(
        select(CatalogueChange).where(
            CatalogueChange.entity_type == entity_type,
            CatalogueChange.entity_id == entity_id,
            CatalogueChange.status == "PENDING",
        ).order_by(CatalogueChange.id.asc())
    ).scalars())


def has_pending_material_change(db: Session, entity_type: str, entity_id: int) -> bool:
    """Whether anything about this record awaits a decision. Suspicious counts."""
    return bool(db.scalar(
        select(func.count()).select_from(CatalogueChange).where(
            CatalogueChange.entity_type == entity_type,
            CatalogueChange.entity_id == entity_id,
            CatalogueChange.status == "PENDING",
            CatalogueChange.classification.in_((MATERIAL, SUSPICIOUS)),
        )
    ))


def history(db: Session, entity_type: str, entity_id: int, *, limit: int = 50,
            offset: int = 0) -> List[CatalogueChange]:
    """Newest-first change history for one entity, bounded."""
    limit = max(1, min(int(limit), 200))
    return list(db.execute(
        select(CatalogueChange).where(
            CatalogueChange.entity_type == entity_type,
            CatalogueChange.entity_id == entity_id,
        ).order_by(CatalogueChange.id.desc()).limit(limit).offset(max(0, offset))
    ).scalars())


def decide(db: Session, change: CatalogueChange, *, status: str, actor: str,
           note: Optional[str] = None) -> CatalogueChange:
    """Approve or reject one pending change. The caller owns the transaction."""
    status = status.upper()
    if status not in ("APPROVED", "REJECTED"):
        raise ValueError("status must be APPROVED or REJECTED")
    # One decision per change, enforced by the database rather than by the value we just read:
    # two reviewers who both saw PENDING would otherwise both succeed, leaving a row recorded as
    # APPROVED and REJECTED at once (proved by tests/test_pg_concurrency.py).
    decided = cast(CursorResult, db.execute(
        update(CatalogueChange)
        .where(CatalogueChange.id == change.id, CatalogueChange.status == "PENDING")
        .values(status=status, decided_by=actor,
                decided_at=datetime.now(timezone.utc), decision_note=(note or None))
        .execution_options(synchronize_session=False)
    )).rowcount
    if decided != 1:
        # See review.resolve_field: the conditional update is a no-op, not a failure of the
        # transaction, and this is called from inside an importer savepoint.
        raise ValueError("this change has already been decided")
    db.expire(change)
    db.refresh(change)
    return change


def queue(db: Session, *, status: str = "PENDING", limit: int = 50, offset: int = 0,
          classification: Optional[str] = None) -> Dict[str, Any]:
    """Bounded, newest-first review queue over recorded changes."""
    limit = max(1, min(int(limit), 200))
    stmt = select(CatalogueChange).where(CatalogueChange.status == status.upper())
    if classification:
        stmt = stmt.where(CatalogueChange.classification == classification.upper())
    total = db.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0
    rows = list(db.execute(
        stmt.order_by(CatalogueChange.id.desc()).limit(limit).offset(max(0, offset))
    ).scalars())
    return {"total": int(total), "limit": limit, "offset": max(0, offset), "items": rows}


# ---------------------------------------------------------------- staleness
@dataclass(frozen=True)
class Staleness:
    """Whether a record needs re-verification, and why. Never a verdict on truth."""

    state: str                     # CURRENT | REVIEW_OVERDUE | STALE | UNKNOWN
    reason: str
    days_since_verification: Optional[int]
    window_days: Optional[int]

    def as_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "reason": self.reason,
                "days_since_verification": self.days_since_verification,
                "window_days": self.window_days}


CURRENT = "CURRENT"
REVIEW_OVERDUE = "REVIEW_OVERDUE"
STALE = "STALE"
UNKNOWN = "UNKNOWN"


def staleness(record: Any, now: Optional[datetime] = None,
              window_days: Optional[int] = None) -> Staleness:
    """Time-based re-verification need for one record, using the policy's own window.

    Time-sensitive facts are the ones students act on with a deadline: a fee, a deadline, an
    intake, a scholarship amount. The window comes from configuration (``policy_max_age_days``)
    unless the caller passes one, and when no window is configured the state is ``UNKNOWN`` -
    an explicit "nobody has defined this", which is the honest answer and never a silent pass.
    """
    now = now or datetime.now(timezone.utc)
    verified = policy._as_utc(policy._field(record, "verified_at"))       # shared date handling
    due = policy._as_utc(policy._field(record, "review_due_at"))
    window: Optional[int] = (window_days if window_days is not None
                             else int(settings.policy_max_age_days or 0)) or None

    if due is not None and due < now:
        return Staleness(REVIEW_OVERDUE,
                         "the review deadline for this record has passed",
                         (now - verified).days if verified else None, window)
    if verified is None:
        return Staleness(UNKNOWN, "no verification date is recorded", None, window)
    if window is None:
        return Staleness(UNKNOWN, "no freshness window is configured", (now - verified).days, None)
    age = (now - verified).days
    if age > window:
        return Staleness(STALE, f"last verified {age} days ago, outside the {window}-day window",
                         age, window)
    return Staleness(CURRENT, "verified inside the freshness window", age, window)


def needs_reverification(records: Sequence[Any], now: Optional[datetime] = None
                         ) -> Dict[str, int]:
    """Count records by staleness state. Bounded, no per-record queries."""
    out: Dict[str, int] = {CURRENT: 0, REVIEW_OVERDUE: 0, STALE: 0, UNKNOWN: 0}
    for record in records:
        out[staleness(record, now).state] += 1
    return out
