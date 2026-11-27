"""W6 - one interpretation of a retrieval scope.

Every retrieval surface in the product - the assistant's four tools, the public matching
endpoints and the RAG corpus - used to decide its own filters. They disagreed: a Scotland
request narrowed universities to Scotland but widened faculty to the whole United Kingdom,
scholarships applied their degree filter only after the fetch limit, the RAG path applied no
filter at all, and a budget was compared against a tuition figure in another currency.

This module is the single place that turns trustworthy inputs into a scope, and the single
place that turns a scope into model-specific predicates. It deliberately does *not* try to make
one SQL expression fit five different tables; each adapter knows its own model.

The governing rule, enforced by :meth:`Scope.ok`: a mandatory constraint that cannot be resolved
must never become "no filter". Callers check ``ok`` and return a clarification or an empty,
restricted result - they never fall through to an unrestricted query.

Provenance is kept: a destination may come from the student's own words, from an active W5
constraint, or from their verified profile. Only the first two are conversational; the profile
default is used where the conversation says nothing, and a slot the student excluded never comes
back from it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import and_, false, or_, select
from sqlalchemy.orm import Session

from app.core import academic_year as academic_year_mod
from app.models.crm import KnowledgeChunk
from app.models.education import (
    Country,
    Professor,
    Program,
    ProgramIntake,
    Scholarship,
    University,
)
from app.services import destinations, fx

# ------------------------------------------------------------------ statuses
#: The constraints all resolved; the scope may be executed.
RESOLVED = "RESOLVED"
#: Something genuinely required is missing and only the student can supply it.
NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
#: Something the student asked for cannot be represented against our data.
UNSUPPORTED = "UNSUPPORTED"
#: Two constraints cannot both hold.
CONTRADICTORY = "CONTRADICTORY"

#: Machine-readable, constant reasons. Never contains the student's own words.
DESTINATION_UNKNOWN = "destination_unknown"
DEGREE_UNSUPPORTED = "degree_unsupported"
BUDGET_UNREADABLE = "budget_unreadable"
BUDGET_CURRENCY_UNKNOWN = "budget_currency_unknown"

DEGREE_LEVELS = frozenset({"BACHELOR", "MASTER", "PHD"})

#: How many candidates a query may load before budget/eligibility filtering. The filters that
#: matter (destination, degree, subject, year) are SQL predicates and run in the database; this
#: cap only bounds the rows we then subject to a per-row check, and it is applied *before*
#: ranking and truncation so a qualifying record can never be cut off by a page size.
CANDIDATE_CAP = 500


@dataclass(frozen=True)
class Scope:
    """A resolved, immutable description of what a retrieval may look at."""

    status: str = RESOLVED
    reasons: Tuple[str, ...] = ()
    clarify_slot: Optional[str] = None

    #: The requested destination, when one was resolved.
    destination: Optional[Country] = None
    #: Country ids that make up the destination family (the destination plus its regions).
    country_ids: Tuple[int, ...] = ()
    #: Destination rows that are *regions*: their institutions are found by region, not country.
    region_ids: Tuple[int, ...] = ()
    #: Country ids the student excluded, so a broader default cannot re-introduce them.
    excluded_ids: Tuple[int, ...] = ()
    #: Region ids the student excluded. A region's institutions carry their sovereign as
    #: ``country_id``, so excluding Scotland by country id alone would leave Glasgow visible.
    excluded_region_ids: Tuple[int, ...] = ()

    degree_level: Optional[str] = None
    subject: Optional[str] = None
    academic_year: Optional[Any] = None
    intake: Optional[str] = None

    #: An explicitly stated annual budget, in the currency the student used.
    budget_amount: Optional[float] = None
    budget_currency: Optional[str] = None
    #: True when a stated budget cannot be converted to a programme's currency.
    budget_unconvertible: bool = False

    @property
    def ok(self) -> bool:
        """Whether this scope may be executed at all."""
        return self.status == RESOLVED

    @property
    def has_destination(self) -> bool:
        return self.destination is not None

    def call_kwargs(self) -> Dict[str, Any]:
        """The plain values this scope resolved to.

        Passed alongside the scope so that whoever calls a tool - including existing callers and
        tests that inspect the arguments - can see the values that were actually applied, while
        the scope itself remains the single authority inside the tool.
        """
        return {
            "country": self.destination.name if self.destination is not None else "",
            "degree_level": self.degree_level or "",
            "subject": self.subject or "",
        }

    @property
    def needs_clarification(self) -> bool:
        return self.status in (NEEDS_CLARIFICATION, UNSUPPORTED, CONTRADICTORY)


# ------------------------------------------------------------------ parsing
_LAKH = 100_000.0
_CRORE = 10_000_000.0

_CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR", "৳": "BDT"}
_CURRENCY_WORDS = {
    "usd": "USD", "dollar": "USD", "dollars": "USD", "gbp": "GBP", "pound": "GBP",
    "pounds": "GBP", "eur": "EUR", "euro": "EUR", "euros": "EUR", "cad": "CAD",
    "aud": "AUD", "nzd": "NZD", "bdt": "BDT", "taka": "BDT", "tk": "BDT",
    "inr": "INR", "aed": "AED", "sgd": "SGD",
}

_BUDGET_CUE = re.compile(
    r"\b(under|below|within|less than|no more than|max|maximum|up to|budget of|budget is|"
    r"can afford|afford)\b", re.I)
_AMOUNT = re.compile(
    r"(?P<sym>[$£€৳])?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<scale>lakhs?|lacs?|crores?|crs?|thousands?|k)?\s*"
    r"(?P<word>usd|gbp|eur|cad|aud|nzd|bdt|inr|aed|sgd|dollars?|pounds?|euros?|taka|tk)?",
    re.I)


def parse_budget(text: str, *, default_currency: str = "USD"
                 ) -> Tuple[Optional[float], Optional[str]]:
    """The annual budget the student explicitly stated, if they stated one.

    Only an amount announced by a budget phrase counts, and only when it is plausibly money
    (a thousand or more), so "IELTS 6.5" and "CGPA 3.5" cannot become budgets.
    """
    if not text:
        return None, None
    cue = _BUDGET_CUE.search(text)
    if not cue:
        return None, None
    tail = text[cue.end():]
    match = _AMOUNT.search(tail)
    if not match:
        return None, None
    # The amount has to be the one the cue introduces, not a figure from a later sentence.
    if len(tail) - len(tail[match.start():]) > 16:
        return None, None
    try:
        amount = float(match.group("num").replace(",", ""))
    except (TypeError, ValueError):
        return None, None
    scale = (match.group("scale") or "").lower()
    if scale.startswith(("lakh", "lac")):
        amount *= _LAKH
    elif scale.startswith(("crore", "cr")):
        amount *= _CRORE
    elif scale.startswith(("thousand", "k")):
        amount *= 1000.0
    if amount < 1000:
        return None, None
    symbol = match.group("sym")
    word = (match.group("word") or "").lower()
    currency = _CURRENCY_SYMBOLS.get(symbol or "", None) or _CURRENCY_WORDS.get(word, None)
    return amount, (currency or default_currency)


_SUBJECT_STOPWORDS = frozenset({"and", "of", "in", "the", "for", "with", "programme", "program"})


def subject_matches(subject: Optional[str], *haystacks: Optional[str]) -> bool:
    """Whether a record genuinely covers the subject the student named.

    A plain substring test is how "art" matches "Earth Science" and how a nursing programme is
    presented as a public-health match. The phrase must appear as whole words; failing that,
    every significant word of it must be present, so "computer science" still matches
    "Computer Sciences" but "art" no longer matches "Earth Science".
    """
    subject = (subject or "").strip().lower()
    if not subject:
        return True
    hay = " ".join(part for part in haystacks if part).lower()
    if not hay:
        return False
    if re.search(rf"(?<![a-z0-9]){re.escape(subject)}(?![a-z0-9])", hay):
        return True
    tokens = [t for t in re.split(r"[^a-z0-9]+", subject)
              if len(t) >= 2 and t not in _SUBJECT_STOPWORDS]
    if not tokens:
        return False
    words = set(re.split(r"[^a-z0-9]+", hay))
    # A plain plural still names the same subject ("art" / "Master of Arts").
    return all(token in words or f"{token}s" in words for token in tokens)


_DEGREE_WORDS = {
    "bachelor": "BACHELOR", "bachelors": "BACHELOR", "bachelor's": "BACHELOR",
    "undergrad": "BACHELOR", "undergraduate": "BACHELOR",
    "master": "MASTER", "masters": "MASTER", "master's": "MASTER", "postgrad": "MASTER",
    "postgraduate": "MASTER",
    "phd": "PHD", "doctorate": "PHD", "doctoral": "PHD",
}


def normalize_degree(value: Optional[str]) -> Optional[str]:
    """A degree level the catalogue actually uses, or ``None``.

    Only the three levels the catalogue stores are accepted. Anything else returns ``None`` so
    the caller can ask or restrict, instead of silently filtering on a value no record can have.
    """
    if not value:
        return None
    text = str(value).strip().upper().rstrip(".")
    if text in DEGREE_LEVELS:
        return text
    return _DEGREE_WORDS.get(str(value).strip().lower().rstrip("."))


# ------------------------------------------------------------------ resolution
def _excluded_ids(db: Session, tokens: Iterable[str]
                  ) -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    """Excluded destination rows, split into (country ids, region ids).

    An excluded region is added to both lists: its institutions carry the sovereign as
    ``country_id`` (excluded by region) but an institution tagged with the region itself as its
    country would only be caught by the country list.
    """
    rows = [row for row in (destinations.resolve_destination(db, token)
                            for token in tokens or ()) if row is not None]
    country_ids, region_ids = _accumulate(db, rows)
    for row in rows:
        if (row.kind or "COUNTRY").upper() == "REGION" and row.id not in country_ids:
            country_ids = country_ids + (row.id,)
    return country_ids, region_ids


def resolve_scope(db: Session, *, context: Optional[Dict[str, Any]] = None,
                  profile: Optional[Dict[str, Any]] = None,
                  question: str = "", intent: Optional[str] = None) -> Scope:
    """Turn trustworthy inputs into a scope. Never guesses, never widens.

    ``context`` is the W5 effective context (already stripped of client input and already
    resolved against the verified profile). ``profile`` is the verified profile facts. The
    question is read only for an explicitly stated budget and academic year - the destination,
    degree and subject come from the resolved context, never from re-parsing the sentence.
    """
    context = context or {}
    profile = profile or {}

    reasons: List[str] = []
    status = RESOLVED

    destination_token = (context.get("country") or "").strip()
    destination = destinations.resolve_destination(db, destination_token) if destination_token else None
    if destination_token and destination is None:
        # An unresolved destination is a mandatory constraint we cannot honour. It must not
        # become "search everywhere".
        return Scope(status=UNSUPPORTED, reasons=(DESTINATION_UNKNOWN,),
                     clarify_slot="country")

    # A region query is served by region, exactly as ``destinations.university_scope`` does: the
    # region's institutions carry their sovereign as ``country_id``, so filtering on country
    # there would return the whole United Kingdom instead of Scotland.
    country_ids, region_ids = _accumulate(db, [destination] if destination is not None else [])

    excluded, excluded_regions = _excluded_ids(db, context.get("excluded_countries") or ())

    degree_raw = context.get("degree_level") or profile.get("target_degree_level")
    degree = normalize_degree(degree_raw)
    if degree_raw and degree is None:
        return Scope(status=UNSUPPORTED, reasons=(DEGREE_UNSUPPORTED,),
                     clarify_slot="degree_level")

    subject = (context.get("subject") or "").strip().lower() or None

    year = academic_year_mod.parse(question) if question else None
    intake = (profile.get("target_intake") or "").strip() or None

    budget, budget_currency = parse_budget(
        question or "", default_currency=(profile.get("budget_currency") or "USD"))
    if budget is not None and not fx.supported(budget_currency):
        reasons.append(BUDGET_CURRENCY_UNKNOWN)
        status = UNSUPPORTED

    return Scope(status=status, reasons=tuple(reasons),
                 clarify_slot=None if status == RESOLVED else (
                     "country" if DESTINATION_UNKNOWN in reasons else None),
                 destination=destination, country_ids=country_ids, region_ids=region_ids,
                 excluded_ids=excluded, excluded_region_ids=excluded_regions,
                 degree_level=degree, subject=subject,
                 academic_year=year, intake=intake,
                 budget_amount=budget, budget_currency=budget_currency)


def _accumulate(db: Session, rows: Iterable[Country]) -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    """Split destination rows into (country family ids, region ids)."""
    country_ids: List[int] = []
    region_ids: List[int] = []
    for row in rows:
        if (row.kind or "COUNTRY").upper() == "REGION":
            if row.id not in region_ids:
                region_ids.append(row.id)
        else:
            for cid in destinations.family_ids(db, row):
                if cid not in country_ids:
                    country_ids.append(cid)
    return tuple(country_ids), tuple(region_ids)


def scope_from_destinations(db: Session, rows: Iterable[Country], *,
                            degree_level: Optional[str] = None,
                            subject: Optional[str] = None) -> Scope:
    """A scope for the one-or-more destinations a public filter accepted.

    The filter endpoints take a list of slugs, so this is the multi-destination form of the same
    contract - same primitives, same region rule, same fail-closed rule when a slug is unknown.
    The caller has already resolved every slug; an unknown one is a failure, not an empty list.
    """
    rows = [r for r in rows if r is not None]
    if not rows:
        # No destination could be resolved from what the caller gave us. That is an unresolved
        # mandatory scope, never permission to search everywhere.
        return Scope(status=UNSUPPORTED, reasons=(DESTINATION_UNKNOWN,), clarify_slot="country",
                     degree_level=normalize_degree(degree_level),
                     subject=(subject or "").strip().lower() or None)
    country_ids, region_ids = _accumulate(db, rows)
    return Scope(destination=rows[0] if len(rows) == 1 else None,
                 country_ids=country_ids, region_ids=region_ids,
                 degree_level=normalize_degree(degree_level),
                 subject=(subject or "").strip().lower() or None)


# ------------------------------------------------------------------ adapters
def university_clause(db: Session, scope: Optional[Scope]):
    """SQL predicate over ``University`` for this destination scope, or ``None``.

    Built from the same primitives as :func:`destinations.university_scope` - ``family_ids`` for a
    country, the region id for a region - so the two cannot disagree; unlike that helper it also
    accepts more than one destination, which the public filters allow, and it honours the
    student's exclusions.

    ``None`` means "no destination was requested" - never "the destination could not be
    resolved": callers must check :attr:`Scope.ok` before building a query at all.
    """
    if scope is None:
        return None
    parts = []
    if scope.country_ids:
        parts.append(University.country_id.in_(list(scope.country_ids)))
    if scope.region_ids:
        parts.append(University.region_id.in_(list(scope.region_ids)))
    if not parts:
        return None
    clause = or_(*parts) if len(parts) > 1 else parts[0]
    if scope.excluded_ids:
        clause = and_(clause, University.country_id.notin_(list(scope.excluded_ids)))
    if scope.excluded_region_ids:
        # NULL-safe on purpose: ``region_id NOT IN (...)`` is NULL - and therefore false - for an
        # institution whose region is unset, which would silently hide every such institution
        # instead of only the excluded region's.
        clause = and_(clause, or_(University.region_id.is_(None),
                                  University.region_id.notin_(list(scope.excluded_region_ids))))
    return clause


def university_ids(db: Session, scope: Optional[Scope]) -> Tuple[int, ...]:
    """Every institution inside this scope, as ids. Empty means "nothing qualifies"."""
    clause = university_clause(db, scope)
    if clause is None:
        return ()
    return tuple(db.execute(select(University.id).where(clause)).scalars())


def destination_country_ids(scope: Scope) -> Tuple[int, ...]:
    """Destination rows (countries and regions) whose own awards belong to this scope."""
    return tuple(scope.country_ids) + tuple(scope.region_ids)


def university_in_scope(db: Session, scope: Scope, university: Optional[University]) -> bool:
    """Python equivalent of :func:`university_clause`, for already-loaded rows."""
    if university is None:
        return not scope.has_destination
    if not scope.has_destination:
        return True
    if university.country_id in scope.excluded_ids:
        return False
    if university.region_id in scope.excluded_region_ids:
        return False
    return (university.country_id in scope.country_ids
            or university.region_id in scope.region_ids)


def program_year_ids(db: Session, scope: Scope) -> Optional[Tuple[int, ...]]:
    """Programme ids with an intake in the requested academic year, or ``None``.

    ``None`` means no year was requested. An empty tuple means a year was requested and no
    programme states one - which callers must treat as "nothing qualifies", never as "no filter".
    """
    if scope.academic_year is None:
        return None
    rows = db.execute(select(ProgramIntake.program_id, ProgramIntake.label,
                             ProgramIntake.year, ProgramIntake.term)).all()
    ids = {
        program_id for program_id, label, year, term in rows
        if academic_year_mod.same_academic_year(
            scope.academic_year, _intake_stub(label, year, term))
    }
    return tuple(sorted(ids))


def _intake_stub(label: Optional[str], year: Optional[int], term: Optional[str]) -> Any:
    """The shape the academic-year comparer understands, without loading ORM rows."""
    return {"label": label, "year": year, "term": term}


def tuition_within_budget(scope: Scope, amount: Optional[float],
                          currency: Optional[str]) -> bool:
    """Whether an annual figure fits the stated budget, in the same unit.

    A figure that cannot be converted is *excluded*, not compared: subtracting two numbers in
    different currencies is how a "within budget" claim becomes a lie.
    """
    if scope.budget_amount is None:
        return True
    if amount is None:
        return False
    stated = (scope.budget_currency or "USD").upper()
    programme = (currency or "USD").upper()
    converted = fx.convert(float(scope.budget_amount), stated, programme)
    if converted is None:
        return False
    return float(amount) <= converted


def chunk_scope_ids(db: Session, scope: Optional[Scope]) -> Optional[Dict[str, set]]:
    """Entity ids that RAG may quote for this scope, per chunk entity type.

    ``None`` means no destination was requested, so every published chunk is eligible. Every
    value is a set of ids inside the scope; an entity type that is absent from the mapping (or
    maps to an empty set) is out of scope, never "global".
    """
    if scope is None or not scope.has_destination:
        return None
    inside = set(university_ids(db, scope))
    destinations_in_scope = {
        cid for cid in destination_country_ids(scope)
        if cid not in scope.excluded_ids and cid not in scope.excluded_region_ids}

    program_ids: set = set()
    professor_ids: set = set()
    if inside:
        ids = sorted(inside)
        program_ids = {pid for (pid,) in db.execute(
            select(Program.id).where(Program.university_id.in_(ids))).all()}
        professor_ids = {pid for (pid,) in db.execute(
            select(Professor.id).where(Professor.university_id.in_(ids))).all()}
    # A year constraint narrows the corpus exactly as it narrows the structured results, so a
    # passage about a programme that cannot run in the requested year cannot be quoted either.
    year_ids = program_year_ids(db, scope)
    if year_ids is not None:
        program_ids &= set(year_ids)

    scholarship_ids: set = set()
    for sid, university_id, country_id in db.execute(
            select(Scholarship.id, Scholarship.university_id, Scholarship.country_id)).all():
        if university_id is not None and university_id in inside:
            scholarship_ids.add(sid)
        elif university_id is None and country_id in destinations_in_scope:
            scholarship_ids.add(sid)

    return {
        "university": inside,
        "program": program_ids,
        "scholarship": scholarship_ids,
        "professor": professor_ids,
        # A country passage belongs to exactly one destination row.
        "country": destinations_in_scope,
    }


def chunk_clause(db: Session, scope: Optional[Scope]):
    """SQL predicate over ``KnowledgeChunk`` restricting the corpus to this destination.

    A passage is eligible only when it is attached to an entity inside the scope. A chunk whose
    entity type is unknown to this map is excluded rather than assumed global: missing metadata
    is not evidence of general applicability.
    """
    allowed = chunk_scope_ids(db, scope) if scope is not None else None
    if allowed is None:
        return None
    parts = [
        and_(KnowledgeChunk.entity_type == entity_type,
             KnowledgeChunk.entity_id.in_(sorted(ids)))
        for entity_type, ids in allowed.items() if ids
    ]
    if not parts:
        return false()
    return or_(*parts)


def reason_text(reason: str) -> str:
    """A short, safe sentence for a resolution reason. No student words, no internals."""
    return {
        DESTINATION_UNKNOWN: "I could not tell which destination you mean.",
        DEGREE_UNSUPPORTED: "I could not tell which study level you mean.",
        BUDGET_UNREADABLE: "I could not read the budget you mentioned.",
        BUDGET_CURRENCY_UNKNOWN: "I do not have a conversion rate for that currency.",
    }.get(reason, "I could not narrow that down safely.")
