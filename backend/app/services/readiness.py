"""
Go-live readiness.

Everything the platform can build itself is built. What is left needs accounts that only
the consultancy can open, and the honest thing to do with that is not to hide it behind a
blank settings form but to state it plainly: what is missing, what stops working without
it, where to get it, and how long it takes.

This module holds no credentials and no secrets. It reads which runtime keys have a value
and turns that into a checklist. The "how to obtain" text is deliberately specific -
"Meta Business Suite -> WhatsApp -> API Setup" rather than "configure WhatsApp" - because
a vague instruction is how a go-live slips a fortnight.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.core import runtime
from app.core.config import get_settings

# Each requirement: the runtime keys that satisfy it, what breaks without it, and the
# literal path through the vendor's console to find the value.
REQUIREMENTS: List[Dict[str, Any]] = [
    {
        "key": "whatsapp",
        "label": "WhatsApp Business API",
        "why": "Send template messages and reply in-thread. Click-to-chat already works "
               "without this; only outbound automation is blocked.",
        "blocks": ["Automated lead replies", "Appointment reminders", "Document chase-ups"],
        "settings": ["whatsapp_phone_number_id", "whatsapp_access_token",
                     "whatsapp_verify_token", "whatsapp_app_secret"],
        "where": "Meta Business Suite -> WhatsApp Manager -> API Setup. The phone number "
                 "ID sits beside the number; create a permanent token under System Users "
                 "rather than using the temporary 24-hour one.",
        "effort": "Half a day, plus 1-3 days for message-template approval.",
        "test": "whatsapp",
        "degrades_to": "Click-to-chat links, which work today.",
    },
    {
        "key": "meta",
        "label": "Meta Pixel & Conversions API",
        "why": "Attribute leads to ad campaigns and let Meta optimise delivery against "
               "real enquiries rather than clicks.",
        "blocks": ["Server-side conversion tracking", "Campaign ROI reporting",
                   "Lookalike audiences built from genuine leads"],
        "settings": ["meta_pixel_id", "meta_capi_access_token"],
        "where": "Meta Events Manager -> Data Sources -> your pixel. The pixel ID is on "
                 "the overview; generate the CAPI token under Settings -> Conversions API.",
        "effort": "An hour.",
        "test": "meta_capi",
        "degrades_to": "First-party UTM attribution, which is already recorded.",
    },
    {
        "key": "facebook_page",
        "label": "Facebook Page link",
        "why": "Pull page details and post updates from the admin.",
        "blocks": ["Page feed on the site", "Posting from the admin"],
        "settings": ["facebook_page_access_token"],
        "where": "Meta Graph API Explorer -> select the page -> generate a page access "
                 "token with pages_read_engagement, then extend it to a long-lived token.",
        "effort": "An hour.",
        "test": "facebook_page",
        "degrades_to": "A plain link to the page, which is live now.",
    },
    {
        "key": "llm",
        "label": "Assistant model key",
        "why": "Answers student questions over the platform's own verified content.",
        "blocks": ["AI assistant replies"],
        "settings": ["xai_api_key", "mistral_api_key", "gemini_api_key"],
        "satisfied_by_any": True,   # one key is enough; the rest are fallbacks
        "where": "console.x.ai, console.mistral.ai or aistudio.google.com. Any one is "
                 "enough - the others become automatic fallbacks in the configured order.",
        "effort": "Fifteen minutes.",
        "test": "llm",
        "degrades_to": "Retrieval-only answers: the assistant still quotes stored records "
                       "and their sources, it just cannot phrase them conversationally.",
    },
    {
        "key": "embeddings",
        "label": "Embedding provider",
        "why": "Higher-quality semantic search than the built-in local vectoriser.",
        "blocks": [],
        "settings": ["mistral_api_key", "gemini_api_key"],
        "satisfied_by_any": True,
        "optional": True,
        "where": "Reuses the Mistral or Gemini key above; pick the provider under "
                 "Platform settings -> Embeddings, then reindex from the same screen.",
        "effort": "Minutes, plus a reindex.",
        "test": "embeddings",
        "degrades_to": "The local hashing vectoriser, which is active and working.",
    },
]


def _has_value(key: str) -> bool:
    """True when a runtime key resolves to something non-empty, from DB or environment."""
    settings = get_settings()
    return bool(str(getattr(settings, key, "") or "").strip())


def report(db: Session) -> Dict[str, Any]:
    """The readiness checklist. Never returns a secret - only whether one is present."""
    runtime.invalidate()
    items: List[Dict[str, Any]] = []

    for spec in REQUIREMENTS:
        keys = spec["settings"]
        present = [k for k in keys if _has_value(k)]
        satisfied = bool(present) if spec.get("satisfied_by_any") else len(present) == len(keys)

        items.append({
            "key": spec["key"],
            "label": spec["label"],
            "status": "READY" if satisfied else ("OPTIONAL" if spec.get("optional") else "MISSING"),
            "optional": bool(spec.get("optional")),
            "why": spec["why"],
            "blocks": spec["blocks"],
            "degrades_to": spec["degrades_to"],
            "where": spec["where"],
            "effort": spec["effort"],
            "test": spec["test"],
            "settings": [{"key": k, "present": k in present} for k in keys],
            "missing": [k for k in keys if k not in present],
        })

    required = [i for i in items if not i["optional"]]
    ready = [i for i in required if i["status"] == "READY"]
    return {
        "ready": len(ready),
        "required": len(required),
        "complete": len(ready) == len(required),
        "summary": (
            f"{len(ready)} of {len(required)} required integrations are configured. "
            "Everything else on the platform runs without them."
            if len(ready) < len(required)
            else "Every required integration is configured."
        ),
        "items": items,
    }
