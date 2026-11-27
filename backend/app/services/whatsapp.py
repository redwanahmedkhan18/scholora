"""WhatsApp conversation engine (§41-§46, §87-§88).

Works in two modes:
  * link mode  - deep links (wa.me) that pre-fill context-aware messages. No API key needed.
  * cloud mode - WhatsApp Business Cloud API webhook + send (requires credentials in .env).
The same intent router powers both, and it calls the SAME tools the website uses.
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.content import SiteSetting
from app.models.crm import Lead, WhatsAppContact, WhatsAppConversation, WhatsAppMessage
from app.services import assistant, domain_gate

MENU = """Welcome to {brand}.

How can we help?

1. Find University
2. Find Program
3. Check Eligibility
4. Find Scholarship
5. Find PhD Supervisor
6. Check Tuition
7. Check Intake
8. Generate Brochure
9. Book Counseling
10. Talk to Counselor

Reply with a number or just tell us what you need."""

MENU_MAP = {
    "1": "find_university", "2": "find_program", "3": "check_eligibility", "4": "find_scholarship",
    "5": "find_faculty", "6": "tuition", "7": "intake", "8": "brochure", "9": "appointment", "10": "handoff",
}


def deep_link(db: Session, message: str = "", context: Optional[Dict[str, Any]] = None) -> str:
    row = db.get(SiteSetting, 1)
    number = (row.whatsapp_number if row else "") or ""
    number = "".join(ch for ch in number if ch.isdigit())
    text = message or (row.whatsapp_default_message if row else "") or "Hello, I would like to study abroad."
    if context:
        extras = " | ".join(f"{k}: {v}" for k, v in context.items() if v)
        if extras:
            text = f"{text}\n\n{extras}"
    return f"https://wa.me/{number}?text={urllib.parse.quote(text)}"


def handle_text(db: Session, wa_id: str, profile_name: str, text: str) -> Dict[str, Any]:
    contact = db.execute(select(WhatsAppContact).where(WhatsAppContact.wa_id == wa_id)).scalars().first()
    if not contact:
        contact = WhatsAppContact(wa_id=wa_id, profile_name=profile_name)
        db.add(contact)
        db.commit()
        db.refresh(contact)
    convo = db.execute(select(WhatsAppConversation).where(
        WhatsAppConversation.contact_id == contact.id)).scalars().first()
    if not convo:
        convo = WhatsAppConversation(contact_id=contact.id, state="MENU", context={})
        db.add(convo)
        db.commit()
        db.refresh(convo)

    db.add(WhatsAppMessage(conversation_id=convo.id, direction="IN", body=text))
    site_settings = db.get(SiteSetting, 1)
    brand = site_settings.brand_name if site_settings else "our consultancy"

    stripped = text.strip()
    intent = MENU_MAP.get(stripped) or assistant.detect_intent(stripped)
    ctx: Dict[str, Any] = dict(convo.context or {})

    if stripped.lower() in ("hi", "hello", "menu", "start", "/start"):
        reply, intent = MENU.format(brand=brand), "menu"
    elif intent == "handoff":
        lead = _ensure_lead(db, contact, profile_name, ctx)
        convo.handed_off = True
        reply = (f"A counselor will contact you shortly.\n\nReference: {lead.reference}\n\n"
                 "Your profile, conversation history and shortlisted programmes have been shared with the counselor.")
    elif intent == "appointment":
        lead = _ensure_lead(db, contact, profile_name, ctx)
        reply = ("Please share: preferred date, time and counseling type "
                 "(career / university selection / PhD / scholarship / visa).\n\n"
                 f"Reference: {lead.reference}")
    else:
        # The same counselling domain gate as the website, before any retrieval or tool call.
        # WhatsApp is a second front door; it must not be a way around the restriction. The
        # conversation state supplies the follow-up context, and a refused/clarifying turn never
        # licenses the next one.
        previous_allowed = convo.state not in ("MENU", "CLARIFY", "REFUSED", "DEGRADED", "START")
        gate = domain_gate.classify(stripped, previous_allowed=previous_allowed,
                                    previous_intent=convo.state.lower())
        if gate.decision == domain_gate.REFUSE:
            reply = domain_gate.DOMAIN_REFUSAL
            intent = "refused"
        elif gate.decision == domain_gate.CLARIFY:
            reply = gate.clarification or domain_gate.DEFAULT_CLARIFY
            intent = "clarify"
        else:
            result = assistant.answer(db, stripped, ctx)
            reply = result["answer"]
            if result["citations"]:
                reply += "\n\nSources:\n" + "\n".join(f"- {c['label']}: {c['url']}" for c in result["citations"][:3] if c.get("url"))
            reply += "\n\nReply 10 at any time to talk to a counselor."
            ctx.update({k: v for k, v in result.get("data", {}).items() if k in ("country", "degree_level")})

    convo.context = ctx
    convo.state = intent.upper()
    convo.last_message_at = datetime.now(timezone.utc)
    db.add(WhatsAppMessage(conversation_id=convo.id, direction="OUT", body=reply, intent=intent))
    db.commit()
    return {"reply": reply, "intent": intent, "conversation_id": convo.id, "handed_off": convo.handed_off}


def _ensure_lead(db: Session, contact: WhatsAppContact, profile_name: str, ctx: Dict[str, Any]) -> Lead:
    if contact.lead_id:
        lead = db.get(Lead, contact.lead_id)
        if lead:
            return lead
    year = datetime.now(timezone.utc).year
    count = db.execute(select(Lead)).scalars().all()
    lead = Lead(reference=f"LEAD-{year}-{len(count) + 1:06d}", full_name=profile_name or "WhatsApp user",
                phone=contact.wa_id, source="WHATSAPP", stage="NEW",
                interest_country=ctx.get("country"), interest_degree=ctx.get("degree_level"))
    db.add(lead)
    db.commit()
    db.refresh(lead)
    contact.lead_id = lead.id
    db.commit()
    return lead


async def send_message(to: str, body: str) -> Dict[str, Any]:
    """Cloud API send. No-op (queued) when credentials are not configured."""
    if not (settings.whatsapp_access_token and settings.whatsapp_phone_number_id):
        return {"status": "SKIPPED_NO_CREDENTIALS", "to": to, "body": body}
    url = f"{settings.meta_graph_base_url}/{settings.whatsapp_phone_number_id}/messages"
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}}
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, json=payload, headers=headers)
        return {"status": r.status_code, "response": r.json()}
