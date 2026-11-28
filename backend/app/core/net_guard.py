"""W11 - safe URL ingestion.

Every outbound request to an operator-supplied or catalogue URL goes through this module. It is
the difference between "we read a university's page" and "an untrusted string made the server
fetch its own metadata service".

Controls, all deterministic and testable:

* only ``http``/``https``, no embedded credentials, only ports 80/443;
* reject localhost, cloud metadata names, internal/local suffixes and non-public IP literals;
* resolve DNS and reject the request if **any** answer is private, loopback, link-local,
  reserved, multicast or unspecified;
* connect to the IP that was validated, not to the hostname, so a second DNS answer cannot
  redirect the connection (DNS rebinding);
* follow redirects manually, revalidating and re-resolving every hop; refuse HTTPS downgrade;
* connection/read timeouts and a hard response-size limit;
* an optional content-type allow-list;
* errors carry a code and a host, never a full URL (query strings can hold tokens), response
  body or credentials.

Tests patch the resolver and use an ``httpx.MockTransport``; no test opens a socket.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit

import httpx

log = logging.getLogger(__name__)

ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = (80, 443)
DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 15.0
DEFAULT_CONNECT_TIMEOUT = 5.0
USER_AGENT = "ScholaraUrlGuard/1.0 (+https://scholara.com.bd/bots)"

#: Names that are never a university website, whatever they resolve to today.
BLOCKED_HOSTS = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
    "metadata", "metadata.google.internal", "metadata.goog", "instance-data",
    "instance-data.ec2.internal", "metadata.azure.internal", "metadata.azure.com",
})
BLOCKED_SUFFIXES = (
    ".localhost", ".local", ".localdomain", ".internal", ".intranet", ".lan", ".home.arpa",
    ".onion", ".in-addr.arpa", ".ip6.arpa",
)
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
DEFAULT_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


class UrlSafetyError(Exception):
    """A refused or failed request. ``code`` is stable and safe to return to an operator."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}{': ' + detail if detail else ''}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SafeTarget:
    original_url: str
    scheme: str
    host: str
    port: int
    ip: str
    path_query: str
    host_header: str

    @property
    def pinned_url(self) -> str:
        host = f"[{self.ip}]" if ":" in self.ip else self.ip
        default = 443 if self.scheme == "https" else 80
        port = "" if self.port == default else f":{self.port}"
        return f"{self.scheme}://{host}{port}{self.path_query}"

    @property
    def default_port(self) -> int:
        return 443 if self.scheme == "https" else 80


@dataclass
class SafeResponse:
    status_code: int
    url: str
    headers: Dict[str, str]
    content: bytes
    text: str
    redirects: Tuple[str, ...] = ()

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").split(";")[0].strip().lower()


Resolver = Callable[[str, int], Sequence[str]]


def _default_resolver(host: str, port: int) -> Sequence[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as exc:
        raise UrlSafetyError("DNS_ERROR", type(exc).__name__) from exc
    return sorted({str(info[4][0]) for info in infos if info[4]})


def _public_ip(value: str) -> Optional[ipaddress._BaseAddress]:
    """Return the parsed address when it is globally routable, else ``None``.

    ``is_global`` already excludes private, loopback, link-local and reserved ranges; the
    explicit checks are kept because mixed Python/OS stacks have differed historically and this
    is not a place to trust one predicate.
    """
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return None
    if (address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast or address.is_unspecified
            or not address.is_global):
        return None
    return address


def _clean_host(raw: str) -> str:
    host = (raw or "").strip().lower().rstrip(".")
    if not host:
        raise UrlSafetyError("INVALID_URL", "missing host")
    if any(ch.isspace() or ch in "/\\@#" for ch in host):
        raise UrlSafetyError("INVALID_URL", "malformed host")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UrlSafetyError("INVALID_URL", "invalid internationalised host") from exc


def validate_url(url: str, *, resolver: Optional[Resolver] = None) -> SafeTarget:
    """Validate a URL and pin it to one public IP. Raises ``UrlSafetyError``."""
    raw = (url or "").strip()
    if not raw:
        raise UrlSafetyError("INVALID_URL", "empty")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise UrlSafetyError("INVALID_URL", "unparseable") from exc
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UrlSafetyError("UNSUPPORTED_SCHEME", scheme or "missing")
    if parsed.username is not None or parsed.password is not None:
        raise UrlSafetyError("CREDENTIALS_IN_URL")
    host = _clean_host(parsed.hostname or "")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UrlSafetyError("INVALID_PORT") from exc
    port = port or (443 if scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise UrlSafetyError("DISALLOWED_PORT", str(port))
    if host in BLOCKED_HOSTS or any(host.endswith(suffix) for suffix in BLOCKED_SUFFIXES):
        raise UrlSafetyError("BLOCKED_HOST", host)

    literal: Optional[ipaddress._BaseAddress] = None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and _public_ip(host) is None:
        raise UrlSafetyError("PRIVATE_ADDRESS", "IP literal is not globally routable")

    addresses = [host] if literal is not None else list((resolver or _default_resolver)(host, port))
    if not addresses:
        raise UrlSafetyError("DNS_ERROR", "no addresses")
    for address in addresses:
        if _public_ip(address) is None:
            # A single private answer is enough to refuse: a round-robin attacker can otherwise
            # win the race on the connection.
            raise UrlSafetyError("PRIVATE_ADDRESS", "host resolves to a non-public address")
    ip = str(addresses[0])
    path_query = parsed.path or "/"
    if parsed.query:
        path_query += f"?{parsed.query}"
    default = 443 if scheme == "https" else 80
    host_header = host if port == default else f"{host}:{port}"
    return SafeTarget(raw, scheme, host, port, ip, path_query, host_header)


def _client(*, transport: Optional[httpx.BaseTransport] = None,
            timeout: float = DEFAULT_TIMEOUT,
            connect_timeout: float = DEFAULT_CONNECT_TIMEOUT) -> httpx.Client:
    return httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(timeout, connect=connect_timeout),
        verify=True,
        trust_env=False,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
    )


def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    allowed_content_types: Optional[Iterable[str]] = None,
    resolver: Optional[Resolver] = None,
    transport: Optional[httpx.BaseTransport] = None,
) -> SafeResponse:
    """Fetch a URL through every W11 control. Never follows an unvalidated redirect."""
    method = method.upper()
    if method not in ("GET", "HEAD"):
        raise UrlSafetyError("INVALID_METHOD", method)
    allowed = tuple(t.lower() for t in (allowed_content_types or ()))
    current = url
    redirects: List[str] = []
    client = _client(transport=transport, timeout=timeout, connect_timeout=connect_timeout)
    try:
        for _hop in range(max_redirects + 1):
            target = validate_url(current, resolver=resolver)
            request_headers = dict(headers or {})
            request_headers["Host"] = target.host_header
            extensions: Dict[str, Any] = {}
            if target.scheme == "https":
                extensions["sni_hostname"] = target.host
            try:
                request = client.build_request(method, target.pinned_url,
                                               headers=request_headers, extensions=extensions)
                response = client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                raise UrlSafetyError("TIMEOUT", type(exc).__name__) from exc
            except httpx.HTTPError as exc:
                raise UrlSafetyError("NETWORK_ERROR", type(exc).__name__) from exc

            try:
                if response.status_code in REDIRECT_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise UrlSafetyError("UNSAFE_REDIRECT", "redirect without Location")
                    if len(redirects) >= max_redirects:
                        raise UrlSafetyError("REDIRECT_LIMIT")
                    next_url = urljoin(current, location)
                    next_scheme = urlsplit(next_url).scheme.lower()
                    if scheme_of(current) == "https" and next_scheme == "http":
                        raise UrlSafetyError("UNSAFE_REDIRECT", "HTTPS downgrade refused")
                    redirects.append(next_url)
                    current = next_url
                    continue

                content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                if allowed and (not content_type or not any(
                        content_type.startswith(prefix) for prefix in allowed)):
                    raise UrlSafetyError("CONTENT_TYPE", content_type or "missing")

                content = b""
                if method != "HEAD":
                    chunks: List[bytes] = []
                    total = 0
                    try:
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise UrlSafetyError("RESPONSE_TOO_LARGE", str(max_bytes))
                            chunks.append(chunk)
                    except UrlSafetyError:
                        raise
                    except httpx.TimeoutException as exc:
                        raise UrlSafetyError("TIMEOUT", type(exc).__name__) from exc
                    except httpx.HTTPError as exc:
                        raise UrlSafetyError("NETWORK_ERROR", type(exc).__name__) from exc
                    content = b"".join(chunks)

                encoding = getattr(response, "encoding", None) or "utf-8"
                try:
                    text = content.decode(encoding, errors="replace")
                except LookupError:
                    text = content.decode("utf-8", errors="replace")
                return SafeResponse(
                    status_code=response.status_code, url=current,
                    headers={k.lower(): v for k, v in response.headers.items()},
                    content=content, text=text, redirects=tuple(redirects),
                )
            finally:
                response.close()
        raise UrlSafetyError("REDIRECT_LIMIT")
    finally:
        client.close()


def scheme_of(url: str) -> str:
    try:
        return urlsplit(url).scheme.lower()
    except ValueError:
        return ""


def safe_host(url: str) -> str:
    """A log-safe host for an untrusted URL: never the path, query or credentials."""
    try:
        return (urlsplit(url).hostname or "")[:120]
    except ValueError:
        return ""


def log_refusal(url: str, error: UrlSafetyError) -> None:
    log.warning("url_safety: refused request host=%s reason=%s", safe_host(url), error.code)


__all__ = [
    "UrlSafetyError", "SafeTarget", "SafeResponse", "validate_url", "fetch",
    "safe_host", "log_refusal", "DEFAULT_CONTENT_TYPES",
]
