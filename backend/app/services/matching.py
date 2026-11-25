"""University / program / scholarship / faculty matching (§28/§35/§26)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.education import Country, Professor, Program, Scholarship, University
from app.services import costing
from app.services.eligibility import DOES_NOT_MEET, MEETS, PARTIALLY, REVIEW, UNAVAILABLE, evaluate

_WEIGHTS = {"academic": 25, "english": 20, "budget": 15, "subject": 15, "intake": 10, "scholarship": 8, "research": 7}


def _loaded_programs(db: Session, filters: Dict[str, Any]) -> List[Program]:
    stmt = (select(Program)
            .options(selectinload(Program.university).selectinload(University.country),
                     selectinload(Program.requirement),
                     selectinload(Program.intakes),
                     selectinload(Program.department))
            .where(Program.is_published.is_(True)))
    if filters.get("degree_level"):
        stmt = stmt.where(Program.degree_level == filters["degree_level"].upper())
    if filters.get("country_ids"):
        stmt = stmt.join(University).where(University.country_id.in_(filters["country_ids"]))
    if filters.get("subject"):
        s = f"%{filters['subject'].lower()}%"
        stmt = stmt.where(or_(Program.name.ilike(s), Program.discipline.ilike(s)))
    return list(db.execute(stmt).scalars().unique())


def match_programs(db: Session, student: Dict[str, Any], filters: Optional[Dict[str, Any]] = None,
                   limit: int = 20) -> Dict[str, Any]:
    filters = dict(filters or {})
    pref_countries = student.get("preferred_countries") or []
    country_ids: List[int] = []
    if filters.get("country_ids"):
        country_ids = filters["country_ids"]
    elif pref_countries:
        rows = db.execute(select(Country.id).where(or_(
            Country.name.in_(pref_countries),
            Country.slug.in_([str(c).lower().replace(" ", "-") for c in pref_countries]),
            Country.iso2.in_([str(c).upper() for c in pref_countries]),
        ))).scalars().all()
        country_ids = list(rows)
    if country_ids:
        filters["country_ids"] = country_ids
    filters.setdefault("degree_level", student.get("target_degree_level"))
    filters.setdefault("subject", student.get("target_discipline"))

    programs = _loaded_programs(db, filters)
    # The subject filter is deliberately "soft": if the exact discipline returns a thin
    # list we widen the candidate pool and let the relevance score do the ranking, so a
    # student never sees an empty match page just because of a wording difference.
    if filters.get("subject") and len(programs) < max(limit, 8):
        widened = dict(filters)
        widened.pop("subject", None)
        seen = {p.id for p in programs}
        programs += [p for p in _loaded_programs(db, widened) if p.id not in seen]

    budget = student.get("budget_per_year")
    target_intake = (student.get("target_intake") or filters.get("intake") or "").lower().strip()
    interests = [i.lower() for i in (student.get("research_interests") or [])]

    results: List[Dict[str, Any]] = []
    stats = {"total": 0, "within_budget": 0, "academic_ok": 0, "with_scholarship": 0, "intake_available": 0, "research_aligned": 0}

    for p in programs:
        elig = evaluate(student, p, p.requirement)
        cost = costing.estimate(p, budget, student.get("budget_currency", "USD"))
        reasons: List[Dict[str, str]] = []
        score = 0.0

        academic_checks = [c for c in elig["checks"] if c["criterion"] in ("Minimum CGPA", "Relevant academic background")]
        academic_state = _worst([c["status"] for c in academic_checks]) if academic_checks else UNAVAILABLE
        score += _points(_WEIGHTS["academic"], academic_state)
        # The explanation must describe the check that *produced* the verdict. Taking the first
        # check instead of the deciding one printed contradictions like
        # "INFORMATION_UNAVAILABLE - Meets the stated minimum.", where the status came from the
        # background check and the sentence came from the CGPA check.
        deciding = next((c for c in academic_checks if c["status"] == academic_state), None)
        if deciding is None:
            academic_detail = "Not published"
        else:
            academic_detail = deciding["note"] or f"{deciding['provided']} vs {deciding['required']}"
            # Name the criterion when more than one was considered, so "not stated by the
            # university" is not mistaken for a comment on the student's grades.
            if len(academic_checks) > 1:
                academic_detail = f"{deciding['criterion']}: {academic_detail}"
        reasons.append({"factor": "Academic requirement", "status": academic_state,
                        "detail": academic_detail})
        if academic_state in (MEETS, PARTIALLY):
            stats["academic_ok"] += 1

        eng = next((c for c in elig["checks"] if c["criterion"] == "English language requirement"), None)
        eng_state = eng["status"] if eng else UNAVAILABLE
        score += _points(_WEIGHTS["english"], eng_state)
        reasons.append({"factor": "English requirement", "status": eng_state,
                        "detail": f"{eng['provided']} vs required {eng['required']}" if eng else "Not published"})

        if budget:
            bstate = {"WITHIN_STATED_BUDGET": MEETS, "SLIGHTLY_ABOVE_BUDGET": PARTIALLY,
                      "ABOVE_STATED_BUDGET": DOES_NOT_MEET}.get(cost["budget_status"], UNAVAILABLE)
            score += _points(_WEIGHTS["budget"], bstate)
            reasons.append({"factor": "Budget", "status": bstate,
                            "detail": f"Estimated {cost['currency']} {cost['estimated_annual_total']:,.0f}/year vs stated budget {cost['currency']} {budget:,.0f}"})
            if bstate == MEETS:
                stats["within_budget"] += 1
        else:
            score += _WEIGHTS["budget"] * 0.4
            reasons.append({"factor": "Budget", "status": UNAVAILABLE, "detail": "No budget provided"})

        subject = (student.get("target_discipline") or filters.get("subject") or "").lower()
        if subject:
            hay = " ".join([p.name.lower(), (p.discipline or "").lower(), " ".join(p.subject_tags or []).lower()])
            sstate = MEETS if subject in hay else (PARTIALLY if any(w in hay for w in subject.split() if len(w) > 3) else REVIEW)
            score += _points(_WEIGHTS["subject"], sstate)
            reasons.append({"factor": "Program relevance", "status": sstate, "detail": f"Target field: {student.get('target_discipline') or filters.get('subject')}"})
        else:
            score += _WEIGHTS["subject"] * 0.5

        intake_match = None
        if target_intake:
            intake_match = next((i for i in p.intakes if target_intake in (i.label or "").lower()), None)
            istate = MEETS if intake_match else REVIEW
            score += _points(_WEIGHTS["intake"], istate)
            reasons.append({"factor": "Target intake", "status": istate,
                            "detail": (f"{intake_match.label} - deadline {intake_match.application_deadline}" if intake_match
                                       else f"'{student.get('target_intake')}' not listed for this program")})
            if intake_match:
                stats["intake_available"] += 1
        else:
            score += _WEIGHTS["intake"] * 0.5
            if p.intakes:
                stats["intake_available"] += 1

        schol = db.execute(select(Scholarship).where(
            Scholarship.is_published.is_(True),
            or_(Scholarship.university_id == p.university_id,
                Scholarship.country_id == p.university.country_id))).scalars().all()
        schol = [s for s in schol if not s.degree_levels or p.degree_level in s.degree_levels]
        if schol:
            score += _WEIGHTS["scholarship"]
            stats["with_scholarship"] += 1
            reasons.append({"factor": "Scholarship opportunity", "status": MEETS,
                            "detail": f"{len(schol)} scholarship(s) linked to this program/country"})

        research_hits: List[str] = []
        if interests and p.degree_level == "PHD":
            pool = " ".join((p.research_areas or []) + (p.subject_tags or [])).lower()
            research_hits = [i for i in interests if i in pool]
            rstate = MEETS if research_hits else REVIEW
            score += _points(_WEIGHTS["research"], rstate)
            if research_hits:
                stats["research_aligned"] += 1
                reasons.append({"factor": "Research alignment", "status": MEETS,
                                "detail": "Documented overlap: " + ", ".join(research_hits)})

        results.append({
            "program": p,
            "score": round(min(score, 100.0), 1),
            "eligibility": elig,
            "cost": cost,
            "reasons": reasons,
            "scholarships": schol[:4],
            "intake": intake_match,
            "research_hits": research_hits,
        })

    stats["total"] = len(results)
    results.sort(key=lambda r: (-r["score"], r["program"].tuition_annual or 1e9))
    return {"stats": stats, "results": results[:limit]}


def _points(weight: float, status: str) -> float:
    return {MEETS: weight, PARTIALLY: weight * 0.6, REVIEW: weight * 0.45,
            UNAVAILABLE: weight * 0.35, DOES_NOT_MEET: 0.0}.get(status, 0.0)


def _worst(states: List[str]) -> str:
    order = [MEETS, PARTIALLY, REVIEW, UNAVAILABLE, DOES_NOT_MEET]
    return max(states, key=lambda s: order.index(s)) if states else UNAVAILABLE


# ---------------------------------------------------------------- scholarships
def match_scholarships(db: Session, student: Dict[str, Any], limit: int = 25) -> List[Dict[str, Any]]:
    rows = db.execute(select(Scholarship).options(
        selectinload(Scholarship.university), selectinload(Scholarship.country)
    ).where(Scholarship.is_published.is_(True))).scalars().all()
    nationality = (student.get("nationality") or "Bangladesh").lower()
    level = (student.get("target_degree_level") or "").upper()
    out: List[Dict[str, Any]] = []
    for s in rows:
        checks = []
        if s.degree_levels:
            ok = (not level) or level in s.degree_levels
            checks.append({"criterion": "Degree level", "status": MEETS if ok else DOES_NOT_MEET,
                           "required": ", ".join(s.degree_levels), "provided": level or "Not provided"})
        if s.eligible_nationalities:
            ok = any(n.lower() in ("all", "international", nationality) for n in s.eligible_nationalities)
            checks.append({"criterion": "Nationality", "status": MEETS if ok else DOES_NOT_MEET,
                           "required": ", ".join(s.eligible_nationalities), "provided": student.get("nationality", "Bangladesh")})
        if s.min_cgpa is not None:
            sc = student.get("cgpa")
            scale = student.get("cgpa_scale") or 4.0
            norm = (sc / scale) * (s.cgpa_scale or 4.0) if sc else None
            if norm is None:
                st = UNAVAILABLE
            elif norm + 1e-9 >= s.min_cgpa:
                st = MEETS
            else:
                st = DOES_NOT_MEET
            checks.append({"criterion": "Minimum CGPA", "status": st,
                           "required": f"{s.min_cgpa}/{s.cgpa_scale or 4.0}", "provided": sc or "Not provided"})
        if s.min_ielts is not None:
            sc = student.get("ielts_overall")
            st = UNAVAILABLE if sc is None else (MEETS if sc + 1e-9 >= s.min_ielts else DOES_NOT_MEET)
            checks.append({"criterion": "English (IELTS)", "status": st, "required": s.min_ielts, "provided": sc or "Not provided"})
        overall = _worst([c["status"] for c in checks]) if checks else UNAVAILABLE
        statement = {
            MEETS: "The stated eligibility criteria appear to be met.",
            PARTIALLY: "Most stated criteria appear to be met; some need review.",
            REVIEW: "Stated criteria require counselor review.",
            UNAVAILABLE: "Not enough profile information to evaluate the stated criteria.",
            DOES_NOT_MEET: "One or more stated minimum criteria do not appear to be met.",
        }[overall]
        out.append({"scholarship": s, "status": overall, "checks": checks, "statement": statement})
    rank = {MEETS: 0, PARTIALLY: 1, REVIEW: 2, UNAVAILABLE: 3, DOES_NOT_MEET: 4}
    out.sort(key=lambda r: rank[r["status"]])
    return out[:limit]


# ---------------------------------------------------------------- faculty / research
def match_faculty(db: Session, interests: List[str], country_id: Optional[int] = None,
                  university_id: Optional[int] = None, field: Optional[str] = None,
                  limit: int = 20) -> List[Dict[str, Any]]:
    stmt = select(Professor).options(
        selectinload(Professor.university).selectinload(University.country),
        selectinload(Professor.department),
        selectinload(Professor.publications))
    if university_id:
        stmt = stmt.where(Professor.university_id == university_id)
    if country_id:
        stmt = stmt.join(University).where(University.country_id == country_id)
    rows = list(db.execute(stmt).scalars().unique())
    terms = [t.strip().lower() for t in interests if t and t.strip()]
    field_l = (field or "").lower()
    out: List[Dict[str, Any]] = []
    for prof in rows:
        pool = " ".join((prof.research_interests or []) + (prof.research_keywords or []) +
                        [prof.bio or "", prof.lab_name or "", (prof.department.name if prof.department else "")]).lower()
        if field_l and field_l not in pool and field_l not in (prof.department.name.lower() if prof.department else ""):
            continue
        matched, evidence = [], []
        for t in terms:
            if t in pool:
                matched.append(t)
                for pub in prof.publications:
                    text = (pub.title + " " + " ".join(pub.keywords or [])).lower()
                    if t in text:
                        evidence.append({"type": "publication", "title": pub.title, "venue": pub.venue,
                                         "year": pub.year, "url": pub.url})
                        break
        if terms and not matched:
            continue
        if prof.lab_name and matched:
            evidence.append({"type": "lab", "title": prof.lab_name, "url": prof.lab_url})
        score = round(100 * len(matched) / max(len(terms), 1), 1) if terms else 50.0
        out.append({"professor": prof, "matched_interests": matched, "evidence": evidence[:5], "score": score})
    out.sort(key=lambda r: (-r["score"], r["professor"].full_name))
    return out[:limit]
