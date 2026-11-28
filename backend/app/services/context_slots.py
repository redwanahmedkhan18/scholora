"""W5 - server-owned conversation context slots.

An assistant answer is built from three different kinds of information, and the whole point of
this module is to keep them apart:

* **Verified profile facts** (CGPA, target degree, preferred countries). These live in the
  database and are read fresh on every turn by ``_profile_context`` in the router. They are
  *never* copied into a slot, so a conversational query can never overwrite them.
* **Conversational constraints** the student stated in this conversation ("universities in
  Canada", "what about master's programs?"). These are the slots. They belong to the
  conversation, not to the student's profile, and they expire with the session.
* **Externally sourced facts** (programmes, fees, deadlines). These come from retrieval and
  are governed by ``verification_policy``; a slot never becomes a source.

A slot is therefore always ``source="user"``: something the student actually said. It carries
the turn it was learned in, so a later reader can tell an explicit statement from a carried one.

Everything here is deterministic keyword/table matching. Extraction is a convenience, never an
authority: a slot only ever holds a value that appears in the student's own words or comes from
a verified profile row.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select, update

from app.models.base import utcnow

log = logging.getLogger(__name__)

#: Bumped when the persisted shape changes incompatibly. An unknown version is discarded, never
#: guessed at, so an older or newer row cannot be misread.
SCHEMA_VERSION = 1

#: A conversation needs a handful of constraints, not a transcript. Both bounds are enforced on
#: write and re-checked on read, so a corrupted row shrinks instead of propagating.
MAX_SLOTS = 8
MAX_VALUE_CHARS = 80
MAX_SERIALIZED_BYTES = 2048

#: Only user-stated values are ever persisted. ``PROFILE`` is reserved so a future reader can
#: name profile-derived context explicitly; nothing in W5 writes it.
USER = "user"

#: The complete set of conversational slots. An unknown name is dropped, never stored, so a
#: caller cannot smuggle arbitrary keys into the stored object.
SLOT_NAMES = ("country", "degree_level", "subject")

DEGREE_LEVELS = frozenset({"BACHELOR", "MASTER", "PHD"})

#: Mirrors ``assistant._guess_subject`` so a carried slot and the legacy per-question fallback
#: agree on what counts as a named subject.
SUBJECT_TOKENS: Tuple[str, ...] = (
    "artificial intelligence", "data science", "computer science", "business analytics",
    "public health", "nursing", "mechanical", "civil", "electrical", "cyber security",
    "finance", "mba", "economics",
)

#: Slots an intent genuinely cannot answer without. Deliberately short: a missing country or
#: subject still produces a useful, wider answer, and asking for information that is not needed
#: is worse than a broader result. Only an eligibility check (which evaluates a specific level)
#: and a faculty search (which needs a research area) qualify.
REQUIRED_BY_INTENT: Dict[str, Tuple[str, ...]] = {
    "check_eligibility": ("degree_level",),
    "find_faculty": ("subject",),
}

#: The only intents a clarification may leave pending. A bare reply to our own question
#: ("master's") carries no intent of its own, so the question we asked stays in flight long
#: enough to answer it - but only for a recognised intent, never for free text.
PENDING_INTENTS = frozenset(REQUIRED_BY_INTENT)

_QUESTION_TEXT = {
    "degree_level": "Which level are you asking about - bachelor's, master's or PhD?",
    "subject": "Which subject or research area should I look at?",
}

_CHIPS = {
    "degree_level": ("Bachelor's", "Master's", "PhD"),
    "subject": ("Computer science", "Data science", "Public health"),
}

_FORGET = re.compile(
    r"\b(start over|start again|forget (that|it|this|everything)|reset (the )?conversation|"
    r"new conversation|clear (the )?context)\b",
    re.I,
)


# ------------------------------------------------------------------ shape helpers
def empty_context() -> Dict[str, Any]:
    """A fresh, empty, valid context document."""
    return {"v": SCHEMA_VERSION, "slots": {}, "asked": [], "rejected": [],
            "pending": None, "updated_at": None}


def _now_iso() -> str:
    return utcnow().isoformat()


def _valid_value(name: str, value: Any,
                 known_countries: Optional[Iterable[str]] = None) -> Optional[str]:
    """Return the normalized value, or ``None`` when it is not acceptable for this slot.

    ``known_countries`` is the catalogue's own list. When it is supplied, a country that is not
    in the catalogue is rejected, so a crafted or corrupted document cannot smuggle an invented
    destination into retrieval. Without it the value is only shape-checked, which is what the
    pure unit paths use.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > MAX_VALUE_CHARS:
        return None
    if name == "degree_level":
        return value.upper() if value.upper() in DEGREE_LEVELS else None
    if name == "subject":
        low = value.lower()
        return low if low in SUBJECT_TOKENS else None
    if name == "country":
        # Stored as the catalogue name it was resolved to; never a free-text country.
        if known_countries is not None and value not in set(known_countries):
            return None
        return value
    return None


def known_countries(db) -> set:
    """The catalogue's country names, so a stored slot can be checked against reality."""
    from app.models.education import Country

    return {name for (name,) in db.execute(select(Country.name)).all() if name}


def normalize(raw: Any, *, known_countries: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Coerce stored JSON into a valid context document.

    Never raises: a request must not fail because a row is malformed. Anything unrecognised is
    dropped and the result is still a usable empty context, so corruption degrades to "no
    context" rather than to a wrong answer or a 500.
    """
    ctx = empty_context()
    if not isinstance(raw, dict):
        return ctx
    if raw.get("v") != SCHEMA_VERSION:
        # A different schema version is not interpreted at all.
        return ctx

    slots = raw.get("slots")
    if isinstance(slots, dict):
        clean: Dict[str, Dict[str, Any]] = {}
        for name in SLOT_NAMES:
            entry = slots.get(name)
            if not isinstance(entry, dict):
                continue
            value = _valid_value(name, entry.get("value"), known_countries)
            if value is None:
                continue
            if entry.get("source") != USER:
                continue
            at = entry.get("at")
            clean[name] = {"value": value, "source": USER,
                           "at": at if isinstance(at, str) else None}
            if len(clean) >= MAX_SLOTS:
                break
        ctx["slots"] = clean

    asked = raw.get("asked")
    if isinstance(asked, list):
        ctx["asked"] = [name for name in SLOT_NAMES if name in asked]

    rejected = raw.get("rejected")
    if isinstance(rejected, list):
        ctx["rejected"] = [name for name in SLOT_NAMES if name in rejected]

    pending = raw.get("pending")
    if isinstance(pending, str) and pending in PENDING_INTENTS:
        ctx["pending"] = pending

    updated = raw.get("updated_at")
    ctx["updated_at"] = updated if isinstance(updated, str) else None
    return ctx


def _serialized_size(ctx: Dict[str, Any]) -> int:
    import json

    return len(json.dumps(ctx, separators=(",", ":")).encode("utf-8"))


def values(ctx: Dict[str, Any]) -> Dict[str, str]:
    """The plain ``{slot: value}`` view. The only shape the rest of the app reads."""
    return {name: entry["value"] for name, entry in (ctx.get("slots") or {}).items()}


# ------------------------------------------------------------------- extraction
#: A message that is quoted, hypothetical or illustrative is not a statement of preference. It
#: is read as carrying no constraint at all - never as an affirmation and never as a rejection.
_HYPOTHETICAL = re.compile(
    r'"|\u201c|\u201d|\u00ab|\u00bb|\bwhat if\b|\bsuppose\b|\bhypothetical(ly)?\b|'
    r"\bfor example\b|\be\.g\.\b|\bimagine\b|\bsomeone (said|asked|might)\b", re.I)

#: Aliases that are also ordinary English words ("contact us"). They are only read as a
#: destination when the text marks them as one: upper-case ("US") or after a locative
#: preposition ("in the us"). Everything else must survive word-boundary matching alone.
_CONTEXT_DEPENDENT_ALIASES = frozenset({"us"})

_LOCATIVE_BEFORE = re.compile(
    r"(?:\b(?:in|to|from|into|across|within|towards?|visiting|studying|study|universities in|"
    r"university in|schools in|colleges in)\b\s+)(?:the\s+)?$", re.I)


def _alias_terms() -> Tuple[str, ...]:
    """Every alias the destination resolver supports, most specific first.

    Derived from ``destinations.ALIASES`` on purpose: the platform's resolver is the single
    source of truth for what counts as a destination name, so a new alias added there is
    recognised by the extractor without a second edit, and the two lists cannot drift apart.
    """
    from app.services.destinations import ALIASES

    return tuple(sorted(ALIASES, key=lambda term: (-len(term), term)))


def _alias_counts_as_country(term: str, matched_text: str, before: str) -> bool:
    """Whether this occurrence of an ambiguous alias really names a destination."""
    if term not in _CONTEXT_DEPENDENT_ALIASES:
        return True
    if matched_text.isupper():
        return True
    return bool(_LOCATIVE_BEFORE.search(before))

_DEGREE_WORDS: Dict[str, Tuple[str, ...]] = {
    "PHD": ("phd", "doctor", "doctorate", "supervisor", "doctoral"),
    "MASTER": ("master", "masters", "msc", "ms", "mba", "postgrad", "postgraduate"),
    "BACHELOR": ("bachelor", "bachelors", "bsc", "undergrad", "undergraduate"),
}


def _rejection_patterns(value: str) -> Tuple[re.Pattern[str], ...]:
    """The ways a student rejects a specific value, as anchored regular expressions.

    Deliberately narrow and explainable. A cue has to sit next to the value it rejects, so
    "I'm not sure about the cost in Canada" is not a rejection of Canada, while "not Canada",
    "I don't want Canada", "anywhere except Canada" and "Canada is out" all are.
    """
    v = re.escape(value.lower())
    return (
        re.compile(rf"\b(?:not|never)\s+(?:in|to|for|about)?\s*(?:a|an|the)?\s*{v}\b"),
        re.compile(rf"\b(?:don'?t|do not|doesn'?t|didn'?t|no longer|not)\b[^.?!]{{0,24}}?"
                   rf"\b(?:want|like|consider|need|study in|go to|apply to)\b[^.?!]{{0,16}}?"
                   rf"(?:the\s+)?{v}\b"),
        re.compile(rf"\b(?:anywhere|everywhere|anything|any\s+place)\s+(?:except|but)\s+"
                   rf"(?:the\s+)?{v}\b"),
        re.compile(rf"\b(?:except|excluding|other than|rather than|instead of|avoid|avoiding|"
                   rf"outside|away from)\b\s*(?:the\s+)?(?:for\s+)?{v}\b"),
        re.compile(rf"\b{v}\b\s*(?:is|are)?\s*(?:out|not (?:for me|an option|suitable))\b"),
    )


def _is_rejected(low: str, value: str) -> bool:
    return any(pattern.search(low) for pattern in _rejection_patterns(value))


def _mentions(text: str, patterns: Dict[str, Tuple[str, ...]]) -> List[Tuple[str, int, str]]:
    """Every canonical value named in the text, with its position and the words that named it.

    The surface form matters: a student who writes "the US" is rejecting the destination the
    catalogue calls "United States", and the rejection has to be matched against the words they
    actually used.
    """
    found: List[Tuple[str, int, str]] = []
    for canonical, words in patterns.items():
        best: Optional[Tuple[int, str]] = None
        for word in words:
            match = re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text)
            if match and (best is None or match.start() < best[0]):
                best = (match.start(), word)
        if best is not None:
            found.append((canonical, best[0], best[1]))
    return sorted(found, key=lambda item: item[1])


def _country_mentions(db, question: str) -> List[Tuple[str, int, str]]:
    """Destinations named in the text, resolved through the catalogue.

    Word boundaries, not substrings, so "usability" is not the United States. Every resolver
    alias is accepted - "US", "u.s.", "the states", "Korea", "Dubai" - and each candidate is
    resolved by the platform resolver, so the canonical value is the one the rest of the
    application already uses. A candidate the resolver cannot place is dropped rather than
    guessed at.
    """
    from app.models.education import Country
    from app.services import destinations

    if not question:
        return []
    low = question.lower()
    found: Dict[str, Tuple[int, str]] = {}

    for name in db.execute(select(Country.name)).scalars():
        if not name:
            continue
        match = re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", question, re.I)
        if match:
            found.setdefault(name, (match.start(), name))

    for term in _alias_terms():
        pattern = rf"(?<![a-z]){re.escape(term)}(?![a-z])"
        for match in re.finditer(pattern, question, re.I):
            if not _alias_counts_as_country(term, match.group(0), low[:match.start()]):
                continue
            destination = destinations.resolve_destination(db, term)
            if destination is not None and destination.name:
                found.setdefault(destination.name, (match.start(), match.group(0)))
            break
    return sorted(((name, pos, surface) for name, (pos, surface) in found.items()),
                  key=lambda item: item[1])


def interpret(db, question: str) -> Dict[str, Any]:
    """Read one message into affirmed, rejected and ambiguous slots. Deterministic.

    Nothing here decides what the student *wants*: it records what they said. A value that is
    rejected is never affirmed, a slot with more than one candidate is ambiguous rather than
    guessed, and a hypothetical or quoted message teaches nothing at all.
    """
    empty: Dict[str, Any] = {"affirmed": {}, "rejected": [], "ambiguous": []}
    if not question:
        return empty
    if _HYPOTHETICAL.search(question):
        return empty

    low = question.lower()
    country_mentions = _country_mentions(db, question)
    per_slot = {
        "country": country_mentions,
        "degree_level": _mentions(low, _DEGREE_WORDS),
        "subject": _mentions(low, {token: (token,) for token in SUBJECT_TOKENS}),
    }

    affirmed: Dict[str, str] = {}
    rejected: List[str] = []
    ambiguous: List[str] = []
    for name in SLOT_NAMES:
        items = per_slot.get(name) or []
        if not items:
            continue
        kept = [value for value, _pos, surface in items
                if not (_is_rejected(low, value) or _is_rejected(low, surface))]
        if len(kept) != len(items):
            rejected.append(name)
        distinct = sorted(set(kept))
        if len(distinct) == 1:
            affirmed[name] = distinct[0]
        elif len(distinct) > 1:
            # Two destinations, levels or subjects in one sentence: do not guess between them.
            ambiguous.append(name)
    return {"affirmed": affirmed, "rejected": rejected, "ambiguous": ambiguous}


def extract(db, question: str) -> Dict[str, str]:
    """Only the affirmed values. Kept for callers that do not care about rejections."""
    return interpret(db, question)["affirmed"]


def forget_requested(question: str) -> bool:
    return bool(_FORGET.search(question or ""))


# ----------------------------------------------------------------------- merge
def merge(ctx: Dict[str, Any], extracted: Dict[str, str], *, now: Optional[str] = None,
          forget: bool = False,
          rejected: Iterable[str] = (),
          ambiguous: Iterable[str] = (),
          known_countries: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Apply this turn's explicit statements to a context and return the new document.

    Rules, in order:
    * ``forget`` clears every slot (and the asked flags, so a new conversation can clarify).
    * A rejected slot is removed, so retrieval stops being narrowed by a value the student
      explicitly ruled out. It is never replaced by a guess.
    * An ambiguous slot is removed for the same reason: silently keeping one of two candidates
      would be a choice the student did not make.
    * A slot stated in this turn replaces any carried value - a newer explicit statement wins.
    * A carried slot survives a turn that says nothing about it.
    * Nothing else can ever write a slot.
    """
    at = now or _now_iso()
    base = normalize(ctx, known_countries=known_countries)
    if forget:
        fresh = empty_context()
        fresh["updated_at"] = at
        return fresh

    out = empty_context()
    out["slots"] = dict(base["slots"])
    out["asked"] = list(base["asked"])
    out["pending"] = base.get("pending")
    # A cleared slot also stops counting as asked: if it is still required, the assistant may
    # ask for it once more rather than silently carrying on without it.
    excluded = list(base["rejected"])
    for name in list(rejected) + list(ambiguous):
        out["slots"].pop(name, None)
        out["asked"] = [asked for asked in out["asked"] if asked != name]
        # Remember the exclusion: without it, a standing profile preference for the same slot
        # would quietly put the rejected value straight back into retrieval.
        if name not in excluded:
            excluded.append(name)
    for name, value in extracted.items():
        if name not in SLOT_NAMES:
            continue
        clean = _valid_value(name, value, known_countries)
        if clean is None:
            continue
        out["slots"][name] = {"value": clean, "source": USER, "at": at}
        # A slot that now has a value has nothing left to ask for, and is no longer excluded.
        out["asked"] = [asked for asked in out["asked"] if asked != name]
        excluded = [item for item in excluded if item != name]
    out["rejected"] = [name for name in SLOT_NAMES if name in excluded]
    out["updated_at"] = at
    return out


def mark_asked(ctx: Dict[str, Any], slot: Optional[str]) -> Dict[str, Any]:
    """Remember that we asked for a slot, so the same question is never repeated."""
    out = normalize(ctx)
    if slot and slot in SLOT_NAMES and slot not in out["asked"]:
        out["asked"] = out["asked"] + [slot]
    return out


def missing_slot(intent: str, ctx: Dict[str, Any], effective: Optional[Dict[str, str]] = None
                 ) -> Optional[str]:
    """The one slot this intent cannot answer without, if it is absent and unasked.

    Returns ``None`` when nothing is required, when the value is already known (from a slot, a
    verified profile field or this turn's question) or when we have already asked for it. That
    last rule is what makes a clarification loop impossible.
    """
    required = REQUIRED_BY_INTENT.get(intent) or ()
    if not required:
        return None
    state = normalize(ctx)
    known = dict(values(state))
    if effective:
        known.update({k: v for k, v in effective.items() if v})
    for name in required:
        if known.get(name):
            continue
        # A research area the profile already declares answers a faculty search just as well as
        # a named subject, so that must not produce a pointless question.
        if name == "subject" and (effective or {}).get("research_interests"):
            continue
        if name in state["asked"]:
            continue
        return name
    return None


# ------------------------------------------------------------- retrieval view
def effective(db, ctx: Dict[str, Any], *, profile_country: str = "",
              profile_degree: str = "") -> Dict[str, str]:
    """What the answer engine should retrieve with, and where each value came from.

    Precedence is: a constraint the student stated in this conversation (or in an earlier turn
    of it) beats a standing profile preference, which beats nothing. The profile preference is
    only *read* here; it is never altered, so asking about another country does not become a new
    preference.
    """
    state = normalize(ctx)
    carried = values(state)
    excluded = set(state["rejected"])
    resolved: Dict[str, str] = {}
    for name in SLOT_NAMES:
        if carried.get(name):
            resolved[name] = carried[name]
    # A standing profile preference is a default, and a default the student has explicitly
    # ruled out this conversation is not a default at all.
    if not resolved.get("country") and profile_country and "country" not in excluded:
        resolved["country"] = profile_country
    if not resolved.get("degree_level") and profile_degree and "degree_level" not in excluded:
        resolved["degree_level"] = profile_degree
    return resolved


def summary(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """The client-facing view: the constraints only, never provenance or internal state."""
    state = normalize(ctx)
    return {"version": SCHEMA_VERSION, "constraints": values(state),
            "excluded": list(state["rejected"])}


# --------------------------------------------------------- persistence (CAS)
def _rowcount(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


def cas_write(db, row, ctx: Dict[str, Any], *, expected_revision: int,
              generation: int, profile_id: Optional[int]) -> bool:
    """Persist a context only if the row is still the one this request read.

    The ``WHERE`` clause is the concurrency control: id, the expected revision, the credential
    generation and the current owner must all still match, so a superseded request, a profile
    switch or a revoked session cannot write. One statement, no read-then-write window.
    """
    from app.models.crm import AssistantSession

    payload = normalize(ctx)
    if _serialized_size(payload) > MAX_SERIALIZED_BYTES:
        # Never persist an oversized object; drop to empty rather than truncate into a lie.
        log.warning("assistant context exceeded its size bound and was not persisted")
        payload = empty_context()

    stmt = update(AssistantSession).where(
        AssistantSession.id == row.id,
        AssistantSession.context_revision == int(expected_revision),
        AssistantSession.credential_generation == int(generation),
        AssistantSession.revoked_at.is_(None),
    )
    if profile_id is None:
        stmt = stmt.where(AssistantSession.ownership == "ANONYMOUS")
    else:
        stmt = stmt.where(AssistantSession.student_profile_id == int(profile_id))
    # ``synchronize_session=False`` is deliberate and load-bearing. With ORM synchronisation on,
    # SQLAlchemy writes the new values into the identity map, so a later plain ``commit()``
    # flushes them again as an *unguarded* UPDATE that no longer carries the revision predicate -
    # which defeats the compare-and-swap and double-counts the revision. The guarded statement is
    # the only writer; the in-memory row is refreshed explicitly afterwards.
    result = db.execute(stmt.execution_options(synchronize_session=False).values(
        context_json=payload, context_revision=int(expected_revision) + 1))
    return _rowcount(result) == 1


def persist_turn(db, row, *, question: str, generation: int,
                 profile_id: Optional[int], asked_slot: Optional[str] = None,
                 pending_intent: Optional[str] = None, keep_pending: bool = False,
                 reading: Optional[Dict[str, Any]] = None,
                 known_countries: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Merge this turn into the stored context with a bounded compare-and-swap retry.

    The merge is re-applied to the *fresh* row on a conflict, so a concurrent turn's slot is
    kept instead of being overwritten by a stale copy of the document. No slot is ever silently
    lost, and a session that cannot win the swap simply keeps the other writer's context.
    """
    from app.models.crm import AssistantSession

    forget = forget_requested(question)
    reading = reading or interpret(db, question)
    extracted = reading["affirmed"]
    for attempt in range(2):
        current = normalize(getattr(row, "context_json", None),
                            known_countries=known_countries)
        merged = merge(current, extracted, forget=forget,
                       rejected=reading["rejected"], ambiguous=reading["ambiguous"],
                       known_countries=known_countries)
        # Record that we asked for a slot only when the turn did not also supply it, so the
        # same question is never asked twice.
        if asked_slot and asked_slot not in merged["slots"]:
            merged = mark_asked(merged, asked_slot)
        # The question stays in flight only while we are waiting for its answer. ``keep_pending``
        # lets an unrelated aside (the assistant's own clarification, a refusal) leave it alone.
        if keep_pending and not forget:
            # ``and not forget``: an explicit reset clears the pending question too, whatever the
            # assistant decided about that turn.
            merged["pending"] = current.get("pending")
        else:
            merged["pending"] = (pending_intent
                                 if pending_intent in PENDING_INTENTS else None)
        if cas_write(db, row, merged, expected_revision=int(row.context_revision or 0),
                     generation=generation, profile_id=profile_id):
            # Do not assign the new values onto the ORM object: that would mark it dirty and the
            # next flush would write them again without the revision guard. Expiring the two
            # attributes leaves the database as the only authority and makes the next read
            # reflect whatever is actually stored.
            db.expire(row, ["context_json", "context_revision"])
            return merged
        # Lost the swap: another writer committed first. Re-read and merge onto its result.
        reloaded = db.execute(
            select(AssistantSession).where(AssistantSession.id == row.id)).scalars().first()
        if reloaded is None:
            break
        db.refresh(reloaded)
        if int(reloaded.context_revision or 0) == int(row.context_revision or 0) \
                and attempt == 1:
            break
        row = reloaded
    log.info("assistant context not persisted after a concurrent update")
    return normalize(getattr(row, "context_json", None))


def clear(db, row, *, commit: bool = False) -> None:
    """Drop the conversation context, e.g. when the identity behind the session changes."""
    from app.models.crm import AssistantSession

    db.execute(update(AssistantSession)
               .where(AssistantSession.id == row.id)
               .values(context_json=None,
                       context_revision=int(getattr(row, "context_revision", 0) or 0) + 1))
    if commit:
        db.commit()


def load(row, *, known_countries: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    return normalize(getattr(row, "context_json", None), known_countries=known_countries)


# ------------------------------------------------------------- clarification
def clarify_payload(slot: str) -> Tuple[str, List[str]]:
    question = _QUESTION_TEXT.get(slot) or "Could you tell me a little more?"
    return question, list(_CHIPS.get(slot) or ("Computer science", "Public health", "Finance"))


def clarify_response(slot: str, *, intent: str, policy_block: Dict[str, Any]) -> Dict[str, Any]:
    """A clarification shaped exactly like an answer, so the client needs no new branch."""
    text, chips = clarify_payload(slot)
    return {
        "answer": text,
        "intent": "clarify",
        "clarify_slot": slot,
        "domain": {"decision": "ALLOW", "reason": "missing_context", "score": 1.0,
                   "matched": [], "clarification": text},
        "policy": policy_block,
        "academic_year": None,
        "claim_validation": {"status": "NOT_APPLICABLE", "claims_checked": 0,
                             "claims_rejected": 0, "reasons": []},
        "data": {},
        "sources": [],
        "grounded": False,
        "escalated": False,
        "tools_used": [],
        "retrieved": [],
        "mode": "clarify",
        "provider": None,
        "model": None,
        "latency_ms": 0,
        "suggestions": chips,
        "disclaimer": "",
    }
