"""Personalized degree-specific brochure generator (§19-§22/§38)."""
from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.crm import Brochure
from app.models.education import Professor, Program, Scholarship, University
from app.services import costing
from app.services.eligibility import evaluate


def _source(obj) -> Dict[str, Any]:
    return {
        "source_url": getattr(obj, "source_url", None),
        "source_type": getattr(obj, "source_type", None),
        "verified_at": getattr(obj, "verified_at", None).isoformat() if getattr(obj, "verified_at", None) else None,
        "confidence": getattr(obj, "confidence", "UNVERIFIED"),
    }


def build_payload(db: Session, program: Program, student: Dict[str, Any],
                  target_intake: Optional[str] = None) -> Dict[str, Any]:
    uni: University = program.university
    req = program.requirement
    elig = evaluate(student, program, req)
    cost = costing.estimate(program, student.get("budget_per_year"), student.get("budget_currency", "USD"))
    intakes = sorted(program.intakes, key=lambda i: (i.year or 9999, i.month or 99))
    chosen = next((i for i in intakes if target_intake and target_intake.lower() in (i.label or "").lower()), None)
    scholarships = db.execute(select(Scholarship).where(
        Scholarship.is_published.is_(True),
        or_(Scholarship.university_id == uni.id, Scholarship.country_id == uni.country_id),
    )).scalars().all()
    scholarships = [s for s in scholarships if not s.degree_levels or program.degree_level in s.degree_levels]

    payload: Dict[str, Any] = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "degree_level": program.degree_level,
            "prepared_for": student.get("full_name") or "Prospective student",
            "target_intake": target_intake or (chosen.label if chosen else None),
            "disclaimer": ("All figures and requirements are taken from the cited sources on the dates shown. "
                           "Universities may update their requirements at any time - always confirm with the "
                           "official source or your counselor before applying or paying any fee."),
        },
        "cover": {
            "title": "Personalized University Guide",
            "program": program.name,
            "award": program.award,
            "university": uni.name,
            "country": uni.country.name if uni.country else None,
            "flag": uni.country.flag_emoji if uni.country else None,
            "logo": uni.logo_url,
            "hero": uni.hero_image or (uni.country.hero_image if uni.country else None),
        },
        "university": {
            "name": uni.name, "city": uni.city, "campus": uni.campus,
            "established_year": uni.established_year, "overview": uni.overview,
            "accreditation": uni.accreditation, "website": uni.website,
            "application_portal": uni.application_portal,
            "students": uni.student_count, "international_students": uni.international_students,
            "rankings": [{"provider": r.provider, "edition_year": r.edition_year, "overall": r.rank_overall,
                          "national": r.rank_national, "subject": r.subject, "subject_rank": r.rank_subject,
                          **_source(r)} for r in uni.rankings],
            **_source(uni),
        },
        "program": {
            "name": program.name, "award": program.award, "degree_level": program.degree_level,
            "duration_months": program.duration_months, "credits": program.credits,
            "department": program.department.name if program.department else None,
            "faculty_school": (program.department.faculty.name
                               if program.department and program.department.faculty else None),
            "study_mode": program.study_mode, "language": program.language, "campus": program.campus,
            "specializations": program.specializations, "overview": program.overview,
            "career_outcomes": program.career_outcomes, "application_procedure": program.application_procedure,
            **_source(program),
        },
        "eligibility": elig,
        "requirements": _requirements_block(program, req),
        "costs": cost,
        "scholarships": [{
            "name": s.name, "provider": s.provider, "amount_type": s.amount_type, "percentage": s.percentage,
            "amount_value": s.amount_value, "currency": s.currency, "covers_tuition": s.covers_tuition,
            "stipend": s.stipend, "eligibility_summary": s.eligibility_summary,
            "deadline": s.deadline.isoformat() if s.deadline else None,
            "application_method": s.application_method, **_source(s),
        } for s in scholarships],
        "intakes": [{
            "label": i.label, "status": i.status,
            "application_opens": i.application_opens.isoformat() if i.application_opens else None,
            "application_deadline": i.application_deadline.isoformat() if i.application_deadline else None,
            "class_start": i.class_start.isoformat() if i.class_start else None,
            "notes": i.notes, **_source(i),
        } for i in intakes],
        "student_life": {"university": uni.student_life, "accommodation": uni.accommodation,
                         "country": uni.country.student_life if uni.country else None},
        "sources": _collect_sources(program, req, uni, scholarships, intakes),
    }

    if program.degree_level == "PHD":
        profs = db.execute(select(Professor).options(selectinload(Professor.publications),
                                                     selectinload(Professor.department))
                           .where(Professor.university_id == uni.id)).scalars().unique().all()
        interests = [i.lower() for i in (student.get("research_interests") or [])]
        faculty_block = []
        for p in profs:
            pool = " ".join((p.research_interests or []) + (p.research_keywords or [])).lower()
            matched = [i for i in interests if i in pool]
            if interests and not matched:
                continue
            faculty_block.append({
                "name": p.full_name, "title": p.title, "photo": p.photo_url,
                "department": p.department.name if p.department else None,
                "research_interests": p.research_interests, "matched_interests": matched,
                "lab": p.lab_name, "lab_url": p.lab_url, "profile_url": p.profile_url,
                "google_scholar": p.google_scholar, "orcid": p.orcid,
                "email": p.email if p.email_status in ("OFFICIAL_UNIVERSITY_EMAIL", "PUBLIC_PROFESSIONAL_EMAIL") else None,
                "email_status": p.email_status, "email_source_url": p.email_source_url,
                "accepting_phd": p.accepting_phd, "funding_notes": p.funding_notes,
                "publications": [{"title": pub.title, "venue": pub.venue, "year": pub.year, "url": pub.url}
                                 for pub in sorted(p.publications, key=lambda x: -(x.year or 0))[:5]],
                **_source(p),
            })
        payload["phd"] = {
            "research_areas": program.research_areas,
            "faculty": faculty_block[:8],
            "funding": program.career_outcomes,
            "notes": ("Faculty contact details are shown only when published on an official or public professional "
                      "source. Where an email is marked unavailable, contact the department through the official "
                      "channel listed above."),
        }
    return payload


def _requirements_block(program: Program, req) -> Dict[str, Any]:
    if not req:
        return {"available": False,
                "message": "Verified requirement data is currently unavailable for this program. "
                           "Please confirm with the university or a counselor before applying."}
    block = {
        "available": True,
        "academic": {
            "min_qualification": req.min_qualification, "min_cgpa": req.min_cgpa, "cgpa_scale": req.cgpa_scale,
            "relevant_majors": req.relevant_majors, "required_subjects": req.required_subjects,
            "prerequisites": req.prerequisites, "degree_equivalency": req.degree_equivalency,
            "study_gap_policy": req.study_gap_policy, "work_experience_years": req.work_experience_years,
            "research_experience": req.research_experience,
        },
        "english": {"ielts_overall": req.ielts_overall, "ielts_band_min": req.ielts_band_min,
                    "toefl_ibt": req.toefl_ibt, "pte_academic": req.pte_academic, "duolingo": req.duolingo,
                    "cambridge": req.cambridge, "moi_accepted": req.moi_accepted,
                    "other_accepted_tests": req.other_english_tests},
        "standardised_tests": {"gre_required": req.gre_required, "gre_score": req.gre_score,
                               "gmat_required": req.gmat_required, "gmat_score": req.gmat_score},
        "documents": req.documents,
        "notes": req.notes,
        **_source(req),
    }
    if program.degree_level == "MASTER":
        block["masters_specific"] = {"bachelor_prerequisite": req.min_qualification,
                                     "relevant_major": req.relevant_majors,
                                     "work_experience_years": req.work_experience_years,
                                     "research_experience": req.research_experience}
    if program.degree_level == "PHD":
        block["doctoral_specific"] = {"research_proposal": "Required", "writing_sample": "Programme dependent",
                                      "references": "Typically 2-3 academic references",
                                      "supervisor_contact": "Recommended before applying"}
    return block


def _collect_sources(program, req, uni, scholarships, intakes) -> List[Dict[str, Any]]:
    items = []
    def add(label, obj):
        url = getattr(obj, "source_url", None)
        if url:
            items.append({"label": label, "url": url, "source_type": getattr(obj, "source_type", None),
                          "verified_at": getattr(obj, "verified_at", None).isoformat() if getattr(obj, "verified_at", None) else None,
                          "confidence": getattr(obj, "confidence", "UNVERIFIED")})
    add(f"{uni.name} - institution record", uni)
    add(f"{program.name} - programme page", program)
    if req:
        add(f"{program.name} - entry requirements", req)
    for i in intakes:
        add(f"{program.name} - {i.label} intake", i)
    for s in scholarships:
        add(f"Scholarship: {s.name}", s)
    seen, out = set(), []
    for i in items:
        if i["url"] in seen:
            continue
        seen.add(i["url"])
        out.append(i)
    return out


def create(db: Session, program: Program, student: Dict[str, Any], target_intake: Optional[str] = None,
           generated_by: str = "WEBSITE", student_profile_id: Optional[int] = None) -> Brochure:
    payload = build_payload(db, program, student, target_intake)
    row = Brochure(
        token=secrets.token_urlsafe(12),
        student_name=student.get("full_name") or "Prospective student",
        student_profile_id=student_profile_id,
        program_id=program.id,
        degree_level=program.degree_level,
        target_intake=target_intake,
        payload=payload,
        generated_by=generated_by,
    )
    prev = db.execute(select(Brochure).where(Brochure.program_id == program.id,
                                             Brochure.student_name == row.student_name)).scalars().all()
    row.version = len(prev) + 1
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
