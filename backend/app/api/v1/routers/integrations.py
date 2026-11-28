"""WhatsApp webhook/send, assistant endpoint, Meta Conversions API + ads helpers."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import assistant_sessions as sessions
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_internal_caller, require_role, require_staff
from app.core.privacy import safe_source_url
from app.core.webhooks import require_meta_signature
from app.models.content import AdCampaign, SiteSetting
from app.models.crm import (
    ANALYTICS_EVENT_NAME_MAX,
    AnalyticsEvent,
    AssistantSession,
    AuditLog,
    Lead,
    StudentProfile,
    User,
)
from app.services import assistant, assistant_engine, context_slots, domain_gate, read_audit, whatsapp

router = APIRouter(tags=["integrations"])

logger = logging.getLogger(__name__)

#: The relay persists ``f"conversion_{event_name.lower()}"`` into ``analytics_events.name``, so the
#: caller's name gets ``len("conversion_") == 11`` characters less room than the column itself.
#: This is the real requirement, not a round number: the composed name must fit the column, and the
#: budget is derived from it so the two cannot drift apart.
CONVERSION_PREFIX = "conversion_"
CONVERSION_EVENT_NAME_MAX = ANALYTICS_EVENT_NAME_MAX - len(CONVERSION_PREFIX)
#: Meta's Conversions API accepts standard event names and custom ones built from letters, digits,
#: underscores and hyphens. Anything else is not a name the vendor can recognise, and it must not
#: become a row in our own analytics table either.
EVENT_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"


# ------------------------------------------------------------------ assistant
class AskIn(BaseModel):
    """One assistant question.

    There is no ``context`` field. It used to exist and was never read (F-7): the trusted context is
    built server-side from the session's own profile (``_profile_context`` and
    ``context_slots.effective``), so a client-sent context object could only ever be silently
    dropped. A field that cannot influence the answer is not accepted at the boundary at all, so a
    caller can no longer believe it supplied trusted facts.
    """

    model_config = ConfigDict(extra="forbid")

    question: str
    session_id: Optional[str] = None
    profile_token: Optional[str] = None


class SessionIn(BaseModel):
    profile_token: Optional[str] = None


def _check_origin(request: Request) -> None:
    """Reject a browser request from an origin that is not configured for this site.

    Requests with no Origin/Referer are non-browser callers (tests, mobile, server-to-server)
    and are allowed; the CSRF token still protects cookie-authenticated state changes.
    """
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin:
        referer = (request.headers.get("referer") or "").strip()
        if referer:
            from urllib.parse import urlsplit
            parsed = urlsplit(referer)
            origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    if not origin:
        return
    allowed = list(settings.cors_origin_list)
    if "*" in allowed:
        from app.core.config import normalize_origin
        allowed = [normalize_origin(settings.public_site_url)]
        if not settings.is_production:
            # Local development is served over plain HTTP on loopback. Anything else is still
            # refused, so a wildcard CORS setting cannot turn into cross-site session writes.
            import re as _re
            if _re.fullmatch(r"https?://(localhost|127\.0\.0\.1)(:\d+)?", origin):
                return
    if origin not in allowed:
        raise HTTPException(403, "This origin is not allowed to change an assistant session.")


def _session_row(db: Session, request: Request):
    raw = request.cookies.get(sessions.COOKIE_NAME)
    if not raw:
        return None
    return sessions.resolve(db, raw)


def _require_csrf(request: Request, row) -> None:
    if not sessions.verify_csrf(row, request.headers.get(sessions.CSRF_HEADER)):
        raise HTTPException(403, "Missing or invalid CSRF token for this session.")


def _profile_context(db: Session, profile_id: Optional[int]) -> Dict[str, Any]:
    """Only a profile-bound session reaches this function. Never a client-supplied token."""
    if not profile_id:
        return {}
    profile = db.get(StudentProfile, int(profile_id))
    if profile is None:
        return {}
    return {
        "profile": {
            "cgpa": profile.cgpa, "cgpa_scale": profile.cgpa_scale,
            "ielts_overall": profile.ielts_overall,
            "target_degree_level": profile.target_degree_level,
            "target_discipline": profile.target_discipline,
            "budget_per_year": profile.budget_per_year,
            "preferred_countries": profile.preferred_countries,
            "research_interests": profile.research_interests,
        },
        "research_interests": profile.research_interests or [],
    }


# ------------------------------------------------------------ W4 session routes
@router.post("/assistant/session")
def create_assistant_session(body: SessionIn, request: Request, response: Response,
                             db: Session = Depends(get_db)):
    """Issue a session. The credential goes into an HttpOnly host-only cookie, never the body."""
    _check_origin(request)
    try:
        credential, row = sessions.issue(db, profile_token=body.profile_token)
    except sessions.SessionError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    sessions.set_cookie(response, credential, row)
    return {**sessions.public_state(row), "csrf_token": sessions.csrf_token(row)}


@router.get("/assistant/session")
def get_assistant_session(request: Request, db: Session = Depends(get_db)):
    row = _session_row(db, request)
    if row is None:
        raise HTTPException(401, "No active assistant session.")
    return {**sessions.public_state(row), "csrf_token": sessions.csrf_token(row)}


@router.post("/assistant/session/bind")
def bind_assistant_session(body: SessionIn, request: Request, response: Response,
                           db: Session = Depends(get_db)):
    """Explicitly bind an anonymous session to a profile and rotate the credential."""
    _check_origin(request)
    if not body.profile_token:
        raise HTTPException(400, "A profile token is required to bind a session.")
    row = _session_row(db, request)
    if row is None:
        raise HTTPException(401, "No active assistant session.")
    _require_csrf(request, row)
    credential: Optional[str]
    try:
        if row.ownership == sessions.ANONYMOUS:
            credential, row = sessions.bind_profile(db, row, body.profile_token)
        else:
            # Explicit A -> B transition. A matching token is a no-op; a different verified
            # token rotates the credential and binding atomically.
            credential, row = sessions.switch_profile(db, row, body.profile_token)
    except sessions.SessionError as exc:
        if row.ownership == sessions.PROFILE:
            sessions.revoke(db, row, "PROFILE_REPLACED")
            sessions.clear_cookie(response)
        raise HTTPException(exc.status, exc.detail) from exc
    if credential:
        sessions.set_cookie(response, credential, row)
    return {**sessions.public_state(row), "csrf_token": sessions.csrf_token(row)}


@router.post("/assistant/session/rotate")
def rotate_assistant_session(request: Request, response: Response,
                             db: Session = Depends(get_db)):
    _check_origin(request)
    row = _session_row(db, request)
    if row is None:
        raise HTTPException(401, "No active assistant session.")
    _require_csrf(request, row)
    try:
        credential, row = sessions.rotate(db, row)
    except sessions.SessionError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    sessions.set_cookie(response, credential, row)
    return {**sessions.public_state(row), "csrf_token": sessions.csrf_token(row)}


@router.post("/assistant/session/revoke")
def revoke_assistant_session(request: Request, response: Response,
                             db: Session = Depends(get_db)):
    _check_origin(request)
    row = _session_row(db, request)
    if row is None:
        sessions.clear_cookie(response)
        return {"revoked": False}
    _require_csrf(request, row)
    revoked = sessions.revoke(db, row, "USER_REVOKED")
    sessions.clear_cookie(response)
    return {"revoked": revoked}


def _keeps_pending(result: dict) -> bool:
    """Whether an unanswered slot question survives this turn.

    A clarification the assistant itself asked (``mode="clarify"`` with no slot) is an aside -
    "which subject do you mean?" - and must not throw away the question already in flight. A
    refused or degraded turn is likewise about something else. Only a turn that actually answers
    the student, or a new slot clarification, changes the pending state.
    """
    mode = result.get("mode")
    if result.get("clarify_slot"):
        return False
    return mode in ("clarify", "refused", "degraded")


def _pending_after(question: str, result: dict) -> Optional[str]:
    """The slot question this turn leaves in flight, if any."""
    if not result.get("clarify_slot"):
        return None
    return assistant.detect_intent(question)


@router.post("/assistant/ask")
def ask(body: AskIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """RAG + tool-calling answer, grounded in the verified database only.

    Identity comes only from a verified server-owned session cookie. The legacy
    ``session_id`` remains as correlation/audit metadata and never grants profile access or
    follow-up permission. For a profile-bound session, ``profile_token`` is compared with the
    stored keyed fingerprint: a missing, replaced or invalid token revokes the session and is
    never used to change ownership. Binding itself remains explicit on the bind route.
    """
    _check_origin(request)
    row = _session_row(db, request)
    if row is not None and request.method == "POST":
        _require_csrf(request, row)
    if row is None and request.cookies.get(sessions.COOKIE_NAME):
        # Expired/revoked/forged: clear it and continue anonymously. Never a 500, never a guess.
        sessions.clear_cookie(response)

    if row is not None and row.ownership == sessions.PROFILE:
        # A profile-bound session is only valid while the browser still presents the same
        # portal profile. Missing, replaced or invalid tokens revoke it rather than silently
        # continuing with the old student's data.
        if not sessions.matches_profile(row, body.profile_token):
            sessions.revoke(db, row, "PROFILE_MISMATCH")
            sessions.clear_cookie(response)
            raise HTTPException(409, "Your profile changed. Start a new assistant session.")

    profile_id = row.student_profile_id if row is not None and row.ownership == sessions.PROFILE else None
    # Client context is never identity, and (W5) it is never a student attribute either. The
    # answer's context is an *allowlist* built here - the verified profile, then the resolved
    # session slots - rather than the request body with a few keys removed. A blocklist has to
    # name every key that matters, and it missed one: `excluded_countries` reached the scope
    # resolver, so a caller could seed a retrieval filter (and a malformed value - `42`, or a list
    # holding an integer - raised while building the scope, turning a bad body into a 500 on an
    # anonymous endpoint). Starting from an empty mapping means the set of things the engine can
    # depend on is exactly the set of things this function writes, and it cannot grow by accident.
    context: Dict[str, Any] = {}
    context.update(_profile_context(db, profile_id))

    # ---- W5: conversation context ---------------------------------------
    # The stored context belongs to this session row, so it inherits W4's ownership and W7's
    # erasure behaviour. Reading it is safe; writing it happens only after revalidation below.
    catalogue = context_slots.known_countries(db) if row is not None else None
    slot_state = (context_slots.load(row, known_countries=catalogue)
                  if row is not None else context_slots.empty_context())
    reading = (context_slots.interpret(db, body.question) if row is not None
               else {"affirmed": {}, "rejected": [], "ambiguous": []})
    slot_state = context_slots.merge(slot_state, reading["affirmed"],
                                     forget=context_slots.forget_requested(body.question),
                                     rejected=reading["rejected"],
                                     ambiguous=reading["ambiguous"],
                                     known_countries=catalogue)
    profile_ctx = context.get("profile") or {}
    preferred = profile_ctx.get("preferred_countries") or []
    effective = context_slots.effective(
        db, slot_state,
        profile_country=(preferred[0] if preferred else ""),
        profile_degree=(profile_ctx.get("target_degree_level") or ""))
    context.update({name: value for name, value in effective.items() if value})

    # Legacy, non-authoritative correlation only. The engine does not read it for continuity.
    legacy_session_id = body.session_id or request.headers.get("x-session-id") or "anonymous"
    start_generation = int(row.credential_generation or 0) if row is not None else 0
    start_profile = row.student_profile_id if row is not None else None
    if row is not None:
        # A real ask is activity, so idle expiry measures inactivity rather than age.
        sessions.touch(db, row)
    result = assistant_engine.ask(db, body.question, context=context,
                                  session_id=legacy_session_id,
                                  student_profile_id=profile_id, session=row,
                                  slot_state=slot_state)
    if row is not None and not sessions.revalidate(
            db, row, generation=start_generation, profile_id=start_profile):
        # Generation finished after the session was rotated, rebound or revoked. The answer
        # belongs to a superseded identity and must not be returned as this request's result -
        # and neither may its slot changes, so nothing is persisted on this path.
        raise HTTPException(409, "Your assistant session changed. Please ask again.")
    # An explicit "start over" is honoured whatever the assistant decided about the question:
    # a student asking to clear the conversation must not be told "no" because their wording
    # was also out of scope.
    forget_now = context_slots.forget_requested(body.question)
    mode = result.get("mode")
    # An explicit exclusion is an instruction about the student's own preferences, not a fact
    # learned from an answer. It is honoured even when the assistant declines to answer the
    # question, because leaving a rejected destination active would keep narrowing later
    # searches. An affirmation is different: that still only counts on a turn that was answered.
    honour_removal = bool(reading["rejected"] or reading["ambiguous"])
    if row is not None and (forget_now or honour_removal
                            or mode not in ("refused", "degraded")):
        # Only now, with the identity confirmed, may the context be written. The write is a
        # compare-and-swap against the revision this request read, so a concurrent turn or a
        # revocation that lands between the check and the write cannot be overwritten.
        #
        # A refused or degraded turn teaches us nothing about the student, so nothing is learned
        # from it. And this is a convenience, never the answer: if the write itself fails the
        # answer is still returned, because losing a slot must not cost a student their reply.
        try:
            stored_reading = reading
            if not forget_now and mode in ("refused", "degraded"):
                # A refused turn may remove a constraint but must not add one.
                stored_reading = {"affirmed": {}, "rejected": reading["rejected"],
                                  "ambiguous": reading["ambiguous"]}
            slot_state = context_slots.persist_turn(
                db, row, question=body.question, generation=start_generation,
                profile_id=profile_id, asked_slot=result.get("clarify_slot"),
                pending_intent=_pending_after(body.question, result),
                keep_pending=_keeps_pending(result), reading=stored_reading,
                known_countries=catalogue)
        except Exception:  # pragma: no cover - defensive
            db.rollback()
            logger.warning("assistant context could not be persisted for this turn")
    result["context"] = context_slots.summary(slot_state)
    result["session"] = sessions.public_state(row) if row is not None else None
    # The question itself is never sent to analytics: it is the student's own words, possibly
    # with a CGPA, a budget or an email address in it. Behaviour is recorded as metadata only.
    db.add(AnalyticsEvent(name="assistant_question", meta={
        "intent": result["intent"], "grounded": result["grounded"],
        "escalated": result["escalated"], "mode": result.get("mode"),
        "domain_decision": (result.get("domain") or {}).get("decision"),
        "domain_reason": (result.get("domain") or {}).get("reason"),
    }))
    db.commit()
    return result


@router.get("/assistant/policy")
def assistant_policy():
    """The assistant's contract: one fixed refusal, three gate decisions, one trust policy.

    The frontend renders the refusal from here rather than hard-coding a second copy, so there
    is exactly one wording in the product.
    """
    from app.core import verification_policy

    return {
        "domain": domain_gate.policy(),
        "verification": verification_policy.deployment_summary(),
        "answers": {
            "grounding": "Only records that pass the verification policy are quoted.",
            "abstention": "If a figure is not verified and current, the assistant says so.",
            "no_fabrication": "The assistant never invents a fee, requirement, deadline or outcome.",
        },
    }


@router.get("/assistant/health")
def assistant_health(db: Session = Depends(get_db)):
    """Corpus readiness, schema drift and policy version - counts and versions only.

    Deliberately public: the widget needs to know whether it can answer at all. It exposes no
    connection details, no table names, no student data and no credentials.
    """
    from app.core import diagnostics

    health = diagnostics.assistant_health(db)
    health["gate"] = {"domain_gate_enabled": bool(settings.domain_gate_enabled),
                      "semantic_tiebreak_enabled": bool(settings.domain_semantic_enabled),
                      "refusal": domain_gate.DOMAIN_REFUSAL}
    return health


@router.get("/assistant/tools")
def tools():
    return {"tools": assistant.TOOL_SPECS,
            "policy": "The assistant answers only from records that pass the verification policy. "
                      "If a value is not stored, verified and current, it declines to answer.",
            "refusal": domain_gate.DOMAIN_REFUSAL}


# ------------------------------------------------------- W4 admin (least privilege)
@router.get("/admin/assistant/sessions")
def admin_assistant_sessions(active: bool = True, limit: int = 100,
                             db: Session = Depends(get_db),
                             user: User = Depends(require_role("ADMIN"))):
    """Non-sensitive session metadata only. Never conversation content or credentials."""
    stmt = select(AssistantSession).order_by(AssistantSession.id.desc()).limit(min(max(limit, 1), 200))
    if active:
        stmt = stmt.where(AssistantSession.revoked_at.is_(None))
    rows = db.execute(stmt).scalars().all()
    # One event per request. The returned rows carry a session reference, a profile id and
    # activity timestamps, so this is a staff read of student-linked data under D3. Nothing from
    # the rows, the ``active`` filter or the ``limit`` is copied into the event.
    try:
        read_audit.audit_student_read(db, user, resource="assistant-sessions", operation="list")
    except read_audit.AuditUnavailable:
        raise HTTPException(
            503, "This view is unavailable because the access could not be recorded."
        ) from None
    return {"items": [{**sessions.public_state(r),
                       "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
                       "revoked_reason": r.revoked_reason} for r in rows]}


@router.post("/admin/assistant/sessions/{sid}/revoke")
def admin_revoke_assistant_session(sid: str, db: Session = Depends(get_db),
                                   user: User = Depends(require_role("ADMIN"))):
    ok = sessions.revoke_by_sid(db, sid, "ADMIN_REVOKED")
    db.add(AuditLog(actor_id=user.id, actor_email=user.email,
                    action="ASSISTANT_SESSION_REVOKED", entity_type="assistant_session",
                    detail={"sid": sid, "revoked": ok}))
    db.commit()
    if not ok:
        raise HTTPException(404, "No active session with that id.")
    return {"sid": sid, "revoked": True}


# ------------------------------------------------------------------ WhatsApp
@router.get("/whatsapp/webhook")
def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == settings.whatsapp_verify_token:
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "Verification failed")


@router.post("/whatsapp/webhook")
async def receive_webhook(raw: bytes = Depends(require_meta_signature), db: Session = Depends(get_db)):
    """Signature-verified by the dependency; an unsigned call never reaches this body."""
    body = json.loads(raw or b"{}")
    replies = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts = {c["wa_id"]: c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                wa_id = msg.get("from")
                text = (msg.get("text") or {}).get("body", "")
                result = whatsapp.handle_text(db, wa_id, contacts.get(wa_id, ""), text)
                await whatsapp.send_message(wa_id, result["reply"])
                replies.append(result)
    return {"status": "ok", "handled": len(replies)}


class SimulateIn(BaseModel):
    wa_id: str = "8801700000000"
    name: str = "Test Student"
    text: str


@router.post("/whatsapp/simulate")
def simulate(body: SimulateIn, db: Session = Depends(get_db)):
    """Lets the website (and the admin panel) exercise the exact WhatsApp bot flow without credentials."""
    return whatsapp.handle_text(db, body.wa_id, body.name, body.text)


class SendIn(BaseModel):
    to: str
    body: str


@router.post("/whatsapp/send")
async def send(body: SendIn, user: User = Depends(require_staff)):
    return await whatsapp.send_message(body.to, body.body)


# ------------------------------------------------------------------ Facebook / ads
@router.get("/marketing/links")
def marketing_links(campaign: Optional[str] = None, db: Session = Depends(get_db)):
    """Everything the marketing team needs to point an ad at this site."""
    s = db.get(SiteSetting, 1)
    if s is None:  # created by migration/seed; absence is a real outage, not empty links
        raise HTTPException(status_code=503, detail="Site settings are not initialised")
    base = (s.site_url or "").rstrip("/")
    camp = None
    if campaign:
        camp = db.execute(select(AdCampaign).where(AdCampaign.slug == campaign)).scalars().first()
    def tracked(path: str, source: str, medium: str, name: str, content: Optional[str] = None) -> str:
        params = {"utm_source": source, "utm_medium": medium, "utm_campaign": name}
        if content:
            params["utm_content"] = content
        return f"{base}{path}?{urlencode(params)}"
    links = {
        "facebook_page": s.facebook_page_url,
        "messenger": s.messenger_url or (f"https://m.me/{s.facebook_page_id}" if s.facebook_page_id else None),
        "whatsapp_click_to_chat": whatsapp.deep_link(db),
        "facebook_ads_manager": "https://www.facebook.com/adsmanager/manage/campaigns",
        "meta_business_suite": "https://business.facebook.com/latest/home",
        "google_ads": "https://ads.google.com/aw/campaigns",
        "google_search_console": "https://search.google.com/search-console",
        "google_business_profile": "https://business.google.com/",
        "default_landing_links": {
            "home": tracked("/", "facebook", "paid_social", "always_on"),
            "profile_assessment": tracked("/profile-assessment", "facebook", "paid_social", "eligibility_campaign"),
            "scholarships": tracked("/scholarships", "facebook", "paid_social", "scholarship_campaign"),
            "phd": tracked("/phd", "facebook", "paid_social", "phd_campaign"),
            "appointment": tracked("/appointment", "google", "cpc", "book_counseling"),
        },
    }
    if camp:
        links["campaign"] = {
            **{"name": camp.name, "channel": camp.channel, "status": camp.status},
            "landing_url": tracked(camp.landing_path, camp.utm_source, camp.utm_medium,
                                   camp.utm_campaign, camp.utm_content),
            "whatsapp_url": whatsapp.deep_link(db, camp.whatsapp_prefill or camp.headline or ""),
            "pixel_event": camp.pixel_event,
        }
    return links


class ConversionIn(BaseModel):
    """One server-side conversion event, for our own server or an operator's runner.

    No personal identifier is accepted, and that is the point of this shape.

    The Conversions API can take hashed e-mail and phone to improve ad matching. Scholara's only
    consent control is ``consent_contact``, and its wording is "I agree to be contacted about my
    enquiry on WhatsApp, phone or email" - permission to be *contacted about an enquiry*, not
    permission to disclose an identifier to an advertising platform for matching. A hash is a
    pseudonym, not anonymity: Meta matches on it, so sending it is a disclosure the visitor never
    agreed to. Until a dedicated marketing-matching consent exists (an explicit product and legal
    decision, recorded in the docs as an open item), this endpoint forwards no identifier at all -
    neither raw nor hashed. It also cannot be used to relay somebody else's address, because it
    accepts no address in the first place.

    ``lead_reference`` is our own reference (``SCH-...``), not an identifier: it lets the caller
    tie the event to a record we already hold, and it is never sent to Meta.
    """

    model_config = ConfigDict(extra="forbid")

    event_name: str = Field(default="Lead", min_length=1, max_length=CONVERSION_EVENT_NAME_MAX,
                            pattern=EVENT_NAME_PATTERN)
    event_source_url: Optional[str] = None
    value: Optional[float] = None
    currency: str = Field(default="BDT", min_length=3, max_length=3)
    lead_reference: Optional[str] = None

    @field_validator("event_name")
    @classmethod
    def _bounded_event_name(cls, value: str) -> str:
        """Reject, never truncate: a shortened event name is a different event (F-5).

        The bound above is Pydantic's, which answers 422 with the field name. This second check
        exists because the composed column value is what the database constrains: if the prefix
        ever changes, the failure has to stay a 422 rather than become PostgreSQL's 500 again.
        """
        if len(f"{CONVERSION_PREFIX}{value.lower()}") > ANALYTICS_EVENT_NAME_MAX:
            raise ValueError(
                f"event_name must be at most {CONVERSION_EVENT_NAME_MAX} characters")
        return value

    @field_validator("currency")
    @classmethod
    def _iso_currency(cls, value: str) -> str:
        """An ISO 4217 code, upper-cased. Anything else is refused rather than forwarded."""
        code = (value or "").strip().upper()
        if len(code) != 3 or not code.isalpha() or not code.isascii():
            raise ValueError("currency must be a three-letter ISO 4217 code")
        return code


@router.post("/marketing/conversion")
async def server_conversion(body: ConversionIn, db: Session = Depends(get_db),
                            _caller: None = Depends(require_internal_caller)):
    """Meta Conversions API relay (server-side ad attribution), without personal identifiers.

    Three boundaries, each enforced here rather than by convention:

    * **Server-to-server only.** The route requires the internal token, so an arbitrary internet
      client cannot push events - the open-relay and conversion-inflation path is closed, and the
      caller is identified by the deployment rather than by its payload.
    * **No identifier leaves the platform.** The payload carries the event name, value, currency,
      time and a bare landing path, and no ``user_data`` at all. A hash of an address is still that
      person's data, and nothing the product collects authorizes disclosing it to an ad network.
    * **A truthful landing page.** ``event_source_url`` is normalised to a bare path before it is
      persisted or relayed, so a query string (``?email=...``) or a private portal link cannot
      leave the platform through the vendor call or land in the analytics table.

    Vendor failures are reported as a bare status code. The access token travels as a query
    parameter, so an exception from the client library could otherwise quote a URL that contains
    it - the reason the call is wrapped and the response body is not echoed to the caller.
    """
    source_path = safe_source_url(body.event_source_url)
    lead_id: Optional[int] = None
    if body.lead_reference:
        lead = db.execute(select(Lead).where(
            Lead.reference == body.lead_reference)).scalars().first()
        if lead is None:
            # A conversion attributed to a record we do not hold is a bug in the caller, not a
            # reason to invent one. Refuse before anything is persisted or forwarded.
            raise HTTPException(422, "That lead reference does not exist.")
        lead_id = lead.id

    db.add(AnalyticsEvent(name=f"{CONVERSION_PREFIX}{body.event_name.lower()}", path=source_path,
                          meta={"value": body.value, "currency": body.currency}))
    db.commit()

    s = db.get(SiteSetting, 1)
    pixel_id = (s.meta_pixel_id if s else None) or settings.meta_pixel_id
    token = settings.meta_capi_access_token
    if not (pixel_id and token):
        return {"status": "LOGGED_LOCALLY",
                "note": "Set META_CAPI_ACCESS_TOKEN and a pixel id to forward to Meta."}
    import time

    import httpx

    # Omit the URL rather than send a null or the original: a vendor event without a landing
    # page is recoverable (the browser pixel carries it), a leaked capability is not.
    event: Dict[str, Any] = {"event_source_url": source_path} if source_path else {}
    payload = {"data": [{
        **event, "event_name": body.event_name, "event_time": int(time.time()),
        "action_source": "website",
        # Deliberately absent: user_data. See the class docstring - no consent covers it.
        "custom_data": {"value": body.value, "currency": body.currency},
    }]}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{settings.meta_graph_base_url}/{pixel_id}/events",
                                  params={"access_token": token}, json=payload)
    except Exception as exc:                      # noqa: BLE001 - never echo the URL or token
        logger.warning("conversion relay failed for event=%s (%s)",
                       body.event_name, type(exc).__name__)
        return {"status": "RELAY_FAILED"}

    if r.status_code >= 400:
        # The vendor body can quote the request; keep it out of our response and our log.
        logger.warning("conversion relay rejected event=%s status=%s lead=%s",
                       body.event_name, r.status_code, lead_id)
    return {"status": r.status_code}
