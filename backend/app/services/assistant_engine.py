"""Assistant orchestration: RAG + tool calling + guardrails + audit trail (§64-§66, §85).

Layers, applied in order:

1. **Retrieval** over our own verified corpus (``services.retrieval``).
2. **Tools** — the structured queries in ``services.assistant`` (universities, programmes,
   requirements, tuition, intakes, scholarships, faculty).
3. **Generation** — an LLM composes the answer *only* from what layers 1-2 returned. The
   provider is **Grok (xAI), Mistral AI or Gemini**, tried in the configured order by
   ``core.llm``; there is no OpenAI dependency. With no provider key the deterministic
   templates are used, so the feature degrades rather than disappears.
4. **Guardrails** — the output is scanned and rewritten before it leaves the process.
5. **Persistence** — question, answer, tools, sources and grounding flag are stored so any
   answer can be audited later.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core import llm
from app.core.config import settings
from app.models.crm import AssistantConversation
from app.services import assistant as tools_mod
from app.services import retrieval

# --------------------------------------------------------------------- policy
SYSTEM_PROMPT = """You are the Scholara study-abroad assistant for a consultancy in Uttara, Dhaka.

ABSOLUTE RULES — breaking any of these is a critical failure:
1. Use ONLY the CONTEXT provided below. Never use your own knowledge about a university,
   programme, fee, requirement, deadline or scholarship. If the context does not contain it,
   say you do not have a verified figure and point to the official source or a counselor.
2. NEVER state or imply an admission probability, a chance of acceptance, or a visa success
   rate. No percentages, no "high/low chance", no "you will get in".
3. Scholarships are always phrased as "stated eligibility criteria appear to be met" —
   never "you qualify" or "you will receive".
4. NEVER output a faculty email address unless it appears verbatim in the context with an
   email_status of OFFICIAL_UNIVERSITY_EMAIL or PUBLIC_PROFESSIONAL_EMAIL. Never guess one.
5. When a figure's confidence is OUTDATED, NEEDS_REVIEW or CONFLICTING, say so in the answer.
6. For fees, contracts, refunds, visa decisions or anything legal, hand off to a human counselor.
7. Be concise and concrete. Short paragraphs or bullets. No marketing language.
8. Write in plain English for a Bangladeshi student audience.
"""

# --------------------------------------------------------------- guardrails
# The platform's hardest rule (§05) is that it never states an admission probability, a
# guarantee or an unsourced email. Patching offending *words* is unsafe: rewriting
# "visa success rate is 98%" word-by-word leaves "...which we do not predict is 98%" — the
# number survives the scrub. So a sentence that makes a prohibited claim is dropped **whole**.
#
# Each pattern must be specific enough that legitimate sentences survive. A bare percentage is
# fine ("the scholarship covers 50% of tuition"); a percentage in the same sentence as an
# admission, acceptance or visa outcome is not.
_OUTCOME = r"(admission|admit|accept|acceptance|get\s+in|getting\s+in|offer|visa|scholarship|approval|selection)"

PROHIBITED_CLAIMS: list[tuple[str, re.Pattern[str]]] = [
    # A number presented as an outcome likelihood, in any word order.
    ("admission_probability",
     re.compile(rf"\d{{1,3}}\s*(%|percent)\b(?=[^.!?]*\b(chance|probabilit|likelihood|odds|success\s*rate|{_OUTCOME})\b)", re.I)),
    ("admission_probability",
     re.compile(rf"\b(chance|probabilit|likelihood|odds|success\s*rate)\b(?=[^.!?]*\d{{1,3}}\s*(%|percent))", re.I)),
    # Qualitative likelihood of an outcome.
    ("admission_probability",
     re.compile(rf"\b(chances?|odds|probability|likelihood)\s+(of|for|at|to)\b[^.!?]*\b{_OUTCOME}", re.I)),
    ("admission_probability",
     re.compile(rf"\b{_OUTCOME}\s+(chances?|odds|probability|likelihood|success\s*rate)\b", re.I)),
    ("admission_probability",
     re.compile(rf"\b(high|good|strong|excellent|low|poor|slim|decent|fair)\s+(chances?|odds|likelihood|probability)\b", re.I)),
    ("admission_probability",
     re.compile(r"\b(visa|admission|acceptance)\s+success\s*rate\b", re.I)),
    # Predictions and promises.
    ("guaranteed_outcome", re.compile(r"\bguarantee\w*\b", re.I)),
    ("guaranteed_outcome",
     re.compile(rf"\byou\s+(will|'ll|are\s+(sure|certain|likely)\s+to|should)\s+\w*\s*\b(get|be|receive|secure|win|obtain)\b[^.!?]*\b{_OUTCOME}", re.I)),
    ("guaranteed_outcome",
     re.compile(rf"\b(definitely|certainly|surely|without\s+doubt)\b[^.!?]*\b{_OUTCOME}", re.I)),
    # Scholarship/eligibility phrasing the policy forbids (§05: "stated criteria appear to be met").
    ("unapproved_eligibility_phrasing", re.compile(r"\byou\s+(do\s+)?qualify\b", re.I)),
    ("unapproved_eligibility_phrasing", re.compile(r"\byou\s+are\s+(fully\s+)?(eligible|qualified)\b", re.I)),
    ("unapproved_eligibility_phrasing", re.compile(r"\byou\s+will\s+receive\s+(the\s+|a\s+)?(scholarship|funding|award|waiver)\b", re.I)),
]

#: Appended once when any sentence was quarantined, so the student is told why it reads terse.
#: Deliberately worded without the phrases the policy battery scans for, so the notice itself
#: can never trip the very rule it is explaining.
QUARANTINE_NOTE = (
    "Part of that answer was removed. Scholara reports only what our verified records state — "
    "whether your profile appears to meet the stated criteria — and the university alone "
    "decides the outcome."
)

EMAIL_RE = re.compile(r"\b[\w\.\-\+]+@[\w\-]+\.[\w\.\-]+\b")

_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|\n+|$)")

#: Spans whose internal full stops must not be treated as sentence ends. Without this an
#: address like ``a.b@c.edu`` is split into four "sentences" and quarantining one of them
#: leaves the rest of the address in the answer.
_PROTECTED_RE = re.compile(
    r"""(?:
          [\w\.\-\+]+@[\w\-]+\.[\w\.\-]+          # email addresses
        | https?://\S+ | www\.\S+                     # URLs
        | \b\d+(?:\.\d+)+\w*                          # 6.5, 1.2.3, 24.5k
        | \b(?:e\.g|i\.e|etc|vs|approx|No|Inc|Ltd|Ph\.D|B\.Sc|M\.Sc|U\.S|U\.K
             |Dr|Mr|Mrs|Ms|Prof|St|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.
    )""",
    re.I | re.X,
)
_SENTINEL = "\x00"

ESCALATION_TRIGGERS = [
    "refund", "contract", "fee structure", "how much do you charge", "your charges",
    "legal", "lawyer", "complaint", "visa rejected", "visa refusal", "appeal",
    "talk to a human", "speak to someone", "call me", "counselor", "counsellor",
]

HANDOFF_TEXT = (
    "This needs a counselor rather than an automated answer. You can book a free session at "
    "our Uttara office, or message us on WhatsApp and a counselor will pick it up during "
    "office hours (Saturday to Thursday, 10:00 to 19:00)."
)


def _sentences(text: str) -> list[str]:
    """Split into sentences, preserving trailing punctuation and newlines.

    Full stops inside emails, URLs, decimals and common abbreviations are masked first so a
    sentence is never cut through the middle of one. ``"".join(_sentences(t)) == t``.
    """
    spans: list[str] = []

    def _mask(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return f"{_SENTINEL}{len(spans) - 1}{_SENTINEL}"

    masked = _PROTECTED_RE.sub(_mask, text)

    def _unmask(chunk: str) -> str:
        for index, original in enumerate(spans):
            chunk = chunk.replace(f"{_SENTINEL}{index}{_SENTINEL}", original)
        return chunk

    return [_unmask(m.group(0)) for m in _SENTENCE_RE.finditer(masked) if m.group(0).strip()]


def _apply_guardrails(text: str, allowed_emails: set[str]) -> tuple[str, list[str]]:
    """Scrub generated text. Returns (clean_text, violations).

    Order matters. Emails are replaced first, so the sentence pass never has to reason about
    addresses. Then a sentence making a prohibited claim is removed **whole** — never partially
    rewritten, because a partial rewrite can leave the offending number behind. An empty result
    is a valid outcome and tells the caller to fall back to the deterministic template.
    """
    violations: list[str] = []

    # 1. Strip any email address that is not an approved, sourced one. The replacement text
    #    contains no full stop, so it cannot affect the sentence split that follows.
    def _email_guard(match: re.Match[str]) -> str:
        addr = match.group(0)
        if addr.lower() in allowed_emails:
            return addr
        violations.append("unsourced_email")
        return "[email not published on an official source — contact the department]"

    working = EMAIL_RE.sub(_email_guard, text or "")

    # 2. Quarantine whole sentences that make a prohibited claim.
    kept: list[str] = []
    for sentence in _sentences(working):
        hit = next((name for name, pattern in PROHIBITED_CLAIMS if pattern.search(sentence)), None)
        if hit:
            violations.append(hit)
            continue
        kept.append(sentence)

    clean = "".join(kept).strip()
    if clean and any(v != "unsourced_email" for v in violations):
        clean = f"{clean}\n\n{QUARANTINE_NOTE}"
    return clean, violations


# ------------------------------------------------------------------ LLM layer
def _llm_available() -> bool:
    """True when Grok, Mistral or Gemini is configured and generation is switched on."""
    return llm.available()


def _call_llm(question: str, context_block: str, profile_note: str) -> Optional[llm.LLMResult]:
    """One grounded completion from the first provider that answers. None on total failure.

    The prompt is identical whichever provider serves it, so the data-integrity guardrails
    applied afterwards behave the same way across Grok, Mistral and Gemini.
    """
    return llm.complete(
        SYSTEM_PROMPT,
        (
            f"CONTEXT (the only facts you may use):\n{context_block}\n\n"
            f"{profile_note}\n\nSTUDENT QUESTION: {question}"
        ),
    )


def _context_block(hits: List[Dict[str, Any]], structured: Dict[str, Any]) -> str:
    lines: list[str] = []
    for i, hit in enumerate(hits, 1):
        meta = []
        if hit.get("source_url"):
            meta.append(f"source={hit['source_url']}")
        if hit.get("confidence"):
            meta.append(f"confidence={hit['confidence']}")
        if hit.get("verified_at"):
            meta.append(f"verified={hit['verified_at'][:10]}")
        lines.append(f"[{i}] {hit['title']} ({', '.join(meta) or 'no source recorded'})\n{hit['content']}")
    if structured:
        lines.append("STRUCTURED TOOL RESULTS:\n" + json.dumps(structured, default=str)[:6000])
    return "\n\n".join(lines) if lines else "(no verified records matched this question)"


# ----------------------------------------------------------------- main entry
def ask(
    db: Session,
    question: str,
    *,
    context: Optional[Dict[str, Any]] = None,
    session_id: str = "anonymous",
    student_profile_id: Optional[int] = None,
) -> Dict[str, Any]:
    started = time.time()
    context = context or {}
    question = (question or "").strip()

    if not question:
        return {
            "answer": "Ask me about a university, a programme, its requirements, the real annual "
                      "cost, scholarships or a PhD supervisor.",
            "intent": "empty", "sources": [], "data": {}, "grounded": True,
            "escalated": False, "tools_used": [], "suggestions": _suggestions("general"),
        }

    low = question.lower()
    escalate = any(trigger in low for trigger in ESCALATION_TRIGGERS)

    # ---- layer 1: retrieval -------------------------------------------
    hits = retrieval.search(db, question, limit=6)

    # ---- layer 2: structured tools ------------------------------------
    structured = tools_mod.answer(db, question, context)
    intent = structured.get("intent", "general")
    data = structured.get("data", {}) or {}
    tools_used = [k for k, v in data.items() if v]

    # Collect the only email addresses the answer is permitted to contain.
    allowed_emails: set[str] = set()
    for person in data.get("faculty", []) or []:
        status = (person.get("email_status") or "").upper()
        if person.get("email") and status in {"OFFICIAL_UNIVERSITY_EMAIL", "PUBLIC_PROFESSIONAL_EMAIL"}:
            allowed_emails.add(person["email"].lower())

    # ---- layer 3: generation ------------------------------------------
    answer_text: Optional[str] = None
    mode = "template"
    provider_name: Optional[str] = None
    provider_model: Optional[str] = None
    if _llm_available() and (hits or data):
        profile = context.get("profile") or {}
        profile_note = (
            "STUDENT PROFILE (use only to tailor the explanation, never to predict an outcome): "
            + json.dumps({k: v for k, v in profile.items() if v not in (None, "", [])}, default=str)[:1200]
            if profile else "No student profile was provided."
        )
        result = _call_llm(question, _context_block(hits, data), profile_note)
        if result:
            answer_text = result.text
            mode = "llm"
            provider_name = result.provider
            provider_model = result.model

    if not answer_text:
        answer_text = structured.get("answer") or ""
        if not answer_text.strip() and hits:
            answer_text = _render_from_hits(hits)

    grounded = bool(hits or data)
    if not grounded:
        answer_text = (
            "I don't have a verified record covering that yet, so I won't guess. "
            "Search the universities or programmes pages, or ask a counselor on WhatsApp — "
            "we only publish figures we can link to an official source."
        )

    if escalate:
        answer_text = f"{answer_text}\n\n{HANDOFF_TEXT}".strip()

    # ---- layer 4: guardrails ------------------------------------------
    answer_text, violations = _apply_guardrails(answer_text, allowed_emails)

    # A generated answer can be quarantined down to nothing. That is a success for the policy
    # but a failure for the student, so fall back to the deterministic template and record
    # that the model had to be overridden.
    if mode == "llm" and not answer_text.strip():
        fallback = structured.get("answer") or (_render_from_hits(hits) if hits else "")
        answer_text, extra = _apply_guardrails(fallback, allowed_emails)
        violations.extend(extra)
        violations.append("llm_output_rejected")
        mode = "template"
        if not answer_text.strip():
            answer_text = (
                "I can't answer that within our data-integrity rules. A counselor can walk you "
                "through it — message us on WhatsApp and we'll pick it up during office hours."
            )

    # ---- sources -------------------------------------------------------
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        url = hit.get("source_url") or hit.get("url_path")
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append({
            "label": hit["title"], "url": hit.get("source_url"), "page": hit.get("url_path"),
            "source_type": hit.get("source_type"), "verified_at": hit.get("verified_at"),
            "confidence": hit.get("confidence"),
        })
    for citation in structured.get("citations", []) or []:
        url = citation.get("url")
        if url and url not in seen:
            seen.add(url)
            sources.append(citation)

    # ---- layer 5: audit trail -----------------------------------------
    latency = int((time.time() - started) * 1000)
    try:
        db.add(AssistantConversation(
            session_id=session_id[:80], student_profile_id=student_profile_id,
            question=question[:4000], answer=answer_text[:8000], intent=intent,
            tools_used=tools_used + ([f"guardrail:{v}" for v in violations] if violations else []),
            sources=sources[:10], grounded=grounded, escalated=escalate, latency_ms=latency,
        ))
        db.commit()
    except Exception:
        db.rollback()

    return {
        "answer": answer_text,
        "intent": intent,
        "data": data,
        "sources": sources[:8],
        "grounded": grounded,
        "escalated": escalate,
        "tools_used": tools_used,
        "retrieved": [{"title": h["title"], "page": h["url_path"], "score": h["score"]} for h in hits],
        "mode": mode,
        "provider": provider_name,
        "model": provider_model,
        "latency_ms": latency,
        "suggestions": _suggestions(intent),
        "disclaimer": (
            "Answers are built from Scholara's own source-verified records. They are not an "
            "admission decision, a scholarship award or a visa outcome."
        ),
    }


def _render_from_hits(hits: List[Dict[str, Any]]) -> str:
    lines = ["Here is what our verified records hold:"]
    for hit in hits[:4]:
        snippet = hit["content"][:260].rsplit(" ", 1)[0]
        lines.append(f"\n**{hit['title']}** — {snippet}…")
        if hit.get("confidence") and hit["confidence"] != "VERIFIED":
            lines.append(f"  (status: {hit['confidence'].replace('_', ' ').lower()} — confirm before acting)")
    return "\n".join(lines)


def _suggestions(intent: str) -> List[str]:
    base = {
        "find_program": ["What are the entry requirements?", "What does a year actually cost?",
                         "Which intakes are open?"],
        "find_scholarship": ["Which scholarships fit my CGPA?", "When are the deadlines?",
                             "Do any cover living costs?"],
        "find_faculty": ["Who works on machine learning?", "Which supervisors are accepting students?",
                         "How should I email a supervisor?"],
        "tuition": ["What is the total first-year cost?", "Which countries fit a 20 lakh budget?",
                    "Are there tuition waivers?"],
        "intake": ["What is the next deadline?", "When do Fall 2027 applications open?",
                   "How early should I apply?"],
        "check_eligibility": ["Check my eligibility for this programme", "Is MOI accepted anywhere?",
                              "Does a study gap matter?"],
    }
    return base.get(intent, [
        "Which countries suit a 25 lakh taka budget?",
        "Masters in computer science with IELTS 6.5",
        "Fully funded PhD supervisors in machine learning",
        "What does a year in Canada actually cost?",
    ])
