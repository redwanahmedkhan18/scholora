"""Multi-provider LLM layer for the grounded assistant.

Scholara does not depend on OpenAI. The assistant can be driven by any combination of:

* **Grok** (xAI)      — ``https://api.x.ai/v1``
* **Mistral AI**      — ``https://api.mistral.ai/v1``
* **Gemini** (Google) — ``https://generativelanguage.googleapis.com/v1beta/openai``

All three speak the same OpenAI-style ``POST /chat/completions`` wire format with a
``Authorization: Bearer <key>`` header, so one small ``httpx`` client covers all of them and we
carry **no vendor SDK** at all.

Behaviour that matters:

* Providers are tried **in the configured order** (``LLM_PROVIDER_ORDER``) and the first one that
  is configured *and* answers wins. A provider that is missing a key is skipped silently; one that
  errors is recorded and the next is tried. This is failover, not fan-out — exactly one answer is
  ever paid for and used.
* If no provider is configured or all of them fail, ``complete()`` returns ``None`` and the
  assistant falls back to its deterministic templates. The feature degrades, it never disappears.
* API keys are never returned, logged or rendered. ``health()`` reports presence only, and
  ``_redact()`` scrubs any key that manages to appear in an upstream error string.

The grounding guardrails live in ``services.assistant_engine`` and are applied to whatever text
comes back here, so swapping providers cannot weaken the data-integrity policy.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

# --------------------------------------------------------------------------- catalogue

#: Stable provider keys. The order here is only the fallback when the setting is unparseable.
PROVIDER_KEYS = ("grok", "mistral", "gemini")

#: Human-facing metadata. ``env_key`` is shown in the admin so an operator knows what to set.
PROVIDER_META: Dict[str, Dict[str, str]] = {
    "grok": {
        "label": "Grok (xAI)",
        "env_key": "XAI_API_KEY",
        "env_model": "XAI_MODEL",
        "console": "https://console.x.ai",
        "docs": "https://docs.x.ai/docs/guides/chat",
    },
    "mistral": {
        "label": "Mistral AI",
        "env_key": "MISTRAL_API_KEY",
        "env_model": "MISTRAL_MODEL",
        "console": "https://console.mistral.ai",
        "docs": "https://docs.mistral.ai/capabilities/completion",
    },
    "gemini": {
        "label": "Gemini (Google AI Studio)",
        "env_key": "GEMINI_API_KEY",
        "env_model": "GEMINI_MODEL",
        "console": "https://aistudio.google.com/apikey",
        "docs": "https://ai.google.dev/gemini-api/docs/openai",
    },
}


@dataclass(frozen=True)
class Provider:
    """A resolved provider: what to call, with which key and model."""

    key: str
    label: str
    api_key: str
    model: str
    base_url: str
    env_key: str
    env_model: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip())

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


@dataclass
class LLMResult:
    """A successful completion plus the provenance of which model produced it."""

    text: str
    provider: str
    label: str
    model: str
    latency_ms: int
    attempts: List[Dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- resolution
def _provider(key: str) -> Optional[Provider]:
    meta = PROVIDER_META.get(key)
    if not meta:
        return None
    api_key = getattr(settings, f"{'xai' if key == 'grok' else key}_api_key", "") or ""
    model = getattr(settings, f"{'xai' if key == 'grok' else key}_model", "") or ""
    base_url = getattr(settings, f"{'xai' if key == 'grok' else key}_base_url", "") or ""
    return Provider(
        key=key,
        label=meta["label"],
        api_key=api_key.strip(),
        model=model.strip(),
        base_url=base_url.strip(),
        env_key=meta["env_key"],
        env_model=meta["env_model"],
    )


def provider_order() -> List[str]:
    """The configured preference order, de-duplicated and filtered to known providers."""
    raw = (settings.llm_provider_order or "").split(",")
    seen: List[str] = []
    for item in raw:
        name = item.strip().lower()
        if name in PROVIDER_META and name not in seen:
            seen.append(name)
    return seen or list(PROVIDER_KEYS)


def providers() -> List[Provider]:
    out: List[Provider] = []
    for key in provider_order():
        resolved = _provider(key)
        if resolved:
            out.append(resolved)
    return out


def configured_providers() -> List[Provider]:
    return [p for p in providers() if p.configured]


def available() -> bool:
    """True when at least one provider has a key and generation is switched on."""
    return bool(settings.llm_enabled) and bool(configured_providers())


# --------------------------------------------------------------------------- redaction
def _redact(text: str) -> str:
    """Strip any configured API key out of a string before it is stored or displayed."""
    out = text or ""
    for provider in providers():
        if provider.api_key and provider.api_key in out:
            out = out.replace(provider.api_key, "***redacted***")
    return out[:400]


# --------------------------------------------------------------------------- last-error state
_lock = threading.Lock()
_last_error: Dict[str, str] = {}
_last_ok: Dict[str, float] = {}


def _record_error(key: str, message: str) -> None:
    with _lock:
        _last_error[key] = _redact(message)


def _record_ok(key: str) -> None:
    with _lock:
        _last_error.pop(key, None)
        _last_ok[key] = time.time()


# --------------------------------------------------------------------------- the call
def _post(provider: Provider, messages: List[Dict[str, str]], *, max_tokens: int, temperature: float) -> str:
    """One HTTP round trip. Raises on any non-2xx or malformed body."""
    payload: Dict[str, Any] = {
        "model": provider.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
        response = client.post(provider.endpoint, json=payload, headers=headers)
        if response.status_code >= 400:
            detail = response.text[:200].replace("\n", " ")
            raise RuntimeError(f"HTTP {response.status_code}: {detail}")
        body = response.json()

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("no choices returned")
    message = choices[0].get("message") or {}
    content = message.get("content")
    # Gemini occasionally returns content as a list of parts rather than a string.
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    text = (content or "").strip()
    if not text:
        raise RuntimeError("empty completion")
    return text


def complete(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> Optional[LLMResult]:
    """Try each configured provider in order; return the first success, or None.

    Never raises. A total failure is a normal, expected outcome that the caller handles by
    falling back to deterministic templates.
    """
    if not settings.llm_enabled:
        return None

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    tokens = max_tokens if max_tokens is not None else settings.llm_max_tokens
    temp = temperature if temperature is not None else settings.llm_temperature

    attempts: List[Dict[str, Any]] = []
    for provider in providers():
        if not provider.configured:
            attempts.append({"provider": provider.key, "status": "skipped", "reason": "no API key"})
            continue
        started = time.perf_counter()
        try:
            text = _post(provider, messages, max_tokens=tokens, temperature=temp)
        except Exception as exc:  # noqa: BLE001 - every failure is a fallback signal
            message = _redact(f"{type(exc).__name__}: {exc}")
            _record_error(provider.key, message)
            attempts.append({"provider": provider.key, "status": "error", "reason": message})
            continue
        latency = int((time.perf_counter() - started) * 1000)
        _record_ok(provider.key)
        attempts.append({"provider": provider.key, "status": "ok", "latency_ms": latency})
        return LLMResult(
            text=text,
            provider=provider.key,
            label=provider.label,
            model=provider.model,
            latency_ms=latency,
            attempts=attempts,
        )
    return None


# --------------------------------------------------------------------------- introspection
def health() -> Dict[str, Any]:
    """Provider status for the admin. Reports key *presence*, never key material."""
    items: List[Dict[str, Any]] = []
    for index, provider in enumerate(providers()):
        meta = PROVIDER_META[provider.key]
        with _lock:
            last_error = _last_error.get(provider.key)
            last_ok = _last_ok.get(provider.key)
        items.append(
            {
                "provider": provider.key,
                "label": provider.label,
                "priority": index + 1,
                "configured": provider.configured,
                "model": provider.model,
                "base_url": provider.base_url,
                "env_key": provider.env_key,
                "env_model": provider.env_model,
                "console_url": meta["console"],
                "docs_url": meta["docs"],
                "last_error": last_error,
                "last_success_at": last_ok,
            }
        )
    active = next((i for i in items if i["configured"]), None)
    return {
        "enabled": bool(settings.llm_enabled),
        "available": available(),
        "mode": "llm" if available() else "template",
        "order": provider_order(),
        "active_provider": active["provider"] if active else None,
        "active_model": active["model"] if active else None,
        "timeout_seconds": settings.llm_timeout_seconds,
        "max_tokens": settings.llm_max_tokens,
        "temperature": settings.llm_temperature,
        "providers": items,
        "note": (
            "The assistant answers only from verified Scholara records. "
            "With no provider configured it uses deterministic templates instead of generation."
        ),
    }


def ping(key: str) -> Dict[str, Any]:
    """Live round trip against one provider, for the admin 'Test' button."""
    provider = _provider(key)
    if provider is None:
        return {"provider": key, "ok": False, "error": "unknown provider"}
    if not provider.configured:
        return {
            "provider": key,
            "label": provider.label,
            "ok": False,
            "error": f"{provider.env_key} is not set",
        }
    started = time.perf_counter()
    try:
        text = _post(
            provider,
            [
                {"role": "system", "content": "Reply with the single word: ready"},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=16,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        message = _redact(f"{type(exc).__name__}: {exc}")
        _record_error(key, message)
        return {"provider": key, "label": provider.label, "ok": False, "error": message}
    latency = int((time.perf_counter() - started) * 1000)
    _record_ok(key)
    return {
        "provider": key,
        "label": provider.label,
        "ok": True,
        "model": provider.model,
        "latency_ms": latency,
        "reply": text[:120],
    }
