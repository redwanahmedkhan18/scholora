"""
Runtime-editable settings: the four things that used to need a redeploy.

Credentials, the embedding provider and the retrieval weights were all compile-time values
in `Settings` - a `.env` edit and a restart. An operator in Uttara cannot do that, which
meant "add your WhatsApp token" was really "ask an engineer". This module puts a database
overlay in front of `Settings` so those values can be changed from the admin UI and take
effect on the next request, everywhere, without touching a call site.

Design notes
------------
* **Allowlist, not free-for-all.** Only keys in `OVERRIDABLE` can be set from the UI. A
  database row for `secret_key` or `database_url` is ignored, so a compromised admin session
  cannot repoint the database or forge tokens.
* **Secrets are encrypted at rest** with Fernet, keyed off `SECRET_KEY`. They are never
  returned to the browser - the API sends a masked preview and a `has_value` flag.
* **Reads must be cheap.** `settings.whatsapp_access_token` is touched on hot paths, so the
  overlay is an in-process dict refreshed on a TTL and invalidated immediately on write. No
  database round trip per attribute access.
* **Env stays the fallback.** A key with no database row behaves exactly as before, so
  existing deployments and the test suite are unaffected.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 20.0

_lock = threading.RLock()
_cache: Dict[str, Any] = {}
_cache_loaded_at: float = 0.0
_available = True          # flipped off if the table does not exist yet (fresh install)


# --------------------------------------------------------------------------- the allowlist
@dataclass(frozen=True)
class Overridable:
    key: str
    group: str
    label: str
    kind: str = "string"            # string | int | float | bool | choice
    secret: bool = False
    help: str = ""
    choices: Optional[List[str]] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    # Changing some values invalidates stored vectors; the UI warns before saving.
    requires_reindex: bool = False


OVERRIDABLE: Dict[str, Overridable] = {o.key: o for o in [
    # ---- WhatsApp Business -------------------------------------------------
    Overridable("whatsapp_phone_number_id", "whatsapp", "Phone number ID",
                help="From Meta Business Manager > WhatsApp > API Setup."),
    Overridable("whatsapp_access_token", "whatsapp", "Access token", secret=True,
                help="Permanent system-user token. Outbound sending stays disabled until this is set."),
    Overridable("whatsapp_verify_token", "whatsapp", "Webhook verify token", secret=True,
                help="You invent this string, then paste the same value into Meta's webhook setup."),
    Overridable("whatsapp_app_secret", "whatsapp", "App secret", secret=True,
                help="Used to validate the X-Hub-Signature-256 on every inbound webhook."),

    # ---- Meta / Facebook ---------------------------------------------------
    Overridable("facebook_page_access_token", "meta", "Page access token", secret=True,
                help="Lets the platform read the linked Facebook page."),
    Overridable("meta_pixel_id", "meta", "Pixel ID",
                help="Numeric ID of the Meta Pixel used for browser-side events."),
    Overridable("meta_capi_access_token", "meta", "Conversions API token", secret=True,
                help="Server-side event token. Lead events are queued but not sent until this exists."),

    # ---- Assistant LLMs ----------------------------------------------------
    Overridable("llm_enabled", "llm", "Assistant enabled", kind="bool",
                help="When off, the assistant answers from templates and retrieval only."),
    Overridable("llm_provider_order", "llm", "Provider order",
                help="Comma-separated. The first configured and responding provider wins."),
    Overridable("xai_api_key", "llm", "xAI (Grok) API key", secret=True),
    Overridable("mistral_api_key", "llm", "Mistral API key", secret=True),
    Overridable("gemini_api_key", "llm", "Gemini API key", secret=True),
    Overridable("llm_temperature", "llm", "Temperature", kind="float", minimum=0.0, maximum=1.0,
                help="Low values keep the assistant close to retrieved text."),
    Overridable("llm_max_tokens", "llm", "Max tokens", kind="int", minimum=64, maximum=4096),

    # ---- Embeddings --------------------------------------------------------
    Overridable("embeddings_enabled", "embeddings", "Hosted embeddings enabled", kind="bool",
                requires_reindex=True,
                help="Off falls back to the local deterministic vectoriser - free, but no semantic knowledge."),
    Overridable("embedding_provider_order", "embeddings", "Provider order",
                requires_reindex=True,
                help="Comma-separated: mistral, gemini. First configured one is used."),
    Overridable("mistral_embed_model", "embeddings", "Mistral model", requires_reindex=True),
    Overridable("gemini_embed_model", "embeddings", "Gemini model", requires_reindex=True),
    Overridable("embedding_dim", "embeddings", "Vector dimensions", kind="int",
                minimum=64, maximum=4096, requires_reindex=True,
                help="Must match the model's output. Changing it invalidates every stored vector."),

    # ---- Retrieval tuning --------------------------------------------------
    Overridable("retrieval_dense_weight", "retrieval", "Dense weight", kind="float",
                minimum=0.0, maximum=1.0,
                help="Share of the fused score from vector similarity; the remainder comes from BM25."),
    Overridable("retrieval_min_score", "retrieval", "Dense floor", kind="float",
                minimum=0.0, maximum=1.0,
                help="A chunk with no shared vocabulary needs at least this cosine score to qualify."),
]}

GROUP_LABELS = {
    "whatsapp": "WhatsApp Business",
    "meta": "Meta / Facebook",
    "llm": "Assistant model",
    "embeddings": "Embeddings",
    "retrieval": "Retrieval tuning",
}


# --------------------------------------------------------------------------- encryption
def _fernet():
    """Fernet keyed off SECRET_KEY so secrets are unreadable in a database dump."""
    try:
        from cryptography.fernet import Fernet
    except ModuleNotFoundError as exc:      # pragma: no cover - dependency is declared
        raise RuntimeError(
            "storing credentials requires the 'cryptography' package "
            "(pip install -r requirements.txt)"
        ) from exc
    from app.core.config import get_settings
    raw = (get_settings().secret_key or "dev-secret-change-me").encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except Exception:
        # Wrong SECRET_KEY, or a value written before encryption existed. Never raise on a
        # read path - an unreadable credential must degrade to "not configured".
        log.warning("runtime: could not decrypt a stored secret; treating it as unset")
        return ""


# --------------------------------------------------------------------------- coercion
def coerce(spec: Overridable, raw: str) -> Any:
    if spec.kind == "bool":
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if spec.kind == "int":
        return int(float(raw))
    if spec.kind == "float":
        return float(raw)
    return str(raw)


def validate(spec: Overridable, value: Any) -> Any:
    if spec.kind in {"int", "float"}:
        if spec.minimum is not None and value < spec.minimum:
            raise ValueError(f"{spec.label} must be at least {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:
            raise ValueError(f"{spec.label} must be at most {spec.maximum}")
    if spec.choices and str(value) not in spec.choices:
        raise ValueError(f"{spec.label} must be one of {', '.join(spec.choices)}")
    return value


# --------------------------------------------------------------------------- cache
def _load_locked() -> None:
    """Refresh the overlay. Never raises: a broken overlay must not take the site down."""
    global _cache_loaded_at, _available
    try:
        from sqlalchemy import select

        from app.core.database import SessionLocal
        from app.models.content import RuntimeSetting

        out: Dict[str, Any] = {}
        with SessionLocal() as db:
            for row in db.execute(select(RuntimeSetting)).scalars().all():
                spec = OVERRIDABLE.get(row.key)
                if spec is None:
                    continue            # key removed from the allowlist since it was written
                raw = decrypt(row.value) if row.is_secret else row.value
                if raw is None or raw == "":
                    continue            # an empty override means "fall back to env"
                try:
                    out[row.key] = coerce(spec, raw)
                except (TypeError, ValueError):
                    log.warning("runtime: ignoring unparseable override %s", row.key)
        _cache.clear()
        _cache.update(out)
        _available = True
    except Exception as exc:                                   # table missing, DB down, ...
        _available = False
        log.debug("runtime: overlay unavailable (%s)", exc)
    finally:
        _cache_loaded_at = time.monotonic()


def refresh(force: bool = True) -> None:
    with _lock:
        if force or (time.monotonic() - _cache_loaded_at) > CACHE_TTL_SECONDS:
            _load_locked()


def override(name: str) -> Any:
    """The hot path. Returns the override for `name`, or None to fall back to env."""
    if name not in OVERRIDABLE:
        return None
    if (time.monotonic() - _cache_loaded_at) > CACHE_TTL_SECONDS:
        refresh(force=True)
    return _cache.get(name)


def invalidate() -> None:
    global _cache_loaded_at
    with _lock:
        _cache_loaded_at = 0.0


# --------------------------------------------------------------------------- read / write
def mask(value: str) -> str:
    """`sk-abcd...wxyz` - enough to recognise which key is installed, not enough to use it."""
    s = str(value or "")
    if len(s) <= 8:
        return "••••" if s else ""
    return f"{s[:4]}{'•' * 6}{s[-4:]}"


def describe(db) -> List[Dict[str, Any]]:
    """Everything the admin UI needs: value or mask, origin, and the spec metadata."""
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.models.content import RuntimeSetting

    rows = {r.key: r for r in db.execute(select(RuntimeSetting)).scalars().all()}
    base = get_settings()
    out: List[Dict[str, Any]] = []
    for spec in OVERRIDABLE.values():
        row = rows.get(spec.key)
        env_value = getattr(base, spec.key, None)
        stored = None
        if row is not None:
            stored = decrypt(row.value) if row.is_secret else row.value
            if stored == "":
                stored = None

        effective = stored if stored is not None else env_value
        has_value = bool(str(effective or "").strip()) if spec.kind == "string" else effective is not None
        item: Dict[str, Any] = {
            "key": spec.key, "group": spec.group, "group_label": GROUP_LABELS.get(spec.group, spec.group),
            "label": spec.label, "kind": spec.kind, "secret": spec.secret, "help": spec.help,
            "choices": spec.choices, "minimum": spec.minimum, "maximum": spec.maximum,
            "requires_reindex": spec.requires_reindex,
            "origin": "database" if stored is not None else ("environment" if has_value else "unset"),
            "has_value": bool(has_value),
            "updated_at": row.updated_at.isoformat() if row is not None and row.updated_at else None,
            "updated_by": row.updated_by if row is not None else None,
        }
        # Secrets never leave the server in readable form.
        item["value"] = mask(str(effective)) if spec.secret else (
            coerce(spec, str(effective)) if effective is not None and spec.kind != "string" else effective)
        out.append(item)
    return out


def apply(db, values: Dict[str, Any], actor: str = "system",
          commit: bool = True) -> Dict[str, Any]:
    """Persist overrides. Returns a summary; raises ValueError on an invalid value.

    Pass ``commit=False`` when the caller needs the change and its audit record to be one
    transaction - the admin endpoint does, so a credential can never change unlogged.
    """
    from sqlalchemy import select

    from app.models.content import RuntimeSetting

    changed, cleared, reindex = [], [], False
    for key, raw in values.items():
        spec = OVERRIDABLE.get(key)
        if spec is None:
            raise ValueError(f"{key} is not a runtime-editable setting")

        row = db.execute(select(RuntimeSetting).where(RuntimeSetting.key == key)).scalars().first()

        # Empty string clears the override and hands the key back to the environment.
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            if row is not None:
                db.delete(row)
                cleared.append(key)
                reindex = reindex or spec.requires_reindex
            continue

        value = validate(spec, coerce(spec, str(raw)))
        text = "true" if value is True else "false" if value is False else str(value)
        stored = encrypt(text) if spec.secret else text

        if row is None:
            row = RuntimeSetting(key=key, value=stored, is_secret=spec.secret,
                                 group=spec.group, updated_by=actor)
            db.add(row)
            changed.append(key)
            reindex = reindex or spec.requires_reindex
        else:
            previous = decrypt(row.value) if row.is_secret else row.value
            if previous != text:
                row.value = stored
                row.is_secret = spec.secret
                row.group = spec.group
                row.updated_by = actor
                changed.append(key)
                reindex = reindex or spec.requires_reindex

    if commit:
        db.commit()
        invalidate()
    else:
        db.flush()
    return {"changed": changed, "cleared": cleared, "reindex_recommended": reindex}
