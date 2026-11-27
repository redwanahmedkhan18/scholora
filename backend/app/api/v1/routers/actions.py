"""Student-facing write endpoints: profiles, eligibility, matching, brochures, leads, bookings."""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api import serializers as S
from app.core.database import get_db
from app.core.privacy import safe_analytics_path, safe_source_url, scrub_analytics_value
from app.models.content import AdCampaign, Event, EventRegistration, SiteSetting
from app.models.crm import AnalyticsEvent, Appointment, Brochure, Lead, Notification, SearchQueryLog, StudentProfile
from app.models.education import Program, University
from app.services import brochure as brochure_service
from app.services import costing, destinations, matching, scoping, whatsapp
from app.services.eligibility import evaluate

router = APIRouter(tags=["actions"])


# ------------------------------------------------------------------ schemas
# ------------------------------------------------------------------ schemas
#: Contact consent. A counselor may only be asked to call or message someone who explicitly
#: said yes, so the value is required, must be a real boolean (``strict`` refuses the string
#: "true", the number 1 and null), and must be affirmative. Omitting the field is a 422, not a
#: silent grant: the field has no default on purpose.
AffirmativeConsent = Annotated[bool, Field(strict=True)]

CONSENT_REQUIRED_MESSAGE = "We need your permission before a counselor can contact you."


def _require_affirmative_consent(value: bool) -> bool:
    if value is not True:
        raise ValueError(CONSENT_REQUIRED_MESSAGE)
    return value


class ProfileIn(BaseModel):
    # Reject unknown fields instead of ignoring them. Pydantic's default is to drop what
    # it does not recognise, which here meant a caller sending the obvious-looking
    # "ielts": 6.5 or "budget_annual": 5000 got a confident recommendation computed as
    # though the student had never taken IELTS and had no budget - "English requirement:
    # not provided", every programme scored identically. Silently discarding the inputs
    # that drive the advice is the worst possible failure for this endpoint, so a
    # misspelled field is now a 422 that names it.
    model_config = ConfigDict(extra="forbid")

    full_name: str = "Prospective student"
    email: Optional[str] = None
    phone: Optional[str] = None
    nationality: str = "Bangladesh"
    city: Optional[str] = None
    highest_qualification: Optional[str] = None
    major: Optional[str] = None
    institution: Optional[str] = None
    cgpa: Optional[float] = None
    cgpa_scale: Optional[float] = 4.0
    graduation_year: Optional[int] = None
    study_gap_years: Optional[float] = 0
    backlogs: Optional[int] = 0
    completed_subjects: List[str] = Field(default_factory=list)
    english_test: Optional[str] = None
    ielts_overall: Optional[float] = None
    ielts_lowest_band: Optional[float] = None
    toefl_ibt: Optional[float] = None
    pte_academic: Optional[float] = None
    duolingo: Optional[float] = None
    moi_available: bool = False
    gre_score: Optional[str] = None
    gmat_score: Optional[str] = None
    work_experience_years: Optional[float] = 0
    job_title: Optional[str] = None
    research_experience: Optional[str] = None
    publications_count: Optional[int] = 0
    research_interests: List[str] = Field(default_factory=list)
    budget_per_year: Optional[float] = None
    budget_currency: str = "USD"
    funding_source: Optional[str] = None
    preferred_countries: List[str] = Field(default_factory=list)
    preferred_universities: List[str] = Field(default_factory=list)
    target_degree_level: Optional[str] = None
    target_discipline: Optional[str] = None
    target_intake: Optional[str] = None
    career_goal: Optional[str] = None
    consent_marketing: bool = False
    source: str = "WEBSITE"


class LeadIn(BaseModel):
    full_name: str
    phone: str
    email: Optional[str] = None
    message: Optional[str] = None
    interest_country: Optional[str] = None
    interest_degree: Optional[str] = None
    interest_subject: Optional[str] = None
    intake: Optional[str] = None
    source: str = "WEBSITE"
    channel_detail: Optional[str] = None
    utm: Dict[str, Any] = Field(default_factory=dict)
    page_path: Optional[str] = None
    consent_contact: AffirmativeConsent
    profile: Optional[ProfileIn] = None

    @field_validator("consent_contact")
    @classmethod
    def _consent_is_affirmative(cls, value: bool) -> bool:
        return _require_affirmative_consent(value)


class AppointmentIn(BaseModel):
    full_name: str
    phone: str
    email: Optional[str] = None
    service_type: Optional[str] = None
    counselor_name: Optional[str] = None
    scheduled_date: date
    scheduled_time: str
    mode: Optional[str] = None
    notes: Optional[str] = None
    utm: Dict[str, Any] = Field(default_factory=dict)
    consent_contact: AffirmativeConsent

    @field_validator("consent_contact")
    @classmethod
    def _consent_is_affirmative(cls, value: bool) -> bool:
        return _require_affirmative_consent(value)


class EligibilityIn(BaseModel):
    profile: ProfileIn
    program_id: Optional[int] = None
    program_slug: Optional[str] = None


class MatchIn(BaseModel):
    profile: ProfileIn
    degree_level: Optional[str] = None
    country_slugs: List[str] = Field(default_factory=list)
    subject: Optional[str] = None
    intake: Optional[str] = None
    limit: int = 20


class BrochureIn(BaseModel):
    profile: ProfileIn
    program_id: Optional[int] = None
    program_slug: Optional[str] = None
    target_intake: Optional[str] = None
    channel: str = "WEBSITE"


class EventRegIn(BaseModel):
    event_id: int
    full_name: str
    phone: str
    email: Optional[str] = None
    preferred_country: Optional[str] = None
    degree_level: Optional[str] = None
    consent_contact: AffirmativeConsent

    @field_validator("consent_contact")
    @classmethod
    def _consent_is_affirmative(cls, value: bool) -> bool:
        return _require_affirmative_consent(value)


class TrackIn(BaseModel):
    name: str
    path: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    utm: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)


# ------------------------------------------------------------------ helpers
def _next_reference(db: Session, prefix: str) -> str:
    year = datetime.now(timezone.utc).year
    if prefix == "LEAD":
        n = db.execute(select(func.count(Lead.id))).scalar_one() + 1
    else:
        n = db.execute(select(func.count(Appointment.id))).scalar_one() + 1
    return f"{prefix}-{year}-{n:06d}"


def _save_profile(db: Session, profile: ProfileIn) -> StudentProfile:
    row = StudentProfile(public_token=secrets.token_urlsafe(10), **profile.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _campaign_from_utm(db: Session, utm: Dict[str, Any]) -> Optional[int]:
    name = (utm or {}).get("utm_campaign")
    if not name:
        return None
    c = db.execute(select(AdCampaign).where(AdCampaign.utm_campaign == name)).scalars().first()
    return c.id if c else None


def _resolve_program(db: Session, program_id: Optional[int], slug: Optional[str]) -> Program:
    stmt = select(Program).options(selectinload(Program.university).selectinload(University.country),
                                   selectinload(Program.requirement), selectinload(Program.intakes),
                                   selectinload(Program.department))
    if program_id:
        p = db.execute(stmt.where(Program.id == program_id)).scalars().first()
    elif slug:
        p = db.execute(stmt.where(Program.slug == slug)).scalars().first()
    else:
        raise HTTPException(400, "program_id or program_slug is required")
    if not p:
        raise HTTPException(404, "Program not found")
    return p


# ------------------------------------------------------------------ endpoints
@router.post("/eligibility/check")
def check_eligibility(body: EligibilityIn, db: Session = Depends(get_db)):
    p = _resolve_program(db, body.program_id, body.program_slug)
    student = body.profile.model_dump()
    result = evaluate(student, p, p.requirement)
    cost = costing.estimate(p, student.get("budget_per_year"), student.get("budget_currency", "USD"))
    return {"program": S.program(p, full=True), "eligibility": result, "cost": cost}


_EMPTY_MATCH_STATS = {"total": 0, "within_budget": 0, "academic_ok": 0, "with_scholarship": 0,
                       "intake_available": 0, "research_aligned": 0}


def _match_scope(db: Session, body: MatchIn):
    """The one scope for a matching request: destination, degree and subject, or ``None``.

    ``None`` means nothing was stated, so the profile's own preferences lead - that is not an
    unresolved scope. An unknown destination slug returns a scope that cannot be executed, which
    every caller turns into an empty result rather than an unrestricted one.
    """
    if not body.country_slugs:
        return None
    resolved = [destinations.resolve_destination(db, slug) for slug in body.country_slugs]
    if any(row is None for row in resolved):
        return scoping.Scope(status=scoping.UNSUPPORTED, reasons=(scoping.DESTINATION_UNKNOWN,),
                             clarify_slot="country")
    rows = [row for row in resolved if row is not None]
    return scoping.scope_from_destinations(db, rows, degree_level=body.degree_level,
                                           subject=body.subject)


@router.post("/match/programs")
def match_programs(body: MatchIn, db: Session = Depends(get_db)):
    student = body.profile.model_dump()
    if body.degree_level:
        student["target_degree_level"] = body.degree_level
    if body.subject:
        student["target_discipline"] = body.subject
    if body.intake:
        student["target_intake"] = body.intake
    scope = _match_scope(db, body)
    if scope is not None and not scope.ok:
        return {"stats": _EMPTY_MATCH_STATS, "results": [], "alternatives": []}
    res = matching.match_programs(db, student, {}, limit=body.limit, scope=scope)
    db.add(SearchQueryLog(query=body.subject, degree_level=body.degree_level, subject=body.subject,
                          intake=body.intake, results_count=res["stats"]["total"], channel="PROFILE_MATCH"))
    db.commit()
    return {
        "stats": res["stats"],
        "results": [{
            "program": S.program(r["program"], full=True),
            "exact_match": True,
            "score": r["score"],
            "eligibility": r["eligibility"],
            "cost": r["cost"],
            "reasons": r["reasons"],
            "scholarships": [S.scholarship(s) for s in r["scholarships"]],
            "intake": S.intake(r["intake"]) if r["intake"] else None,
            "research_hits": r["research_hits"],
        } for r in res["results"]],
        # Useful near misses, kept out of the ranking above and labelled for what they are: a
        # student who asked for public health is never shown nursing as if it matched.
        "alternatives": [{
            "program": S.program(p, full=True),
            "exact_match": False,
            "label": "Related alternative - not an exact match for the requested subject",
        } for p in res.get("alternatives") or []],
    }


@router.post("/match/scholarships")
def match_scholarships(body: MatchIn, db: Session = Depends(get_db)):
    profile = body.profile.model_dump()
    # MatchIn carries degree_level at the top level and the engine reads the profile's
    # target_degree_level. Accepting a field and then ignoring it is worse than rejecting
    # it: the caller gets a confident answer to a question it did not ask - a PhD-only
    # fellowship returned as MEETS for a master's applicant. Honour it as a fallback,
    # letting an explicit profile value win.
    if body.degree_level and not profile.get("target_degree_level"):
        profile["target_degree_level"] = body.degree_level.upper()
    scope = _match_scope(db, body)
    if scope is not None and not scope.ok:
        return []
    rows = matching.match_scholarships(db, profile, limit=body.limit, scope=scope)
    return [{"scholarship": S.scholarship(r["scholarship"]), "status": r["status"],
             "checks": r["checks"], "statement": r["statement"]} for r in rows]


class FacultyMatchIn(BaseModel):
    interests: List[str]
    country_slug: Optional[str] = None
    university_slug: Optional[str] = None
    field: Optional[str] = None
    limit: int = 20


@router.post("/match/faculty")
def match_faculty(body: FacultyMatchIn, db: Session = Depends(get_db)):
    uni_ids: Optional[List[int]] = None
    if body.country_slug:
        target = destinations.resolve_destination(db, body.country_slug)
        if target is None:
            # An unknown destination is an empty answer, never a worldwide search.
            return []
        # The same destination semantics as every other surface: a region stays inside the
        # region instead of resolving to its sovereign and returning the whole country.
        uni_ids = list(scoping.university_ids(db, scoping.resolve_scope(
            db, context={"country": body.country_slug})))
    uni_id = None
    if body.university_slug:
        uni_row = db.execute(select(University).where(University.slug == body.university_slug)).scalars().first()
        uni_id = uni_row.id if uni_row else None
        if uni_ids is not None and uni_id not in uni_ids:
            # Both filters were given and they disagree: the university is outside the stated
            # country, so the answer is empty rather than whichever filter was read last.
            return []
    if uni_id is not None:
        # The university is the narrower filter and it is already known to be inside the country.
        rows = matching.match_faculty(db, body.interests, university_id=uni_id,
                                      field=body.field, limit=body.limit)
    else:
        rows = matching.match_faculty(db, body.interests, university_ids=uni_ids,
                                      field=body.field, limit=body.limit)
    db.add(SearchQueryLog(query=", ".join(body.interests), country=body.country_slug, degree_level="PHD",
                          subject=body.field, results_count=len(rows), channel="FACULTY_SEARCH"))
    db.commit()
    return [{**S.professor(r["professor"], full=True, matched=r["matched_interests"], evidence=r["evidence"]),
             "match_score": r["score"]} for r in rows]


@router.post("/brochures/generate")
def generate_brochure(body: BrochureIn, db: Session = Depends(get_db)):
    p = _resolve_program(db, body.program_id, body.program_slug)
    student = body.profile.model_dump()
    row = brochure_service.create(db, p, student, body.target_intake, generated_by=body.channel)
    db.add(AnalyticsEvent(name="brochure_generated", entity_type="program", entity_id=p.id,
                          meta={"degree_level": p.degree_level, "channel": body.channel}))
    db.commit()
    return {"token": row.token, "version": row.version, "degree_level": row.degree_level,
            "url": f"/brochure/{row.token}", "payload": row.payload}


@router.get("/brochures/{token}")
def get_brochure(token: str, db: Session = Depends(get_db)):
    row = db.execute(select(Brochure).where(Brochure.token == token)).scalars().first()
    if not row:
        raise HTTPException(404, "Brochure not found")
    row.downloads = (row.downloads or 0) + 1
    db.commit()
    return {"token": row.token, "version": row.version, "student_name": row.student_name,
            "degree_level": row.degree_level, "target_intake": row.target_intake,
            "created_at": S._dt(row.created_at), "payload": row.payload}


@router.post("/leads")
def create_lead(body: LeadIn, request: Request, db: Session = Depends(get_db)):
    profile_id = None
    if body.profile:
        profile_id = _save_profile(db, body.profile).id
    # Persistence boundary. ``page_path`` and ``utm`` are client-supplied, and this endpoint is
    # unauthenticated, so the browser's analytics gate is not a guarantee here. A portal link is
    # a bearer capability: it must not land in a CRM row or the analytics table. Attribution is
    # unaffected - only the path is normalised and only portal segments inside ``utm`` are
    # rewritten, so ``utm_campaign`` still resolves to the same ``campaign_id``.
    safe_path = safe_source_url(body.page_path)
    safe_utm = scrub_analytics_value(dict(body.utm or {}), ignore_case=True)
    row = Lead(reference=_next_reference(db, "LEAD"),
               full_name=body.full_name, phone=body.phone, email=body.email, message=body.message,
               interest_country=body.interest_country, interest_degree=body.interest_degree,
               interest_subject=body.interest_subject, intake=body.intake, source=body.source,
               channel_detail=body.channel_detail, utm=safe_utm, page_path=safe_path,
               campaign_id=_campaign_from_utm(db, safe_utm), student_profile_id=profile_id,
               consent_contact=body.consent_contact, stage="NEW")
    db.add(row)
    db.add(AnalyticsEvent(name="lead_created", path=safe_path, utm=safe_utm,
                          meta={"source": body.source}))
    db.add(Notification(channel="WHATSAPP", recipient=body.phone, trigger="LEAD_CREATED",
                        title="We received your request",
                        body=f"Thank you {body.full_name}. A counselor will contact you shortly."))
    db.commit()
    db.refresh(row)
    wa = whatsapp.deep_link(db, f"Hello, I just submitted an enquiry (Ref: {row.reference}).",
                            {"Country": body.interest_country, "Degree": body.interest_degree,
                             "Subject": body.interest_subject})
    return {"reference": row.reference, "id": row.id, "whatsapp_url": wa,
            "message": "Thank you. A counselor will contact you shortly."}


def _booking_options(db: Session) -> Dict[str, Any]:
    """The appointment options currently on offer, straight from admin settings."""
    row = db.get(SiteSetting, 1)

    def pick(vals):
        return [str(v.get("value")) if isinstance(v, dict) else str(v) for v in (vals or [])]

    return {
        "service_types": pick(getattr(row, "appointment_service_types", None)),
        "modes": pick(getattr(row, "appointment_modes", None)),
        "slots": pick(getattr(row, "appointment_slots", None)),
        "lead_days": int(getattr(row, "appointment_lead_days", 1) or 0),
    }


def _validate_booking(db: Session, body: "AppointmentIn") -> tuple[str, str]:
    """
    Check the submitted service type, mode and time against what the office actually offers.

    These lists are admin-editable, so they cannot be a Pydantic Literal or a frontend enum -
    the authoritative list lives in the database and is checked here. An empty configured list
    means "no restriction configured", which keeps older installs working. Returns the
    resolved (service_type, mode), falling back to the first configured option.
    """
    opts = _booking_options(db)

    service = body.service_type or (opts["service_types"][0] if opts["service_types"] else None)
    if opts["service_types"] and service not in opts["service_types"]:
        raise HTTPException(400, f"Unknown service type. Allowed: {', '.join(opts['service_types'])}")

    mode = body.mode or (opts["modes"][0] if opts["modes"] else None)
    if opts["modes"] and mode not in opts["modes"]:
        raise HTTPException(400, f"Unknown appointment mode. Allowed: {', '.join(opts['modes'])}")

    if opts["slots"] and body.scheduled_time not in opts["slots"]:
        raise HTTPException(400, f"That time is not bookable. Available: {', '.join(opts['slots'])}")

    earliest = date.today() + timedelta(days=opts["lead_days"])
    if body.scheduled_date < earliest:
        raise HTTPException(400, f"The earliest bookable date is {earliest.isoformat()}.")

    if not service or not mode:
        raise HTTPException(400, "Appointment booking is not configured. Please contact the office.")
    return service, mode


@router.post("/appointments")
def create_appointment(body: AppointmentIn, db: Session = Depends(get_db)):
    service_type, mode = _validate_booking(db, body)
    row = Appointment(reference=_next_reference(db, "APPT"), full_name=body.full_name, phone=body.phone,
                      email=body.email, service_type=service_type, counselor_name=body.counselor_name,
                      scheduled_date=body.scheduled_date, scheduled_time=body.scheduled_time,
                      mode=mode, notes=body.notes, status="REQUESTED",
                      consent_contact=body.consent_contact)
    db.add(row)
    # Same boundary as /leads: the booking form is public and unauthenticated, so the browser's
    # ``utm`` cannot be trusted. Campaign attribution reads the scrubbed copy too, so a private
    # path cannot reach either the stored row or the campaign lookup.
    safe_utm = scrub_analytics_value(dict(body.utm or {}), ignore_case=True)
    lead = Lead(reference=_next_reference(db, "LEAD"), full_name=body.full_name, phone=body.phone,
                email=body.email, message=f"Appointment request: {service_type}",
                source="WEBSITE", channel_detail="appointment", utm=safe_utm,
                campaign_id=_campaign_from_utm(db, safe_utm), stage="CONTACTED",
                consent_contact=body.consent_contact)
    db.add(lead)
    db.add(Notification(channel="WHATSAPP", recipient=body.phone, trigger="APPOINTMENT_REQUESTED",
                        title="Appointment requested",
                        body=f"{body.full_name}, your {service_type.replace('_', ' ').lower()} session request for "
                             f"{body.scheduled_date} {body.scheduled_time} has been received."))
    db.commit()
    db.refresh(row)
    return {"reference": row.reference, "status": row.status,
            "whatsapp_url": whatsapp.deep_link(db, f"Hello, I booked an appointment (Ref: {row.reference})."),
            "message": "Appointment requested. Our team will confirm the slot shortly."}


@router.post("/events/register")
def register_event(body: EventRegIn, db: Session = Depends(get_db)):
    ev = db.get(Event, body.event_id)
    if not ev:
        raise HTTPException(404, "Event not found")
    row = EventRegistration(event_id=ev.id, full_name=body.full_name, phone=body.phone, email=body.email,
                            preferred_country=body.preferred_country, degree_level=body.degree_level,
                            consent_contact=body.consent_contact)
    db.add(row)
    db.add(Lead(reference=_next_reference(db, "LEAD"), full_name=body.full_name, phone=body.phone,
                email=body.email, message=f"Event registration: {ev.title}", source="EVENT",
                channel_detail=ev.slug, stage="NEW", interest_country=body.preferred_country,
                interest_degree=body.degree_level, consent_contact=body.consent_contact))
    db.add(Notification(channel="WHATSAPP", recipient=body.phone, trigger="EVENT_REGISTERED",
                        title=ev.title, body=f"You are registered for {ev.title} on {ev.start_at:%d %b %Y, %I:%M %p}."))
    db.commit()
    return {"status": "REGISTERED", "event": ev.title,
            "reminder": "A WhatsApp and email reminder will be sent before the event."}


@router.post("/profiles")
def save_profile(body: ProfileIn, db: Session = Depends(get_db)):
    row = _save_profile(db, body)
    return {"token": row.public_token, "id": row.id}


@router.get("/profiles/{token}")
def get_profile(token: str, db: Session = Depends(get_db)):
    row = db.execute(select(StudentProfile).where(StudentProfile.public_token == token)).scalars().first()
    if not row:
        raise HTTPException(404, "Profile not found")
    return {c.name: S._dt(getattr(row, c.name)) for c in row.__table__.columns}


@router.post("/track")
def track(body: TrackIn, db: Session = Depends(get_db)):
    # The browser boundary strips these before sending; this is the server-side fail-closed
    # backstop for a compromised or older client. Behaviour here is unchanged from W4: the
    # helpers moved to app.core.privacy but their semantics are identical.
    utm = scrub_analytics_value(dict(body.utm or {}))
    meta = scrub_analytics_value(dict(body.meta or {}))
    db.add(AnalyticsEvent(name=body.name, path=safe_analytics_path(body.path),
                          entity_type=body.entity_type, entity_id=body.entity_id,
                          utm=utm, meta=meta,
                          campaign_id=_campaign_from_utm(db, utm)))
    db.commit()
    return {"ok": True}


@router.get("/whatsapp/link")
def whatsapp_link(message: Optional[str] = None, context: Optional[str] = None, db: Session = Depends(get_db)):
    ctx = {}
    if context:
        for part in context.split("|"):
            if ":" in part:
                k, v = part.split(":", 1)
                ctx[k.strip()] = v.strip()
    return {"url": whatsapp.deep_link(db, message or "", ctx)}
