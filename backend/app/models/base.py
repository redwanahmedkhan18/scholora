"""Shared mixins: timestamps, SEO, and the source-verification contract (spec §39/§40/§69)."""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB

# PostgreSQL has two JSON types and the difference is not cosmetic. Plain `json` stores the
# document as text and has NO equality operator, so any query with SELECT DISTINCT over a table
# containing one fails outright with "could not identify an equality operator for type json" -
# which is exactly what the programme and university search endpoints do. `jsonb` is the parsed
# binary form: it supports equality, comparison and GIN indexing.
#
# SQLAlchemy's generic JSON maps to `json` on PostgreSQL, so it has to be asked for explicitly.
# The variant keeps SQLite on its own JSON type, where this distinction does not exist.
JSONType = JSON().with_variant(JSONB(), "postgresql")
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SeoMixin:
    seo_title: Mapped[Optional[str]] = mapped_column(String(255), default=None)
    seo_description: Mapped[Optional[str]] = mapped_column(Text, default=None)
    seo_keywords: Mapped[Optional[str]] = mapped_column(Text, default=None)
    og_image: Mapped[Optional[str]] = mapped_column(String(500), default=None)
    noindex: Mapped[bool] = mapped_column(default=False)


class VerificationMixin:
    """Every fact a student could act on must carry provenance."""
    source_url: Mapped[Optional[str]] = mapped_column(String(600), default=None)
    source_type: Mapped[str] = mapped_column(String(40), default="TIER_1_OFFICIAL_UNIVERSITY")
    retrieved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    verified_by: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    review_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    confidence: Mapped[str] = mapped_column(String(20), default="UNVERIFIED")  # §40


# ---- Controlled vocabularies (kept as constants, not DB enums, for portability) ----
SOURCE_TIERS = [
    "TIER_1_OFFICIAL_UNIVERSITY",
    "TIER_2_OFFICIAL_DEPARTMENT",
    "TIER_3_OFFICIAL_ADMISSIONS_PORTAL",
    "TIER_4_OFFICIAL_SCHOLARSHIP_PROVIDER",
    "TIER_5_OFFICIAL_RANKING_PROVIDER",
    "TIER_6_INTERNAL_CONSULTANCY",
    "TIER_7_THIRD_PARTY",
]
CONFIDENCE_STATES = ["VERIFIED", "NEEDS_REVIEW", "OUTDATED", "CONFLICTING", "UNVERIFIED"]
ELIGIBILITY_STATES = ["MEETS", "DOES_NOT_MEET", "PARTIALLY_MEETS", "REQUIRES_REVIEW", "INFORMATION_UNAVAILABLE"]
DEGREE_LEVELS = ["FOUNDATION", "DIPLOMA", "BACHELOR", "MASTER", "PHD"]
FUNDING_SOURCES = ["Family", "Self funded", "Bank loan", "Sponsor", "Scholarship dependent"]
# Student-profile vocabularies. Bangladesh-specific qualification ladder, the CGPA scales
# actually used by BD institutions, and the English tests the eligibility engine reads.
EDUCATION_LEVELS = ["SSC", "HSC", "Diploma", "Bachelor", "Master", "MPhil"]
CGPA_SCALES = ["4", "5", "10", "100"]
ENGLISH_TESTS = ["IELTS", "TOEFL", "PTE", "Duolingo", "None yet"]
COUNTRY_STATUSES = ["ACTIVE", "COMING_SOON", "PAUSED", "INACTIVE"]
PARTNER_STATUSES = ["PARTNER", "DIRECT_APPLICATION", "REFERRED", "NON_PARTNER"]
LEAD_STAGES = [
    "NEW", "CONTACTED", "PROFILE_COLLECTED", "COUNSELING", "SHORTLIST", "DOCUMENTS",
    "APPLICATION", "OFFER", "VISA", "PRE_DEPARTURE", "DEPARTED", "LOST",
]
APPLICATION_STATUSES = [
    "PLANNING", "ELIGIBILITY_CHECKED", "DOCUMENTS_PENDING", "APPLICATION_PREPARATION", "SUBMITTED",
    "UNDER_REVIEW", "CONDITIONAL_OFFER", "UNCONDITIONAL_OFFER", "DEPOSIT", "COE_I20_CAS",
    "VISA_PREPARATION", "VISA_SUBMITTED", "VISA_GRANTED", "COMPLETED", "WITHDRAWN", "REJECTED",
]
