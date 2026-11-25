"""Cache + distributed primitives.

Uses Redis (Upstash over TLS in production) when REDIS_URL is reachable, otherwise a
process-local fallback with the same interface. The fallback keeps development and
single-node deploys working with zero infrastructure; production sets REDIS_URL and gets
shared state. A configured-but-unreachable Redis is surfaced by the readiness endpoint
instead of being mistaken for healthy shared state.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from app.core.config import settings
from app.core.connectivity import normalize_redis_url, redis_connect_kwargs, redis_summary

log = logging.getLogger(__name__)

# Optional dependency. The fallback is a process-local backend, so an install without
# redis must still import cleanly. Annotating the module handle as `Any` is deliberate:
# redis-py is optional and ships its own types, but the name is also `None` here.
_redis: Any = None
try:  # pragma: no cover - import guard
    import redis as _redis_module

    _redis = _redis_module
except Exception:  # pragma: no cover - optional dependency
    _redis = None


class _MemoryBackend:
    """Thread-safe in-process stand-in for the subset of Redis we use."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float | None, Any]] = {}
        self._lock = threading.Lock()

    def _alive(self, key: str) -> bool:
        item = self._data.get(key)
        if item is None:
            return False
        expires, _ = item
        if expires is not None and expires < time.time():
            self._data.pop(key, None)
            return False
        return True

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._data[key][1] if self._alive(key) else None

    def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        with self._lock:
            self._data[key] = (time.time() + ex if ex else None, value)

    def delete(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._data.pop(key, None)

    def incr(self, key: str) -> int:
        with self._lock:
            current = int(self._data[key][1]) if self._alive(key) else 0
            current += 1
            expires = self._data[key][0] if self._alive(key) else None
            self._data[key] = (expires, str(current))
            return current

    def expire(self, key: str, seconds: int) -> None:
        with self._lock:
            if self._alive(key):
                self._data[key] = (time.time() + seconds, self._data[key][1])

    def ttl(self, key: str) -> int:
        with self._lock:
            if not self._alive(key):
                return -2
            expires = self._data[key][0]
            return -1 if expires is None else max(0, int(expires - time.time()))

    def scan_prefix(self, prefix: str) -> list[str]:
        with self._lock:
            return [k for k in list(self._data) if k.startswith(prefix) and self._alive(k)]


class Cache:
    """Thin facade so callers never branch on which backend is live."""

    def __init__(self) -> None:
        self._memory = _MemoryBackend()
        self._client = None
        self.backend = "memory"
        self._prefix = settings.cache_key_prefix or ""
        self._raw_url = (settings.redis_url or "").strip()
        self._last_error: Optional[str] = None
        self._url_summary = redis_summary(self._raw_url) if self._raw_url else None

        if self._raw_url and _redis is not None:
            try:
                url = normalize_redis_url(self._raw_url)
                client = _redis.from_url(url, **redis_connect_kwargs(url))
                client.ping()
                self._client = client
                self.backend = "redis"
            except Exception as exc:  # noqa: BLE001 - fallback is intentional in dev
                self._last_error = type(exc).__name__
                self._client = None
                log.warning("cache: Redis unavailable (%s); using in-process fallback",
                            self._last_error)
        elif self._raw_url and _redis is None:
            self._last_error = "RedisPackageMissing"

    # ---------------------------------------------------------------- metadata
    @property
    def configured(self) -> bool:
        return bool(self._raw_url)

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def url_summary(self) -> Optional[dict[str, Any]]:
        return dict(self._url_summary) if self._url_summary else None

    def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(self._client.ping())
        except Exception as exc:  # noqa: BLE001
            self._last_error = type(exc).__name__
            return False

    # ---------------------------------------------------------------- keys
    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    # ---------------------------------------------------------------- raw ops
    def get(self, key: str) -> Optional[str]:
        if self._client is None:
            return self._memory.get(self._k(key))
        try:
            value = self._client.get(self._k(key))
        except Exception:
            return self._memory.get(self._k(key))
        if value is None:
            return None
        # decode_responses=True normally yields str; a bytes value is still honoured so
        # a misconfigured client degrades to a correct string instead of a broken cache.
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        try:
            if self._client:
                self._client.set(self._k(key), value, ex=ttl)
                return
        except Exception:
            pass
        self._memory.set(self._k(key), value, ex=ttl)

    def delete(self, *keys: str) -> None:
        if not keys:
            return
        prefixed = [self._k(key) for key in keys]
        try:
            if self._client:
                self._client.delete(*prefixed)
                return
        except Exception:
            pass
        self._memory.delete(*prefixed)

    def incr_with_ttl(self, key: str, ttl: int) -> tuple[int, int]:
        """Increment a counter, setting the window TTL on first use. Returns (count, ttl_left)."""
        prefixed = self._k(key)
        try:
            if self._client:
                pipe = self._client.pipeline()
                pipe.incr(prefixed)
                pipe.ttl(prefixed)
                count, remaining = pipe.execute()
                if remaining is None or remaining < 0:
                    self._client.expire(prefixed, ttl)
                    remaining = ttl
                return int(count), int(remaining)
        except Exception:
            pass
        count = self._memory.incr(prefixed)
        if count == 1:
            self._memory.expire(prefixed, ttl)
        return count, max(0, self._memory.ttl(prefixed))

    # ------------------------------------------------------------ json helpers
    def get_json(self, key: str) -> Any:
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def set_json(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        try:
            self.set(key, json.dumps(value, default=str), ttl=ttl)
        except Exception:
            pass

    def invalidate_prefix(self, prefix: str) -> int:
        """Drop every key under a prefix. Used when admin edits content."""
        prefixed = self._k(prefix)
        try:
            if self._client:
                removed = 0
                for key in self._client.scan_iter(match=f"{prefixed}*", count=500):
                    self._client.delete(key)
                    removed += 1
                return removed
        except Exception:
            pass
        keys = self._memory.scan_prefix(prefixed)
        self._memory.delete(*keys)
        return len(keys)


cache = Cache()
