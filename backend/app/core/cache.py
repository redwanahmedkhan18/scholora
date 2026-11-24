"""Cache + distributed primitives.

Uses Redis when REDIS_URL is reachable, otherwise a process-local fallback with the
same interface. The fallback keeps development and single-node deploys working with
zero infrastructure; production simply sets REDIS_URL and gets shared state.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

from app.core.config import settings

try:  # pragma: no cover - import guard
    import redis as _redis  # type: ignore
except Exception:  # pragma: no cover
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
        url = (settings.redis_url or "").strip()
        if url and _redis is not None:
            try:
                client = _redis.from_url(url, decode_responses=True, socket_connect_timeout=1)
                client.ping()
                self._client = client
                self.backend = "redis"
            except Exception:
                self._client = None

    # ---------------------------------------------------------------- raw ops
    def get(self, key: str) -> Optional[str]:
        try:
            return self._client.get(key) if self._client else self._memory.get(key)
        except Exception:
            return self._memory.get(key)

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        try:
            if self._client:
                self._client.set(key, value, ex=ttl)
                return
        except Exception:
            pass
        self._memory.set(key, value, ex=ttl)

    def delete(self, *keys: str) -> None:
        if not keys:
            return
        try:
            if self._client:
                self._client.delete(*keys)
                return
        except Exception:
            pass
        self._memory.delete(*keys)

    def incr_with_ttl(self, key: str, ttl: int) -> tuple[int, int]:
        """Increment a counter, setting the window TTL on first use. Returns (count, ttl_left)."""
        try:
            if self._client:
                pipe = self._client.pipeline()
                pipe.incr(key)
                pipe.ttl(key)
                count, remaining = pipe.execute()
                if remaining is None or remaining < 0:
                    self._client.expire(key, ttl)
                    remaining = ttl
                return int(count), int(remaining)
        except Exception:
            pass
        count = self._memory.incr(key)
        if count == 1:
            self._memory.expire(key, ttl)
        return count, max(0, self._memory.ttl(key))

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
        try:
            if self._client:
                removed = 0
                for key in self._client.scan_iter(match=f"{prefix}*", count=500):
                    self._client.delete(key)
                    removed += 1
                return removed
        except Exception:
            pass
        keys = self._memory.scan_prefix(prefix)
        self._memory.delete(*keys)
        return len(keys)


cache = Cache()
