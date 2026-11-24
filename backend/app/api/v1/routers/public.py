"""Public, cacheable, SEO-facing read endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api import serializers as S
from app.core.database import get_db
from app.services import fx
from app.models.base import (APPLICATION_STATUSES, CONFIDENCE_STATES, COUNTRY_STATUSES,
                             CGPA_SCALES, DEGREE_LEVELS, EDUCATION_LEVELS, ELIGIBILITY_STATES,
                             ENGLISH_TESTS, FUNDING_SOURCES, LEAD_STAGES,
                             PARTNER_STATUSES, SOURCE_TIERS)
from app.models.content import (AdCampaign, BlogPost, Event, Faq, HomepageSection, HomepageSlide,
                                Service, SiteSetting, SuccessStory)
from app.models.crm import SearchQueryLog
from app.models.education import (Country, Department, Faculty, Professor, Program, ProgramIntake,
                                  Scholarship, University)
from app.services import rotation

router = APIRouter(tags=["public"])


# ----------------------------------------------------------------- settings
@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    row = db.get(SiteSetting, 1)
    if not row:
        raise HTTPException(404, "Site settings not initialised")
    return S.site_settings(row)



# ------------------------------------------------------------- vocabularies
# The controlled vocabularies the UI needs to render dropdowns and badges. Before this
# existed the frontend re-declared degree levels in four files and confidence/eligibility
# labels in a fifth, so adding a level meant editing five places and the API and the UI could
# silently disagree. These are system vocabularies - changing them changes engine behaviour,
# so they are served read-only rather than made CMS-editable. Business choices that really
# are editable (appointment services, modes, time slots) live in /settings instead.
def _options(values, labels: dict[str, str] | None = None):
    labels = labels or {}
    return [{"value": v, "label": labels.get(v, v.replace("_", " ").title())} for v in values]


_DEGREE_LABELS = {"FOUNDATION": "Foundation", "DIPLOMA": "Diploma", "BACHELOR": "Bachelor",
                  "MASTER": "Master", "PHD": "PhD"}
_CONFIDENCE_LABELS = {"VERIFIED": "Verified", "NEEDS_REVIEW": "Needs review",
                      "OUTDATED": "Outdated", "CONFLICTING": "Sources conflict",
                      "UNVERIFIED": "Unverified"}
_ELIGIBILITY_LABELS = {"MEETS": "Meets", "DOES_NOT_MEET": "Does not meet",
                       "PARTIALLY_MEETS": "Partially meets", "REQUIRES_REVIEW": "Requires review",
                       "INFORMATION_UNAVAILABLE": "Information unavailable"}


@router.get("/vocabularies")
def get_vocabularies():
    """Every controlled list the frontend renders, with display labels."""
    return {
        "degree_levels": _options(DEGREE_LEVELS, _DEGREE_LABELS),
        "confidence_states": _options(CONFIDENCE_STATES, _CONFIDENCE_LABELS),
        "eligibility_states": _options(ELIGIBILITY_STATES, _ELIGIBILITY_LABELS),
        "lead_stages": _options(LEAD_STAGES),
        "application_statuses": _options(APPLICATION_STATUSES),
        "country_statuses": _options(COUNTRY_STATUSES),
        "partner_statuses": _options(PARTNER_STATUSES),
        "source_tiers": _options(SOURCE_TIERS),
        # Free-text on the model, but the UI should offer a consistent set.
        "funding_sources": [{"value": v, "label": v} for v in FUNDING_SOURCES],
        "education_levels": [{"value": v, "label": v} for v in EDUCATION_LEVELS],
        "cgpa_scales": [{"value": v, "label": f"Out of {v}"} for v in CGPA_SCALES],
        "english_tests": [{"value": v, "label": v} for v in ENGLISH_TESTS],
        # Derived from the FX table, never typed out separately: the cost engine refuses any
        # pair it cannot convert (BUDGET_CURRENCY_MISMATCH), so offering a currency the engine
        # does not know would hand the student a budget verdict it then declines to give.
        "budget_currencies": [{"value": c, "label": c} for c in sorted(fx.RATES)],
        "budget_currency_rates_as_of": fx.RATES_AS_OF,
    }


# ----------------------------------------------------------------- homepage
def _country_counts(db: Session) -> Dict[int, Dict[str, int]]:
    uni = dict(db.execute(select(University.country_id, func.count(University.id))
                          .group_by(University.country_id)).all())
    prog = dict(db.execute(select(University.country_id, func.count(Program.id))
                           .join(Program, Program.university_id == University.id)
                           .group_by(University.country_id)).all())
    schol = dict(db.execute(select(Scholarship.country_id, func.count(Scholarship.id))
                            .group_by(Scholarship.country_id)).all())
    story = dict(db.execute(select(SuccessStory.country_id, func.count(SuccessStory.id))
                            .where(SuccessStory.is_published.is_(True))
                            .group_by(SuccessStory.country_id)).all())
    out: Dict[int, Dict[str, int]] = {}
    for cid in set(list(uni) + list(prog) + list(schol) + list(story)):
        if cid is None:
            continue
        out[cid] = {"universities": uni.get(cid, 0), "programs": prog.get(cid, 0),
                    "scholarships": schol.get(cid, 0), "success_stories": story.get(cid, 0)}
    return out


@router.get("/homepage")
def homepage(local_hour: Optional[int] = Query(None, ge=0, le=23), db: Session = Depends(get_db)):
    """One payload -> the browser rotates client-side every `duration_seconds` (§96)."""
    play = rotation.playlist(db, local_hour=local_hour)
    counts = _country_counts(db)
    sections = db.execute(select(HomepageSection).where(HomepageSection.is_enabled.is_(True))
                          .order_by(HomepageSection.position)).scalars().all()
    countries = db.execute(select(Country).where(Country.status.in_(["ACTIVE", "COMING_SOON"]))
                           .order_by(Country.priority, Country.name)).scalars().all()
    featured_unis = db.execute(select(University).options(selectinload(University.country),
                                                          selectinload(University.rankings))
                               .where(University.is_featured.is_(True), University.is_published.is_(True))
                               .order_by(University.priority).limit(12)).scalars().unique().all()
    prog_counts = dict(db.execute(select(Program.university_id, func.count(Program.id))
                                  .group_by(Program.university_id)).all())
    popular = db.execute(select(Program).options(selectinload(Program.university).selectinload(University.country),
                                                 selectinload(Program.intakes), selectinload(Program.requirement))
                         .where(Program.is_featured.is_(True), Program.is_published.is_(True)).limit(12)
                         ).scalars().unique().all()
    schols = db.execute(select(Scholarship).options(selectinload(Scholarship.country), selectinload(Scholarship.university))
                        .where(Scholarship.is_published.is_(True))
                        .order_by(Scholarship.is_featured.desc(), Scholarship.deadline).limit(8)).scalars().unique().all()
    stories = db.execute(select(SuccessStory).where(SuccessStory.is_published.is_(True))
                         .order_by(SuccessStory.is_featured.desc(), SuccessStory.published_at.desc())
                         .limit(9)).scalars().all()
    services = db.execute(select(Service).where(Service.status == "ACTIVE")
                          .order_by(Service.priority).limit(12)).scalars().all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    events = db.execute(select(Event).where(Event.start_at >= now - timedelta(days=1))
                        .order_by(Event.start_at).limit(6)).scalars().all()
    posts = db.execute(select(BlogPost).where(BlogPost.is_published.is_(True))
                       .order_by(BlogPost.published_at.desc()).limit(6)).scalars().all()
    phd_programs = db.execute(select(Program).options(selectinload(Program.university).selectinload(University.country),
                                                      selectinload(Program.intakes), selectinload(Program.requirement))
                              .where(Program.degree_level == "PHD").limit(6)).scalars().unique().all()
    research_profs = db.execute(select(Professor).options(selectinload(Professor.university).selectinload(University.country),
                                                          selectinload(Professor.department),
                                                          selectinload(Professor.publications)).limit(8)
                                ).scalars().unique().all()
    stats = site_stats(db)
    return {
        "hero": {**play, "slides": [S.slide(s) for s in play["slides"]]},
        "sections": [S.section(s) for s in sections],
        "countries": [S.country(c, counts.get(c.id)) for c in countries],
        "featured_universities": [S.university(u, program_count=prog_counts.get(u.id, 0)) for u in featured_unis],
        "popular_programs": [S.program(p) for p in popular],
        "scholarships": [S.scholarship(s) for s in schols],
        "success_stories": [S.success_story(s) for s in stories],
        "services": [S.service(s) for s in services],
        "events": [S.event(e) for e in events],
        "posts": [S.blog_post(p) for p in posts],
        "phd": {"programs": [S.program(p) for p in phd_programs],
                "professors": [S.professor(p) for p in research_profs]},
        "stats": stats,
    }


@router.get("/homepage/hero")
def hero(local_hour: Optional[int] = Query(None, ge=0, le=23), db: Session = Depends(get_db)):
    play = rotation.playlist(db, local_hour=local_hour)
    return {**play, "slides": [S.slide(s) for s in play["slides"]]}


@router.get("/stats")
def site_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """§75 - homepage counters come from the database, never hard-coded."""
    def c(model, *where):
        stmt = select(func.count(model.id))
        for w in where:
            stmt = stmt.where(w)
        return db.execute(stmt).scalar_one()
    return {
        "universities": c(University, University.is_published.is_(True)),
        "programs": c(Program, Program.is_published.is_(True)),
        "countries": c(Country, Country.status == "ACTIVE"),
        "scholarships": c(Scholarship, Scholarship.is_published.is_(True)),
        "success_stories": c(SuccessStory, SuccessStory.is_published.is_(True)),
        "professors": c(Professor),
        "services": c(Service, Service.status == "ACTIVE"),
        "upcoming_events": c(Event, Event.start_at >= datetime.now(timezone.utc).replace(tzinfo=None)),
    }


# ----------------------------------------------------------------- countries
@router.get("/countries")
def list_countries(status: Optional[str] = None, featured: Optional[bool] = None, db: Session = Depends(get_db)):
    stmt = select(Country)
    if status:
        stmt = stmt.where(Country.status == status.upper())
    if featured is not None:
        stmt = stmt.where(Country.is_featured.is_(featured))
    rows = db.execute(stmt.order_by(Country.priority, Country.name)).scalars().all()
    counts = _country_counts(db)
    return [S.country(c, counts.get(c.id)) for c in rows]


@router.get("/countries/{slug}")
def country_detail(slug: str, db: Session = Depends(get_db)):
    c = db.execute(select(Country).where(Country.slug == slug)).scalars().first()
    if not c:
        raise HTTPException(404, "Country not found")
    counts = _country_counts(db).get(c.id, {})
    unis = db.execute(select(University).options(selectinload(University.country), selectinload(University.rankings))
                      .where(University.country_id == c.id, University.is_published.is_(True))
                      .order_by(University.priority).limit(24)).scalars().unique().all()
    prog_counts = dict(db.execute(select(Program.university_id, func.count(Program.id))
                                  .group_by(Program.university_id)).all())
    programs = db.execute(select(Program).options(selectinload(Program.university).selectinload(University.country),
                                                  selectinload(Program.intakes), selectinload(Program.requirement))
                          .join(University).where(University.country_id == c.id).limit(12)).scalars().unique().all()
    schols = db.execute(select(Scholarship).options(selectinload(Scholarship.country), selectinload(Scholarship.university))
                        .where(Scholarship.country_id == c.id).limit(12)).scalars().unique().all()
    stories = db.execute(select(SuccessStory).where(SuccessStory.country_id == c.id,
                                                    SuccessStory.is_published.is_(True)).limit(6)).scalars().all()
    events = db.execute(select(Event).where(Event.country_id == c.id).order_by(Event.start_at).limit(4)).scalars().all()
    return {"country": S.country(c, counts, full=True),
            "universities": [S.university(u, program_count=prog_counts.get(u.id, 0)) for u in unis],
            "programs": [S.program(p) for p in programs],
            "scholarships": [S.scholarship(s) for s in schols],
            "success_stories": [S.success_story(s) for s in stories],
            "events": [S.event(e) for e in events]}


# ----------------------------------------------------------------- universities
@router.get("/universities")
def list_universities(q: Optional[str] = None, country: Optional[str] = None, city: Optional[str] = None,
                      degree_level: Optional[str] = None, subject: Optional[str] = None,
                      max_tuition: Optional[float] = None, min_tuition: Optional[float] = None,
                      intake: Optional[str] = None, max_ielts: Optional[float] = None,
                      max_cgpa: Optional[float] = None, scholarship: Optional[bool] = None,
                      ranking_provider: Optional[str] = None, featured: Optional[bool] = None,
                      sort: str = "priority", page: int = 1, page_size: int = 12,
                      db: Session = Depends(get_db)):
    """§13/§14 - search by name + country and much more."""
    stmt = select(University).options(selectinload(University.country), selectinload(University.rankings)) \
        .where(University.is_published.is_(True))
    if q:
        stmt = stmt.where(or_(University.name.ilike(f"%{q}%"), University.city.ilike(f"%{q}%")))
    if country:
        stmt = stmt.join(Country, University.country_id == Country.id).where(
            or_(Country.slug == country.lower(), Country.name.ilike(country), Country.iso2 == country.upper()))
    if city:
        stmt = stmt.where(University.city.ilike(f"%{city}%"))
    if featured is not None:
        stmt = stmt.where(University.is_featured.is_(featured))

    needs_program_join = any([degree_level, subject, max_tuition, min_tuition, intake, max_ielts, max_cgpa, scholarship])
    if needs_program_join:
        stmt = stmt.join(Program, Program.university_id == University.id)
        if degree_level:
            stmt = stmt.where(Program.degree_level == degree_level.upper())
        if subject:
            s = f"%{subject}%"
            stmt = stmt.where(or_(Program.name.ilike(s), Program.discipline.ilike(s)))
        if max_tuition:
            stmt = stmt.where(Program.tuition_annual <= max_tuition)
        if min_tuition:
            stmt = stmt.where(Program.tuition_annual >= min_tuition)
        if scholarship:
            stmt = stmt.where(Program.scholarship_available.is_(True))
        if intake:
            stmt = stmt.join(ProgramIntake, ProgramIntake.program_id == Program.id).where(
                ProgramIntake.label.ilike(f"%{intake}%"))
        if max_ielts or max_cgpa:
            from app.models.education import ProgramRequirement as PR
            stmt = stmt.join(PR, PR.program_id == Program.id)
            if max_ielts:
                stmt = stmt.where(or_(PR.ielts_overall <= max_ielts, PR.ielts_overall.is_(None)))
            if max_cgpa:
                stmt = stmt.where(or_(PR.min_cgpa <= max_cgpa, PR.min_cgpa.is_(None)))
        stmt = stmt.distinct()

    rows = db.execute(stmt).scalars().unique().all()
    if sort == "name":
        rows.sort(key=lambda u: u.name)
    elif sort == "ranking":
        def rk(u):
            r = sorted(u.rankings, key=lambda x: -x.edition_year)
            try:
                return int(str(r[0].rank_overall).split("-")[0].replace("#", "")) if r else 99999
            except Exception:
                return 99999
        rows.sort(key=rk)
    else:
        rows.sort(key=lambda u: (u.priority, u.name))

    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start:start + page_size]
    prog_counts = dict(db.execute(select(Program.university_id, func.count(Program.id))
                                  .group_by(Program.university_id)).all())
    db.add(SearchQueryLog(query=q, country=country, degree_level=degree_level, subject=subject,
                          intake=intake, results_count=total, channel="WEBSITE"))
    db.commit()
    return {"total": total, "page": page, "page_size": page_size,
            "items": [S.university(u, program_count=prog_counts.get(u.id, 0)) for u in page_rows]}


@router.get("/universities/{slug}")
def university_detail(slug: str, db: Session = Depends(get_db)):
    u = db.execute(select(University).options(selectinload(University.country), selectinload(University.rankings))
                   .where(University.slug == slug)).scalars().first()
    if not u:
        raise HTTPException(404, "University not found")
    programs = db.execute(select(Program).options(selectinload(Program.university).selectinload(University.country),
                                                  selectinload(Program.intakes), selectinload(Program.requirement),
                                                  selectinload(Program.department))
                          .where(Program.university_id == u.id, Program.is_published.is_(True))).scalars().unique().all()
    schols = db.execute(select(Scholarship).options(selectinload(Scholarship.country), selectinload(Scholarship.university))
                        .where(Scholarship.university_id == u.id)).scalars().unique().all()
    profs = db.execute(select(Professor).options(selectinload(Professor.university).selectinload(University.country),
                                                 selectinload(Professor.department), selectinload(Professor.publications))
                       .where(Professor.university_id == u.id)).scalars().unique().all()
    departments = db.execute(select(Department).where(Department.university_id == u.id)).scalars().all()
    stories = db.execute(select(SuccessStory).where(SuccessStory.university_id == u.id,
                                                    SuccessStory.is_published.is_(True))).scalars().all()
    by_level: Dict[str, List[Dict[str, Any]]] = {}
    for p in programs:
        by_level.setdefault(p.degree_level, []).append(S.program(p))
    return {"university": S.university(u, full=True, program_count=len(programs)),
            "programs": [S.program(p) for p in programs],
            "programs_by_level": by_level,
            # The spec's four-level hierarchy, served as a tree. `departments` stays flat
            # alongside it so existing consumers are unaffected.
            "faculties": [S.faculty(f) for f in sorted(u.faculties, key=lambda f: (f.position, f.name))],
            "departments": [{"id": d.id, "name": d.name, "slug": d.slug,
                             "faculty": ({"id": d.faculty.id, "name": d.faculty.name,
                                          "slug": d.faculty.slug, "kind": d.faculty.kind}
                                         if d.faculty else None),
                             "website": d.website, "research_areas": d.research_areas} for d in departments],
            "scholarships": [S.scholarship(s) for s in schols],
            "professors": [S.professor(p) for p in profs],
            "success_stories": [S.success_story(s) for s in stories]}



# ----------------------------------------------------------------- faculties
# Academic divisions (Faculty of Engineering, Business School). Distinct from /faculty/{slug},
# which is a professor - the specification uses "faculty" in both senses, so the division
# lives under the plural and the person under the singular.
@router.get("/faculties")
def list_faculties(university: Optional[str] = None, country: Optional[str] = None,
                   kind: Optional[str] = None, q: Optional[str] = None,
                   page: int = 1, page_size: int = 24, db: Session = Depends(get_db)):
    stmt = select(Faculty).join(University, University.id == Faculty.university_id)
    if university:
        stmt = stmt.where(University.slug == university)
    if country:
        stmt = stmt.join(Country, Country.id == University.country_id).where(Country.slug == country)
    if kind:
        stmt = stmt.where(Faculty.kind == kind.upper())
    if q:
        stmt = stmt.where(Faculty.name.ilike(f"%{q.strip()}%"))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page_size = max(1, min(page_size, 100))
    rows = db.execute(
        stmt.order_by(University.name, Faculty.position, Faculty.name)
        .offset((max(page, 1) - 1) * page_size).limit(page_size)
    ).scalars().all()
    return {"total": total, "page": max(page, 1), "page_size": page_size,
            "items": [{**S.faculty(f, with_departments=False),
                       "university": {"name": f.university.name, "slug": f.university.slug,
                                      "country": f.university.country.name if f.university.country else None,
                                      "country_slug": f.university.country.slug if f.university.country else None}}
                      for f in rows]}


@router.get("/faculties/{slug}")
def faculty_detail(slug: str, db: Session = Depends(get_db)):
    f = db.execute(select(Faculty).where(Faculty.slug == slug)).scalars().first()
    if not f:
        raise HTTPException(404, "Faculty not found")

    dept_ids = [d.id for d in f.departments]
    programs = db.execute(
        select(Program).where(Program.department_id.in_(dept_ids)).order_by(Program.name)
    ).scalars().all() if dept_ids else []

    by_level: Dict[str, List[Dict[str, Any]]] = {}
    for p in programs:
        by_level.setdefault(p.degree_level, []).append(S.program(p))

    professors = db.execute(
        select(Professor).where(Professor.department_id.in_(dept_ids)).order_by(Professor.full_name)
    ).scalars().all() if dept_ids else []

    u = f.university
    return {
        "faculty": S.faculty(f),
        "university": {"name": u.name, "slug": u.slug, "logo_url": u.logo_url,
                       "country": u.country.name if u.country else None,
                       "country_slug": u.country.slug if u.country else None},
        "programs": [S.program(p) for p in programs],
        "programs_by_level": by_level,
        "program_count": len(programs),
        "professors": [S.professor(p) for p in professors],
    }


# ----------------------------------------------------------------- programs
@router.get("/programs")
def list_programs(q: Optional[str] = None, country: Optional[str] = None, degree_level: Optional[str] = None,
                  subject: Optional[str] = None, university: Optional[str] = None,
                  max_tuition: Optional[float] = None, intake: Optional[str] = None,
                  scholarship: Optional[bool] = None, sort: str = "relevance",
                  page: int = 1, page_size: int = 12, db: Session = Depends(get_db)):
    stmt = (select(Program).options(selectinload(Program.university).selectinload(University.country),
                                    selectinload(Program.intakes), selectinload(Program.requirement))
            .where(Program.is_published.is_(True)))
    if q:
        stmt = stmt.where(or_(Program.name.ilike(f"%{q}%"), Program.discipline.ilike(f"%{q}%")))
    if subject:
        stmt = stmt.where(or_(Program.name.ilike(f"%{subject}%"), Program.discipline.ilike(f"%{subject}%")))
    if degree_level:
        stmt = stmt.where(Program.degree_level == degree_level.upper())
    if max_tuition:
        stmt = stmt.where(Program.tuition_annual <= max_tuition)
    if scholarship:
        stmt = stmt.where(Program.scholarship_available.is_(True))
    if country or university:
        stmt = stmt.join(University, Program.university_id == University.id)
        if country:
            stmt = stmt.join(Country, University.country_id == Country.id).where(
                or_(Country.slug == country.lower(), Country.name.ilike(country)))
        if university:
            stmt = stmt.where(or_(University.slug == university, University.name.ilike(f"%{university}%")))
    if intake:
        stmt = stmt.join(ProgramIntake, ProgramIntake.program_id == Program.id).where(
            ProgramIntake.label.ilike(f"%{intake}%"))
    rows = list(db.execute(stmt.distinct()).scalars().unique())
    if sort == "tuition_asc":
        rows.sort(key=lambda p: p.tuition_annual or 1e9)
    elif sort == "tuition_desc":
        rows.sort(key=lambda p: -(p.tuition_annual or 0))
    elif sort == "name":
        rows.sort(key=lambda p: p.name)
    else:
        rows.sort(key=lambda p: (not p.is_featured, p.name))
    total = len(rows)
    start = (page - 1) * page_size
    db.add(SearchQueryLog(query=q or subject, country=country, degree_level=degree_level,
                          subject=subject, intake=intake, results_count=total))
    db.commit()
    return {"total": total, "page": page, "page_size": page_size,
            "items": [S.program(p) for p in rows[start:start + page_size]]}


@router.get("/programs/{slug}")
def program_detail(slug: str, db: Session = Depends(get_db)):
    p = db.execute(select(Program).options(selectinload(Program.university).selectinload(University.country),
                                           selectinload(Program.intakes), selectinload(Program.requirement),
                                           selectinload(Program.department))
                   .where(Program.slug == slug)).scalars().first()
    if not p:
        raise HTTPException(404, "Program not found")
    p.views = (p.views or 0) + 1
    db.commit()
    schols = db.execute(select(Scholarship).options(selectinload(Scholarship.country), selectinload(Scholarship.university))
                        .where(or_(Scholarship.university_id == p.university_id,
                                   Scholarship.country_id == p.university.country_id))).scalars().unique().all()
    schols = [s for s in schols if not s.degree_levels or p.degree_level in s.degree_levels]
    related = db.execute(select(Program).options(selectinload(Program.university).selectinload(University.country),
                                                 selectinload(Program.intakes), selectinload(Program.requirement))
                         .where(Program.degree_level == p.degree_level, Program.id != p.id,
                                Program.discipline == p.discipline).limit(6)).scalars().unique().all()
    faculty = []
    if p.degree_level == "PHD":
        profs = db.execute(select(Professor).options(selectinload(Professor.university).selectinload(University.country),
                                                     selectinload(Professor.department),
                                                     selectinload(Professor.publications))
                           .where(Professor.university_id == p.university_id)).scalars().unique().all()
        faculty = [S.professor(x) for x in profs]
    from app.services import costing
    return {"program": S.program(p, full=True),
            "cost_estimate": costing.estimate(p),
            "scholarships": [S.scholarship(s) for s in schols],
            "related": [S.program(r) for r in related],
            "faculty": faculty}


# ----------------------------------------------------------------- scholarships
@router.get("/scholarships")
def list_scholarships(q: Optional[str] = None, country: Optional[str] = None, university: Optional[str] = None,
                      degree_level: Optional[str] = None, page: int = 1, page_size: int = 12,
                      db: Session = Depends(get_db)):
    stmt = select(Scholarship).options(selectinload(Scholarship.country), selectinload(Scholarship.university)) \
        .where(Scholarship.is_published.is_(True))
    if q:
        stmt = stmt.where(or_(Scholarship.name.ilike(f"%{q}%"), Scholarship.provider.ilike(f"%{q}%")))
    if country:
        stmt = stmt.join(Country, Scholarship.country_id == Country.id).where(
            or_(Country.slug == country.lower(), Country.name.ilike(country)))
    if university:
        stmt = stmt.join(University, Scholarship.university_id == University.id).where(University.slug == university)
    rows = list(db.execute(stmt).scalars().unique())
    if degree_level:
        rows = [s for s in rows if not s.degree_levels or degree_level.upper() in s.degree_levels]
    rows.sort(key=lambda s: (not s.is_featured, s.deadline or __import__("datetime").date(2100, 1, 1)))
    total = len(rows)
    start = (page - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size,
            "items": [S.scholarship(s) for s in rows[start:start + page_size]]}


@router.get("/scholarships/{slug}")
def scholarship_detail(slug: str, db: Session = Depends(get_db)):
    s = db.execute(select(Scholarship).options(selectinload(Scholarship.country), selectinload(Scholarship.university))
                   .where(Scholarship.slug == slug)).scalars().first()
    if not s:
        raise HTTPException(404, "Scholarship not found")
    return S.scholarship(s)


# ----------------------------------------------------------------- faculty / PhD
@router.get("/faculty")
def list_faculty(q: Optional[str] = None, country: Optional[str] = None, university: Optional[str] = None,
                 field: Optional[str] = None, interests: Optional[str] = None,
                 accepting: Optional[str] = None, page: int = 1, page_size: int = 12,
                 db: Session = Depends(get_db)):
    from app.services import matching
    country_id = None
    if country:
        row = db.execute(select(Country).where(or_(Country.slug == country.lower(),
                                                   Country.name.ilike(country)))).scalars().first()
        country_id = row.id if row else None
    uni_id = None
    if university:
        row = db.execute(select(University).where(University.slug == university)).scalars().first()
        uni_id = row.id if row else None
    terms = [t.strip() for t in (interests or "").split(",") if t.strip()]
    matches = matching.match_faculty(db, terms, country_id=country_id, university_id=uni_id,
                                     field=field, limit=500)
    if q:
        ql = q.lower()
        matches = [m for m in matches if ql in m["professor"].full_name.lower()]
    if accepting:
        matches = [m for m in matches if m["professor"].accepting_phd == accepting.upper()]
    total = len(matches)
    start = (page - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size,
            "items": [S.professor(m["professor"], matched=m["matched_interests"], evidence=m["evidence"])
                      | {"match_score": m["score"]} for m in matches[start:start + page_size]]}


@router.get("/faculty/{slug}")
def faculty_detail(slug: str, db: Session = Depends(get_db)):
    p = db.execute(select(Professor).options(selectinload(Professor.university).selectinload(University.country),
                                             selectinload(Professor.department), selectinload(Professor.publications))
                   .where(Professor.slug == slug)).scalars().first()
    if not p:
        raise HTTPException(404, "Faculty member not found")
    phd_programs = db.execute(select(Program).options(selectinload(Program.university).selectinload(University.country),
                                                      selectinload(Program.intakes), selectinload(Program.requirement))
                              .where(Program.university_id == p.university_id,
                                     Program.degree_level == "PHD")).scalars().unique().all()
    return {"professor": S.professor(p, full=True), "phd_programs": [S.program(x) for x in phd_programs]}


@router.get("/research-areas")
def research_areas(db: Session = Depends(get_db)):
    from app.models.education import ResearchArea
    rows = db.execute(select(ResearchArea).order_by(ResearchArea.name)).scalars().all()
    return [{"id": r.id, "name": r.name, "slug": r.slug, "parent": r.parent, "keywords": r.keywords} for r in rows]


# ----------------------------------------------------------------- content
@router.get("/services")
def list_services(featured: Optional[bool] = None, db: Session = Depends(get_db)):
    stmt = select(Service).where(Service.status == "ACTIVE")
    if featured is not None:
        stmt = stmt.where(Service.is_featured.is_(featured))
    rows = db.execute(stmt.order_by(Service.priority)).scalars().all()
    return [S.service(s) for s in rows]


@router.get("/services/{slug}")
def service_detail(slug: str, db: Session = Depends(get_db)):
    s = db.execute(select(Service).where(Service.slug == slug)).scalars().first()
    if not s:
        raise HTTPException(404, "Service not found")
    return S.service(s, full=True)


@router.get("/success-stories")
def list_stories(country: Optional[str] = None, university: Optional[str] = None, degree_level: Optional[str] = None,
                 year: Optional[int] = None, scholarship: Optional[bool] = None, q: Optional[str] = None,
                 page: int = 1, page_size: int = 12, db: Session = Depends(get_db)):
    stmt = select(SuccessStory).where(SuccessStory.is_published.is_(True))
    if country:
        stmt = stmt.join(Country, SuccessStory.country_id == Country.id).where(
            or_(Country.slug == country.lower(), Country.name.ilike(country)))
    if university:
        stmt = stmt.where(SuccessStory.university_name.ilike(f"%{university}%"))
    if degree_level:
        stmt = stmt.where(SuccessStory.degree_level == degree_level.upper())
    if year:
        stmt = stmt.where(SuccessStory.year == year)
    if q:
        stmt = stmt.where(or_(SuccessStory.student_name.ilike(f"%{q}%"), SuccessStory.headline.ilike(f"%{q}%")))
    rows = list(db.execute(stmt.order_by(SuccessStory.is_featured.desc(),
                                         SuccessStory.published_at.desc())).scalars().unique())
    if scholarship:
        rows = [r for r in rows if r.scholarship]
    # §50 - facet counts come from the database
    all_rows = db.execute(select(SuccessStory).where(SuccessStory.is_published.is_(True))).scalars().all()
    countries = {c.id: c for c in db.execute(select(Country)).scalars().all()}
    facet: Dict[str, int] = {}
    for r in all_rows:
        if r.country_id and r.country_id in countries:
            facet[countries[r.country_id].name] = facet.get(countries[r.country_id].name, 0) + 1
    total = len(rows)
    start = (page - 1) * page_size
    items = []
    for r in rows[start:start + page_size]:
        d = S.success_story(r)
        if r.country_id and r.country_id in countries:
            c = countries[r.country_id]
            d["country"] = {"id": c.id, "name": c.name, "slug": c.slug, "flag_emoji": c.flag_emoji}
        items.append(d)
    return {"total": total, "page": page, "page_size": page_size, "facets": {"countries": facet}, "items": items}


@router.get("/success-stories/{slug}")
def story_detail(slug: str, db: Session = Depends(get_db)):
    s = db.execute(select(SuccessStory).where(SuccessStory.slug == slug)).scalars().first()
    if not s:
        raise HTTPException(404, "Success story not found")
    data = S.success_story(s, full=True)
    if s.country_id:
        c = db.get(Country, s.country_id)
        if c:
            data["country"] = {"id": c.id, "name": c.name, "slug": c.slug, "flag_emoji": c.flag_emoji}
    return data


@router.get("/events")
def list_events(upcoming: bool = True, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stmt = select(Event)
    stmt = stmt.where(Event.start_at >= now - timedelta(days=1)) if upcoming else stmt
    rows = db.execute(stmt.order_by(Event.start_at)).scalars().all()
    return [S.event(e, full=True) for e in rows]


@router.get("/events/{slug}")
def event_detail(slug: str, db: Session = Depends(get_db)):
    e = db.execute(select(Event).where(Event.slug == slug)).scalars().first()
    if not e:
        raise HTTPException(404, "Event not found")
    return S.event(e, full=True)


@router.get("/blog")
def list_posts(category: Optional[str] = None, tag: Optional[str] = None, q: Optional[str] = None,
               page: int = 1, page_size: int = 9, db: Session = Depends(get_db)):
    stmt = select(BlogPost).where(BlogPost.is_published.is_(True))
    if category:
        stmt = stmt.where(BlogPost.category == category)
    if q:
        stmt = stmt.where(or_(BlogPost.title.ilike(f"%{q}%"), BlogPost.excerpt.ilike(f"%{q}%")))
    rows = list(db.execute(stmt.order_by(BlogPost.published_at.desc())).scalars().all())
    if tag:
        rows = [r for r in rows if tag in (r.tags or [])]
    total = len(rows)
    start = (page - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size,
            "items": [S.blog_post(b) for b in rows[start:start + page_size]]}


@router.get("/blog/{slug}")
def post_detail(slug: str, db: Session = Depends(get_db)):
    b = db.execute(select(BlogPost).where(BlogPost.slug == slug)).scalars().first()
    if not b:
        raise HTTPException(404, "Post not found")
    related = db.execute(select(BlogPost).where(BlogPost.id != b.id, BlogPost.is_published.is_(True))
                         .order_by(BlogPost.published_at.desc()).limit(3)).scalars().all()
    return {"post": S.blog_post(b, full=True), "related": [S.blog_post(r) for r in related]}


@router.get("/faqs")
def list_faqs(scope: Optional[str] = None, category: Optional[str] = None, db: Session = Depends(get_db)):
    stmt = select(Faq).where(Faq.is_published.is_(True))
    if scope:
        stmt = stmt.where(Faq.scope == scope.upper())
    if category:
        stmt = stmt.where(Faq.category == category)
    rows = db.execute(stmt.order_by(Faq.position)).scalars().all()
    return [S.faq(f) for f in rows]


@router.get("/campaigns/active")
def active_campaigns(db: Session = Depends(get_db)):
    rows = db.execute(select(AdCampaign).where(AdCampaign.status == "RUNNING")).scalars().all()
    return [S.campaign(c) for c in rows]


# ----------------------------------------------------------------- global search
@router.get("/search")
def global_search(q: str, limit: int = 8, db: Session = Depends(get_db)):
    like = f"%{q}%"
    unis = db.execute(select(University).options(selectinload(University.country), selectinload(University.rankings))
                      .where(University.name.ilike(like)).limit(limit)).scalars().unique().all()
    progs = db.execute(select(Program).options(selectinload(Program.university).selectinload(University.country),
                                               selectinload(Program.intakes), selectinload(Program.requirement))
                       .where(or_(Program.name.ilike(like), Program.discipline.ilike(like))).limit(limit)
                       ).scalars().unique().all()
    countries = db.execute(select(Country).where(Country.name.ilike(like)).limit(5)).scalars().all()
    schols = db.execute(select(Scholarship).options(selectinload(Scholarship.country), selectinload(Scholarship.university))
                        .where(Scholarship.name.ilike(like)).limit(5)).scalars().unique().all()
    profs = db.execute(select(Professor).options(selectinload(Professor.university).selectinload(University.country),
                                                 selectinload(Professor.department), selectinload(Professor.publications))
                       .where(Professor.full_name.ilike(like)).limit(5)).scalars().unique().all()
    db.add(SearchQueryLog(query=q, results_count=len(unis) + len(progs), channel="WEBSITE"))
    db.commit()
    return {"query": q,
            "universities": [S.university(u) for u in unis],
            "programs": [S.program(p) for p in progs],
            "countries": [S.country(c) for c in countries],
            "scholarships": [S.scholarship(s) for s in schols],
            "faculty": [S.professor(p) for p in profs]}


@router.get("/sitemap-data")
def sitemap_data(db: Session = Depends(get_db)):
    """Feeds the Next.js sitemap generator (§71)."""
    return {
        "countries": [{"slug": c.slug, "updated": S._dt(c.updated_at)} for c in db.execute(select(Country)).scalars().all()],
        "universities": [{"slug": u.slug, "country": u.country.slug if u.country else None, "updated": S._dt(u.updated_at)}
                         for u in db.execute(select(University).options(selectinload(University.country))
                                             .where(University.is_published.is_(True))).scalars().unique().all()],
        "programs": [{"slug": p.slug, "updated": S._dt(p.updated_at)}
                     for p in db.execute(select(Program).where(Program.is_published.is_(True))).scalars().all()],
        "scholarships": [{"slug": s.slug, "updated": S._dt(s.updated_at)}
                         for s in db.execute(select(Scholarship).where(Scholarship.is_published.is_(True))).scalars().all()],
        # "faculty" = professors (/faculty/{slug}); "faculties" = academic divisions
        # (/faculties/{slug}). The specification overloads the word; the routes do not.
        "faculty": [{"slug": p.slug, "updated": S._dt(p.updated_at)} for p in db.execute(select(Professor)).scalars().all()],
        "faculties": [{"slug": f.slug, "updated": S._dt(f.updated_at)}
                      for f in db.execute(select(Faculty)).scalars().all()],
        "services": [{"slug": s.slug, "updated": S._dt(s.updated_at)} for s in db.execute(select(Service)).scalars().all()],
        "success_stories": [{"slug": s.slug, "updated": S._dt(s.updated_at)}
                            for s in db.execute(select(SuccessStory).where(SuccessStory.is_published.is_(True))).scalars().all()],
        "events": [{"slug": e.slug, "updated": S._dt(e.updated_at)} for e in db.execute(select(Event)).scalars().all()],
        "posts": [{"slug": b.slug, "updated": S._dt(b.updated_at)}
                  for b in db.execute(select(BlogPost).where(BlogPost.is_published.is_(True))).scalars().all()],
    }
