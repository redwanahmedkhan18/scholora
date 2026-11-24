"""Embeddings for pgvector retrieval.

Mirrors ``core.llm``: the same three vendors, the same failover discipline, no vendor SDK.
Only two of the three publish an embeddings endpoint, so the catalogue here is shorter:

* **Mistral**  — ``POST /v1/embeddings``, model ``mistral-embed`` (1024 dims natively).
* **Gemini**   — ``POST /v1beta/openai/embeddings`` on the OpenAI-compatibility endpoint.
* *(xAI does not currently publish a public embeddings model, so Grok is text-only here.)*

**There is always a third option that needs no key at all.** ``_local_embed`` is a deterministic
hashing vectoriser: token and character-trigram features hashed into ``embedding_dim`` buckets and
L2-normalised. It is not semantic — it will not match "fees" to "tuition" — but it is a genuine
dense vector, so the pgvector column, the HNSW index and the cosine ranking are all exercised for
real on a machine with no API keys. Swapping in a hosted provider later changes vector *quality*,
not the code path.

Every vector in a given index must share one dimensionality, so ``settings.embedding_dim`` is the
single source of truth and is what the migration builds the column against. A provider returning a
different width is rejected rather than silently truncated — a mixed-width index returns nonsense.
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from app.core import local_semantic
from app.core.config import settings

#: Providers that publish an embeddings endpoint, in failover order.
EMBED_PROVIDER_KEYS = ("mistral", "gemini")

EMBED_PROVIDER_META: Dict[str, Dict[str, str]] = {
    "mistral": {
        "label": "Mistral AI",
        "env_key": "MISTRAL_API_KEY",
        "env_model": "MISTRAL_EMBED_MODEL",
        "path": "/embeddings",
    },
    "gemini": {
        "label": "Gemini (Google AI Studio)",
        "env_key": "GEMINI_API_KEY",
        "env_model": "GEMINI_EMBED_MODEL",
        "path": "/embeddings",
    },
}

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-\.\+]*")
_lock = threading.Lock()
_last_error: Dict[str, str] = {}


@dataclass(frozen=True)
class EmbedProvider:
    key: str
    label: str
    api_key: str
    model: str
    base_url: str
    env_key: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip())

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/embeddings"


def dim() -> int:
    return int(settings.embedding_dim)


def _provider(key: str) -> Optional[EmbedProvider]:
    meta = EMBED_PROVIDER_META.get(key)
    if not meta:
        return None
    return EmbedProvider(
        key=key,
        label=meta["label"],
        api_key=(getattr(settings, f"{key}_api_key", "") or "").strip(),
        model=(getattr(settings, f"{key}_embed_model", "") or "").strip(),
        base_url=(getattr(settings, f"{key}_base_url", "") or "").strip(),
        env_key=meta["env_key"],
    )


def provider_order() -> List[str]:
    """Admin-configurable preference order, filtered to vendors we actually implement."""
    raw = [p.strip().lower() for p in (settings.embedding_provider_order or "").split(",")]
    chosen = [p for p in raw if p in EMBED_PROVIDER_KEYS]
    # Anything configured but not listed still works, just last.
    return chosen + [k for k in EMBED_PROVIDER_KEYS if k not in chosen]


def providers() -> List[EmbedProvider]:
    out = []
    for key in provider_order():
        resolved = _provider(key)
        if resolved:
            out.append(resolved)
    return out


def hosted_available() -> bool:
    """True when a real embedding vendor can be called."""
    return bool(settings.embeddings_enabled) and any(p.configured for p in providers())


# ------------------------------------------------------------------ local fallback
def _hash_bucket(token: str, salt: str, width: int) -> int:
    digest = hashlib.blake2b(f"{salt}:{token}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % width


def _local_embed(text: str) -> List[float]:
    """Deterministic hashing vectoriser — no key, no network, same width as the hosted ones.

    Features are whole tokens (weighted higher) plus character trigrams, which gives the vector
    some tolerance to morphology and typos. Signed buckets reduce hash-collision cancellation.
    """
    width = dim()
    vector = [0.0] * width
    lowered = (text or "").lower()
    tokens = _TOKEN_RE.findall(lowered)

    for token in tokens:
        bucket = _hash_bucket(token, "tok", width)
        sign = 1.0 if _hash_bucket(token, "sgn", 2) == 0 else -1.0
        vector[bucket] += 2.0 * sign
        for i in range(len(token) - 2):
            trigram = token[i:i + 3]
            tri_bucket = _hash_bucket(trigram, "tri", width)
            tri_sign = 1.0 if _hash_bucket(trigram, "sgn", 2) == 0 else -1.0
            vector[tri_bucket] += 0.5 * tri_sign

    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


# ------------------------------------------------------------------ hosted providers
def _post_batch(provider: EmbedProvider, texts: List[str]) -> List[List[float]]:
    payload: Dict[str, Any] = {"model": provider.model, "input": texts}
    # Both vendors accept an output width on their OpenAI-compatible endpoint; asking for ours
    # keeps every vector in the index the same shape.
    payload["dimensions"] = dim()
    headers = {"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
        response = client.post(provider.endpoint, json=payload, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:160]}")
        body = response.json()
    rows = body.get("data") or []
    if len(rows) != len(texts):
        raise RuntimeError(f"expected {len(texts)} vectors, got {len(rows)}")
    vectors = [row.get("embedding") or [] for row in rows]
    for vector in vectors:
        if len(vector) != dim():
            raise RuntimeError(
                f"provider returned width {len(vector)}, index is {dim()} - "
                f"set EMBEDDING_DIM to match or pick another model"
            )
    return vectors


def _redact(message: str) -> str:
    out = message or ""
    for provider in providers():
        if provider.api_key and provider.api_key in out:
            out = out.replace(provider.api_key, "***redacted***")
    return out[:300]


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a batch. Tries each hosted provider, then falls back to the local vectoriser.

    Never raises and never returns a wrong-width vector, so callers can always store the result.
    """
    if not texts:
        return []
    if settings.embeddings_enabled:
        for provider in providers():
            if not provider.configured:
                continue
            try:
                vectors = _post_batch(provider, texts)
            except Exception as exc:  # noqa: BLE001
                with _lock:
                    _last_error[provider.key] = _redact(f"{type(exc).__name__}: {exc}")
                continue
            with _lock:
                _last_error.pop(provider.key, None)
            return vectors
    # Prefer the fitted local semantic model over hashing. Hashing has no concept of
    # meaning - "fees" and "tuition" are unrelated buckets to it - so it is the last
    # resort, not the default.
    vectors = local_semantic.transform(texts, dim())
    if vectors is not None:
        return vectors
    return [_local_embed(t) for t in texts]


def embed_query(text: str) -> List[float]:
    vectors = embed_texts([text])
    return vectors[0] if vectors else []


def active_backend() -> str:
    """Which path ``embed_texts`` will actually take right now."""
    if settings.embeddings_enabled:
        for provider in providers():
            if provider.configured:
                return provider.key
    return "local"


def active_model_name() -> str:
    """Stable identifier for whatever produced a vector, stored alongside it.

    Vectors from different models are not comparable, so a provider or model change has to
    be detectable after the fact - otherwise a switch silently degrades every search result
    with no error anywhere. `provider:model` is recorded on each chunk and compared against
    this value to report drift.
    """
    backend = active_backend()
    if backend == "local":
        fp = local_semantic.fingerprint()
        return f"local-lsa:{fp}:{dim()}" if fp else f"local-hashing:{dim()}"
    for provider in providers():
        if provider.key == backend:
            return f"{provider.key}:{provider.model}"
    return backend


def health() -> Dict[str, Any]:
    items = []
    for index, provider in enumerate(providers()):
        with _lock:
            error = _last_error.get(provider.key)
        items.append(
            {
                "provider": provider.key,
                "label": provider.label,
                "priority": index + 1,
                "configured": provider.configured,
                "model": provider.model,
                "env_key": provider.env_key,
                "last_error": error,
            }
        )
    backend = active_backend()
    return {
        "enabled": bool(settings.embeddings_enabled),
        "dimensions": dim(),
        "active_backend": backend,
        "hosted": backend != "local",
        "providers": items,
        "note": (
            "Local mode is a deterministic hashing vectoriser: real dense vectors through the "
            "real pgvector path, but lexical rather than semantic. Set MISTRAL_API_KEY or "
            "GEMINI_API_KEY for semantic embeddings, then rebuild the index."
        ),
    }
