"""Catalogue integrity audit (W9).

A read-only report, not a fixer. It answers one question for a data manager: *where is our
catalogue not trustworthy yet?* The checks are structural - they can falsify a record's
trustworthiness, and none of them can establish it. Passing every check here does not make a
university's tuition official; only a verified source and a human decision do that
(``core.verification_policy`` and the W12 review path).

Bounded by design: each collection is read with a cap, so a large catalogue degrades into "the
first N rows were checked" rather than into a slow request or an out-of-memory event. The report
says which caps applied.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import verification_policy as policy
from app.models.base import DEGREE_LEVELS, SOURCE_TIERS
from app.models.crm import CatalogueChange, DataVerification
from app.models.education import Country, Program, ProgramIntake, Scholarship, University

#: Hard bound on how many rows of each collection are inspected per request.
AUDIT_CAP = 2000

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

CATEGORIES: Dict[str, Dict[str, str]] = {
    "missing_provenance": {"severity": "HIGH",
                           "detail": "a published record has no source link"},
    "invalid_source_type": {"severity": "HIGH",
                            "detail": "the source tier is not one of the declared tiers"},
    "invalid_source_url": {"severity": "MEDIUM",
                           "detail": "the source link is not a usable http(s) URL"},
    "duplicate_university": {"severity": "MEDIUM",
                             "detail": "two institutions share a name or slug"},
    "duplicate_program": {"severity": "MEDIUM",
                          "detail": "the same programme is recorded twice for one institution"},
    "invalid_degree_level": {"severity": "HIGH",
                             "detail": "the degree level is not in the controlled vocabulary"},
    "country_region_mismatch": {"severity": "HIGH",
                                "detail": "the region does not belong to the institution's country"},
    "tuition_without_currency": {"severity": "HIGH",
                                 "detail": "a fee has an amount but no supported currency"},
    "invalid_tuition": {"severity": "HIGH",
                        "detail": "a fee is negative or not a number"},
    "invalid_intake": {"severity": "MEDIUM",
                       "detail": "an intake has no usable label or year"},
    "expired_open_intake": {"severity": "MEDIUM",
                            "detail": "an intake is marked OPEN but its application deadline passed"},
    "stale_verification": {"severity": "MEDIUM",
                           "detail": "the review deadline passed, so the figure needs re-verification"},
    "unregistered_provenance": {"severity": "LOW",
                                "detail": "a verified record has no field-level provenance rows"},
    "conflicting_evidence": {"severity": "HIGH",
                             "detail": "two sources disagree and the conflict is unresolved"},
    "pending_material_change": {"severity": "INFO",
                                "detail": "a recorded change is waiting for a reviewer"},
}

_HTTP = ("http", "https")


def _finding(category: str, entity_type: str, entity_id: Optional[int], detail: str,
             **extra: Any) -> Dict[str, Any]:
    return {"category": category, "severity": CATEGORIES[category]["severity"],
            "entity_type": entity_type, "entity_id": entity_id,
            "detail": detail, **extra}


def _url_problem(url: Optional[str]) -> Optional[str]:
    if not url:
        return "missing"
    parts = urlsplit(str(url).strip())
    if parts.scheme.lower() not in _HTTP or not parts.netloc:
        return "not an http(s) URL"
    return None


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def audit(db: Session, *, sample: int = 5, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Return bounded integrity findings grouped by category."""
    now = now or datetime.now(timezone.utc)
    today = now.date() if isinstance(now, datetime) else date.today()

    findings: List[Dict[str, Any]] = []
    checked = {"universities": 0, "programs": 0, "program_intakes": 0, "scholarships": 0,
               "countries": 0, "data_verifications": 0, "catalogue_changes": 0}
    truncated: List[str] = []

    def _load(model, key: str):
        rows = list(db.execute(select(model).limit(AUDIT_CAP)).scalars())
        checked[key] = len(rows)
        total = db.scalar(select(func.count()).select_from(model)) or 0
        if total > len(rows):
            truncated.append(f"{key}: audited {len(rows)} of {total}")
        return rows

    countries = _load(Country, "countries")
    country_by_id = {c.id: c for c in countries}

    universities = _load(University, "universities")
    seen_names: Dict[str, int] = {}
    for u in universities:
        problem = _url_problem(getattr(u, "source_url", None))
        if problem:
            findings.append(_finding("missing_provenance" if problem == "missing"
                                     else "invalid_source_url", "university", u.id,
                                     f"{u.name}: source {problem}"))
        tier = (getattr(u, "source_type", "") or "").upper()
        if tier not in SOURCE_TIERS:
            findings.append(_finding("invalid_source_type", "university", u.id,
                                     f"{u.name}: {tier or 'missing'}"))
        key = _norm(u.name)
        if key in seen_names and seen_names[key] != u.id:
            findings.append(_finding("duplicate_university", "university", u.id,
                                     f"{u.name} duplicates institution {seen_names[key]}"))
        else:
            seen_names[key] = u.id
        if u.region_id:
            region = country_by_id.get(u.region_id)
            if region is not None and region.parent_country_id not in (None, u.country_id):
                findings.append(_finding(
                    "country_region_mismatch", "university", u.id,
                    f"{u.name}: region {region.name} belongs to country {region.parent_country_id}"))
        due = policy._as_utc(getattr(u, "review_due_at", None))
        if due is not None and due < now:
            findings.append(_finding("stale_verification", "university", u.id,
                                     f"{u.name}: review was due {due.date()}"))

    programs = _load(Program, "programs")
    seen_programs: Dict[str, int] = {}
    for p in programs:
        if not getattr(p, "is_published", True):
            continue
        problem = _url_problem(getattr(p, "source_url", None))
        if problem:
            findings.append(_finding("missing_provenance" if problem == "missing"
                                     else "invalid_source_url", "program", p.id,
                                     f"{p.name}: source {problem}"))
        tier = (getattr(p, "source_type", "") or "").upper()
        if tier not in SOURCE_TIERS:
            findings.append(_finding("invalid_source_type", "program", p.id,
                                     f"{p.name}: {tier or 'missing'}"))
        degree = (getattr(p, "degree_level", "") or "").upper()
        if degree not in DEGREE_LEVELS:
            findings.append(_finding("invalid_degree_level", "program", p.id,
                                     f"{p.name}: {degree or 'missing'}"))
        if p.tuition_annual is not None:
            if float(p.tuition_annual) < 0:
                findings.append(_finding("invalid_tuition", "program", p.id,
                                         f"{p.name}: tuition {p.tuition_annual}"))
            from app.services import fx

            if not fx.supported(getattr(p, "currency", None)):
                findings.append(_finding(
                    "tuition_without_currency", "program", p.id,
                    f"{p.name}: currency {getattr(p, 'currency', None) or 'missing'}"))
        key = f"{p.university_id}:{degree}:{_norm(p.name)}"
        if key in seen_programs and seen_programs[key] != p.id:
            findings.append(_finding("duplicate_program", "program", p.id,
                                     f"{p.name} duplicates programme {seen_programs[key]}"))
        else:
            seen_programs[key] = p.id
        due = policy._as_utc(getattr(p, "review_due_at", None))
        if due is not None and due < now:
            findings.append(_finding("stale_verification", "program", p.id,
                                     f"{p.name}: review was due {due.date()}"))

    intakes = _load(ProgramIntake, "program_intakes")
    for i in intakes:
        if not (i.label or "").strip() or i.year is None:
            findings.append(_finding("invalid_intake", "program_intake", i.id,
                                     f"label={i.label!r} year={i.year!r}"))
        elif not (1990 <= int(i.year) <= 2100):
            findings.append(_finding("invalid_intake", "program_intake", i.id,
                                     f"implausible year {i.year}"))
        deadline = i.application_deadline
        if (getattr(i, "status", "") or "").upper() == "OPEN" and isinstance(deadline, date) \
                and deadline < today:
            findings.append(_finding("expired_open_intake", "program_intake", i.id,
                                     f"{i.label} is OPEN but closed on {deadline}"))

    scholarships = _load(Scholarship, "scholarships")
    for s in scholarships:
        problem = _url_problem(getattr(s, "source_url", None))
        if problem:
            findings.append(_finding("missing_provenance" if problem == "missing"
                                     else "invalid_source_url", "scholarship", s.id,
                                     f"{s.name}: source {problem}"))
        tier = (getattr(s, "source_type", "") or "").upper()
        if tier not in SOURCE_TIERS:
            findings.append(_finding("invalid_source_type", "scholarship", s.id,
                                     f"{s.name}: {tier or 'missing'}"))
        if s.amount_value is not None and float(s.amount_value) < 0:
            findings.append(_finding("invalid_tuition", "scholarship", s.id,
                                     f"{s.name}: amount {s.amount_value}"))
        due = policy._as_utc(getattr(s, "review_due_at", None))
        if due is not None and due < now:
            findings.append(_finding("stale_verification", "scholarship", s.id,
                                     f"{s.name}: review was due {due.date()}"))

    # Field-level provenance: a record claiming VERIFIED with no register rows has no evidence
    # for any individual field, which is exactly the "one global timestamp" pattern W9 forbids.
    registers = _load(DataVerification, "data_verifications")
    registered: Dict[tuple, int] = {}
    for v in registers:
        reg_key = (v.entity_type, v.entity_id)
        registered[reg_key] = registered.get(reg_key, 0) + 1
        if (v.status or "").upper() == "CONFLICTING":
            findings.append(_finding("conflicting_evidence", v.entity_type, v.entity_id,
                                     f"field {v.field_name} has conflicting sources",
                                     field_name=v.field_name))
    for entity_type, rows in (("university", universities), ("program", programs),
                              ("scholarship", scholarships)):
        for row in rows:
            if (getattr(row, "confidence", "") or "").upper() == "VERIFIED" \
                    and (entity_type, row.id) not in registered:
                findings.append(_finding("unregistered_provenance", entity_type, row.id,
                                         "marked VERIFIED with no field-level provenance"))

    changes = _load(CatalogueChange, "catalogue_changes")
    for c in changes:
        if (c.status or "").upper() == "PENDING" \
                and (c.classification or "") in ("MATERIAL", "SUSPICIOUS"):
            findings.append(_finding("pending_material_change", c.entity_type, c.entity_id,
                                     f"{c.field_name} is awaiting review",
                                     field_name=c.field_name, change_id=c.id))

    counts: Dict[str, int] = {category: 0 for category in CATEGORIES}
    for f in findings:
        counts[f["category"]] += 1

    ordered = sorted(findings, key=lambda f: (SEVERITY_ORDER[f["severity"]], f["category"],
                                              f["entity_type"], f["entity_id"] or 0))
    return {
        "generated_at": now.isoformat(),
        "policy_version": policy.POLICY_VERSION,
        "counts": counts,
        "total_findings": len(ordered),
        "critical_or_high": sum(counts[c] for c in counts
                                if CATEGORIES[c]["severity"] in ("CRITICAL", "HIGH")),
        "checked": checked,
        "truncated": truncated,
        "categories": CATEGORIES,
        "findings": ordered[: max(1, min(int(sample) * 40, 400))],
    }
