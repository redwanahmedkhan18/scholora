"""Eligibility engine (§33/§34).

Deterministic, explainable, never probabilistic. Output is one of:
MEETS | DOES_NOT_MEET | PARTIALLY_MEETS | REQUIRES_REVIEW | INFORMATION_UNAVAILABLE
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.education import Program, ProgramRequirement

MEETS = "MEETS"
DOES_NOT_MEET = "DOES_NOT_MEET"
PARTIALLY = "PARTIALLY_MEETS"
REVIEW = "REQUIRES_REVIEW"
UNAVAILABLE = "INFORMATION_UNAVAILABLE"

_ORDER = {MEETS: 0, PARTIALLY: 1, REVIEW: 2, UNAVAILABLE: 3, DOES_NOT_MEET: 4}


def _check(label: str, status: str, required: Any, provided: Any, note: str = "") -> Dict[str, Any]:
    return {
        "criterion": label,
        "status": status,
        "required": required if required not in (None, "") else "Not published",
        "provided": provided if provided not in (None, "") else "Not provided",
        "note": note,
    }


def _normalise_cgpa(value: Any, scale: Any) -> Optional[float]:
    """Put a CGPA on a 4.0 scale.

    Accepts strings as well as numbers: the CGPA_SCALES vocabulary is string-typed ("4",
    "5", "10", "100") even though the columns are floats, so a caller assembling a student
    dict by hand can legitimately hold either. Anything unparseable yields None, which the
    caller reports as INFORMATION_UNAVAILABLE rather than guessing."""
    if value is None or value == "":
        return None
    try:
        value = float(value)
        scale = float(scale) if scale not in (None, "") else 4.0
    except (TypeError, ValueError):
        return None
    if scale <= 0:
        return None
    return round((value / scale) * 4.0, 3)


def evaluate(student: Dict[str, Any], program: Program, req: Optional[ProgramRequirement]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    if req is None:
        return {
            "overall": UNAVAILABLE,
            "checks": [_check("Published requirements", UNAVAILABLE, None, None,
                              "Verified requirement data is not yet published for this program.")],
            "summary": "Verified requirement data is currently unavailable. Please confirm with the university or a counselor.",
        }

    # ---- Academic: CGPA ----
    s_cgpa = _normalise_cgpa(student.get("cgpa"), student.get("cgpa_scale"))
    r_cgpa = _normalise_cgpa(req.min_cgpa, req.cgpa_scale)
    if r_cgpa is None:
        checks.append(_check("Minimum CGPA", UNAVAILABLE, None, student.get("cgpa"),
                             "No published minimum CGPA for this program."))
    elif s_cgpa is None:
        checks.append(_check("Minimum CGPA", UNAVAILABLE, f"{req.min_cgpa}/{req.cgpa_scale or 4.0}", None,
                             "Add your CGPA to evaluate this criterion."))
    elif s_cgpa + 1e-9 >= r_cgpa:
        checks.append(_check("Minimum CGPA", MEETS, f"{req.min_cgpa}/{req.cgpa_scale or 4.0}",
                             f"{student.get('cgpa')}/{student.get('cgpa_scale') or 4.0}",
                             "Meets the stated minimum."))
    elif s_cgpa >= r_cgpa - 0.15:
        checks.append(_check("Minimum CGPA", PARTIALLY, f"{req.min_cgpa}/{req.cgpa_scale or 4.0}",
                             f"{student.get('cgpa')}/{student.get('cgpa_scale') or 4.0}",
                             "Marginally below the stated minimum - many universities review such cases individually."))
    else:
        checks.append(_check("Minimum CGPA", DOES_NOT_MEET, f"{req.min_cgpa}/{req.cgpa_scale or 4.0}",
                             f"{student.get('cgpa')}/{student.get('cgpa_scale') or 4.0}",
                             "Below the stated minimum."))

    # ---- Academic: qualification / relevant major ----
    if req.relevant_majors:
        major = (student.get("major") or "").lower()
        if not major:
            checks.append(_check("Relevant academic background", UNAVAILABLE, ", ".join(req.relevant_majors), None))
        elif any(m.lower() in major or major in m.lower() for m in req.relevant_majors):
            checks.append(_check("Relevant academic background", MEETS, ", ".join(req.relevant_majors), student.get("major")))
        else:
            checks.append(_check("Relevant academic background", REVIEW, ", ".join(req.relevant_majors),
                                 student.get("major"), "Background differs from the listed majors - counselor review advised."))

    # ---- English ----
    english_status, english_detail = _english(student, req)
    checks.append(english_detail)

    # ---- Prerequisites ----
    if req.prerequisites:
        done = [c.lower() for c in (student.get("completed_subjects") or [])]
        missing = [p for p in req.prerequisites if not any(p.lower() in d or d in p.lower() for d in done)]
        if not done:
            checks.append(_check("Prerequisite courses", UNAVAILABLE, ", ".join(req.prerequisites), None,
                                 "List completed subjects to evaluate prerequisites."))
        elif missing:
            checks.append(_check("Prerequisite courses", PARTIALLY, ", ".join(req.prerequisites),
                                 ", ".join(student.get("completed_subjects") or []),
                                 f"Not evidenced: {', '.join(missing)}"))
        else:
            checks.append(_check("Prerequisite courses", MEETS, ", ".join(req.prerequisites),
                                 ", ".join(student.get("completed_subjects") or [])))

    # ---- Work experience ----
    if req.work_experience_years:
        exp = student.get("work_experience_years")
        if exp is None:
            checks.append(_check("Work experience", UNAVAILABLE, f"{req.work_experience_years} year(s)", None))
        elif exp >= req.work_experience_years:
            checks.append(_check("Work experience", MEETS, f"{req.work_experience_years} year(s)", f"{exp} year(s)"))
        else:
            checks.append(_check("Work experience", DOES_NOT_MEET, f"{req.work_experience_years} year(s)", f"{exp} year(s)"))

    # ---- Study gap ----
    if req.max_study_gap_years is not None:
        gap = student.get("study_gap_years")
        if gap is None:
            checks.append(_check("Study gap policy", UNAVAILABLE, f"Max {req.max_study_gap_years} year(s)", None))
        elif gap <= req.max_study_gap_years:
            checks.append(_check("Study gap policy", MEETS, f"Max {req.max_study_gap_years} year(s)", f"{gap} year(s)"))
        else:
            checks.append(_check("Study gap policy", REVIEW, f"Max {req.max_study_gap_years} year(s)", f"{gap} year(s)",
                                 "Gap exceeds the stated policy - supporting documentation is usually required."))

    # ---- GRE / GMAT ----
    if req.gre_required:
        checks.append(_check("GRE", MEETS if student.get("gre_score") else REVIEW,
                             req.gre_score or "Required", student.get("gre_score"),
                             "" if student.get("gre_score") else "GRE score not provided."))
    if req.gmat_required:
        checks.append(_check("GMAT", MEETS if student.get("gmat_score") else REVIEW,
                             req.gmat_score or "Required", student.get("gmat_score"),
                             "" if student.get("gmat_score") else "GMAT score not provided."))

    # ---- Research proposal expectation for PhD ----
    if program.degree_level == "PHD":
        has_research = bool(student.get("research_interests")) or bool(student.get("research_experience"))
        checks.append(_check("Research alignment evidence", MEETS if has_research else REVIEW,
                             "Research proposal / documented research interest",
                             "Provided" if has_research else "Not provided"))

    worst = max((_ORDER[c["status"]] for c in checks), default=0)
    overall = [k for k, v in _ORDER.items() if v == worst][0]
    if overall == MEETS:
        summary = "The stated published requirements for this program appear to be met."
    elif overall == PARTIALLY:
        summary = "Most stated requirements appear to be met; one or more criteria are marginal and need counselor review."
    elif overall == REVIEW:
        summary = "Some criteria require manual review by a counselor or confirmation from the university."
    elif overall == UNAVAILABLE:
        summary = "Verified information is currently unavailable for one or more criteria. Please confirm with the university or a counselor before applying."
    else:
        summary = "One or more stated minimum requirements do not appear to be met based on the information provided."
    return {"overall": overall, "checks": checks, "summary": summary}


def _english(student: Dict[str, Any], req: ProgramRequirement):
    pairs = [
        ("IELTS", student.get("ielts_overall"), req.ielts_overall),
        ("TOEFL iBT", student.get("toefl_ibt"), req.toefl_ibt),
        ("PTE Academic", student.get("pte_academic"), req.pte_academic),
        ("Duolingo", student.get("duolingo"), req.duolingo),
    ]
    published = [(n, s, r) for n, s, r in pairs if r is not None]
    if not published:
        return UNAVAILABLE, _check("English language requirement", UNAVAILABLE, None, None,
                                   "No published English requirement found for this program.")
    attempted = [(n, s, r) for n, s, r in published if s is not None]
    if not attempted:
        if student.get("moi_available") and req.moi_accepted:
            return MEETS, _check("English language requirement", MEETS, "MOI accepted", "Medium of Instruction letter",
                                 "This program states that an MOI letter is accepted.")
        names = ", ".join(f"{n} {r}" for n, s, r in published)
        return UNAVAILABLE, _check("English language requirement", UNAVAILABLE, names, None,
                                   "Add an English test score (or MOI) to evaluate this criterion.")
    best = None
    for name, score, required in attempted:
        if score + 1e-9 >= required:
            band_ok = True
            if name == "IELTS" and req.ielts_band_min and student.get("ielts_lowest_band") is not None:
                band_ok = student["ielts_lowest_band"] + 1e-9 >= req.ielts_band_min
            status = MEETS if band_ok else PARTIALLY
            note = "" if band_ok else f"Overall score meets {required}, but a minimum band of {req.ielts_band_min} is stated."
            cand = (status, _check("English language requirement", status, f"{name} {required}", f"{name} {score}", note))
        elif score >= required - 0.5:
            cand = (PARTIALLY, _check("English language requirement", PARTIALLY, f"{name} {required}", f"{name} {score}",
                                      "Slightly below the stated minimum - a re-test or pre-sessional English route may apply."))
        else:
            cand = (DOES_NOT_MEET, _check("English language requirement", DOES_NOT_MEET, f"{name} {required}", f"{name} {score}"))
        if best is None or _ORDER[cand[0]] < _ORDER[best[0]]:
            best = cand
    return best
