"""WhatsApp webhook/send, assistant endpoint, Meta Conversions API + ads helpers."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_staff
from app.core.webhooks import require_meta_signature
from app.models.content import AdCampaign, SiteSetting
from app.models.crm import AnalyticsEvent, User
from app.services import assistant, assistant_engine, whatsapp

router = APIRouter(tags=["integrations"])


# ------------------------------------------------------------------ assistant
class AskIn(BaseModel):
    question: str
    context: Dict[str, Any] = {}
    session_id: Optional[str] = None
    profile_token: Optional[str] = None


@router.post("/assistant/ask")
def ask(body: AskIn, request: Request, db: Session = Depends(get_db)):
    """RAG + tool-calling answer, grounded in the verified database only."""
    profile_id = None
    context = dict(body.context or {})
    if body.profile_token:
        from app.models.crm import StudentProfile

        profile = db.execute(
            select(StudentProfile).where(StudentProfile.public_token == body.profile_token)
        ).scalars().first()
        if profile:
            profile_id = profile.id
            context.setdefault("profile", {
                "cgpa": profile.cgpa, "cgpa_scale": profile.cgpa_scale,
                "ielts_overall": profile.ielts_overall,
                "target_degree_level": profile.target_degree_level,
                "target_discipline": profile.target_discipline,
                "budget_per_year": profile.budget_per_year,
                "preferred_countries": profile.preferred_countries,
                "research_interests": profile.research_interests,
            })
            context.setdefault("research_interests", profile.research_interests or [])

    session_id = body.session_id or request.headers.get("x-session-id") or "anonymous"
    result = assistant_engine.ask(db, body.question, context=context,
                                  session_id=session_id, student_profile_id=profile_id)
    db.add(AnalyticsEvent(name="assistant_question", meta={
        "intent": result["intent"], "grounded": result["grounded"],
        "escalated": result["escalated"], "mode": result.get("mode"),
    }))
    db.commit()
    return result


@router.get("/assistant/tools")
def tools():
    return {"tools": assistant.TOOL_SPECS,
            "policy": "The assistant answers only from the verified database. "
                      "If a value is not stored and verified, it declines to answer."}


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
    event_name: str = "Lead"
    event_source_url: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    value: Optional[float] = None
    currency: str = "BDT"


@router.post("/marketing/conversion")
async def server_conversion(body: ConversionIn, db: Session = Depends(get_db)):
    """Meta Conversions API relay (server-side ad attribution). Hashes PII before sending."""
    db.add(AnalyticsEvent(name=f"conversion_{body.event_name.lower()}", path=body.event_source_url,
                          meta={"value": body.value, "currency": body.currency}))
    db.commit()
    s = db.get(SiteSetting, 1)
    pixel_id = (s.meta_pixel_id if s else None) or settings.meta_pixel_id
    token = settings.meta_capi_access_token
    if not (pixel_id and token):
        return {"status": "LOGGED_LOCALLY", "note": "Set META_CAPI_ACCESS_TOKEN and a pixel id to forward to Meta."}
    import time

    import httpx
    def h(v: Optional[str]) -> Optional[str]:
        return hashlib.sha256(v.strip().lower().encode()).hexdigest() if v else None
    payload = {"data": [{"event_name": body.event_name, "event_time": int(time.time()),
                         "action_source": "website", "event_source_url": body.event_source_url,
                         "user_data": {k: v for k, v in {"em": h(body.email), "ph": h(body.phone)}.items() if v},
                         "custom_data": {"value": body.value, "currency": body.currency}}]}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{settings.meta_graph_base_url}/{pixel_id}/events",
                              params={"access_token": token}, json=payload)
        return {"status": r.status_code, "response": r.json()}
