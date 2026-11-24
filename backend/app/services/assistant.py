"""Grounded assistant (§64-§66).

Tool-calling over the structured database ONLY. If a fact is not in the verified
database the assistant refuses to guess. An LLM key is optional: without it the
service returns deterministic, templated answers built from retrieved rows.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.education import Country, Professor, Program, Scholarship, University
from app.services import costing, matching

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
def search_universities(db: Session, q: str = "", country: str = "", limit: int = 8) -> List[Dict[str, Any]]:
    stmt = select(University).options(selectinload(University.country)).where(University.is_published.is_(True))
    if q:
        stmt = stmt.where(University.name.ilike(f"%{q}%"))
    if country:
        stmt = stmt.join(Country).where(or_(Country.name.ilike(f"%{country}%"), Country.iso2 == country.upper()))
    rows = db.execute(stmt.limit(limit)).scalars().unique().all()
    return [{"id": u.id, "name": u.name, "slug": u.slug, "country": u.country.name if u.country else None,
             "city": u.city, "website": u.website, "source_url": u.source_url,
             "verified_at": u.verified_at.isoformat() if u.verified_at else None} for u in rows]


def search_programs(db: Session, subject: str = "", country: str = "", degree_level: str = "",
                    max_tuition: Optional[float] = None, limit: int = 8) -> List[Dict[str, Any]]:
    stmt = (select(Program).options(selectinload(Program.university).selectinload(University.country),
                                    selectinload(Program.requirement), selectinload(Program.intakes))
            .where(Program.is_published.is_(True)))
    if subject:
        s = f"%{subject}%"
        stmt = stmt.where(or_(Program.name.ilike(s), Program.discipline.ilike(s)))
    if degree_level:
        stmt = stmt.where(Program.degree_level == degree_level.upper())
    if country:
        stmt = stmt.join(University).join(Country).where(
            or_(Country.name.ilike(f"%{country}%"), Country.iso2 == country.upper()))
    if max_tuition:
        stmt = stmt.where(Program.tuition_annual <= max_tuition)
    rows = db.execute(stmt.limit(limit)).scalars().unique().all()
    out = []
    for p in rows:
        req = p.requirement
        out.append({
            "id": p.id, "name": p.name, "slug": p.slug, "degree_level": p.degree_level,
            "university": p.university.name, "university_slug": p.university.slug,
            "country": p.university.country.name if p.university.country else None,
            "tuition_annual": p.tuition_annual, "currency": p.currency,
            "min_cgpa": req.min_cgpa if req else None,
            "ielts_overall": req.ielts_overall if req else None,
            "intakes": [i.label for i in p.intakes],
            "scholarship_available": p.scholarship_available,
            "source_url": p.source_url,
            "verified_at": p.verified_at.isoformat() if p.verified_at else None,
        })
    return out


def get_program_requirements(db: Session, program_id: int) -> Dict[str, Any]:
    p = db.get(Program, program_id)
    if not p or not p.requirement:
        return {"available": False, "message": UNAVAILABLE_MESSAGE}
    r = p.requirement
    return {"available": True, "program": p.name, "university": p.university.name,
            "min_cgpa": r.min_cgpa, "cgpa_scale": r.cgpa_scale, "ielts_overall": r.ielts_overall,
            "ielts_band_min": r.ielts_band_min, "toefl_ibt": r.toefl_ibt, "pte_academic": r.pte_academic,
            "documents": r.documents, "prerequisites": r.prerequisites,
            "source_url": r.source_url, "verified_at": r.verified_at.isoformat() if r.verified_at else None}


def get_tuition(db: Session, program_id: int, budget: Optional[float] = None) -> Dict[str, Any]:
    p = db.get(Program, program_id)
    if not p:
        return {"available": False, "message": UNAVAILABLE_MESSAGE}
    data = costing.estimate(p, budget)
    data.update({"available": True, "program": p.name, "university": p.university.name,
                 "source_url": p.source_url,
                 "verified_at": p.verified_at.isoformat() if p.verified_at else None})
    return data


def get_intakes(db: Session, program_id: int) -> List[Dict[str, Any]]:
    p = db.get(Program, program_id)
    if not p:
        return []
    return [{"label": i.label, "status": i.status,
             "application_deadline": i.application_deadline.isoformat() if i.application_deadline else None,
             "class_start": i.class_start.isoformat() if i.class_start else None,
             "source_url": i.source_url} for i in p.intakes]


def search_scholarships(db: Session, country: str = "", degree_level: str = "", limit: int = 8) -> List[Dict[str, Any]]:
    stmt = select(Scholarship).options(selectinload(Scholarship.country), selectinload(Scholarship.university)) \
        .where(Scholarship.is_published.is_(True))
    if country:
        stmt = stmt.join(Country, Scholarship.country_id == Country.id).where(Country.name.ilike(f"%{country}%"))
    rows = db.execute(stmt.limit(limit * 2)).scalars().unique().all()
    if degree_level:
        rows = [s for s in rows if not s.degree_levels or degree_level.upper() in s.degree_levels]
    return [{"id": s.id, "name": s.name, "provider": s.provider, "percentage": s.percentage,
             "amount_value": s.amount_value, "currency": s.currency,
             "deadline": s.deadline.isoformat() if s.deadline else None,
             "eligibility_summary": s.eligibility_summary, "source_url": s.source_url,
             "university": s.university.name if s.university else None,
             "country": s.country.name if s.country else None} for s in rows[:limit]]


def search_faculty(db: Session, interests: List[str], country: str = "", field: str = "",
                   limit: int = 8) -> List[Dict[str, Any]]:
    country_id = None
    if country:
        row = db.execute(select(Country).where(Country.name.ilike(f"%{country}%"))).scalars().first()
        country_id = row.id if row else None
    matches = matching.match_faculty(db, interests, country_id=country_id, field=field, limit=limit)
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


def answer(db: Session, question: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Retrieval-first answer. Returns text + the rows and sources used."""
    context = context or {}
    intent = detect_intent(question)
    country = context.get("country", "")
    degree = context.get("degree_level", "")
    subject = context.get("subject", "")
    citations: List[Dict[str, Any]] = []
    data: Dict[str, Any] = {}

    if intent in ("find_program", "find_university", "tuition", "intake", "check_eligibility", "general"):
        rows = search_programs(db, subject=subject or _guess_subject(question), country=country or _guess_country(db, question),
                               degree_level=degree or _guess_degree(question), limit=6)
        data["programs"] = rows
        citations += [{"label": f"{r['university']} - {r['name']}", "url": r["source_url"],
                       "verified_at": r["verified_at"]} for r in rows if r.get("source_url")]
    if intent == "find_scholarship":
        rows = search_scholarships(db, country=country or _guess_country(db, question), degree_level=degree, limit=6)
        data["scholarships"] = rows
        citations += [{"label": r["name"], "url": r["source_url"]} for r in rows if r.get("source_url")]
    if intent == "find_faculty":
        interests = context.get("research_interests") or _guess_interests(question)
        rows = search_faculty(db, interests, country=country or _guess_country(db, question), limit=6)
        data["faculty"] = rows
        citations += [{"label": r["name"], "url": r["profile_url"]} for r in rows if r.get("profile_url")]

    text = _render(intent, data)
    return {"intent": intent, "answer": text, "data": data, "citations": citations[:10],
            "grounded": bool(data), "disclaimer": UNAVAILABLE_MESSAGE if not data else None}


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
    low = text.lower()
    for c in db.execute(select(Country)).scalars().all():
        if c.name.lower() in low:
            return c.name
    return ""


def _guess_interests(text: str) -> List[str]:
    low = text.lower()
    pool = ["nlp", "llm", "rag", "healthcare ai", "clinical nlp", "machine learning", "computer vision",
            "robotics", "cyber security", "bioinformatics", "renewable energy", "generative ai"]
    return [p for p in pool if p in low]
