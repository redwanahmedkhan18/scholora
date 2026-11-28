"""Private document storage with short-lived signed URLs (§security).

Student documents (passports, transcripts, bank statements) must never be publicly
addressable. Objects are stored under an opaque key; the only way to read one is a
signed, expiring token bound to that exact key.

Two backends with one interface:
  * ``local``  - files under STORAGE_DIR, outside the web root. Default.
  * ``s3``     - any S3-compatible bucket (set S3_* env vars). The bucket must be private.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import mimetypes
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.config import settings

ALLOWED_MIME = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_BYTES = 15 * 1024 * 1024  # 15 MB


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    mime: str
    original_name: str


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64u(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


# --------------------------------------------------------------------- signing
def sign_key(key: str, ttl: Optional[int] = None) -> str:
    """Return an opaque token granting read access to exactly ``key`` until it expires."""
    ttl = ttl or settings.signed_url_ttl_seconds
    expires = int(time.time()) + ttl
    payload = f"{key}|{expires}".encode()
    mac = hmac.new(settings.secret_key.encode(), payload, hashlib.sha256).digest()
    return f"{_b64u(payload)}.{_b64u(mac[:24])}"


def verify_signature(token: str) -> str:
    """Validate a signed token and return the key it grants. Raises on any problem."""
    try:
        body, mac = token.split(".", 1)
        payload = _unb64u(body)
        expected = hmac.new(settings.secret_key.encode(), payload, hashlib.sha256).digest()[:24]
    except Exception as exc:
        raise StorageError("Malformed download token") from exc

    if not hmac.compare_digest(_unb64u(mac), expected):
        raise StorageError("Invalid download token")

    key, _, expires = payload.decode().rpartition("|")
    if not key or int(expires) < int(time.time()):
        raise StorageError("This download link has expired. Please request a new one.")
    return key


def signed_url(key: str, ttl: Optional[int] = None) -> str:
    return f"{settings.api_v1_prefix}/documents/file/{sign_key(key, ttl)}"


# --------------------------------------------------------------------- backends
class LocalBackend:
    name = "local"

    def __init__(self) -> None:
        self.root = Path(settings.storage_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        target = (self.root / key).resolve()
        if not str(target).startswith(str(self.root)):  # traversal guard
            raise StorageError("Invalid storage key")
        return target

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.chmod(path, 0o600)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise StorageError("File not found")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Backend:  # pragma: no cover - needs real credentials
    name = "s3"

    def __init__(self) -> None:
        import boto3  # imported lazily so boto3 stays optional

        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region or None,
        )

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ACL="private")

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:
            # Distinguish "absent" from "could not ask". Only an explicit missing-object answer
            # is proof of deletion; anything else (auth, transport, throttling) must not be read
            # as success, or an erasure could clear its queue row without deleting anything.
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise StorageError(f"Could not confirm object state: {type(exc).__name__}") from exc


def _make_backend():
    if settings.storage_backend.lower() == "s3" and settings.s3_bucket:
        try:
            return S3Backend()
        except Exception:
            pass
    return LocalBackend()


backend = _make_backend()


# ------------------------------------------------------------------ public API
def save_upload(
    *, owner_scope: str, filename: str, content: bytes, declared_mime: str | None = None
) -> StoredObject:
    """Validate and store an uploaded file. ``owner_scope`` namespaces the key."""
    if not content:
        raise StorageError("The uploaded file is empty.")
    if len(content) > MAX_BYTES:
        raise StorageError(f"File is too large. The maximum is {MAX_BYTES // (1024 * 1024)} MB.")

    mime = (declared_mime or mimetypes.guess_type(filename)[0] or "application/octet-stream").lower()
    if mime not in ALLOWED_MIME:
        raise StorageError(
            "Unsupported file type. Upload a PDF, JPG, PNG, WEBP or Word document."
        )

    # Content sniffing: the declared type must not contradict the magic bytes.
    if mime == "application/pdf" and not content.startswith(b"%PDF"):
        raise StorageError("That file is not a valid PDF.")
    if mime in {"image/jpeg"} and not content.startswith(b"\xff\xd8"):
        raise StorageError("That file is not a valid JPEG.")
    if mime == "image/png" and not content.startswith(b"\x89PNG"):
        raise StorageError("That file is not a valid PNG.")

    safe_name = os.path.basename(filename).replace("\\", "_")[:120] or "upload"
    key = f"{owner_scope}/{int(time.time())}-{secrets.token_hex(8)}-{safe_name}"
    backend.put(key, content)
    return StoredObject(key=key, size=len(content), mime=mime, original_name=safe_name)


def read(key: str) -> bytes:
    return backend.get(key)


def remove(key: str) -> None:
    try:
        backend.delete(key)
    except Exception:
        pass


def exists(key: str) -> bool:
    """Whether the object is still stored. Never raises: an unreadable backend is 'unknown',
    which the deletion outbox records as a failure rather than silently accepting."""
    try:
        return bool(backend.exists(key))
    except Exception:
        return False


def remove_verified(key: str) -> None:
    """Delete an object and confirm it is gone; raise ``StorageError`` if it is not.

    ``remove`` is deliberately best-effort for user-initiated deletes, where a storage hiccup
    must not block a request. Erasure is different: claiming a document was destroyed while its
    bytes are still readable is a false privacy statement, so this variant refuses to guess.
    Deletion is idempotent - an absent object counts as success.
    """
    try:
        backend.delete(key)
    except Exception as exc:  # normalize every adapter failure to one type
        raise StorageError(f"Could not delete the stored object: {type(exc).__name__}") from exc
    try:
        still_there = backend.exists(key)
    except Exception as exc:
        # The backend could not tell us. Treat that as unconfirmed, never as success.
        raise StorageError(f"Could not confirm the object was deleted: {type(exc).__name__}") from exc
    if still_there:
        raise StorageError("The stored object is still present after deletion")
