"""A direct edit to a verified catalogue record invalidates that verification (H-1).

The trust layer makes one promise: a figure reaches a student as *verified* only because a named
person reviewed the value and its evidence. Two write paths had to honour it. The importer did
(``importer.commit_batch`` forces ``NEEDS_REVIEW`` and clears the verification dates whenever a
material field changes). The generic admin CRUD did not: a counselor could ``PUT`` a new
``tuition_annual`` on a VERIFIED programme and the record kept its confidence, its reviewer, its
verification date and its official source, so the read-time policy went on presenting the new
figure as verified against evidence nobody had looked at. That was a two-request path to a false
official figure, and the reason this module exists.

The rule implemented here is the importer's rule, applied to the direct-edit path:

* **A factual claim changed → the verification no longer covers the record.** Confidence becomes
  ``NEEDS_REVIEW``, ``verified_at`` / ``verified_by`` are cleared, and ``review_due_at`` is cleared
  too. Clearing the deadline is deliberate: the policy treats a missing deadline (when one is
  required) as overdue, which is the fail-closed reading, and a record that is already
  ``NEEDS_REVIEW`` is withheld whatever the deadline says.
* **The claim is registered, not asserted.** One ``DataVerification`` row per changed field is
  appended with ``status=NEEDS_REVIEW`` and no verification date - the same shape the importer
  writes. Because ``review.recompute_entity_confidence`` derives the entity state from *all* its
  register rows, the appended row keeps the entity at ``NEEDS_REVIEW`` even if every earlier field
  is re-verified; without it, approving an unrelated field would silently re-certify the edit.
* **The change is auditable.** A ``CatalogueChange`` row records previous value, new value, the
  actor and the classification, so the review queue can decide it and the history keeps both
  values. It reuses ``catalogue_changes.record`` - the same classifier, the same per-field
  advisory lock, the same version sequence - rather than a second implementation of the rules.

What is *not* material, and why, is an explicit list (:data:`NON_FACTUAL_FIELDS`). Presentation
and routing columns (slug, SEO fields, images, ranking/priority, feature flags, counters) are not
claims about the world, so editing them must not send a verified record back for review. Everything
else on a governed model is a factual claim and invalidates. Provenance columns are not claims
either: ``source_url`` / ``source_type`` / ``retrieved_at`` support a decision without making one,
which is why they stay writable and never invalidate on their own - the generic CRUD cannot use
them to certify a value.

Nothing here can promote trust. It only ever moves a record *out* of ``VERIFIED``; the review
workflow in :mod:`app.services.review` is still the only thing that can put it back.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core import verification_policy as policy
from app.models.base import VerificationMixin
from app.models.crm import DataVerification
from app.services import catalogue_changes

log = logging.getLogger(__name__)

#: Columns that are not claims about the world. Editing one of these on a verified record is an
#: editorial or routing change and must not invalidate the verification:
#:
#: * identity and bookkeeping - ``id``, ``created_at``, ``updated_at``;
#: * routing - ``slug``;
#: * presentation - SEO/OpenGraph text and images, logos and hero images;
#: * exposure and ordering - publish/feature flags, priority, position, counters.
#:
#: Every other column on a governed model is treated as a factual claim. That direction is the
#: safe one: an over-broad list would let a fee edit keep its badge, while an over-narrow one
#: only costs a reviewer a glance at something that really was content.
NON_FACTUAL_FIELDS = frozenset({
    "id", "created_at", "updated_at", "deleted_at",
    "slug", "position", "priority", "sort_order", "display_order", "views",
    "is_featured", "is_published", "is_active",
    "logo_url", "hero_image", "cover_image_url", "banner_url", "og_image", "photo_url",
    "seo_title", "seo_description", "seo_keywords", "noindex",
    "meta_title", "meta_description", "og_title", "og_description",
})

#: Evidence and decision columns. Not claims themselves (see the module docstring), so they are
#: never the trigger for an invalidation - but they are also never enough to certify one.
PROVENANCE_FIELDS = frozenset({"source_url", "source_type", "retrieved_at", "source_note",
                               "email_source_url", "provenance"})
TRUST_FIELDS = frozenset({"confidence", "verified_at", "verified_by", "review_due_at"})

#: Never writable through a direct edit, whatever the payload says.
UNWRITABLE_FIELDS = TRUST_FIELDS | PROVENANCE_FIELDS | frozenset({"id", "created_at"})


def governs(model: Any) -> bool:
    """Whether this model carries a verification claim at all.

    Derived from the mixin rather than from a hand-maintained list: the set of governed resources
    is exactly the set of models whose rows can be ``VERIFIED``, so the two cannot drift apart.
    """
    try:
        return issubclass(model, VerificationMixin)
    except TypeError:                      # not a class
        return False


def entity_type_for(model: Any) -> Optional[str]:
    """The entity type the verification register and change log key on.

    Reuses :data:`app.core.verification_policy.ENTITY_TYPES_BY_CLASS`, which already answers
    "which register rows describe this record", so a change recorded here lands in the same
    history a reviewer and the importer already read.
    """
    types = policy.ENTITY_TYPES_BY_CLASS.get(getattr(model, "__name__", ""))
    return types[0] if types else None


def material_changes(row: Any, payload: Dict[str, Any]) -> List[Tuple[str, Any, Any]]:
    """The (field, previous, new) triples in this payload that change a factual claim.

    Runs *before* the payload is applied, so ``previous`` is the stored value. A value that is
    only formatted differently is not a change - the decision comes from
    :func:`app.services.catalogue_changes.classify`, the same classifier the importer and the
    change queue use, so "no change" means the same thing on every path.
    """
    found: List[Tuple[str, Any, Any]] = []
    for field, new in (payload or {}).items():
        if field in UNWRITABLE_FIELDS or field in NON_FACTUAL_FIELDS:
            continue
        if not hasattr(row, field):
            continue
        previous = getattr(row, field)
        kind = catalogue_changes.classify(field, previous, new)
        if kind in (catalogue_changes.NO_CHANGE, catalogue_changes.FORMAT_ONLY):
            continue
        found.append((field, previous, new))
    return found


def _register_claim(db: Session, *, entity_type: str, entity_id: int, field: str, value: Any,
                    actor: str, source_url: Optional[str],
                    source_type: Optional[str]) -> None:
    """Append a NEEDS_REVIEW register row for the edited field.

    Mirrors ``importer._record_provenance``: the value is recorded with no verification date and
    no verifier, so ``review.recompute_entity_confidence`` cannot derive ``VERIFIED`` from a
    register that still describes the old figure.
    """
    db.add(DataVerification(
        entity_type=entity_type, entity_id=entity_id, field_name=field,
        field_value=_evidence_text(value),
        source_url=(source_url or None),
        source_type=(source_type or "TIER_1_OFFICIAL_UNIVERSITY"),
        retrieved_at=datetime.now(timezone.utc),
        verified_at=None, verified_by=None,
        status="NEEDS_REVIEW",
    ))
    log.info("direct edit re-registered %s.%s for review by %s", entity_type, field, actor)


def _evidence_text(value: Any) -> Optional[str]:
    """The value exactly as recorded, for the evidence column (C2-10).

    No length budget: the register is what a trust decision is derived from, so it has to hold the
    value it attests to. This used to clip to fit a ``VARCHAR(400)`` column, which made every
    edit of a long field un-verifiable - the derivation compared the clip with the stored value,
    disagreed, and had no way to be answered. ``catalogue_changes`` still clips its history
    columns (they are a change log, not evidence and not comparable input).
    """
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def apply_direct_edit(db: Session, *, model: Any, row: Any, payload: Dict[str, Any],
                      actor: str, blocked: frozenset) -> Dict[str, Any]:
    """Apply a generic-CRUD update, then invalidate any verification it just invalidated.

    One function owns the whole write so the value and its trust state cannot be updated by two
    separate code paths that might disagree: the material plan is computed from the stored row,
    the payload is applied, the trust columns are refused a second time, the register and the
    change log are written, and the caller's single transaction commits all of it (or none of it -
    the router's ``get_db`` rolls the whole thing back on any exception).

    Returns a summary for the caller's audit entry. It carries field names and the previous
    confidence only, never values, so nothing here can leak content into an audit log.
    """
    entity_type = entity_type_for(model)
    if entity_type is None:
        # A model the verification policy does not describe carries no claim to invalidate; the
        # caller's own plain field loop handles it. Refusing to guess an entity type is what keeps
        # a change row from being written under a name the review queue cannot read back.
        for field, value in (payload or {}).items():
            if field in blocked or field in TRUST_FIELDS or field == "id":
                continue
            if hasattr(row, field):
                setattr(row, field, value)
        db.flush()
        return {"changed_fields": [], "invalidated": False, "material_fields": []}
    plan = material_changes(row, payload)

    # 1. Apply the payload. Trust and provenance columns are refused here as well as in the
    #    router: this function is the last place that can guarantee a caller cannot certify a
    #    record through a generic form, so it does not rely on its caller having done it.
    applied: List[str] = []
    for field, value in (payload or {}).items():
        if field in blocked or field in TRUST_FIELDS or field == "id":
            continue
        if not hasattr(row, field):
            continue
        setattr(row, field, value)
        applied.append(field)

    if not plan:
        db.flush()
        return {"changed_fields": applied, "invalidated": False, "material_fields": []}

    previous_confidence = (getattr(row, "confidence", "") or "").upper()

    # 2. The previous verification does not cover the new value. This is a statement of fact, not
    #    a decision to be recomputed: the register still describes the old figure, so deriving the
    #    state from it would keep saying VERIFIED.
    #
    #    Written as SQL, not as ``row.confidence = "NEEDS_REVIEW"``. An ORM assignment only emits
    #    a column when the session sees a change, and the loaded value here is *already*
    #    NEEDS_REVIEW whenever the record still has an open claim - which is exactly the row an
    #    approval targets. In that case no UPDATE was emitted at all, the invalidation never
    #    reached the database, and a concurrent approval's VERIFIED survived over an unreviewed
    #    value: "entity says VERIFIED while the register derives NEEDS_REVIEW" (C2-10).
    withholding: Dict[str, Any] = {"confidence": "NEEDS_REVIEW"}
    if hasattr(model, "verified_at"):
        withholding["verified_at"] = None
    if hasattr(model, "verified_by"):
        withholding["verified_by"] = None
    if hasattr(model, "review_due_at"):
        withholding["review_due_at"] = None
    db.execute(
        update(model)
        .where(model.id == row.id)
        .values(**withholding)
        .execution_options(synchronize_session=False)
    )
    db.flush()
    # Drop the stale identity-map copy so anything that reads the row next in this transaction
    # sees what the database now holds, not what was true when the edit was built.
    db.expire(row)

    # 3. Register the edited values as claims awaiting review, and record the change so a human
    #    can decide it and the history keeps both values.
    source_url = payload.get("source_url") if isinstance(payload, dict) else None
    source_type = payload.get("source_type") if isinstance(payload, dict) else None
    for field, previous, new in plan:
        _register_claim(db, entity_type=entity_type, entity_id=row.id, field=field, value=new,
                        actor=actor, source_url=source_url, source_type=source_type)
        catalogue_changes.record(
            db, entity_type=entity_type, entity_id=row.id, field_name=field,
            previous=previous, new=new, source_url=source_url, source_type=source_type,
            actor=actor,
        )

    log.info("direct edit invalidated verification of %s id=%s (%s), fields=%s",
             entity_type, row.id, previous_confidence, [f for f, _p, _n in plan])
    return {
        "changed_fields": applied,
        "invalidated": True,
        "material_fields": [field for field, _previous, _new in plan],
        "previous_confidence": previous_confidence,
    }
