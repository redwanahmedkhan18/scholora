"""Privacy boundaries for client-supplied paths, URLs and free-form analytics metadata.

A portal link (``/my/<token>``) is a bearer capability. It must never reach an analytics row,
a CRM record or a third-party relay. The browser gate in ``frontend/src/lib/track.ts`` is the
*first* layer and deliberately not the last: every write re-establishes the invariant on the
server, which is the only boundary a hostile or outdated client cannot bypass.

Everything here is pure and in-process. No helper performs network I/O, and none raise on
malformed input - an unparseable value is dropped (``None``) rather than stored or forwarded.

The semantics are intentionally narrow. ``scrub_analytics_value`` rewrites only strings that
contain a portal capability; it never touches other keys or values, so campaign attribution
(``utm_campaign`` -> ``Lead.campaign_id``) survives intact.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlsplit

#: A portal capability appearing anywhere inside a free-form string.
PORTAL_PATH_PATTERN = re.compile(r"/my/[^\s\"?#]*")

#: The same pattern for the URL boundary. Route matching is case-sensitive in practice, but a
#: boundary that accepts a location must not depend on that: ``/MY/<token>`` still contains a
#: live capability if it is ever echoed into a URL, so it is treated as private there.
PORTAL_PATH_PATTERN_CI = re.compile(r"/my/[^\s\"?#]*", re.IGNORECASE)

_PRIVATE_PREFIX = "/my"
_HTTP_SCHEMES = frozenset({"http", "https"})


def is_private_portal_path(path: Optional[str]) -> bool:
    """True for ``/my`` and anything beneath it (case-sensitive, as the W4 helpers were)."""
    if not path:
        return False
    return path == _PRIVATE_PREFIX or path.startswith(f"{_PRIVATE_PREFIX}/")


def _is_private_portal_path_ci(path: Optional[str]) -> bool:
    """Case-insensitive variant used only at the URL boundary (see ``safe_source_url``)."""
    if not path:
        return False
    lowered = path.lower()
    return lowered == _PRIVATE_PREFIX or lowered.startswith(f"{_PRIVATE_PREFIX}/")


def safe_analytics_path(value: Optional[str]) -> Optional[str]:
    """A storeable path from a client-supplied one: no query string, no fragment, no portal.

    Case-insensitive on purpose, which is the one behaviour change from the W4 helper. A capability
    is only a capability if the router matches the path - but the *token* inside it is sensitive
    whatever the case, and an analytics row is read by staff and copied into exports. ``/MY/<token>``
    is therefore treated exactly like ``/my/<token>``: dropped when it is the path, and truncated at
    the portal segment when the segment is embedded in a longer path.

    It is still a *path* helper; use :func:`safe_source_url` for anything that may be a full URL.
    """
    if not isinstance(value, str):
        return None
    path = value.split("?", 1)[0].split("#", 1)[0].strip()
    if not path:
        return None
    # Defence in depth: a public-looking path can still carry a portal segment inside it.
    path = PORTAL_PATH_PATTERN_CI.sub(_PRIVATE_PREFIX, path)
    if _is_private_portal_path_ci(path):
        return None
    return path or None


def safe_source_url(value: Any) -> Optional[str]:
    """Reduce a client-supplied URL or path to a storeable, forwardable path.

    Full URLs (``https://host/a/b?x=1#frag``) and scheme-relative values (``//host/a/b``) are
    reduced to their pathname; query strings and fragments are dropped. Portal paths, non-HTTP
    schemes and values that are not paths at all are dropped entirely, so they can neither be
    persisted into a 300-character column nor relayed to a third party.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None

    if raw.startswith("//"):
        try:
            parts = urlsplit(f"https:{raw}")
        except ValueError:
            return None
        path = parts.path
    elif "://" in raw or raw.lower().startswith(("http:", "https:")):
        try:
            parts = urlsplit(raw)
        except ValueError:
            return None
        if parts.scheme.lower() not in _HTTP_SCHEMES:
            return None
        path = parts.path or "/"
    else:
        # A bare path; anything without a leading slash is rejected below.
        path = raw.split("?", 1)[0].split("#", 1)[0]

    path = path.strip()
    if not path.startswith("/"):
        return None
    # Defence in depth: an otherwise public path can still carry a portal segment inside it.
    path = PORTAL_PATH_PATTERN_CI.sub(_PRIVATE_PREFIX, path)
    if _is_private_portal_path_ci(path):
        return None
    return path or None


def scrub_analytics_value(value: Any, *, ignore_case: bool = False) -> Any:
    """Defence in depth: no portal capability may survive in free-form analytics fields.

    ``ignore_case`` is False by default because this helper also backs the ``/track`` endpoint,
    whose behaviour is frozen at its accepted W4 semantics. The newer persistence boundaries
    (leads) pass True so a case variant such as ``/MY/<token>`` cannot survive either.
    """
    pattern = PORTAL_PATH_PATTERN_CI if ignore_case else PORTAL_PATH_PATTERN
    if isinstance(value, str):
        return pattern.sub(_PRIVATE_PREFIX, value)
    if isinstance(value, list):
        return [scrub_analytics_value(item, ignore_case=ignore_case) for item in value]
    if isinstance(value, dict):
        return {key: scrub_analytics_value(item, ignore_case=ignore_case) for key, item in value.items()}
    return value
