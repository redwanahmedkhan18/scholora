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
from typing import Any, Dict, Iterable, Optional, cast

from sqlalchemy import exists, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, aliased

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

#: The one non-decision outcome: a claim is taken out of the register's evidence by recording that
#: its evidence has been withdrawn, not by deleting the row. There is no route that sets this
#: through a review decision - only :func:`withdraw_field`, which requires an actor and a reason -
#: and no route that removes a register row at all. Deleting the row was F-2: the entity's state
#: is derived from its register, so a row that disappeared restored ``VERIFIED`` to a figure the
#: register had been holding at ``NEEDS_REVIEW``. Withdrawal keeps the history and fails closed.
#:
#: Every status other than ``VERIFIED`` withholds the entity, so this one needs no special case in
#: the derivation - which is the point of giving withdrawal a state instead of deleting the row.
WITHDRAWN = "WITHDRAWN"

#: Width of the evidence column before it was widened to ``Text`` (C2-10). Rows written under the
#: old schema hold a *prefix* of the value they attest, so a recorded value of exactly this length
#: that is a prefix of a longer stored value cannot be compared - the remainder exists nowhere.
#: Those rows are reported as unanswerable rather than as a disagreement, so the reviewer is given
#: an accurate reason and a way out instead of a silent, permanent withholding.
LEGACY_EVIDENCE_LIMIT = 400

#: Why a record is withheld, and what the authorized caller can do about it (C2-10). Each reason
#: is a statement about the *register*, never about a value a student could infer, and never about
#: the schema: the review API may be read by any DATA_MANAGER.
WITHHELD_REASONS: Dict[str, str] = {
    "no_register": "no field evidence has been registered for this record",
    "conflicting": "a registered field is in conflict",
    "outdated": "every registered field is past its review date",
    "fields_not_verified": "at least one field's current claim is not approved",
    "pending_change": "a material change is waiting for a decision",
    "evidence_mismatch": "the recorded evidence describes a different value than the one stored",
    "evidence_truncated": "the recorded evidence is too short to compare with the stored value "
                          "and looks like a copy truncated at 400 characters; this is a heuristic, "
                          "not proof the original was longer",
    "record_missing": "the record this evidence belongs to no longer exists",
}

#: The supported recovery for the two evidence-level withholdings. Both are actions a reviewer
#: already has: register the field again from the current source, then decide that claim. A new
#: row supersedes the old one for its field, so the unanswerable pair stops being current.
RECOVERY: Dict[str, str] = {
    "evidence_truncated": "register the field value again from the current source "
                          "(POST /admin/verifications, or re-save the field) and decide that "
                          "claim; a new claim supersedes the earlier one",
    "evidence_mismatch": "re-verify the field against the current source, or correct the record "
                         "so its stored value matches the evidence",
}


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
    """The entity's state, derived from its field register. Fails closed.

    Derived, never asserted: a record is VERIFIED only when it has at least one registered field
    and *every* registered field's current row is VERIFIED. Any conflict dominates. Everything else
    is NEEDS_REVIEW, which the read-time policy treats as not presentable.

    Three further conditions were added for F-2, and each one can only ever *withhold* a record:

    * **No undecided material change.** A change recorded against the entity that nobody has
      decided (``CatalogueChange.status == PENDING``) is an open question about a stored value.
      Certifying the entity while it is open is what let an unreviewed figure be attributed to a
      reviewer, so an open question keeps the record at NEEDS_REVIEW until it is decided.
    * **The register must still describe the stored values.** A ``VERIFIED`` row whose recorded
      evidence value no longer matches the column it attests to is a stale approval: it was a
      decision about a different value. It does not certify the current one.
    * **A missing row is not an approval.** Conditions above are evaluated from the rows that
      exist, so the register is only ever read as evidence *for* a value - never as permission
      granted by absence. Rows are no longer deletable (see :func:`withdraw_field` and the
      admin router's review-only resources), so absence cannot be manufactured through the API.
    """
    return explain_entity_state(db, entity_type, entity_id)["state"]


def explain_entity_state(db: Session, entity_type: str, entity_id: int) -> Dict[str, Any]:
    """The derived state, and - when it is withheld - an explicit, caller-safe reason (C2-10).

    A reviewer used to be told a field was approved and handed ``NEEDS_REVIEW`` for the record with
    nothing explaining the difference, which is a dead end: the state is derived, so there is no
    field to edit. Withholding now always carries a reason code, a sentence, the fields involved
    and - where one exists - the supported recovery. None of it describes the schema or discloses a
    value; it is a statement about the register, readable by the reviewers who already see the
    register itself.
    """
    # populate_existing: a row already in the identity map would otherwise be returned with the
    # status it had when this session first read it, and the derivation would be computed from a
    # decision that has since moved. The derivation has to describe the register as it is now.
    rows = list(db.execute(
        select(DataVerification).where(
            DataVerification.entity_type == entity_type,
            DataVerification.entity_id == entity_id,
        ).order_by(DataVerification.id.asc())
        .execution_options(populate_existing=True)
    ).scalars())
    if not rows:
        return _derivation("NEEDS_REVIEW", "no_register")
    current: dict[str, DataVerification] = {}
    for row in rows:
        current[(row.field_name or "").strip().lower()] = row
    states = {_entity_state(r) for r in current.values()}
    if "CONFLICTING" in states:
        return _derivation("CONFLICTING", "conflicting")
    if states == {"VERIFIED"}:
        if _has_undecided_change(db, entity_type, entity_id):
            return _derivation("NEEDS_REVIEW", "pending_change")
        verdict = _evidence_verdict(db, entity_type, entity_id, list(current.values()))
        if not verdict["ok"]:
            return _derivation("NEEDS_REVIEW", verdict["reason"], verdict["fields"])
        return _derivation("VERIFIED", None)
    if states == {"OUTDATED"}:
        return _derivation("OUTDATED", "outdated")
    unverified = [(r.field_name or "").strip() for r in current.values()
                  if _entity_state(r) != "VERIFIED"]
    return _derivation("NEEDS_REVIEW", "fields_not_verified", unverified)


def _derivation(state: str, reason: Optional[str],
                fields: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """One derivation result: the state, why, on which fields, and how to recover."""
    named = sorted({(f or "").strip() for f in (fields or []) if (f or "").strip()})
    return {
        "state": state,
        "reason": reason,
        "explanation": WITHHELD_REASONS.get(reason) if reason else None,
        "fields": named,
        "recovery": RECOVERY.get(reason) if reason else None,
    }


def _has_undecided_change(db: Session, entity_type: str, entity_id: int) -> bool:
    """Whether a material change to this entity is still waiting for a decision."""
    pending = db.scalar(
        select(func.count()).select_from(CatalogueChange).where(
            CatalogueChange.entity_type == entity_type,
            CatalogueChange.entity_id == entity_id,
            CatalogueChange.status == "PENDING",
        )
    )
    return int(pending or 0) > 0


def _loose(value: Any) -> str:
    """Compare-shape for two renderings of one value (``2024-09-01`` vs ``2024-09-01 00:00:00``)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.isoformat()
    return "".join(ch for ch in str(value).strip().lower() if ch.isalnum())


def values_agree(stored: Any, recorded: Any) -> bool:
    """Whether a register's recorded evidence still describes the value actually stored.

    Numbers compare numerically (``20000`` and ``"20000.0"`` are the same fee), everything else
    compares on its alphanumeric characters, so a formatting difference is not read as a stale
    approval. A value that cannot be compared is *not* treated as agreeing: the check exists to
    withhold trust, and an unreadable pair is the reason to withhold it.
    """
    if stored is None and (recorded is None or str(recorded).strip() == ""):
        return True
    if stored is None or recorded is None:
        return False
    if not isinstance(stored, bool) and not isinstance(recorded, bool):
        try:
            return abs(float(stored) - float(recorded)) <= 1e-9
        except (TypeError, ValueError):
            pass
    return _loose(stored) == _loose(recorded)


def _is_truncated_evidence(stored: Any, recorded: Any) -> bool:
    """Whether a recorded value is a legacy prefix of a longer stored value (C2-10).

    Claims written before evidence was stored in full hold at most :data:`LEGACY_EVIDENCE_LIMIT`
    characters. A recorded value of exactly that length that *is* a prefix of the longer stored
    value is the signature of a clip, and the remainder exists nowhere: the pair is unanswerable,
    not disagreeing, and it is reported that way so the reviewer gets a way out instead of a
    silent dead end. Comparison is otherwise unchanged: a value legitimately exactly 400
    characters long equals the stored value, and a recorded value that is not a prefix is a
    genuine disagreement.

    **This is a heuristic, not proof.** Nothing in the stored data says whether the original value
    was longer: a genuine 400-character value that happens to be a prefix of a longer stored value
    (a reviewer who registered a prefix by mistake, say) is indistinguishable from a clip and is
    reported the same way. The system therefore has no "confirmed truncation" state at all, and the
    reviewer-facing wording says the evidence *looks like* a truncated copy rather than asserting a
    cause. The consequence is the safe one either way - the record is withheld and the supported
    recovery (register the value again) resolves both cases.
    """
    if not isinstance(recorded, str) or len(recorded) != LEGACY_EVIDENCE_LIMIT:
        return False
    stored_text = stored.isoformat() if isinstance(stored, datetime) else (
        "" if stored is None else str(stored))
    if len(stored_text) <= LEGACY_EVIDENCE_LIMIT:
        return False
    # Compared on the same normalised shape ``values_agree`` uses, so a clip of a serialised JSON
    # value (``{'note': 'x' * 500}`` against ``{"note": "xxx…"}``) is recognised as a clip rather
    # than reported as a disagreement.
    loose_stored, loose_recorded = _loose(stored_text), _loose(recorded)
    return bool(loose_recorded) and loose_stored.startswith(loose_recorded)


def _evidence_verdict(db: Session, entity_type: str, entity_id: int,
                      rows: list[DataVerification]) -> Dict[str, Any]:
    """Whether every VERIFIED register row still describes the column it attests to, and why not.

    Returns ``{"ok": bool, "reason": str|None, "fields": list[str]}``. Only *stored columns* can
    be compared. The importer also registers the keys of the import spec (``university``, for
    instance), which name a relationship rather than a scalar, so there is nothing to compare and
    nothing is inferred from that - those rows are skipped. Empty field names are skipped for the
    same reason: they cannot certify anything.

    Cannot be answered at all for an entity type without a model in :data:`ENTITY_MODELS` (the
    register also carries faculty and ranking claims). There the question is unanswerable rather
    than false, so this returns ok and the other fail-closed conditions still apply - this check
    never promotes anything, it only withholds.

    A pair that cannot be compared at all (a legacy-clipped record, see
    :func:`_is_truncated_evidence`) is reported separately from one that disagrees. Both withhold;
    they differ in what the reviewer is told and in whether evidence already on file can resolve
    it. Neither verdict is ever derived from a truncated comparison: the stored value is read in
    full, which is why the evidence column stores it in full.
    """
    verdict: Dict[str, Any] = {"ok": True, "reason": None, "fields": []}
    model = ENTITY_MODELS.get((entity_type or "").lower())
    if model is None:
        return verdict
    # As in explain_entity_state: compare the values that are stored now, not the ones this
    # session happened to load earlier.
    entity = db.get(model, entity_id, populate_existing=True)
    if entity is None:
        return {"ok": False, "reason": "record_missing", "fields": []}
    columns = set(entity.__table__.columns.keys())
    mismatched: list[str] = []
    truncated: list[str] = []
    for row in rows:
        if _entity_state(row) != "VERIFIED":
            continue
        field = (row.field_name or "").strip()
        if not field or field not in columns:
            continue
        stored = getattr(entity, field)
        recorded = row.field_value
        if _is_truncated_evidence(stored, recorded):
            truncated.append(field)
        elif not values_agree(stored, recorded):
            mismatched.append(field)
    if mismatched:
        return {"ok": False, "reason": "evidence_mismatch", "fields": mismatched}
    if truncated:
        return {"ok": False, "reason": "evidence_truncated", "fields": truncated}
    return verdict


def refresh_entity_state(db: Session, entity_type: str, entity_id: int,
                         actor_email: str) -> str:
    """Re-derive an entity's trust state from its register and write it, in this transaction.

    The one entry point for "a register row just changed". Every mutation path calls it - a field
    decision, a withdrawal, and a page-level source confirmation - so the entity can never be left
    describing a register that has moved.

    **The entity row is locked before the derivation, and that ordering is the whole guarantee.**
    Both writers of this entity's trust - this function and a direct catalogue edit that
    invalidates the record - touch the entity row, so a row lock serializes them. Locking first
    matters because the derivation must see the *other* transaction's register rows: a
    ``SELECT ... FOR UPDATE`` that has to wait returns only after the competitor commits, so the
    ``SELECT`` issued next runs on a fresh snapshot and sees them.

    Doing it the other way round - derive, then try to guard the write with a conditional
    ``UPDATE ... WHERE NOT EXISTS (subquery)`` - is not sufficient under READ COMMITTED. PostgreSQL
    evaluates such subqueries with the statement's own snapshot and does not re-check them after
    the EvalPlanQual re-read: measured directly on PostgreSQL 16, the promotion still applied with
    ``rowcount = 1`` while a ``NEEDS_REVIEW`` register row committed during the wait. That stale
    snapshot is exactly how a record came to say VERIFIED while its register derived NEEDS_REVIEW.
    """
    model = ENTITY_MODELS.get(entity_type)
    if model is not None:
        db.execute(select(model.id).where(model.id == entity_id).with_for_update())
    derived = recompute_entity_confidence(db, entity_type, entity_id)
    _apply_entity_state(db, entity_type, entity_id, derived, actor_email)
    return derived


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

    derived = refresh_entity_state(db, record.entity_type, record.entity_id, actor_email)
    return {"field_id": record.id, "field_status": record.status,
            "previous_field_status": previous_status,
            "entity_status": derived, "entity_type": record.entity_type,
            "entity_id": record.entity_id,
            # Why the record is still withheld, if it is (C2-10): approving every field and being
            # handed NEEDS_REVIEW with no explanation is a dead end with nothing to act on.
            "derivation": explain_entity_state(db, record.entity_type, record.entity_id)}


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
    conditional *in the statement itself*: it only applies while no field's **current** register row
    disagrees. "Current" is per field, matching the derivation: a withdrawn row that attested an old
    value is history, not a live disagreement, and a field whose newest row is NEEDS_REVIEW,
    OUTDATED, CONFLICTING or WITHDRAWN withholds the promotion. The database sees the edit; the
    cached derivation does not.
    """
    model = ENTITY_MODELS.get(entity_type)
    if model is None:
        return
    entity = db.get(model, entity_id)
    if entity is None or not hasattr(entity, "confidence"):
        return
    if state == "VERIFIED":
        newer = aliased(DataVerification)
        stale_row = (
            exists()
            .where(
                DataVerification.entity_type == entity_type,
                DataVerification.entity_id == entity_id,
                func.upper(DataVerification.status) != "VERIFIED",
                # Nothing later in the register for the same field: this row is that field's
                # current state.
                ~exists().where(
                    newer.entity_type == DataVerification.entity_type,
                    newer.entity_id == DataVerification.entity_id,
                    newer.field_name == DataVerification.field_name,
                    newer.id > DataVerification.id,
                ),
            )
        )
        promoted = cast(CursorResult, db.execute(
            update(model)
            .where(
                model.id == entity_id,
                ~stale_row,
                # F-2, second condition: an undecided material change is an open question about a
                # stored value. Checked in the statement, not only in the derivation, because a
                # change can be appended between the two.
                ~exists().where(
                    CatalogueChange.entity_type == entity_type,
                    CatalogueChange.entity_id == entity_id,
                    CatalogueChange.status == "PENDING",
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

    # Lowering trust is unconditional, because it can only ever withhold a record - and it is
    # written as a statement, not as an ORM delta. An attribute assignment only reaches the
    # database when the session observes a *change*; if the identity map already holds the target
    # state (a record that was NEEDS_REVIEW and is still NEEDS_REVIEW because another transaction
    # promoted it behind this session's back) no UPDATE is emitted at all, the stale promotion
    # survives, and the record goes on claiming VERIFIED over a register that derives NEEDS_REVIEW.
    # That divergence is C2-10. Keying the statement on the primary key makes the withholding
    # happen whatever the cached object happens to hold.
    withholding: Dict[str, Any] = {"confidence": state}
    # Not verified means not verified: leaving a timestamp behind would satisfy the policy's date
    # check for a value nobody stands behind.
    if hasattr(model, "verified_at"):
        withholding["verified_at"] = None
    if hasattr(model, "verified_by"):
        withholding["verified_by"] = None
    db.execute(
        update(model)
        .where(model.id == entity_id)
        .values(**withholding)
        .execution_options(synchronize_session=False)
    )
    db.expire(entity)


def withdraw_field(db: Session, record: DataVerification, *, actor_email: str, note: str,
                   expected_status: Optional[str] = None) -> Dict[str, Any]:
    """Take a claim's evidence out of the register without destroying its history (F-2).

    The register is the entity's evidence, and the entity's state is derived from it. That is why
    deleting a ``NEEDS_REVIEW`` row used to promote the entity: the remaining rows all said
    VERIFIED, so the derivation did too, and an unreviewed figure went public attributed to a
    reviewer who never saw it. Correction and withdrawal are therefore transitions, not deletions:

    * the row stays, with its recorded value and source, so the history that a dispute turns on is
      intact;
    * ``status`` becomes ``WITHDRAWN`` and the verification stamp is cleared, so it can never be
      read as an approval again;
    * the entity is recomputed in the same transaction, and withdrawal can only lower trust;
    * the reason is mandatory. A withdrawal without a reason is a decision a later dispute cannot
      examine, the same rule :func:`resolve_field` applies to every non-approval. Who withdrew the
      claim and why are written to ``audit_logs`` beside it: that table is this codebase's record of
      "who did what", and it deliberately survives an erased staff account.
    """
    reason = (note or "").strip()
    if not reason:
        raise ReviewError("a reason is required to withdraw a claim")

    previous_status = _entity_state(record)
    if previous_status == WITHDRAWN:
        raise ReviewError("this claim has already been withdrawn", code=409)
    if expected_status is not None and previous_status != expected_status.upper():
        raise ReviewError(
            f"this field changed while you were reviewing it (now {previous_status})", code=409)

    # Compare-and-swap, like resolve_field: two withdrawals, or a withdrawal racing a decision,
    # cannot both claim the same state transition. No timestamp is written: the verification stamp
    # is exactly what a withdrawal clears, and putting one back would satisfy the policy's date
    # check for a value nobody stands behind.
    changed = cast(CursorResult, db.execute(
        update(DataVerification)
        .where(DataVerification.id == record.id, DataVerification.status == previous_status)
        .values(status=WITHDRAWN, verified_at=None, verified_by=None)
        .execution_options(synchronize_session=False)
    )).rowcount
    if changed != 1:
        raise ReviewError(
            "this field changed while you were reviewing it; reload and decide again", code=409)
    db.expire(record)
    db.refresh(record)

    derived = refresh_entity_state(db, record.entity_type, record.entity_id, actor_email)
    log.info("claim withdrawn: %s.%s id=%s by %s", record.entity_type, record.field_name,
             record.id, actor_email)
    return {"field_id": record.id, "field_status": record.status,
            "previous_field_status": previous_status, "entity_status": derived,
            "entity_type": record.entity_type, "entity_id": record.entity_id,
            "derivation": explain_entity_state(db, record.entity_type, record.entity_id)}


def delete_field(db: Session, record: DataVerification, *, actor_email: str) -> None:
    """Refuse to delete a register row. The one place that owns the rule (F-2).

    Rows are the evidence an entity's trust is derived from, so a delete is not a data-management
    operation - it is a silent change to a trust decision, with no actor, no reason and no history.
    There is therefore no supported way to delete one: the admin API answers 405 and points here,
    and this function raises rather than deleting, so a future caller that reaches for a delete
    cannot succeed quietly.

    The supported transitions are :func:`resolve_field` (a decision) and :func:`withdraw_field` (a
    correction that keeps the history).
    """
    raise ReviewError(
        "a verification claim is never deleted: decide it with resolve_field, or withdraw it with "
        "withdraw_field so the evidence and the reason are kept",
        code=405,
    )


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
        # Why the derived state is what it is (C2-10). Additive: ``derived_status`` is unchanged.
        "derivation": explain_entity_state(db, entity_type, entity_id),
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
