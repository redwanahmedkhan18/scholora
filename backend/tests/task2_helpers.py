"""Shared fixtures for the Task 2 acceptance tests (W8 / W10 / W1 / W2).

These helpers create *test* rows in the temporary SQLite database only. They are explicitly
not a source of product data: every value is a marker that makes it obvious the row is a test
artefact, and every row is deleted by ``cleanup`` in a ``finally`` block so the rest of the
suite sees the same seeded catalogue it did before.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from app.core.database import SessionLocal
from app.models.crm import DataVerification
from app.models.education import Country, Program, ProgramIntake, University
from sqlalchemy import select


def utc(days: float = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


def us_country(db) -> Country:
    row = db.execute(select(Country).where(Country.iso2 == "US")).scalars().first()
    assert row is not None, "the seeded catalogue must contain the United States"
    return row


def make_university(
    db, *,
    slug: str,
    name: Optional[str] = None,
    city: str = "Arlington",
    confidence: str = "VERIFIED",
    source_type: str = "TIER_1_OFFICIAL_UNIVERSITY",
    verified_days_ago: float = 0,
    review_in_days: float = 90,
    source_url: Optional[str] = "https://example.edu/official",
    verified_by: str = "Acceptance Test Verifier",
    website: Optional[str] = None,
) -> University:
    country = us_country(db)
    row = University(
        name=name or f"Test University {slug}",
        slug=slug,
        country_id=country.id,
        city=city,
        website=website or f"https://{slug}.example.edu",
        overview="Test-only institution created by the Task 2 acceptance suite.",
        confidence=confidence,
        source_type=source_type,
        source_url=source_url,
        verified_at=utc(-verified_days_ago),
        review_due_at=utc(review_in_days) if review_in_days is not None else None,
        verified_by=verified_by,
    )
    db.add(row)
    db.flush()
    return row


def make_program(
    db, university: University, *,
    slug: str,
    name: Optional[str] = None,
    confidence: str = "VERIFIED",
    source_type: str = "TIER_1_OFFICIAL_UNIVERSITY",
    verified_days_ago: float = 0,
    review_in_days: float = 90,
    source_url: Optional[str] = "https://example.edu/programme/official",
    verified_by: str = "Acceptance Test Verifier",
    tuition: float = 21234.0,
) -> Program:
    row = Program(
        university_id=university.id,
        name=name or f"Test Programme {slug}",
        slug=slug,
        degree_level="MASTER",
        award="MSc",
        discipline="Computer Science",
        currency="USD",
        tuition_annual=tuition,
        overview="Test-only programme created by the Task 2 acceptance suite.",
        is_published=True,
        confidence=confidence,
        source_type=source_type,
        source_url=source_url,
        verified_at=utc(-verified_days_ago),
        review_due_at=utc(review_in_days) if review_in_days is not None else None,
        verified_by=verified_by,
    )
    db.add(row)
    db.flush()
    return row


def make_intake(
    db, program: Program, *,
    label: str,
    year: int,
    term: str = "Fall",
    confidence: str = "VERIFIED",
    source_type: str = "TIER_1_OFFICIAL_UNIVERSITY",
    verified_days_ago: float = 0,
    review_in_days: float = 90,
    source_url: Optional[str] = "https://example.edu/intakes/official",
    verified_by: str = "Acceptance Test Verifier",
) -> ProgramIntake:
    row = ProgramIntake(
        program_id=program.id,
        label=label,
        term=term,
        year=year,
        status="OPEN",
        confidence=confidence,
        source_type=source_type,
        source_url=source_url,
        verified_at=utc(-verified_days_ago),
        review_due_at=utc(review_in_days) if review_in_days is not None else None,
        verified_by=verified_by,
    )
    db.add(row)
    db.flush()
    return row


def make_conflict(db, *, entity_type: str, entity_id: int, field: str = "tuition_annual") -> DataVerification:
    row = DataVerification(
        entity_type=entity_type,
        entity_id=entity_id,
        field_name=field,
        field_value="99999",
        source_url="https://aggregator.example.org/conflict",
        source_type="TIER_7_THIRD_PARTY",
        status="CONFLICTING",
        conflict_with="Test conflict: a secondary source disagrees; not yet resolved.",
    )
    db.add(row)
    db.flush()
    return row


def cleanup(db, *objects: Any) -> None:
    """Delete every test row, children first, tolerating already-deleted parents.

    Each delete is committed on its own so a cascade that already removed a child cannot roll
    back the rows that were successfully removed before it.
    """
    for obj in objects:
        if obj is None:
            continue
        try:
            db.delete(obj)
            db.commit()
        except Exception:
            db.rollback()


def new_session():
    return SessionLocal()


def open_assistant_session(client, api: str, profile_token: str | None = None):
    """Create a verified W4 session and return (state, headers) for cookie+CSRF requests.

    The TestClient keeps the HttpOnly cookie; only the non-sensitive CSRF token is returned
    here. Callers should clear cookies in a finally block so the shared client stays clean.
    """
    body = {"profile_token": profile_token} if profile_token else {}
    res = client.post(f"{api}/assistant/session", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data.get("sid") and data.get("csrf_token")
    return data, {"X-CSRF-Token": data["csrf_token"]}


def clear_assistant_cookies(client) -> None:
    client.cookies.clear()


def program_payload_keys() -> List[str]:
    return ["id", "slug", "tuition_annual", "currency", "verified", "verification_reason"]


def financial_numbers(text: str) -> List[str]:
    """Currency amounts in an answer - used to prove a degraded reply contains none."""
    import re

    return re.findall(r"(?:[$€£]\s?\d[\d,.]*|\b\d{4,}\b)", text or "")
