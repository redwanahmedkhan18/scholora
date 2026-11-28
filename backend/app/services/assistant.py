"""Grounded assistant (§64-§66).

Tool-calling over the structured database ONLY. If a fact is not in the verified
database the assistant refuses to guess. An LLM key is optional: without it the
service returns deterministic, templated answers built from retrieved rows.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core import academic_year as academic_year_mod
from app.core import verification_policy as policy
from app.models.education import Country, Professor, Program, Scholarship, University
from app.services import costing, destinations, matching, scoping

UNAVAILABLE_MESSAGE = (
    "Verified information is currently unavailable. Please confirm this requirement with the "
    "university or a counselor before applying."
)

INTENTS = [
    ("find_university", ["university", "universities", "uni", "college"]),
    ("find_program", ["program", "programme", "course", "msc", "bsc", "mba", "major"]),
    ("check_eligibility", ["eligible", "eligibility", "qualify", "cgpa", "chance"]),
    ("find_scholarship", ["scholarship", "funding", "financial aid", "waiver", "stipend"]),
    ("find_faculty", ["supervisor", "professor", "faculty", "research", "phd"]),
    ("tuition", ["tuition", "cost", "fee", "budget", "expense"]),
    ("intake", ["intake", "deadline", "admission date", "semester", "fall", "spring"]),
    ("brochure", ["brochure", "pdf", "guide", "document"]),
    ("appointment", ["appointment", "book", "counseling", "counselling", "visit"]),
    ("handoff", ["human", "counselor", "counsellor", "agent", "call me", "talk to someone", "i need help"]),
]


def detect_intent(text: str) -> str:
    low = (text or "").lower()
    for intent, keys in INTENTS:
        if any(k in low for k in keys):
            return intent
    return "general"


# ------------------------------------------------------------------ tools
def _verdict(row: Any, db: Optional[Session], payload: Dict[str, Any],
             academic_year: Optional[Any] = None) -> Dict[str, Any]:
    """Attach the verification verdict to a serialised row.

    The assistant may only present a figure whose record passes the read-time policy, and the
    caller (and the student) should be able to see which of those two it is.
    """
    decision = (policy.evaluate_record(db, row, academic_year=academic_year) if db is not None
                else policy.evaluate(row, academic_year=academic_year))
    payload["verified"] = decision.usable
    payload["verification_reason"] = decision.reason
    payload["verification_note"] = decision.note
    return payload


def _keeper(rows: List[Any], enforce_policy: bool, db: Optional[Session] = None,
            academic_year: Optional[Any] = None) -> List[Any]:
    if not enforce_policy:
        return rows
    if db is None:
        return [r for r in rows if policy.is_answerable(r, academic_year=academic_year)]
    conflicts = policy.conflicts_in(db)
    return [r for r in rows
            if policy.evaluate_record(db, r, conflicts=conflicts,
                                      academic_year=academic_year).usable]


def search_universities(db: Session, q: str = "", country: str = "",
                        limit: int = 8, enforce_policy: bool = True,
                        academic_year: Optional[Any] = None,
                        scope: Optional[scoping.Scope] = None) -> List[Dict[str, Any]]:
    if scope is None:
        scope = scoping.resolve_scope(db, context={"country": country})
    if not scope.ok:
        # Fail closed: an unresolved mandatory destination never becomes "search everywhere".
        return []
    stmt = select(University).options(selectinload(University.country)).where(University.is_published.is_(True))
    if q:
        stmt = stmt.where(University.name.ilike(f"%{q}%"))
    clause = scoping.university_clause(db, scope)
    if clause is not None:
        stmt = stmt.where(clause)
    rows = db.execute(stmt.limit(scoping.CANDIDATE_CAP)).scalars().unique().all()
    out = []
    for u in _keeper(list(rows), enforce_policy, db, academic_year)[:limit]:
        out.append(_verdict(u, db, {"id": u.id, "name": u.name, "slug": u.slug,
                                "country": u.country.name if u.country else None,
                                "city": u.city, "website": u.website, "source_url": u.source_url,
                                "verified_at": u.verified_at.isoformat() if u.verified_at else None}))
    return out


def search_programs(db: Session, subject: str = "", country: str = "", degree_level: str = "",
                    max_tuition: Optional[float] = None, limit: int = 8,
                    enforce_policy: bool = True,
                    academic_year: Optional[Any] = None,
                    scope: Optional[scoping.Scope] = None,
                    max_tuition_currency: str = "USD") -> List[Dict[str, Any]]:
    if scope is None:
        scope = scoping.resolve_scope(db, context={
            "country": country, "degree_level": degree_level, "subject": subject},
            question="")
        if academic_year is not None and scope.academic_year is None:
            # A caller-supplied year is a hard constraint too: it must reach the query predicate
            # and not only the post-fetch policy check.
            scope = replace(scope, academic_year=academic_year)
        if max_tuition is not None:
            scope = replace(scope, budget_amount=float(max_tuition),
                            budget_currency=max_tuition_currency.upper())
    if not scope.ok:
        return []
    stmt = (select(Program).options(selectinload(Program.university).selectinload(University.country),
                                    selectinload(Program.requirement), selectinload(Program.intakes))
            .where(Program.is_published.is_(True)))
    if scope.subject:
        s = f"%{scope.subject}%"
        # A subject the student named is a hard constraint: no silent widening. The ILIKE is the
        # database-side narrowing; the whole-word check below removes the substring false
        # positives ("art" matching "Earth Science") before any ranking or truncation.
        stmt = stmt.where(or_(Program.name.ilike(s), Program.discipline.ilike(s)))
    if scope.degree_level:
        stmt = stmt.where(Program.degree_level == scope.degree_level)
    clause = scoping.university_clause(db, scope)
    if clause is not None:
        stmt = stmt.join(University, Program.university_id == University.id).where(clause)
    year_ids = scoping.program_year_ids(db, scope)
    if year_ids is not None:
        # An explicitly requested year is a hard constraint, applied in the database. No record
        # stating a year means no confirmed match - never "no filter".
        stmt = stmt.where(Program.id.in_(list(year_ids)))
    rows = db.execute(stmt.limit(scoping.CANDIDATE_CAP)).scalars().unique().all()
    if scope.subject:
        rows = [p for p in rows if scoping.subject_matches(scope.subject, p.name, p.discipline)]
    if scope.budget_amount is not None:
        # Currency-safe and pre-limit: a tuition figure that cannot be converted is excluded, not
        # compared against a number denominated in something else.
        rows = [p for p in rows
                if scoping.tuition_within_budget(scope, p.tuition_annual, p.currency)]
    conflicts = policy.conflicts_in(db) if enforce_policy else None
    out = []
    for p in _keeper(list(rows), enforce_policy, db, academic_year)[:limit]:
        req = p.requirement
        # The programme passing the policy does not verify its requirement row: that row has its
        # own source and date. Quote its figures only when it passes on its own.
        req_ok = bool(req is not None and (
            not enforce_policy or policy.evaluate_record(db, req, conflicts=conflicts).usable))
        intakes_ok = [i for i in p.intakes
                      if not enforce_policy or policy.evaluate_record(
                          db, i, conflicts=conflicts, academic_year=academic_year).usable]
        out.append(_verdict(p, db, {
            "id": p.id, "name": p.name, "slug": p.slug, "degree_level": p.degree_level,
            "university": p.university.name, "university_slug": p.university.slug,
            "country": p.university.country.name if p.university.country else None,
            "tuition_annual": p.tuition_annual, "currency": p.currency,
            "min_cgpa": req.min_cgpa if req_ok else None,
            "ielts_overall": req.ielts_overall if req_ok else None,
            "requirement_verified": req_ok,
            "intakes": [i.label for i in intakes_ok],
            "scholarship_available": p.scholarship_available,
            "source_url": p.source_url,
            "verified_at": p.verified_at.isoformat() if p.verified_at else None,
        }))
    return out


def get_program_requirements(db: Session, program_id: int,
                             enforce_policy: bool = True,
                             academic_year: Optional[Any] = None) -> Dict[str, Any]:
    p = db.get(Program, program_id)
    if not p or not p.requirement:
        return {"available": False, "message": UNAVAILABLE_MESSAGE}
    r = p.requirement
    if enforce_policy and not policy.evaluate_record(
            db, p, academic_year=academic_year).usable:
        return {"available": False, "message": UNAVAILABLE_MESSAGE,
                "verification_reason": policy.evaluate_record(
                    db, p, academic_year=academic_year).reason}
    if enforce_policy and not policy.evaluate_record(db, r).usable:
        # The programme may exist and even be published; its entry requirements may not be
        # quotable yet. Saying so is the honest answer, and it is not an error.
        return {"available": False, "message": UNAVAILABLE_MESSAGE,
                "verification_reason": policy.evaluate_record(db, r).reason}
    return _verdict(r, db, {"available": True, "program": p.name, "university": p.university.name,
            "min_cgpa": r.min_cgpa, "cgpa_scale": r.cgpa_scale, "ielts_overall": r.ielts_overall,
            "ielts_band_min": r.ielts_band_min, "toefl_ibt": r.toefl_ibt, "pte_academic": r.pte_academic,
            "documents": r.documents, "prerequisites": r.prerequisites,
            "source_url": r.source_url,
            "verified_at": r.verified_at.isoformat() if r.verified_at else None})


def get_tuition(db: Session, program_id: int, budget: Optional[float] = None,
                enforce_policy: bool = True,
                academic_year: Optional[Any] = None,
                currency: str = "USD") -> Dict[str, Any]:
    p = db.get(Program, program_id)
    if not p:
        return {"available": False, "message": UNAVAILABLE_MESSAGE}
    if enforce_policy and not policy.evaluate_record(
            db, p, academic_year=academic_year).usable:
        return {"available": False, "message": UNAVAILABLE_MESSAGE,
                "verification_reason": policy.evaluate_record(
                    db, p, academic_year=academic_year).reason}
    # The budget must be compared in the currency it was stated in. Defaulting silently to USD
    # is how a BDT figure became a confident "over budget" verdict on a USD fee.
    data = costing.estimate(p, budget, currency)
    return _verdict(p, db, {"available": True, "program": p.name, "university": p.university.name,
                        "source_url": p.source_url, **data,
                        "verified_at": p.verified_at.isoformat() if p.verified_at else None})


def get_intakes(db: Session, program_id: int, enforce_policy: bool = True,
                academic_year: Optional[Any] = None) -> List[Dict[str, Any]]:
    p = db.get(Program, program_id)
    if not p:
        return []
    intakes = [i for i in p.intakes
               if not enforce_policy or policy.evaluate_record(
                   db, i, academic_year=academic_year).usable]
    return [{"label": i.label, "status": i.status,
             "application_deadline": i.application_deadline.isoformat() if i.application_deadline else None,
             "class_start": i.class_start.isoformat() if i.class_start else None,
             "source_url": i.source_url,
             "verified": (not enforce_policy) or policy.evaluate_record(
                 db, i, academic_year=academic_year).usable} for i in intakes]


def search_scholarships(db: Session, country: str = "", degree_level: str = "",
                        limit: int = 8, enforce_policy: bool = True,
                        academic_year: Optional[Any] = None,
                        scope: Optional[scoping.Scope] = None,
                        subject: str = "") -> List[Dict[str, Any]]:
    if scope is None:
        scope = scoping.resolve_scope(db, context={
            "country": country, "degree_level": degree_level, "subject": subject})
    if not scope.ok:
        return []
    stmt = select(Scholarship).options(selectinload(Scholarship.country), selectinload(Scholarship.university)) \
        .where(Scholarship.is_published.is_(True))
    if scope.has_destination:
        university_ids = scoping.university_ids(db, scope)
        # A destination means the scholarship must belong to it: a country-level award inside the
        # family, or an award tied to an institution inside the family. Excluded countries cannot
        # come back through the wider institution set.
        conditions = [and_(Scholarship.university_id.is_(None),
                           Scholarship.country_id.in_(list(scoping.destination_country_ids(scope))))]
        if university_ids:
            conditions.append(Scholarship.university_id.in_(list(university_ids)))
        stmt = stmt.where(or_(*conditions))
    rows = db.execute(stmt.limit(scoping.CANDIDATE_CAP)).scalars().unique().all()
    if scope.degree_level:
        # Filtered here - not after a fetch limit - because ``degree_levels`` is a JSON list that
        # no portable SQL predicate can contain. Truncation happens only after this filter.
        rows = [s for s in rows
                if not s.degree_levels or scope.degree_level in s.degree_levels]
    if scope.subject:
        # Same pre-limit rule for the discipline list. An award that names no discipline is open
        # to every subject and stays eligible; one that names them must include the requested one.
        rows = [s for s in rows
                if not s.disciplines
                or any(scoping.subject_matches(scope.subject, str(d)) for d in s.disciplines)]
    rows = _keeper(list(rows), enforce_policy, db, academic_year)
    return [_verdict(s, db, {"id": s.id, "name": s.name, "provider": s.provider, "percentage": s.percentage,
                         "amount_value": s.amount_value, "currency": s.currency,
                         "deadline": s.deadline.isoformat() if s.deadline else None,
                         "eligibility_summary": s.eligibility_summary, "source_url": s.source_url,
                         "university": s.university.name if s.university else None,
                         "country": s.country.name if s.country else None}) for s in rows[:limit]]


def search_faculty(db: Session, interests: List[str], country: str = "", field: str = "",
                   limit: int = 8, enforce_policy: bool = True,
                   academic_year: Optional[Any] = None,
                   scope: Optional[scoping.Scope] = None) -> List[Dict[str, Any]]:
    if scope is None:
        scope = scoping.resolve_scope(db, context={"country": country})
    if not scope.ok:
        return []
    # A region request stays inside that region. Previously this widened Scotland to the whole
    # United Kingdom through ``sovereign_of``, which is the opposite of what was asked for.
    university_ids = list(scoping.university_ids(db, scope)) if scope.has_destination else None
    matches = matching.match_faculty(db, interests, university_ids=university_ids, field=field,
                                     limit=limit * 3)
    if enforce_policy:
        conflicts = policy.conflicts_in(db)
        matches = [m for m in matches
                   if policy.evaluate_record(db, m["professor"], conflicts=conflicts,
                                             academic_year=academic_year).usable]
    out = []
    for m in matches:
        p: Professor = m["professor"]
        out.append({"id": p.id, "slug": p.slug, "name": p.full_name, "title": p.title,
                    "university": p.university.name, "department": p.department.name if p.department else None,
                    "research_interests": p.research_interests, "matched": m["matched_interests"],
                    "evidence": m["evidence"], "accepting_phd": p.accepting_phd,
                    "email": p.email if p.email_status != "UNAVAILABLE" else None,
                    "email_status": p.email_status, "email_source_url": p.email_source_url,
                    "profile_url": p.profile_url, "score": m["score"]})
    return out


TOOLS: Dict[str, Callable[..., Any]] = {
    "search_universities": search_universities,
    "search_programs": search_programs,
    "get_program_requirements": get_program_requirements,
    "get_tuition": get_tuition,
    "get_intakes": get_intakes,
    "search_scholarships": search_scholarships,
    "search_faculty": search_faculty,
}

TOOL_SPECS = [
    {"name": "search_universities", "args": ["q", "country", "limit"]},
    {"name": "search_programs", "args": ["subject", "country", "degree_level", "max_tuition", "limit"]},
    {"name": "get_program_requirements", "args": ["program_id"]},
    {"name": "get_tuition", "args": ["program_id", "budget"]},
    {"name": "get_intakes", "args": ["program_id"]},
    {"name": "search_scholarships", "args": ["country", "degree_level", "limit"]},
    {"name": "search_faculty", "args": ["interests", "country", "field", "limit"]},
    {"name": "check_eligibility", "args": ["profile", "program_id"]},
    {"name": "generate_brochure", "args": ["profile", "program_id", "intake"]},
    {"name": "book_appointment", "args": ["name", "phone", "service_type", "date", "time"]},
    {"name": "create_lead", "args": ["name", "phone", "interest"]},
    {"name": "handoff_to_counselor", "args": ["reason"]},
]


def build_scope(db: Session, question: str, context: Optional[Dict[str, Any]] = None,
                *, intent: Optional[str] = None) -> scoping.Scope:
    """The one place a question's retrieval scope is resolved.

    Precedence is fixed and deliberate: a value the student has already stated (the W5 effective
    context - already stripped of client input and already resolved against the profile) wins over
    a figure guessed from this sentence, so a rejected destination mentioned in passing cannot
    become the scope. Only when the conversation says nothing do we read the sentence, and only
    with the deterministic resolvers. The scope itself is built once, by
    :func:`scoping.resolve_scope`, for every retrieval surface.
    """
    context = context or {}
    effective = {
        "country": context.get("country") or _guess_country(db, question),
        "degree_level": context.get("degree_level") or _guess_degree(question),
        "subject": context.get("subject") or _guess_subject(question),
        "excluded_countries": context.get("excluded_countries") or [],
    }
    return scoping.resolve_scope(db, context=effective,
                                 profile=context.get("profile") or {},
                                 question=question, intent=intent)


def answer(db: Session, question: str, context: Optional[Dict[str, Any]] = None,
           *, enforce_policy: bool = True,
           academic_year: Optional[Any] = None,
           intent: Optional[str] = None,
           scope: Optional[scoping.Scope] = None) -> Dict[str, Any]:
    """Retrieval-first answer. Returns text + the rows and sources used.

    ``enforce_policy`` is not a convenience switch: with it on (the default, and the only value
    any student-facing caller uses) an unverified or stale record is not returned at all, so the
    template has nothing to quote and says the information is unavailable.
    """
    context = context or {}
    year = academic_year if academic_year is not None else academic_year_mod.parse(question)
    # ``intent`` is supplied only when a clarification left a question in flight and the
    # student's reply names no intent of its own; every other caller lets the text decide.
    intent = intent or detect_intent(question)
    citations: List[Dict[str, Any]] = []
    data: Dict[str, Any] = {}

    if scope is None:
        scope = build_scope(db, question, context, intent=intent)
    elif year is not None and scope.academic_year is None:
        scope = replace(scope, academic_year=year)
    if not scope.ok:
        # An unresolved mandatory constraint is answered with a targeted question. Falling
        # through here is what used to turn "I could not place that destination" into a
        # worldwide search.
        reason = scope.reasons[0] if scope.reasons else ""
        return {"intent": intent, "answer": scoping.reason_text(reason), "data": {},
                "citations": [], "grounded": False, "disclaimer": None,
                "scope": {"status": scope.status, "reason": reason,
                          "clarify_slot": scope.clarify_slot or "country"}}

    if intent in ("find_program", "find_university", "tuition", "intake", "check_eligibility"):
        rows = search_programs(db, limit=6, enforce_policy=enforce_policy, scope=scope,
                               academic_year=scope.academic_year or year, **scope.call_kwargs())
        data["programs"] = rows
        citations += [{"label": f"{r['university']} - {r['name']}", "url": r["source_url"],
                       "verified_at": r["verified_at"]} for r in rows if r.get("source_url")]
    if intent == "find_scholarship":
        _kw = scope.call_kwargs()
        rows = search_scholarships(db, limit=6, enforce_policy=enforce_policy, scope=scope,
                                   academic_year=scope.academic_year or year,
                                   country=_kw["country"], degree_level=_kw["degree_level"],
                                   subject=_kw["subject"])
        data["scholarships"] = rows
        citations += [{"label": r["name"], "url": r["source_url"]} for r in rows if r.get("source_url")]
    if intent == "find_faculty":
        interests = context.get("research_interests") or _guess_interests(question)
        # Degree level is not passed: professor records describe research, not degree offerings,
        # and inventing a degree claim here would be exactly the fabricated detail W6 forbids.
        rows = search_faculty(db, interests, limit=6, enforce_policy=enforce_policy, scope=scope,
                              academic_year=scope.academic_year or year,
                              country=scope.call_kwargs()["country"])
        data["faculty"] = rows
        citations += [{"label": r["name"], "url": r["profile_url"]} for r in rows if r.get("profile_url")]

    if year is not None:
        data["academic_year"] = year.as_dict()
    text = _render(intent, data)
    return {"intent": intent, "answer": text, "data": data, "citations": citations[:10],
            "grounded": bool(data), "disclaimer": UNAVAILABLE_MESSAGE if not data else None,
            "scope": {"status": scope.status, "reason": None, "clarify_slot": None}}


def _render(intent: str, data: Dict[str, Any]) -> str:
    if data.get("programs"):
        lines = ["Here is what the verified programme database currently holds:"]
        for p in data["programs"][:5]:
            bits = [f"- {p['university']} ({p['country']}) - {p['name']}"]
            if p.get("tuition_annual"):
                bits.append(f"  Tuition: {p['currency']} {p['tuition_annual']:,.0f}/year")
            if p.get("min_cgpa"):
                bits.append(f"  Minimum CGPA: {p['min_cgpa']}")
            if p.get("ielts_overall"):
                bits.append(f"  IELTS: {p['ielts_overall']}")
            if p.get("intakes"):
                bits.append(f"  Intakes: {', '.join(p['intakes'][:3])}")
            lines.append("\n".join(bits))
        lines.append("Every figure above is linked to its official source with a verification date.")
        return "\n".join(lines)
    if data.get("scholarships"):
        lines = ["Scholarships currently recorded with published eligibility criteria:"]
        for s in data["scholarships"][:5]:
            amount = f"{s['percentage']}% tuition" if s.get("percentage") else (
                f"{s['currency']} {s['amount_value']:,.0f}" if s.get("amount_value") else "Variable")
            lines.append(f"- {s['name']} ({amount}) - deadline {s.get('deadline') or 'see source'}")
        lines.append("Eligibility statements describe published criteria only - they are not an award prediction.")
        return "\n".join(lines)
    if data.get("faculty"):
        lines = ["Faculty members with documented research overlap:"]
        for f in data["faculty"][:5]:
            lines.append(f"- {f['name']} ({f['university']}) - {', '.join(f['research_interests'][:4])}")
        lines.append("Contact details are shown only where published on an official or public professional source.")
        return "\n".join(lines)
    return UNAVAILABLE_MESSAGE


def _guess_degree(text: str) -> str:
    low = text.lower()
    if any(k in low for k in ["phd", "doctor", "supervisor"]):
        return "PHD"
    if any(k in low for k in ["master", "msc", "ms ", "mba", "postgrad"]):
        return "MASTER"
    if any(k in low for k in ["bachelor", "bsc", "undergrad"]):
        return "BACHELOR"
    return ""


def _guess_subject(text: str) -> str:
    low = text.lower()
    for s in ["artificial intelligence", "data science", "computer science", "business analytics", "public health",
              "nursing", "mechanical", "civil", "electrical", "cyber security", "finance", "mba", "economics"]:
        if s in low:
            return s
    return ""


def _guess_country(db: Session, text: str) -> str:
    """The destination named in the question, resolved through the destination catalogue.

    Uses the same resolver as the public filters, so "UK", "Scotland" and "America" all work and
    an unknown country never silently becomes a different one.
    """
    low = (text or "").lower()
    rows = list(db.execute(select(Country).order_by(func.length(Country.name).desc())).scalars())
    for c in rows:
        if c.name.lower() in low:
            return c.name
    for alias in ("scotland", "america", "usa", "uae", "uk", "britain"):
        if alias in low:
            found = destinations.resolve_destination(db, alias)
            if found is not None:
                return found.name
    return ""


def _guess_interests(text: str) -> List[str]:
    low = text.lower()
    pool = ["nlp", "llm", "rag", "healthcare ai", "clinical nlp", "machine learning", "computer vision",
            "robotics", "cyber security", "bioinformatics", "renewable energy", "generative ai"]
    return [p for p in pool if p in low]
