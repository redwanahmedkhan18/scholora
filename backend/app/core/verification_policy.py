"""One read-time verification policy, applied wherever a fact could reach a student.

A record is not "verified" because a column says so. It is verified for *this question, today*
only when every condition holds: an explicit VERIFIED state, a dated verification, a link to
the official source it came from, a review deadline that has not passed, an age inside the
configured maximum, a matching academic year when the question names one, no unresolved
conflict, and provenance that is not demo/synthetic or second-hand only.

This module is deliberately pure: it takes a record (ORM object or plain dict) and the current
time, and returns a decision. No database access, so the same function drives retrieval, the
structured tools, the public serializers and the tests, and there is no second implementation
to drift out of step.

Unknown stays unknown. When a record fails the policy the caller must omit the figure or say it
is unavailable - never fall back to an older fee, another intake's requirement, or a guessed
currency.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.core import academic_year as academic_year_mod
from app.core.config import settings

#: Bumped whenever the rules change, so an answer can be audited against the rules in force.
POLICY_VERSION = "2026-11.1"

#: Verification states that may ever be presented as official. Everything else is a claim
#: awaiting review and is treated as unavailable.
VERIFIED_STATE = "VERIFIED"

#: Source tiers that are the university, department, admissions portal, scholarship provider
#: or ranking provider themselves. Anything else - including the owner's own website - is
#: secondary and may not, on its own, make a record VERIFIED.
OFFICIAL_SOURCE_TYPES = frozenset({
    "TIER_1_OFFICIAL_UNIVERSITY",
    "TIER_2_OFFICIAL_DEPARTMENT",
    "TIER_3_OFFICIAL_ADMISSIONS_PORTAL",
    "TIER_4_OFFICIAL_SCHOLARSHIP_PROVIDER",
    "TIER_5_OFFICIAL_RANKING_PROVIDER",
    # Government sources (immigration rules, fee regulations) outrank everything else.
    "OFFICIAL_GOVERNMENT",
})
#: How the demo seed marks itself. A row carrying any of these is never presented as official,
#: whatever its confidence column says - otherwise a placeholder corpus would look verified.
DEMO_MARKERS = ("sample dataset", "demo", "seed", "synthetic", "placeholder", "example data",
                "lorem", "test data")

#: Machine-readable reason codes, most severe first. The order here is the order of evaluation.
REASON_NOT_VERIFIED = "NOT_VERIFIED"
REASON_DEMO = "DEMO_PROVENANCE"
REASON_NO_VERIFICATION_DATE = "NO_VERIFICATION_DATE"
REASON_NO_SOURCE = "NO_SOURCE"
REASON_CONFLICTING = "CONFLICTING"
REASON_REVIEW_OVERDUE = "REVIEW_OVERDUE"
REASON_STALE = "STALE"
REASON_SECONDARY_ONLY = "SECONDARY_ONLY"
REASON_YEAR_MISMATCH = "YEAR_MISMATCH"
REASON_YEAR_UNKNOWN = "YEAR_UNKNOWN"
REASON_OK = "OK"

#: Plain-English explanation the frontend can show beside a record.
REASON_NOTES: Dict[str, str] = {
    REASON_NOT_VERIFIED: "Not presented as an official figure: this record has not been verified.",
    REASON_DEMO: "Not presented as an official figure: this record is demonstration data.",
    REASON_NO_VERIFICATION_DATE: "Not presented as an official figure: no verification date is recorded.",
    REASON_NO_SOURCE: "Not presented as an official figure: no official source link is recorded.",
    REASON_CONFLICTING: ("Not presented as an official figure: our records for this value disagree "
                         "and the conflict has not been resolved."),
    REASON_REVIEW_OVERDUE: "Not presented as current: the review deadline for this record has passed.",
    REASON_STALE: "Not presented as current: this record is older than our verification window.",
    REASON_SECONDARY_ONLY: ("Not presented as an official figure: it comes from a secondary source "
                            "and has not been confirmed against the university or government source."),
    REASON_YEAR_MISMATCH: "Not presented as current: the recorded academic year is not the one asked about.",
    REASON_YEAR_UNKNOWN: "Not presented as current: the academic year this figure applies to is not recorded.",
    REASON_OK: "Verified against an official source, within the review window.",
}


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _field(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


@dataclass(frozen=True)
class PolicyDecision:
    """The verdict for one record, plus everything needed to explain it."""

    usable: bool
    reason: str
    note: str
    policy_version: str = POLICY_VERSION
    age_days: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "verified": self.usable,
            "verification_reason": self.reason,
            "verification_note": self.note,
            "policy_version": self.policy_version,
        }


def _is_demo(record: Any) -> bool:
    provenance = str(_field(record, "provenance", "") or "").lower()
    if provenance in {"demo", "seed", "synthetic"}:
        return True
    haystack = " ".join(
        str(_field(record, f, "") or "") for f in ("verified_by", "source_type", "source_note")
    ).lower()
    return any(marker in haystack for marker in DEMO_MARKERS)


def evaluate(
    record: Any,
    *,
    now: Optional[datetime] = None,
    academic_year: Optional[Any] = None,
    has_conflict: Optional[bool] = None,
) -> PolicyDecision:
    """Decide whether this record may be presented as verified, current and official.

    ``academic_year`` is only passed when the student's question names one; a record whose own
    year is unknown then fails with ``YEAR_UNKNOWN`` rather than being stretched to fit.
    ``has_conflict`` lets a caller pass a conflict discovered outside the row itself (an
    unresolved verification record, for example).
    """
    now = now or datetime.now(timezone.utc)

    # Conflict and demo provenance are reported before the generic "not verified" outcome: the
    # student's answer differs ("our records disagree" vs "not verified yet"), and a caller
    # must be able to say which.
    if has_conflict or (str(_field(record, "confidence", "") or "").upper()) == "CONFLICTING":
        return PolicyDecision(False, REASON_CONFLICTING, REASON_NOTES[REASON_CONFLICTING])
    if _is_demo(record):
        return PolicyDecision(False, REASON_DEMO, REASON_NOTES[REASON_DEMO])
    if (str(_field(record, "confidence", "") or "").upper()) != VERIFIED_STATE:
        return PolicyDecision(False, REASON_NOT_VERIFIED, REASON_NOTES[REASON_NOT_VERIFIED])

    verified_at = _as_utc(_field(record, "verified_at"))
    if verified_at is None:
        return PolicyDecision(False, REASON_NO_VERIFICATION_DATE, REASON_NOTES[REASON_NO_VERIFICATION_DATE])

    source_url = (_field(record, "source_url") or "").strip()
    if not source_url:
        return PolicyDecision(False, REASON_NO_SOURCE, REASON_NOTES[REASON_NO_SOURCE])

    age_days = max(0, int((now - verified_at).total_seconds() // 86400))

    source_type = (str(_field(record, "source_type", "") or "")).upper()
    # The source must be identifiable as official. Unknown, empty and explicitly secondary
    # types all fail: a record whose provenance cannot be classified is not verified.
    if source_type not in OFFICIAL_SOURCE_TYPES:
        return PolicyDecision(False, REASON_SECONDARY_ONLY, REASON_NOTES[REASON_SECONDARY_ONLY], age_days=age_days)

    review_due = _as_utc(_field(record, "review_due_at"))
    if review_due is not None and review_due < now:
        return PolicyDecision(False, REASON_REVIEW_OVERDUE, REASON_NOTES[REASON_REVIEW_OVERDUE], age_days=age_days)
    if review_due is None and settings.policy_require_review_date:
        return PolicyDecision(False, REASON_REVIEW_OVERDUE, REASON_NOTES[REASON_REVIEW_OVERDUE], age_days=age_days)

    max_age = int(settings.policy_max_age_days or 0)
    if max_age > 0 and age_days > max_age:
        return PolicyDecision(False, REASON_STALE, REASON_NOTES[REASON_STALE], age_days=age_days)

    if academic_year:
        # The question named a year, so the record must actually say it applies to one of the
        # years asked about. Stored intake years, intake labels and AY ranges are all understood;
        # a record that names no year is YEAR_UNKNOWN, never stretched to fit.
        requested = (academic_year if isinstance(academic_year, academic_year_mod.AcademicYear)
                     else academic_year_mod.parse(str(academic_year)))
        recorded = academic_year_mod.record_years(record)
        if requested is None or not recorded:
            return PolicyDecision(False, REASON_YEAR_UNKNOWN, REASON_NOTES[REASON_YEAR_UNKNOWN], age_days=age_days)
        if not academic_year_mod.same_academic_year(requested, record):
            return PolicyDecision(False, REASON_YEAR_MISMATCH, REASON_NOTES[REASON_YEAR_MISMATCH], age_days=age_days)

    return PolicyDecision(True, REASON_OK, REASON_NOTES[REASON_OK], age_days=age_days)


def is_answerable(record: Any, **kwargs: Any) -> bool:
    return evaluate(record, **kwargs).usable


#: Which DataVerification entity types can invalidate which catalogue record. A programme's
#: entry requirements are a separate row (`program_requirement`) keyed by the programme id, so a
#: conflict there must block the programme itself, not sit silently beside it.
RELATED_CONFLICT_TYPES: Dict[str, tuple[str, ...]] = {
    "program": ("program", "program_requirement", "program_intake"),
    "program_requirement": ("program_requirement",),
    "program_intake": ("program_intake",),
    "university": ("university",),
    "university_ranking": ("university_ranking",),
    "scholarship": ("scholarship",),
    "professor": ("professor",),
    "faculty": ("faculty",),
    "country": ("country",),
}

#: ORM class name -> entity types to check in ``data_verifications``.
ENTITY_TYPES_BY_CLASS: Dict[str, tuple[str, ...]] = {
    "Program": RELATED_CONFLICT_TYPES["program"],
    "ProgramRequirement": RELATED_CONFLICT_TYPES["program_requirement"],
    "ProgramIntake": RELATED_CONFLICT_TYPES["program_intake"],
    "University": RELATED_CONFLICT_TYPES["university"],
    "UniversityRanking": RELATED_CONFLICT_TYPES["university_ranking"],
    "Scholarship": RELATED_CONFLICT_TYPES["scholarship"],
    "Professor": RELATED_CONFLICT_TYPES["professor"],
    "Faculty": RELATED_CONFLICT_TYPES["faculty"],
    "Country": RELATED_CONFLICT_TYPES["country"],
}


def conflict_entity_types(entity_type: str) -> tuple[str, ...]:
    return RELATED_CONFLICT_TYPES.get((entity_type or "").lower(), ((entity_type or "").lower(),))


def conflicts_in(db: Any) -> set[tuple[str, int]]:
    """Every (entity_type, entity_id) with an unresolved verification conflict.

    One query per retrieval or page request, not one per record. An unresolved conflict is a
    ``data_verifications`` row still in ``CONFLICTING``: the official value and a disagreeing
    source both exist and a human has not settled it yet.

    A conflict recorded against a requirement or intake is also reported against its parent
    programme, because the flattened programme record quotes those values. Otherwise a
    requirement conflict could hide behind a programme that still says VERIFIED.
    """
    from sqlalchemy import func, select

    from app.models.crm import DataVerification
    from app.models.education import ProgramIntake, ProgramRequirement

    rows = db.execute(
        select(DataVerification.entity_type, DataVerification.entity_id)
        .where(func.upper(DataVerification.status) == "CONFLICTING")
    ).all()
    out = {((etype or "").lower(), int(eid)) for etype, eid in rows if eid is not None}

    child_ids = [eid for etype, eid in out if etype in ("program_requirement", "program_intake")]
    if child_ids:
        for model in (ProgramRequirement, ProgramIntake):
            parent_ids = db.execute(
                select(model.program_id).where(model.id.in_(child_ids))
            ).scalars()
            out |= {("program", int(pid)) for pid in parent_ids if pid is not None}
    return out


def has_unresolved_conflict(
    db: Any, record: Any, entity_type: Optional[str] = None,
    conflicts: Optional[set[tuple[str, int]]] = None,
) -> bool:
    entity_id = _field(record, "id")
    if entity_id is None or db is None:
        return False
    types = (
        conflict_entity_types(entity_type) if entity_type
        else ENTITY_TYPES_BY_CLASS.get(type(record).__name__, ())
    )
    ids = {int(entity_id)}
    # Requirement and intake conflicts are recorded against their own row by the importer and
    # against the programme by some manual entries. Either convention must withhold the fact.
    if type(record).__name__ in ("ProgramRequirement", "ProgramIntake"):
        parent_id = _field(record, "program_id")
        if parent_id is not None:
            ids.add(int(parent_id))
            types = tuple(dict.fromkeys((*types, "program", "program_requirement", "program_intake")))
    known = conflicts if conflicts is not None else conflicts_in(db)
    return any((t, i) in known for t in types for i in ids)


def evaluate_record(
    db: Any,
    record: Any,
    *,
    entity_type: Optional[str] = None,
    conflicts: Optional[set[tuple[str, int]]] = None,
    **kwargs: Any,
) -> PolicyDecision:
    """The policy applied to a record that is attached to a database session.

    This is the function student-facing code should call: it adds the conflict register to the
    checks that can be made from the row itself. ``evaluate`` stays pure so it is usable with a
    dict and inside tests.
    """
    if "has_conflict" not in kwargs:
        kwargs["has_conflict"] = has_unresolved_conflict(db, record, entity_type, conflicts)
    return evaluate(record, **kwargs)


def usable(records: Iterable[Any], **kwargs: Any) -> List[Any]:
    return [r for r in records if evaluate(r, **kwargs).usable]


def explain(record: Any, **kwargs: Any) -> Dict[str, Any]:
    """Provenance + verdict payload for an API response."""
    decision = evaluate(record, **kwargs)
    payload = {
        "source_url": _field(record, "source_url"),
        "source_type": _field(record, "source_type"),
        "verified_at": _field(record, "verified_at"),
        "verified_by": _field(record, "verified_by"),
        "review_due_at": _field(record, "review_due_at"),
        "confidence": _field(record, "confidence"),
    }
    payload.update(decision.as_dict())
    payload["max_age_days"] = int(settings.policy_max_age_days or 0)
    return payload


def deployment_summary() -> Dict[str, Any]:
    """What the policy currently enforces - served to the frontend and to /assistant/health."""
    return {
        "policy_version": POLICY_VERSION,
        "max_age_days": int(settings.policy_max_age_days or 0),
        "require_review_date": bool(settings.policy_require_review_date),
        "verified_state": VERIFIED_STATE,
        "official_source_types": sorted(OFFICIAL_SOURCE_TYPES),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "stale_after": (datetime.now(timezone.utc) - timedelta(days=int(settings.policy_max_age_days or 0))).isoformat(),
    }
