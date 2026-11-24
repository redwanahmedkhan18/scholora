"""
Source-centric verification.

Two jobs live here, and the split between them is the important part.

A machine can establish whether a citation still *resolves*. It cannot establish whether
the page says what we claim it says. So this module does the first job honestly and
refuses to pretend it has done the second: reachability never changes a record's
confidence, and only a named human calling `verify_source` can move anything to VERIFIED.

The grouping matters too. The platform holds thousands of field-level claims drawn from a
few dozen pages. Presented field by field the backlog is hopeless; presented page by page
a verifier opens one university fee page and confirms every figure that cites it in a
single action. `source_queue` builds that page-level view.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.crm import DataVerification, SourceCheck

log = logging.getLogger(__name__)

# State vocabulary. UNREACHABLE is not a synonym for BROKEN - see the model docstring.
OK, BROKEN, BLOCKED, UNREACHABLE, UNCHECKED = (
    "OK", "BROKEN", "BLOCKED", "UNREACHABLE", "UNCHECKED")
STATES = (OK, BROKEN, BLOCKED, UNREACHABLE, UNCHECKED)

# Only these say the page is actually gone. Everything else is a statement about our
# access, not about the citation. This distinction was not academic: the "la Caixa"
# Foundation fellowship page - a correct, current, manually verified citation - answers
# 403 to an unrecognised user-agent. Classifying that as BROKEN would have told a
# verifier to go and remove perfectly good data, and at scale it would have condemned
# every well-defended institutional site on the platform.
_GONE = {404, 410}
# Refused, not absent: authentication walls, bot protection, rate limits.
_REFUSED = {401, 403, 406, 429}


def _classify(status: int) -> tuple[str, str]:
    if status < 400:
        return OK, ""
    if status in _GONE:
        return BROKEN, f"page not found ({status})"
    if status in _REFUSED:
        return BLOCKED, (f"the site refused an automated request ({status}); "
                         f"this says nothing about the page - open it by hand")
    if status >= 500:
        return UNREACHABLE, f"server error ({status}); try again later"
    return BLOCKED, f"unexpected response ({status}); check by hand"

_TIMEOUT = httpx.Timeout(12.0, connect=6.0)
_HEADERS = {"User-Agent": "ScholaraSourceChecker/1.0 (+citation verification)"}


# --------------------------------------------------------------------- checking
def check_url(url: str, client: Optional[httpx.Client] = None) -> Dict[str, Any]:
    """
    Resolve one citation URL. Never raises.

    A HEAD is tried first because it is cheap, then a GET, because a surprising number of
    institutional sites reject HEAD with 403/405 while serving GET perfectly well. Calling
    such a page "broken" on the strength of a HEAD would be wrong.
    """
    owned = client is None
    client = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=True, headers=_HEADERS)
    try:
        response = None
        try:
            response = client.head(url)
            if response.status_code in (403, 404, 405, 501):
                response = client.get(url)
        except httpx.HTTPError:
            response = client.get(url)

        status = response.status_code
        state, note = _classify(status)
        result = {"state": state, "http_status": status,
                  "final_url": str(response.url)[:600], "note": note}
        if str(response.url).rstrip("/") != url.rstrip("/"):
            result["note"] = (note + "; redirected").strip("; ")
        return result

    except Exception as exc:                                            # noqa: BLE001
        # Could not reach it. That is a statement about us, not about the page: no
        # network egress, DNS failure, TLS problem, timeout. Saying BROKEN here would
        # condemn every citation on the platform the first time a firewall got in the way.
        return {"state": UNREACHABLE, "http_status": None, "final_url": None,
                "note": f"could not check: {type(exc).__name__}"[:300]}
    finally:
        if owned:
            client.close()


def sweep(db: Session, limit: int = 100, only_unchecked: bool = False) -> Dict[str, Any]:
    """Check the distinct citation URLs in use and record what came back."""
    stmt = (select(DataVerification.source_url, func.count())
            .where(DataVerification.source_url.is_not(None))
            .group_by(DataVerification.source_url)
            .order_by(func.count().desc()))
    urls = [(u, n) for u, n in db.execute(stmt).all() if u]

    existing = {c.url: c for c in db.execute(select(SourceCheck)).scalars().all()}
    if only_unchecked:
        urls = [(u, n) for u, n in urls if u not in existing or existing[u].state == UNCHECKED]
    urls = urls[:limit]

    tally = {OK: 0, BROKEN: 0, BLOCKED: 0, UNREACHABLE: 0}
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True, headers=_HEADERS) as client:
        for url, _count in urls:
            outcome = check_url(url, client=client)
            record = existing.get(url)
            if record is None:
                record = SourceCheck(url=url[:600])
                db.add(record)
                existing[url] = record
            record.state = outcome["state"]
            record.http_status = outcome["http_status"]
            record.final_url = outcome["final_url"]
            record.note = outcome["note"] or None
            record.checked_at = datetime.now(timezone.utc)
            tally[outcome["state"]] = tally.get(outcome["state"], 0) + 1

    db.commit()
    return {"checked": len(urls), **tally,
            "note": "BROKEN means the page is genuinely gone (404/410). BLOCKED means the "
                    "site refused an automated request and the citation may be perfectly "
                    "good. UNREACHABLE means the check itself could not run."}


# ------------------------------------------------------------------ the worklist
def source_queue(db: Session, state: Optional[str] = None,
                 limit: int = 100) -> List[Dict[str, Any]]:
    """
    The verification backlog grouped by the page a human would actually open.

    Each entry says: this URL supports N field claims across these entities, M of them
    still unverified, and here is what we know about whether the page is still live.
    """
    rows = db.execute(
        select(DataVerification.source_url,
               func.count().label("claims"),
               func.count(func.nullif(DataVerification.status, "VERIFIED")).label("pending"),
               func.min(DataVerification.retrieved_at).label("oldest"))
        .where(DataVerification.source_url.is_not(None))
        .group_by(DataVerification.source_url)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()

    checks = {c.url: c for c in db.execute(select(SourceCheck)).scalars().all()}

    out: List[Dict[str, Any]] = []
    for url, claims, pending, oldest in rows:
        check = checks.get(url)
        if state and (check.state if check else UNCHECKED) != state:
            continue
        entities = db.execute(
            select(DataVerification.entity_type, func.count())
            .where(DataVerification.source_url == url)
            .group_by(DataVerification.entity_type)
        ).all()
        out.append({
            "source_url": url,
            "claims": claims,
            "pending": pending,
            "verified": claims - pending,
            "oldest_retrieved_at": oldest.isoformat() if oldest else None,
            "entities": {t: n for t, n in entities},
            "link_state": check.state if check else UNCHECKED,
            "http_status": check.http_status if check else None,
            "final_url": check.final_url if check else None,
            "checked_at": check.checked_at.isoformat() if check and check.checked_at else None,
            "note": check.note if check else None,
        })
    return out


def verify_source(db: Session, *, source_url: str, verifier: str,
                  fields: Optional[List[int]] = None,
                  note: str = "") -> Dict[str, Any]:
    """
    Record that a named human opened this page and confirmed the claims citing it.

    `verifier` is required and is stored verbatim. The whole provenance model rests on a
    person being answerable for the word VERIFIED, so there is no system default here and
    no way to reach this code path from an automated job.
    """
    verifier = (verifier or "").strip()
    if not verifier:
        raise ValueError("a verifier name is required - VERIFIED must be attributable")

    stmt = select(DataVerification).where(DataVerification.source_url == source_url)
    if fields:
        stmt = stmt.where(DataVerification.id.in_(fields))
    records = db.execute(stmt).scalars().all()
    if not records:
        raise ValueError("no claims cite that source URL")

    now = datetime.now(timezone.utc)
    for record in records:
        record.status = "VERIFIED"
        record.verified_at = now
        record.verified_by = verifier[:160]

    db.commit()
    return {"source_url": source_url, "verified": len(records), "verified_by": verifier,
            "verified_at": now.isoformat(), "note": note}
