"""The human verification workflow (W12).

Factual catalogue data becomes official only here: a person with review authority looks at a
field, its evidence and the value it would replace, and records a decision. Three properties are
enforced in this module rather than in the router, so every caller - today's admin API and any
future one - inherits them:

* **An upload cannot certify itself.** A committed import always leaves the record
  ``NEEDS_REVIEW`` (see ``importer.commit_batch``), and a reviewer who uploaded that same entity
  cannot approve it.
* **A field decision is not an entity decision.** Resolving one field used to stamp the whole
  record ``VERIFIED``, which is the "one timestamp proves every field" pattern the trust policy
  forbids. The entity's state is now *derived* from its field register.
* **A refusal carries a reason.** VERIFIED needs none; every other outcome requires a note,
  because "who rejected this and why" is the question a dispute turns on.

Nothing here decides that data is true. It records that a named person reviewed stated evidence
and reached a decision, with the reason kept alongside it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, cast

from sqlalchemy import exists, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.models.crm import CatalogueChange, DataVerification
from app.models.education import (
    Country,
    Program,
    ProgramIntake,
    ProgramRequirement,
    Scholarship,
    University,
)

log = logging.getLogger(__name__)

#: Entity types a reviewer may act on, mapped to their models. A type outside this map is refused
#: rather than guessed, so a URL parameter can never select an arbitrary table.
ENTITY_MODELS: Dict[str, Any] = {
    "country": Country,
    "university": University,
    "program": Program,
    "scholarship": Scholarship,
    "program_intake": ProgramIntake,
    "program_requirement": ProgramRequirement,
}

#: Outcomes a reviewer may record on a field, and what each means for presentation.
FIELD_STATUSES = ("VERIFIED", "NEEDS_REVIEW", "OUTDATED", "CONFLICTING")


class ReviewError(ValueError):
    """A review action that must not proceed. Carries an HTTP-ish code for the caller."""

    def __init__(self, message: str, *, code: int = 400) -> None:
        super().__init__(message)
        self.code = code


def _model(entity_type: str):
    model = ENTITY_MODELS.get((entity_type or "").lower())
    if model is None:
        raise ReviewError(f"'{entity_type}' is not a reviewable entity type", code=404)
    return model


def _entity_state(row: DataVerification) -> str:
    return (row.status or "").upper()


def _is_current(row: DataVerification) -> bool:
    """Same-day reviews are one batch, not a history."""
    verified = row.verified_at
    if verified is None:
        return False
    now = datetime.now(timezone.utc)
    return (now - verified).days < 1


def recompute_entity_confidence(db: Session, entity_type: str, entity_id: int) -> str:
    """The entity's state, derived from its field register.

    Derived, never asserted: a record is VERIFIED only when it has at least one registered field
    and *every* registered field is VERIFIED. Any conflict dominates. Everything else is
    NEEDS_REVIEW, which the read-time policy treats as not presentable.
    """
    rows = list(db.execute(
        select(DataVerification).where(
            DataVerification.entity_type == entity_type,
            DataVerification.entity_id == entity_id,
        )
    ).scalars())
    if not rows:
        return "NEEDS_REVIEW"
    states = {_entity_state(r) for r in rows}
    if "CONFLICTING" in states:
        return "CONFLICTING"
    if states == {"VERIFIED"}:
        return "VERIFIED"
    if states == {"OUTDATED"}:
        return "OUTDATED"
    return "NEEDS_REVIEW"


def resolve_field(db: Session, record: DataVerification, *, status: str, actor_email: str,
                  note: Optional[str] = None,
                  expected_status: Optional[str] = None) -> Dict[str, Any]:
    """Record one field decision. The caller owns the transaction and the audit entry."""
    new_status = (status or "").upper()
    if new_status not in FIELD_STATUSES:
        raise ReviewError(f"status must be one of {', '.join(FIELD_STATUSES)}")

    if expected_status is not None and _entity_state(record).upper() != expected_status.upper():
        # The reviewer was shown a state that is already gone: refuse before doing any work.
        raise ReviewError(
            f"this field changed while you were reviewing it (now {_entity_state(record).upper()})",
            code=409)

    note_text = (note or "").strip()
    if new_status != "VERIFIED" and not note_text:
        # A refusal without a reason is a decision a later dispute cannot examine.
        raise ReviewError("a note is required when a field is not approved")

    if new_status == "VERIFIED" and _same_day_reversal(db, record):
        raise ReviewError("this field was verified today; a second confirmation adds nothing")

    previous_status = (record.status or "").upper()
    now = datetime.now(timezone.utc)
    values: Dict[str, Any] = {"status": new_status}
    if new_status == "VERIFIED":
        values["verified_at"] = now
        values["verified_by"] = actor_email
    else:
        # The verification belongs to the value that was verified. Once the value is disputed,
        # keeping the old stamp would let the policy treat a rejected figure as current.
        values["verified_at"] = None
        values["verified_by"] = None

    # Compare-and-swap on the state that was reviewed. The read-modify-write this replaces let
    # two reviewers both "succeed" - each one overwriting the other's decision, with two audit
    # entries claiming opposite outcomes (proved by tests/test_pg_concurrency.py). The WHERE clause
    # is the concurrency control: whoever wins changes the status, so the loser's update matches
    # no row and is told it lost.
    expectation = (expected_status or previous_status).upper()
    updated_rows = cast(CursorResult, db.execute(
        update(DataVerification)
        .where(DataVerification.id == record.id, DataVerification.status == expectation)
        .values(**values)
        .execution_options(synchronize_session=False)
    )).rowcount
    if updated_rows != 1:
        # No rollback here on purpose. A zero-row conditional update leaves the transaction
        # perfectly usable, and this service is called from inside the importer's per-row
        # savepoint: rolling back would discard the savepoint *and* the work around it. The
        # caller decides what to undo, and the admin route rolls back before answering 409.
        raise ReviewError(
            "this field changed while you were reviewing it; reload and decide again", code=409)

    db.expire(record)
    db.refresh(record)

    derived = recompute_entity_confidence(db, record.entity_type, record.entity_id)
    _apply_entity_state(db, record.entity_type, record.entity_id, derived, actor_email)
    return {"field_id": record.id, "field_status": record.status,
            "previous_field_status": previous_status,
            "entity_status": derived, "entity_type": record.entity_type,
            "entity_id": record.entity_id}


def _same_day_reversal(db: Session, record: DataVerification) -> bool:
    if _entity_state(record) != "VERIFIED":
        return False
    verified = record.verified_at
    if verified is None:
        return False
    now = datetime.now(timezone.utc)
    age = (now - (verified if verified.tzinfo else verified.replace(tzinfo=timezone.utc)))
    return age.days < 1


def _apply_entity_state(db: Session, entity_type: str, entity_id: int, state: str,
                        actor_email: str) -> None:
    """Write the derived state onto the entity row.

    Lowering trust is an unconditional write: it can only ever withhold a record, and a concurrent
    edit that also lowers it is not a conflict. **Promoting** to VERIFIED is different, and this is
    the one place it can happen outside the review queue. ``state`` was derived from the register a
    moment ago, and a direct catalogue edit can commit in between - appending a NEEDS_REVIEW row and
    withdrawing the entity's verification. Writing the stale ``VERIFIED`` onto the entity would put
    an unreviewed figure back in front of students with a green badge, so the promotion is made
    conditional *in the statement itself*: it only applies while no register row for this entity
    disagrees. The database sees the edit; the cached derivation does not.
    """
    model = ENTITY_MODELS.get(entity_type)
    if model is None:
        return
    entity = db.get(model, entity_id)
    if entity is None or not hasattr(entity, "confidence"):
        return
    if state == "VERIFIED":
        promoted = cast(CursorResult, db.execute(
            update(model)
            .where(
                model.id == entity_id,
                ~exists().where(
                    DataVerification.entity_type == entity_type,
                    DataVerification.entity_id == entity_id,
                    func.upper(DataVerification.status) != "VERIFIED",
                ),
            )
            .values(confidence=state, verified_at=datetime.now(timezone.utc),
                    verified_by=actor_email)
            .execution_options(synchronize_session=False)
        )).rowcount
        if promoted != 1:
            # The register moved under this decision: do not certify, and leave the record at
            # whatever the other writer - an edit awaiting review - has already set.
            log.info("promotion of %s id=%s withheld: the register changed during the decision",
                     entity_type, entity_id)
            db.expire(entity)
            return
        db.expire(entity)
        return

    # Lowering trust: unconditional, because it can only ever withhold a record.
    entity.confidence = state
    # Not verified means not verified: leaving a timestamp behind would satisfy the policy's date
    # check for a value nobody stands behind.
    if hasattr(entity, "verified_at"):
        entity.verified_at = None
    if hasattr(entity, "verified_by"):
        entity.verified_by = None


def assert_review_authority(db: Session, record: DataVerification, actor_email: str) -> None:
    """Refuse self-certification: the uploader of an entity may not approve its own data."""
    from app.services import importer

    if importer.batches_uploaded_by(db, entity_type=record.entity_type,
                                    entity_id=record.entity_id, email=actor_email):
        raise ReviewError(
            "this record was uploaded by the same account; a second reviewer must approve it",
            code=409)


def entity_review_detail(db: Session, entity_type: str, entity_id: int, *,
                         history_limit: int = 25) -> Dict[str, Any]:
    """Everything a reviewer needs in one bounded response.

    Current stored values, the registered evidence for each field, the changes waiting for a
    decision, and the recent history. No student data is reachable through this: the map above
    contains catalogue models only.
    """
    model = _model(entity_type)
    entity = db.get(model, entity_id)
    if entity is None:
        raise ReviewError("record not found", code=404)

    fields = list(db.execute(
        select(DataVerification).where(
            DataVerification.entity_type == entity_type,
            DataVerification.entity_id == entity_id,
        ).order_by(DataVerification.field_name.asc())
    ).scalars())
    changes = list(db.execute(
        select(CatalogueChange).where(
            CatalogueChange.entity_type == entity_type,
            CatalogueChange.entity_id == entity_id,
        ).order_by(CatalogueChange.id.desc()).limit(max(1, min(int(history_limit), 200)))
    ).scalars())

    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "current": {
            "confidence": getattr(entity, "confidence", None),
            "verified_at": _iso(getattr(entity, "verified_at", None)),
            "verified_by": getattr(entity, "verified_by", None),
            "source_url": getattr(entity, "source_url", None),
            "source_type": getattr(entity, "source_type", None),
            "retrieved_at": _iso(getattr(entity, "retrieved_at", None)),
            "review_due_at": _iso(getattr(entity, "review_due_at", None)),
            "title": getattr(entity, "name", None) or getattr(entity, "label", None),
        },
        "derived_status": recompute_entity_confidence(db, entity_type, entity_id),
        "fields": [{
            "id": f.id, "field_name": f.field_name, "value": f.field_value,
            "status": f.status, "source_url": f.source_url, "source_type": f.source_type,
            "retrieved_at": _iso(f.retrieved_at), "verified_at": _iso(f.verified_at),
            "verified_by": f.verified_by, "review_due_at": _iso(f.review_due_at),
            "conflict_with": f.conflict_with,
        } for f in fields],
        "changes": [{
            "id": c.id, "field_name": c.field_name, "previous_value": c.previous_value,
            "new_value": c.new_value, "classification": c.classification,
            "status": c.status, "version": c.version, "actor": c.actor,
            "source_url": c.source_url, "created_at": _iso(c.created_at),
            "decided_by": c.decided_by, "decided_at": _iso(c.decided_at),
            "decision_note": c.decision_note,
        } for c in changes],
    }


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else None
