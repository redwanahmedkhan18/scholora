"""Backend-enforced domain gate: scholara answers about studying abroad, and nothing else.

The gate is layer 0 of ``assistant_engine.ask()``. It runs before retrieval, before the
structured tools and before any answer-generating model call, so an unrelated question cannot
reach the corpus, cannot cost a provider request, and cannot be answered "helpfully" from the
model's own knowledge.

Three outcomes
--------------
``ALLOW``    clearly inside higher-studies counselling.
``CLARIFY``  genuinely ambiguous: ask one short question instead of guessing or refusing.
``REFUSE``   clearly unrelated: return the single fixed refusal, with no sources and no data.

Deterministic first
-------------------
Scoring is a weighted lexicon, so the same question always gets the same decision, the decision
can be explained to the student, and there is no second place where a prompt injection could
take effect. An optional semantic tie-breaker exists for the *ambiguous band only*, is off by
default, and can never override a refusal: a denied request is denied before any model is
consulted. That ordering is the whole point - a classifier that can be argued out of a refusal
is not a gate.

Follow-ups
----------
``"and the fees?"`` alone is ambiguous, but not after a question about a programme. A short
anaphoric message inherits the previous turn's allowance. It never inherits the permission to
break a deny rule, and it never applies to a long or self-contained message, so an unrelated
request cannot be smuggled in behind a legitimate turn.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings

log = logging.getLogger(__name__)

#: The one and only refusal. Reused verbatim by the API, the policy endpoint and the tests, so
#: there is no second wording to drift - and no general-purpose answer to fall back to.
DOMAIN_REFUSAL = (
    "I can only help with higher-studies counselling: universities, Bachelor's, Master's and PhD "
    "programmes, admission requirements, tuition and living costs, scholarships, English tests and "
    "study destinations. I can't help with that question — please ask me about studying abroad."
)

ALLOW = "ALLOW"
CLARIFY = "CLARIFY"
REFUSE = "REFUSE"

#: Weighted vocabulary. A *strong* term is unambiguously about studying abroad; a *weak* one only
#: means something once another term (or a follow-up) establishes the context.
STRONG: Dict[str, Tuple[str, ...]] = {
    "level": (
        "bachelor", "bachelors", "bachelor's", "undergraduate", "undergrad", "bsc", "ba ",
        "master", "masters", "master's", "msc", "mba", "postgraduate", "postgrad", "mphil",
        "phd", "ph.d", "doctorate", "doctoral", "dba",
    ),
    "admission": (
        "admission", "admissions", "admitted", "admit", "entry requirement", "entry requirements",
        "eligibility", "eligible", "cgpa", "gpa", "grade point", "degree equivalency",
        "prerequisite", "prerequisites", "backlog", "backlogs", "study gap", "acceptance letter",
        "getting into", "get into", "get admitted",
    ),
    "tests": (
        "ielts", "toefl", "pte", "duolingo", "moi", "medium of instruction", "gre", "gmat",
        "english test", "english proficiency", "english language requirement",
    ),
    "money": (
        "tuition", "tuition fee", "tuition fees", "scholarship", "scholarships", "financial aid",
        "waiver", "assistantship", "stipend", "living cost", "living costs", "cost of living",
        "fee structure",
    ),
    "process": (
        "application deadline", "deadline", "deadlines", "intake", "intakes", "statement of purpose",
        "recommendation letter", "offer letter", "application fee", "student visa", "study visa",
        "cas ", "i20", "i-20", "acceptance deadline", "deposit",
    ),
    "research": (
        "supervisor", "supervisors", "professor", "professors", "phd position", "phd supervisor",
        "research proposal", "lab ", "publication", "publications", "research group",
    ),
    "counselling": (
        "counselor", "counsellor", "appointment", "consultation", "shortlist", "profile assessment",
        "study abroad", "study overseas", "abroad", "overseas",
    ),
    "destination": ("university", "universities", "college", "colleges", "campus"),
}

WEAK: Dict[str, Tuple[str, ...]] = {
    "level": ("degree", "diploma", "course", "courses", "program", "programme", "programmes", "major"),
    "admission": ("chance", "chances", "requirement", "requirements", "transcript", "documents"),
    "money": ("fee", "fees", "cost", "costs", "afford", "budget", "expensive", "cheap", "funding"),
    "process": ("visa", "apply", "application", "interview", "documents", "sop"),
    "research": ("research", "faculty", "supervision", "thesis"),
    "destination": ("cities", "city"),
    "counselling": ("counselling", "counseling", "guidance", "advice", "study", "studying"),
}

#: The approved destinations, as a student would type them. Kept here (and not imported from the
#: catalogue) so the gate still works on a database that has not been seeded yet.
DESTINATION_TERMS: Tuple[str, ...] = (
    "united states", "usa", "america", "canada", "ireland", "malaysia", "cyprus",
    "south korea", "korea", "united kingdom", "uk", "britain", "england", "scotland",
    "australia", "germany", "hungary", "united arab emirates", "uae", "dubai", "new zealand",
    "saudi arabia", "japan", "italy", "finland", "spain",
)

#: A destination name on its own is not evidence of a study question: "what is the capital of
#: Canada?" mentions one and is still out of scope. It is a weak signal that only clears the
#: allow threshold together with a study term, or after an in-scope turn as a follow-up.
DESTINATION_WEIGHT = 0.5

#: Requests that are refused even when they also contain in-scope vocabulary. Each pattern is
#: narrow on purpose: a false refusal costs a student a real answer.
DENY_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("injection_override",
     r"\b(ignore|disregard|forget|bypass)\b[^.!?]{0,40}\b(previous|prior|above|earlier|your)\b[^.!?]{0,20}"
     r"\b(instructions?|prompts?|rules?|directions?)\b"),
    ("identity_change", r"\byou\s+are\s+now\b|\bfrom\s+now\s+on\s+you\b|\bact\s+as\s+(a|an|the)\b|"
                        r"\bpretend\s+(to\s+be|you\s+are|that\s+you)\b"),
    ("instruction_leak", r"\b(system\s+prompt|hidden\s+prompt|initial\s+prompt)\b|"
                        r"\b(reveal|show|print|repeat|output|echo|tell)\b[^.!?]{0,30}"
                        r"\b(your|the)\s+(system\s+|internal\s+|original\s+)?(prompt|instructions?|rules)\b|"
                        r"\bwhat\s+(were|are)\s+you\s+(told|instructed|programmed)\b|"
                        r"\b(jailbreak|developer\s+mode)\b|\bDAN\s+mode\b"),
    ("identity_question", r"\bwho\s+(made|created|built|trained)\s+you\b|"
                        r"\bwhat\s+(model|llm)\s+(are\s+you|is\s+this)\b"),
    ("coding_help",
     r"\b(write|generate|debug|fix|refactor|implement|optimi[sz]e|explain)\b[^.!?]{0,40}"
     r"\b(python|javascript|typescript|java|c\+\+|c#|php|ruby|sql|html|css|react|regex|"
     r"algorithm|script|function)\b"),
    ("homework", r"\b(homework|assignment|exam\s+(answers?|paper)|solve\s+(this|my)\s|"
                r"calculus|algebra|trigonometry|physics\s+problem)\b"),
    ("medical", r"\b(symptoms?|diagnos(is|e)|chest\s+pain|medication|prescri\w*|medical\s+advice|"
                r"is\s+it\s+cancer)\b"),
    ("legal", r"\b(lawsuit|sue\s+someone|legal\s+advice|attorney|power\s+of\s+attorney|divorce)\b"),
    ("entertainment", r"\b(world\s+cup|cricket\s+(score|match)|football\s+match|movie|netflix|"
                     r"celebrity|song\s+lyrics|joke|horoscope)\b"),
    ("politics", r"\b(election|prime\s+minister|president\s+of|political\s+party|"
                r"war\s+in|genocide)\b"),
    ("trading", r"\b(crypto|bitcoin|stock\s+(market|tips?)|forex|trading\s+strategy|"
               r"investment\s+advice)\b"),
    ("sql_injection", r"('\s*or\s*1\s*=\s*1|;\s*drop\s+table|union\s+select)\b"),
)

_DENY_COMPILED = tuple((name, re.compile(pattern, re.I)) for name, pattern in DENY_PATTERNS)

GREETINGS = frozenset({
    "hi", "hello", "hey", "salam", "assalamu alaikum", "assalamualaikum", "good morning",
    "good afternoon", "good evening", "greetings", "start", "menu", "help", "thanks",
    "thank you", "ok", "okay",
})

#: Openers that make a short message a continuation of the previous question.
FOLLOW_UP_OPENERS = (
    "what about", "how about", "and ", "also", "then", "what if", "which one", "which of",
    "how much", "how long", "how many", "why", "when", "where", "is it", "does it", "do they",
    "can i", "could i", "should i", "compare", "more", "tell me more", "and the", "and its",
    "there", "that one", "them", "those", "this one", "it ",
)

#: Words that point back at something already discussed. A short opener plus one of these is a
#: genuine continuation; a short opener plus a new, unrelated subject ("how much is a car?") is
#: not, and must not inherit the previous turn's permission.
ANAPHORS: Tuple[str, ...] = (
    "it", "its", "that", "this", "these", "those", "them", "they", "there", "one", "ones",
    "same", "both",
)

#: Requests for help with no topic yet. These are ambiguous, not unrelated: the right reply is a
#: short clarification, never a refusal.
HELP_PATTERNS: Tuple[re.Pattern[str], ...] = tuple(re.compile(p, re.I) for p in (
    r"\bhelp( me| you)?\b",
    r"\b(can|could|would) you (help|assist|advise)\b",
    r"\bi (need|want|would like) (some )?(help|advice|guidance|information)\b",
    r"\b(i'?m|i am) looking for\b",
    r"\bwhere do i start\b",
    r"\bwhat can you do\b",
    r"\bguide me\b",
))

#: One question per matched area. Never more than one question is asked, ever.
CLARIFY_QUESTIONS: Dict[str, str] = {
    "level": "Which level are you asking about — Bachelor's, Master's or PhD?",
    "admission": "Which programme or university is this about, and are you asking about entry "
                 "requirements or your own eligibility?",
    "tests": "Which English test requirement do you want to check, and for which programme?",
    "money": "Are you asking about tuition, living costs or scholarships for a particular destination?",
    "process": "Is this about application deadlines, documents, or the student visa for a "
               "particular destination?",
    "research": "Are you looking for a PhD supervisor or a research programme in a particular field?",
    "destination": "Which study destination are you asking about?",
    "counselling": "Would you like help choosing a destination, checking eligibility, or booking a "
                   "counselling session?",
}
DEFAULT_CLARIFY = (
    "I can help with universities, programmes, admission requirements, tuition and living costs, "
    "scholarships, English tests and study destinations. Which of those would you like to know about?"
)

#: Chips offered with a clarification, per matched area.
CLARIFY_CHIPS: Dict[str, Tuple[str, ...]] = {
    "level": ("Masters in computer science", "Bachelor's in business", "PhD in machine learning"),
    "admission": ("Entry requirements for a Master's", "Check my CGPA", "Is MOI accepted?"),
    "tests": ("IELTS requirements", "TOEFL or PTE?", "Is MOI accepted?"),
    "money": ("Tuition for a Master's", "Living cost per year", "Scholarships I may qualify for"),
    "process": ("Application deadlines", "Documents I need", "Student visa steps"),
    "research": ("PhD supervisors in AI", "Funded PhD positions", "Research proposal help"),
    "destination": ("Study in Canada", "Study in Germany", "Study in Scotland"),
    "counselling": ("Book a counselling session", "Which country suits my budget?", "Check my eligibility"),
}
DEFAULT_CHIPS: Tuple[str, ...] = (
    "Masters in computer science with IELTS 6.5",
    "What does a year in Canada cost?",
    "Fully funded PhD in machine learning",
    "Which countries fit a 25 lakh taka budget?",
)


@dataclass(frozen=True)
class GateResult:
    """The verdict for one question, with everything needed to explain it."""

    decision: str
    score: float
    reason: str
    families: Tuple[str, ...] = ()
    matched: Tuple[str, ...] = ()
    clarification: Optional[str] = None
    suggestions: Tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "score": round(float(self.score), 3),
            "reason": self.reason,
            "matched": list(self.matched),
            "clarification": self.clarification,
        }


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _term_pattern(term: str) -> re.Pattern[str]:
    """A whole-word match that tolerates a plural ('fee' -> 'fees') but not an embedding."""
    return re.compile(rf"(?<![\w-]){re.escape(term.strip())}(?:s|es)?(?![\w-])", re.I)


_STRONG_COMPILED: Tuple[Tuple[str, str, re.Pattern[str]], ...] = tuple(
    (family, term, _term_pattern(term))
    for family, terms in STRONG.items()
    for term in terms
)
_WEAK_COMPILED: Tuple[Tuple[str, str, re.Pattern[str]], ...] = tuple(
    (family, term, _term_pattern(term))
    for family, terms in WEAK.items()
    for term in terms
)
_DESTINATION_COMPILED: Tuple[Tuple[str, re.Pattern[str]], ...] = tuple(
    (term, _term_pattern(term)) for term in DESTINATION_TERMS
)


def _denied(text: str) -> Optional[str]:
    for name, pattern in _DENY_COMPILED:
        if pattern.search(text):
            return name
    return None


def _score(text: str) -> Tuple[float, List[str], List[str]]:
    """Weighted evidence, counted once per topic family.

    Counting every matching synonym would double a single idea: "requirements" matches both
    ``requirement`` and ``requirements``, and would otherwise look like two independent pieces
    of evidence and clear the allow threshold on its own. A family contributes its strongest
    signal exactly once.
    """
    families: List[str] = []
    matched: List[str] = []
    strong_families: set[str] = set()
    weak_families: set[str] = set()

    for term, pattern in _DESTINATION_COMPILED:
        if pattern.search(text):
            families.append("destination")
            matched.append(term)
            break

    for family, term, pattern in _STRONG_COMPILED:
        if pattern.search(text):
            strong_families.add(family)
            families.append(family)
            matched.append(term)

    for family, term, pattern in _WEAK_COMPILED:
        if pattern.search(text):
            weak_families.add(family)
            families.append(family)
            matched.append(term)

    total = float(len(strong_families)) + 0.4 * len(weak_families - strong_families)
    if "destination" in families:
        total += DESTINATION_WEIGHT
    return min(total, 3.0), sorted(set(families)), matched


def _is_greeting(text: str) -> bool:
    stripped = re.sub(r"[^a-z ]", "", text).strip()
    return stripped in GREETINGS or (len(stripped.split()) <= 2 and stripped in GREETINGS)


def _looks_like_follow_up(text: str, has_signal: bool) -> bool:
    """A short, clearly anaphoric continuation of the previous turn.

    It must *start* with a follow-up opener (not merely contain "and"), be short, and either
    carry in-domain vocabulary or point back at the earlier subject. "how much is a car?"
    therefore does not qualify, however short it is.
    """
    words = text.split()
    if len(words) > 10 or not any(text.startswith(opener) for opener in FOLLOW_UP_OPENERS):
        return False
    if has_signal:
        return True
    return any(re.search(rf"(?<![\w-]){word}(?![\w-])", text) for word in ANAPHORS)


def _asks_for_help(text: str) -> bool:
    return len(text.split()) <= 14 and any(p.search(text) for p in HELP_PATTERNS)


def _clarify(families: List[str]) -> Tuple[str, Tuple[str, ...]]:
    for family in ("level", "admission", "money", "process", "tests", "research",
                   "destination", "counselling"):
        if family in families:
            return CLARIFY_QUESTIONS[family], CLARIFY_CHIPS.get(family, DEFAULT_CHIPS)
    return DEFAULT_CLARIFY, DEFAULT_CHIPS


def _semantic_opinion(question: str) -> Optional[str]:
    """One cheap classification call, used only in the ambiguous band.

    Returns ``COUNSELLING``, ``UNRELATED`` or ``UNCLEAR``. Never raises: any failure is
    ``UNCLEAR``, which the caller turns into a clarification - the safe outcome.
    """
    from app.core import llm

    result = llm.complete(
        "You classify questions for a study-abroad counselling service. Reply with exactly one "
        "word: COUNSELLING if the question is about universities, degree programmes (Bachelor's, "
        "Master's, PhD), admission requirements, tuition or living costs, scholarships, English "
        "language tests, study destinations or student visas; UNRELATED if it is about anything "
        "else; UNCLEAR if it cannot be decided. One word only.",
        question[:500],
        max_tokens=3,
        temperature=0.0,
    )
    if not result:
        return None
    word = (result.text or "").strip().upper().replace(".", "")
    for candidate in ("COUNSELLING", "UNRELATED", "UNCLEAR"):
        if candidate in word:
            return candidate
    return None


def classify(
    question: str,
    *,
    previous_allowed: bool = False,
    previous_intent: str = "",
    enabled: Optional[bool] = None,
) -> GateResult:
    """Decide whether the assistant may answer this question.

    ``previous_allowed`` is the decision of the immediately preceding turn in the same
    conversation. It is only ever consulted for a short, clearly anaphoric follow-up, and a
    denied pattern always wins first. ``previous_intent`` is that turn's intent: when it was
    ``clarify`` and the student replies with in-domain vocabulary, that reply is answering our
    own question, so the assistant continues instead of re-asking the same thing.
    """
    if enabled is None:
        enabled = bool(settings.domain_gate_enabled)
    if not enabled:
        return GateResult(ALLOW, 1.0, "gate_disabled")

    text = _normalise(question)
    if not text:
        clarification, chips = _clarify([])
        return GateResult(CLARIFY, 0.0, "empty", clarification=clarification, suggestions=chips)

    denied = _denied(text)
    if denied:
        # Absolute. No score, no follow-up, no semantic appeal.
        return GateResult(REFUSE, 0.0, f"denied:{denied}")

    if _is_greeting(text):
        clarification, chips = _clarify([])
        return GateResult(CLARIFY, 0.0, "greeting", clarification=clarification, suggestions=chips)

    score, families, matched = _score(text)

    allow_at = float(settings.domain_allow_score or 0.8)
    refuse_below = float(settings.domain_refuse_score or 0.3)
    has_signal = bool(families)

    if score >= allow_at:
        return GateResult(ALLOW, score, "in_scope", tuple(families), tuple(matched))

    if previous_allowed and _looks_like_follow_up(text, has_signal):
        return GateResult(ALLOW, score, "follow_up", tuple(families), tuple(matched))

    # The student is answering the clarification we just asked. Continuing is the whole point of
    # asking; re-asking the same question would be a loop.
    if previous_intent == "clarify" and has_signal:
        return GateResult(ALLOW, score, "clarification_answer", tuple(families), tuple(matched))

    if score < refuse_below:
        if score <= 0 and _asks_for_help(text):
            clarification, chips = _clarify([])
            return GateResult(CLARIFY, score, "needs_topic", tuple(families), tuple(matched),
                              clarification=clarification, suggestions=chips)
        clarification, chips = _clarify(families)
        if score <= 0:
            return GateResult(REFUSE, score, "out_of_scope", tuple(families), tuple(matched))
        return GateResult(CLARIFY, score, "ambiguous", tuple(families), tuple(matched),
                          clarification=clarification, suggestions=chips)

    # Ambiguous band: weak evidence only, and no follow-up/clarification context to lean on.
    if settings.domain_semantic_enabled:
        opinion = _semantic_opinion(text)
        if opinion == "COUNSELLING":
            return GateResult(ALLOW, score, "semantic_allow", tuple(families), tuple(matched))
        if opinion == "UNRELATED":
            clarification, chips = _clarify([])
            return GateResult(REFUSE, score, "semantic_refuse", tuple(families), tuple(matched))

    clarification, chips = _clarify(families)
    return GateResult(CLARIFY, score, "ambiguous", tuple(families), tuple(matched),
                      clarification=clarification, suggestions=chips)


def policy() -> Dict[str, Any]:
    """The contract the frontend and the tests share: one refusal, three decisions."""
    return {
        "refusal": DOMAIN_REFUSAL,
        "decisions": [ALLOW, CLARIFY, REFUSE],
        "domains": [
            "universities", "bachelor programmes", "master programmes", "PhD and research programmes",
            "admission requirements", "eligibility", "tuition and living costs", "scholarships",
            "English language tests", "study destinations", "student visas for study",
        ],
        "gate_enabled": bool(settings.domain_gate_enabled),
        "semantic_tiebreak_enabled": bool(settings.domain_semantic_enabled),
        "allow_score": float(settings.domain_allow_score or 0.8),
        "refuse_score": float(settings.domain_refuse_score or 0.3),
    }
