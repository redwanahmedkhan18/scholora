"""Education intelligence graph: Country -> University -> Faculty -> Department -> Program -> Requirements."""
from datetime import date, datetime
from typing import Any, List, Optional

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import SeoMixin, TimestampMixin, VerificationMixin, JSONType


class Country(Base, TimestampMixin, SeoMixin):
    __tablename__ = "countries"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True)
    iso2: Mapped[Optional[str]] = mapped_column(String(2))
    flag_emoji: Mapped[Optional[str]] = mapped_column(String(16))
    region: Mapped[Optional[str]] = mapped_column(String(80))
    tagline: Mapped[Optional[str]] = mapped_column(String(255))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    hero_image: Mapped[Optional[str]] = mapped_column(String(600))
    card_image: Mapped[Optional[str]] = mapped_column(String(600))
    accent_color: Mapped[Optional[str]] = mapped_column(String(32), default="#1d4ed8")
    why_study: Mapped[List[str]] = mapped_column(JSONType, default=list)
    popular_subjects: Mapped[List[str]] = mapped_column(JSONType, default=list)
    intakes: Mapped[List[str]] = mapped_column(JSONType, default=list)
    currency: Mapped[Optional[str]] = mapped_column(String(8), default="USD")
    tuition_min: Mapped[Optional[float]] = mapped_column(Float)
    tuition_max: Mapped[Optional[float]] = mapped_column(Float)
    living_cost_min: Mapped[Optional[float]] = mapped_column(Float)
    living_cost_max: Mapped[Optional[float]] = mapped_column(Float)
    visa_summary: Mapped[Optional[str]] = mapped_column(Text)
    visa_resources: Mapped[List[dict]] = mapped_column(JSONType, default=list)
    work_rights: Mapped[Optional[str]] = mapped_column(Text)
    post_study_work: Mapped[Optional[str]] = mapped_column(Text)
    student_life: Mapped[Optional[str]] = mapped_column(Text)
    faqs: Mapped[List[dict]] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")   # §53
    is_featured: Mapped[bool] = mapped_column(default=False)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    application_available: Mapped[bool] = mapped_column(default=True)

    universities: Mapped[List["University"]] = relationship(back_populates="country")


class City(Base, TimestampMixin):
    __tablename__ = "cities"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(140), index=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("countries.id"))


class University(Base, TimestampMixin, SeoMixin, VerificationMixin):
    __tablename__ = "universities"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    slug: Mapped[str] = mapped_column(String(280), unique=True, index=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("countries.id"), index=True)
    city: Mapped[Optional[str]] = mapped_column(String(120))
    campus: Mapped[Optional[str]] = mapped_column(String(255))
    logo_url: Mapped[Optional[str]] = mapped_column(String(600))
    hero_image: Mapped[Optional[str]] = mapped_column(String(600))
    institution_type: Mapped[Optional[str]] = mapped_column(String(40), default="PUBLIC")
    established_year: Mapped[Optional[int]] = mapped_column(Integer)
    website: Mapped[Optional[str]] = mapped_column(String(600))
    international_website: Mapped[Optional[str]] = mapped_column(String(600))
    application_portal: Mapped[Optional[str]] = mapped_column(String(600))
    overview: Mapped[Optional[str]] = mapped_column(Text)
    accreditation: Mapped[Optional[str]] = mapped_column(Text)
    student_count: Mapped[Optional[int]] = mapped_column(Integer)
    international_students: Mapped[Optional[int]] = mapped_column(Integer)
    student_life: Mapped[Optional[str]] = mapped_column(Text)
    accommodation: Mapped[Optional[str]] = mapped_column(Text)
    faqs: Mapped[List[dict]] = mapped_column(JSONType, default=list)
    partner_status: Mapped[str] = mapped_column(String(30), default="NON_PARTNER")  # §54 (internal)
    is_featured: Mapped[bool] = mapped_column(default=False)
    is_published: Mapped[bool] = mapped_column(default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)

    country: Mapped[Country] = relationship(back_populates="universities")
    rankings: Mapped[List["UniversityRanking"]] = relationship(back_populates="university", cascade="all, delete-orphan")
    programs: Mapped[List["Program"]] = relationship(back_populates="university", cascade="all, delete-orphan")
    departments: Mapped[List["Department"]] = relationship(back_populates="university", cascade="all, delete-orphan")
    faculties: Mapped[List["Faculty"]] = relationship(back_populates="university",
                                                      cascade="all, delete-orphan",
                                                      order_by="Faculty.position")
    professors: Mapped[List["Professor"]] = relationship(back_populates="university", cascade="all, delete-orphan")
    partnership: Mapped[Optional["UniversityPartnership"]] = relationship(
        back_populates="university", uselist=False, cascade="all, delete-orphan")


class UniversityRanking(Base, TimestampMixin, VerificationMixin):
    """§16 - never store a bare 'World Rank #200'."""
    __tablename__ = "university_rankings"
    id: Mapped[int] = mapped_column(primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), index=True)
    provider: Mapped[str] = mapped_column(String(120))          # QS / THE / ARWU / US News
    edition_year: Mapped[int] = mapped_column(Integer)
    rank_overall: Mapped[Optional[str]] = mapped_column(String(40))
    rank_national: Mapped[Optional[str]] = mapped_column(String(40))
    rank_regional: Mapped[Optional[str]] = mapped_column(String(40))
    subject: Mapped[Optional[str]] = mapped_column(String(160))
    rank_subject: Mapped[Optional[str]] = mapped_column(String(40))
    university: Mapped[University] = relationship(back_populates="rankings")


class UniversityPartnership(Base, TimestampMixin):
    """§54 - commercial data, never exposed on public endpoints."""
    __tablename__ = "university_partnerships"
    id: Mapped[int] = mapped_column(primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="NON_PARTNER")
    representative: Mapped[Optional[str]] = mapped_column(String(160))
    contact_email: Mapped[Optional[str]] = mapped_column(String(200))
    contact_phone: Mapped[Optional[str]] = mapped_column(String(60))
    region: Mapped[Optional[str]] = mapped_column(String(120))
    commission_notes: Mapped[Optional[str]] = mapped_column(Text)
    agreement_reference: Mapped[Optional[str]] = mapped_column(String(160))
    agreement_expiry: Mapped[Optional[date]] = mapped_column(Date)
    university: Mapped[University] = relationship(back_populates="partnership")


class Faculty(Base, TimestampMixin, SeoMixin, VerificationMixin):
    """
    An academic division inside a university: Faculty of Engineering, Business School,
    College of Arts, Institute of Public Health.

    The specification asks for a four-level hierarchy - University > Faculty/School >
    Department > Programme - and this is the level that used to be missing. It lived as
    `departments.faculty_school`, a free-text String(200), which meant the same division was
    retyped once per department with no dean, no website and no provenance, and no way to
    show a division page or catch two spellings of the same school.

    Not to be confused with `Professor`, which the public API exposes under /faculty/{slug}
    in the academic-staff sense. Divisions are addressed under /faculties/*.
    """
    __tablename__ = "faculties"
    id: Mapped[int] = mapped_column(primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    slug: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    # Faculty / School / College / Institute - universities genuinely use all four, and the
    # word is part of the name, so this is for grouping and filtering rather than display.
    kind: Mapped[str] = mapped_column(String(40), default="FACULTY", index=True)
    dean_name: Mapped[Optional[str]] = mapped_column(String(200))
    dean_title: Mapped[Optional[str]] = mapped_column(String(120))
    website: Mapped[Optional[str]] = mapped_column(String(600))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    overview: Mapped[Optional[str]] = mapped_column(Text)
    research_areas: Mapped[List[str]] = mapped_column(JSONType, default=list)
    position: Mapped[int] = mapped_column(Integer, default=0)
    university: Mapped[University] = relationship(back_populates="faculties")
    departments: Mapped[List["Department"]] = relationship(
        back_populates="faculty", order_by="Department.name")

    __table_args__ = (UniqueConstraint("university_id", "name", name="uq_faculty_university_name"),)


class Department(Base, TimestampMixin):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), index=True)
    faculty_id: Mapped[Optional[int]] = mapped_column(ForeignKey("faculties.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(240), index=True)
    website: Mapped[Optional[str]] = mapped_column(String(600))
    overview: Mapped[Optional[str]] = mapped_column(Text)
    research_areas: Mapped[List[str]] = mapped_column(JSONType, default=list)
    university: Mapped[University] = relationship(back_populates="departments")
    faculty: Mapped[Optional["Faculty"]] = relationship(back_populates="departments")


class Program(Base, TimestampMixin, SeoMixin, VerificationMixin):
    __tablename__ = "programs"
    id: Mapped[int] = mapped_column(primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), index=True)
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    slug: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    degree_level: Mapped[str] = mapped_column(String(20), index=True)
    award: Mapped[Optional[str]] = mapped_column(String(80))            # MSc, BSc, PhD
    discipline: Mapped[Optional[str]] = mapped_column(String(160), index=True)
    subject_tags: Mapped[List[str]] = mapped_column(JSONType, default=list)
    specializations: Mapped[List[str]] = mapped_column(JSONType, default=list)
    overview: Mapped[Optional[str]] = mapped_column(Text)
    duration_months: Mapped[Optional[int]] = mapped_column(Integer)
    credits: Mapped[Optional[int]] = mapped_column(Integer)
    study_mode: Mapped[Optional[str]] = mapped_column(String(60), default="Full-time")
    language: Mapped[Optional[str]] = mapped_column(String(60), default="English")
    campus: Mapped[Optional[str]] = mapped_column(String(160))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    tuition_annual: Mapped[Optional[float]] = mapped_column(Float)
    tuition_total: Mapped[Optional[float]] = mapped_column(Float)
    tuition_per_credit: Mapped[Optional[float]] = mapped_column(Float)
    application_fee: Mapped[Optional[float]] = mapped_column(Float)
    deposit: Mapped[Optional[float]] = mapped_column(Float)
    insurance_annual: Mapped[Optional[float]] = mapped_column(Float)
    other_fees: Mapped[List[dict]] = mapped_column(JSONType, default=list)
    living_cost_annual: Mapped[Optional[float]] = mapped_column(Float)
    scholarship_available: Mapped[bool] = mapped_column(default=False)
    career_outcomes: Mapped[Optional[str]] = mapped_column(Text)
    application_procedure: Mapped[List[str]] = mapped_column(JSONType, default=list)
    research_areas: Mapped[List[str]] = mapped_column(JSONType, default=list)   # PhD programs
    is_featured: Mapped[bool] = mapped_column(default=False)
    is_published: Mapped[bool] = mapped_column(default=True)
    views: Mapped[int] = mapped_column(Integer, default=0)

    university: Mapped[University] = relationship(back_populates="programs")
    department: Mapped[Optional[Department]] = relationship()
    requirement: Mapped[Optional["ProgramRequirement"]] = relationship(
        back_populates="program", uselist=False, cascade="all, delete-orphan")
    intakes: Mapped[List["ProgramIntake"]] = relationship(
        back_populates="program", cascade="all, delete-orphan")


class ProgramRequirement(Base, TimestampMixin, VerificationMixin):
    __tablename__ = "program_requirements"
    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"), unique=True)
    min_qualification: Mapped[Optional[str]] = mapped_column(String(255))
    min_cgpa: Mapped[Optional[float]] = mapped_column(Float)
    cgpa_scale: Mapped[Optional[float]] = mapped_column(Float, default=4.0)
    relevant_majors: Mapped[List[str]] = mapped_column(JSONType, default=list)
    required_subjects: Mapped[List[str]] = mapped_column(JSONType, default=list)
    prerequisites: Mapped[List[str]] = mapped_column(JSONType, default=list)
    degree_equivalency: Mapped[Optional[str]] = mapped_column(Text)
    study_gap_policy: Mapped[Optional[str]] = mapped_column(String(255))
    max_study_gap_years: Mapped[Optional[int]] = mapped_column(Integer)
    work_experience_years: Mapped[Optional[float]] = mapped_column(Float)
    research_experience: Mapped[Optional[str]] = mapped_column(Text)
    ielts_overall: Mapped[Optional[float]] = mapped_column(Float)
    ielts_band_min: Mapped[Optional[float]] = mapped_column(Float)
    toefl_ibt: Mapped[Optional[float]] = mapped_column(Float)
    pte_academic: Mapped[Optional[float]] = mapped_column(Float)
    duolingo: Mapped[Optional[float]] = mapped_column(Float)
    cambridge: Mapped[Optional[str]] = mapped_column(String(60))
    moi_accepted: Mapped[Optional[bool]] = mapped_column(default=None)
    other_english_tests: Mapped[List[str]] = mapped_column(JSONType, default=list)
    gre_required: Mapped[Optional[bool]] = mapped_column(default=None)
    gre_score: Mapped[Optional[str]] = mapped_column(String(60))
    gmat_required: Mapped[Optional[bool]] = mapped_column(default=None)
    gmat_score: Mapped[Optional[str]] = mapped_column(String(60))
    documents: Mapped[List[str]] = mapped_column(JSONType, default=list)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    program: Mapped[Program] = relationship(back_populates="requirement")


class ProgramIntake(Base, TimestampMixin, VerificationMixin):
    """§29 - intakes are stored per program, never assumed university-wide."""
    __tablename__ = "program_intakes"
    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"), index=True)
    label: Mapped[str] = mapped_column(String(80))            # "Fall 2027"
    term: Mapped[Optional[str]] = mapped_column(String(40))   # Fall / Spring / January
    month: Mapped[Optional[int]] = mapped_column(Integer)
    year: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN/CLOSED/UPCOMING/ROLLING
    application_opens: Mapped[Optional[date]] = mapped_column(Date)
    application_deadline: Mapped[Optional[date]] = mapped_column(Date)
    class_start: Mapped[Optional[date]] = mapped_column(Date)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    program: Mapped[Program] = relationship(back_populates="intakes")


class Scholarship(Base, TimestampMixin, SeoMixin, VerificationMixin):
    __tablename__ = "scholarships"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    slug: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    provider: Mapped[Optional[str]] = mapped_column(String(200))
    university_id: Mapped[Optional[int]] = mapped_column(ForeignKey("universities.id"), index=True)
    country_id: Mapped[Optional[int]] = mapped_column(ForeignKey("countries.id"), index=True)
    degree_levels: Mapped[List[str]] = mapped_column(JSONType, default=list)
    disciplines: Mapped[List[str]] = mapped_column(JSONType, default=list)
    amount_type: Mapped[str] = mapped_column(String(30), default="PERCENTAGE")  # PERCENTAGE/FIXED/FULL/VARIABLE
    amount_value: Mapped[Optional[float]] = mapped_column(Float)
    percentage: Mapped[Optional[float]] = mapped_column(Float)
    currency: Mapped[Optional[str]] = mapped_column(String(8), default="USD")
    covers_tuition: Mapped[bool] = mapped_column(default=False)
    covers_living: Mapped[bool] = mapped_column(default=False)
    stipend: Mapped[Optional[str]] = mapped_column(String(160))
    eligibility_summary: Mapped[Optional[str]] = mapped_column(Text)
    eligible_nationalities: Mapped[List[str]] = mapped_column(JSONType, default=list)
    min_cgpa: Mapped[Optional[float]] = mapped_column(Float)
    cgpa_scale: Mapped[Optional[float]] = mapped_column(Float, default=4.0)
    min_ielts: Mapped[Optional[float]] = mapped_column(Float)
    other_criteria: Mapped[List[str]] = mapped_column(JSONType, default=list)
    deadline: Mapped[Optional[date]] = mapped_column(Date)
    application_method: Mapped[Optional[str]] = mapped_column(Text)
    is_featured: Mapped[bool] = mapped_column(default=False)
    is_published: Mapped[bool] = mapped_column(default=True)
    university: Mapped[Optional[University]] = relationship()
    country: Mapped[Optional[Country]] = relationship()


class ResearchArea(Base, TimestampMixin):
    __tablename__ = "research_areas"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    slug: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    parent: Mapped[Optional[str]] = mapped_column(String(160))
    keywords: Mapped[List[str]] = mapped_column(JSONType, default=list)


class Professor(Base, TimestampMixin, VerificationMixin):
    """§24/§25 - faculty intelligence with a strict email-provenance policy."""
    __tablename__ = "professors"
    id: Mapped[int] = mapped_column(primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), index=True)
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(200), index=True)
    slug: Mapped[str] = mapped_column(String(240), unique=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(160))
    photo_url: Mapped[Optional[str]] = mapped_column(String(600))
    email: Mapped[Optional[str]] = mapped_column(String(200))
    email_status: Mapped[str] = mapped_column(String(32), default="UNAVAILABLE")
    email_source_url: Mapped[Optional[str]] = mapped_column(String(600))
    office: Mapped[Optional[str]] = mapped_column(String(200))
    profile_url: Mapped[Optional[str]] = mapped_column(String(600))
    google_scholar: Mapped[Optional[str]] = mapped_column(String(600))
    orcid: Mapped[Optional[str]] = mapped_column(String(120))
    researchgate: Mapped[Optional[str]] = mapped_column(String(600))
    linkedin: Mapped[Optional[str]] = mapped_column(String(600))
    personal_site: Mapped[Optional[str]] = mapped_column(String(600))
    bio: Mapped[Optional[str]] = mapped_column(Text)
    research_interests: Mapped[List[str]] = mapped_column(JSONType, default=list)
    research_keywords: Mapped[List[str]] = mapped_column(JSONType, default=list)
    lab_name: Mapped[Optional[str]] = mapped_column(String(200))
    lab_url: Mapped[Optional[str]] = mapped_column(String(600))
    funding_notes: Mapped[Optional[str]] = mapped_column(Text)
    accepting_phd: Mapped[str] = mapped_column(String(20), default="UNKNOWN")  # YES/NO/UNKNOWN
    supervision_notes: Mapped[Optional[str]] = mapped_column(Text)
    h_index: Mapped[Optional[int]] = mapped_column(Integer)
    university: Mapped[University] = relationship(back_populates="professors")
    department: Mapped[Optional[Department]] = relationship()
    publications: Mapped[List["Publication"]] = relationship(
        back_populates="professor", cascade="all, delete-orphan")


class Publication(Base, TimestampMixin):
    __tablename__ = "professor_publications"
    id: Mapped[int] = mapped_column(primary_key=True)
    professor_id: Mapped[int] = mapped_column(ForeignKey("professors.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    venue: Mapped[Optional[str]] = mapped_column(String(300))
    year: Mapped[Optional[int]] = mapped_column(Integer)
    url: Mapped[Optional[str]] = mapped_column(String(600))
    doi: Mapped[Optional[str]] = mapped_column(String(200))
    keywords: Mapped[List[str]] = mapped_column(JSONType, default=list)
    professor: Mapped[Professor] = relationship(back_populates="publications")
