"""CMS + marketing models - everything the consultancy can change without a developer (§94)."""
from datetime import date, datetime
from typing import Any, List, Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import SeoMixin, TimestampMixin, JSONType


class RuntimeSetting(Base, TimestampMixin):
    """A single admin-editable override for one key in `Settings`.

    Only keys on the `app.core.runtime.OVERRIDABLE` allowlist are honoured, so a row here
    cannot repoint the database or replace the signing key. Secret values are Fernet
    encrypted; a database dump therefore leaks no credentials.
    """
    __tablename__ = "runtime_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    group: Mapped[str] = mapped_column(String(40), default="general", index=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(160))


class SiteSetting(Base, TimestampMixin):
    """Singleton row (id=1) that powers branding, contact, social and marketing tags."""
    __tablename__ = "site_settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    brand_name: Mapped[str] = mapped_column(String(120), default="Scholara")
    brand_short: Mapped[str] = mapped_column(String(60), default="Scholara")
    tagline: Mapped[str] = mapped_column(String(255), default="Global Education Intelligence")
    logo_url: Mapped[Optional[str]] = mapped_column(String(600))
    favicon_url: Mapped[Optional[str]] = mapped_column(String(600))
    primary_color: Mapped[str] = mapped_column(String(20), default="#0f3d8a")
    accent_color: Mapped[str] = mapped_column(String(20), default="#f59e0b")
    about_short: Mapped[Optional[str]] = mapped_column(Text)
    office_address: Mapped[Optional[str]] = mapped_column(Text)
    office_hours: Mapped[Optional[str]] = mapped_column(String(255))
    phones: Mapped[List[str]] = mapped_column(JSONType, default=list)
    emails: Mapped[List[str]] = mapped_column(JSONType, default=list)
    map_embed_url: Mapped[Optional[str]] = mapped_column(String(900))
    # --- Appointment booking options (admin-editable) ---
    # These drive the public booking form. They are settings rather than constants because
    # they are business decisions the office changes without a deploy: which services are
    # offered, which hours are bookable, and whether online/phone sessions are available.
    # Each entry is {"value": <stored code>, "label": <what the student reads>}.
    appointment_service_types: Mapped[List[dict]] = mapped_column(JSONType, default=list)
    appointment_modes: Mapped[List[dict]] = mapped_column(JSONType, default=list)
    appointment_slots: Mapped[List[str]] = mapped_column(JSONType, default=list)
    appointment_lead_days: Mapped[int] = mapped_column(Integer, default=1)
    # --- WhatsApp ---
    whatsapp_number: Mapped[Optional[str]] = mapped_column(String(40))
    whatsapp_default_message: Mapped[Optional[str]] = mapped_column(Text)
    whatsapp_enabled: Mapped[bool] = mapped_column(default=True)
    # --- Facebook / social ---
    facebook_page_url: Mapped[Optional[str]] = mapped_column(String(600))
    facebook_page_id: Mapped[Optional[str]] = mapped_column(String(120))
    facebook_page_plugin_enabled: Mapped[bool] = mapped_column(default=True)
    messenger_url: Mapped[Optional[str]] = mapped_column(String(600))
    messenger_enabled: Mapped[bool] = mapped_column(default=True)
    instagram_url: Mapped[Optional[str]] = mapped_column(String(600))
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(600))
    youtube_url: Mapped[Optional[str]] = mapped_column(String(600))
    tiktok_url: Mapped[Optional[str]] = mapped_column(String(600))
    x_url: Mapped[Optional[str]] = mapped_column(String(600))
    # --- Marketing / ads / analytics ---
    ga4_measurement_id: Mapped[Optional[str]] = mapped_column(String(60))
    gtm_container_id: Mapped[Optional[str]] = mapped_column(String(60))
    google_ads_conversion_id: Mapped[Optional[str]] = mapped_column(String(60))
    google_ads_conversion_label: Mapped[Optional[str]] = mapped_column(String(80))
    google_site_verification: Mapped[Optional[str]] = mapped_column(String(160))
    meta_pixel_id: Mapped[Optional[str]] = mapped_column(String(60))
    meta_domain_verification: Mapped[Optional[str]] = mapped_column(String(160))
    tiktok_pixel_id: Mapped[Optional[str]] = mapped_column(String(60))
    bing_verification: Mapped[Optional[str]] = mapped_column(String(160))
    analytics_consent_required: Mapped[bool] = mapped_column(default=True)
    # --- SEO defaults ---
    default_seo_title: Mapped[Optional[str]] = mapped_column(String(255))
    default_seo_description: Mapped[Optional[str]] = mapped_column(Text)
    default_og_image: Mapped[Optional[str]] = mapped_column(String(600))
    site_url: Mapped[str] = mapped_column(String(300), default="http://localhost:3000")
    organization_type: Mapped[str] = mapped_column(String(80), default="EducationalOrganization")
    founding_year: Mapped[Optional[int]] = mapped_column(Integer)
    # --- Homepage rotation defaults (§6/§96) ---
    hero_rotation_seconds: Mapped[int] = mapped_column(Integer, default=30)
    hero_rotation_enabled: Mapped[bool] = mapped_column(default=True)
    hero_time_scheduling_enabled: Mapped[bool] = mapped_column(default=True)
    story_rotation_seconds: Mapped[int] = mapped_column(Integer, default=12)


class HomepageSlide(Base, TimestampMixin):
    """§7 - the 30-second rotation unit."""
    __tablename__ = "homepage_slides"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    eyebrow: Mapped[Optional[str]] = mapped_column(String(160))
    title: Mapped[str] = mapped_column(String(255))
    subtitle: Mapped[Optional[str]] = mapped_column(String(400))
    description: Mapped[Optional[str]] = mapped_column(Text)
    highlights: Mapped[List[str]] = mapped_column(JSONType, default=list)
    background_media: Mapped[Optional[str]] = mapped_column(String(600))
    mobile_media: Mapped[Optional[str]] = mapped_column(String(600))
    media_type: Mapped[str] = mapped_column(String(20), default="IMAGE")  # IMAGE/VIDEO/GRADIENT
    gradient: Mapped[Optional[str]] = mapped_column(String(300))
    theme_color: Mapped[Optional[str]] = mapped_column(String(32))
    country_id: Mapped[Optional[int]] = mapped_column(ForeignKey("countries.id"))
    focus: Mapped[str] = mapped_column(String(40), default="COUNTRY")  # COUNTRY/PHD/SCHOLARSHIP/STORY/SERVICE/EVENT
    focus_ref: Mapped[Optional[str]] = mapped_column(String(160))
    cta_label: Mapped[Optional[str]] = mapped_column(String(80))
    cta_url: Mapped[Optional[str]] = mapped_column(String(600))
    secondary_cta_label: Mapped[Optional[str]] = mapped_column(String(80))
    secondary_cta_url: Mapped[Optional[str]] = mapped_column(String(600))
    stats: Mapped[List[dict]] = mapped_column(JSONType, default=list)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=30)   # default 30 (§7)
    priority: Mapped[int] = mapped_column(Integer, default=10)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    day_start_hour: Mapped[Optional[int]] = mapped_column(Integer)   # §97 time-of-day scheduling
    day_end_hour: Mapped[Optional[int]] = mapped_column(Integer)
    campaign_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ad_campaigns.id"))
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)


class HomepageSection(Base, TimestampMixin):
    """§8 - each homepage band is toggleable/orderable from the CMS."""
    __tablename__ = "homepage_sections"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True)
    title: Mapped[Optional[str]] = mapped_column(String(200))
    subtitle: Mapped[Optional[str]] = mapped_column(String(400))
    position: Mapped[int] = mapped_column(Integer, default=10)
    is_enabled: Mapped[bool] = mapped_column(default=True)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)


class Service(Base, TimestampMixin, SeoMixin):
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(240), unique=True, index=True)
    icon: Mapped[Optional[str]] = mapped_column(String(60))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    hero_image: Mapped[Optional[str]] = mapped_column(String(600))
    process: Mapped[List[dict]] = mapped_column(JSONType, default=list)
    benefits: Mapped[List[str]] = mapped_column(JSONType, default=list)
    documents: Mapped[List[str]] = mapped_column(JSONType, default=list)
    faqs: Mapped[List[dict]] = mapped_column(JSONType, default=list)
    countries: Mapped[List[str]] = mapped_column(JSONType, default=list)
    category: Mapped[Optional[str]] = mapped_column(String(80))
    is_featured: Mapped[bool] = mapped_column(default=False)
    featured_weekday: Mapped[Optional[int]] = mapped_column(Integer)  # §78 rotating featured service
    priority: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class SuccessStory(Base, TimestampMixin, SeoMixin):
    __tablename__ = "success_stories"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(240), unique=True, index=True)
    headline: Mapped[Optional[str]] = mapped_column(String(255))
    photo_url: Mapped[Optional[str]] = mapped_column(String(600))
    country_id: Mapped[Optional[int]] = mapped_column(ForeignKey("countries.id"), index=True)
    university_id: Mapped[Optional[int]] = mapped_column(ForeignKey("universities.id"), index=True)
    university_name: Mapped[Optional[str]] = mapped_column(String(255))
    program_name: Mapped[Optional[str]] = mapped_column(String(255))
    degree_level: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    intake: Mapped[Optional[str]] = mapped_column(String(60))
    year: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    admission_result: Mapped[Optional[str]] = mapped_column(String(160))
    scholarship: Mapped[Optional[str]] = mapped_column(String(160))
    visa_result: Mapped[Optional[str]] = mapped_column(String(160))
    testimonial: Mapped[Optional[str]] = mapped_column(Text)
    journey: Mapped[List[str]] = mapped_column(JSONType, default=list)
    video_url: Mapped[Optional[str]] = mapped_column(String(600))
    consent_reference: Mapped[Optional[str]] = mapped_column(String(160))
    is_featured: Mapped[bool] = mapped_column(default=False)
    is_published: Mapped[bool] = mapped_column(default=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Event(Base, TimestampMixin, SeoMixin):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    university_id: Mapped[Optional[int]] = mapped_column(ForeignKey("universities.id"))
    country_id: Mapped[Optional[int]] = mapped_column(ForeignKey("countries.id"))
    speaker: Mapped[Optional[str]] = mapped_column(String(200))
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    venue: Mapped[Optional[str]] = mapped_column(String(300))
    mode: Mapped[str] = mapped_column(String(20), default="OFFLINE")  # ONLINE/OFFLINE/HYBRID
    capacity: Mapped[Optional[int]] = mapped_column(Integer)
    registration_url: Mapped[Optional[str]] = mapped_column(String(600))
    banner_url: Mapped[Optional[str]] = mapped_column(String(600))
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="UPCOMING")
    is_featured: Mapped[bool] = mapped_column(default=False)


class EventRegistration(Base, TimestampMixin):
    __tablename__ = "event_registrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(40))
    email: Mapped[Optional[str]] = mapped_column(String(200))
    preferred_country: Mapped[Optional[str]] = mapped_column(String(120))
    degree_level: Mapped[Optional[str]] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="REGISTERED")
    #: Tri-state, same meaning as ``Lead.consent_contact``: an event registration is a contact
    #: request (we message the registrant), so it records the explicit answer it was given.
    consent_contact: Mapped[Optional[bool]] = mapped_column(default=None)


class BlogPost(Base, TimestampMixin, SeoMixin):
    __tablename__ = "blog_posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    excerpt: Mapped[Optional[str]] = mapped_column(Text)
    body: Mapped[Optional[str]] = mapped_column(Text)
    cover_image: Mapped[Optional[str]] = mapped_column(String(600))
    author: Mapped[Optional[str]] = mapped_column(String(160))
    category: Mapped[Optional[str]] = mapped_column(String(80))
    tags: Mapped[List[str]] = mapped_column(JSONType, default=list)
    reading_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    is_published: Mapped[bool] = mapped_column(default=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Faq(Base, TimestampMixin):
    __tablename__ = "faqs"
    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(String(400))
    answer: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(80), default="General")
    scope: Mapped[str] = mapped_column(String(40), default="GLOBAL")
    position: Mapped[int] = mapped_column(Integer, default=100)
    is_published: Mapped[bool] = mapped_column(default=True)


class AdCampaign(Base, TimestampMixin):
    """Facebook/Google/WhatsApp campaign manager (§ads requirement)."""
    __tablename__ = "ad_campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(240), unique=True, index=True)
    channel: Mapped[str] = mapped_column(String(40), default="FACEBOOK")  # FACEBOOK/INSTAGRAM/GOOGLE/WHATSAPP/EMAIL/ORGANIC
    objective: Mapped[str] = mapped_column(String(60), default="LEADS")
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")  # DRAFT/SCHEDULED/RUNNING/PAUSED/COMPLETED
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    daily_budget: Mapped[Optional[float]] = mapped_column(Float)
    total_budget: Mapped[Optional[float]] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="BDT")
    audience: Mapped[Optional[str]] = mapped_column(Text)
    headline: Mapped[Optional[str]] = mapped_column(String(255))
    primary_text: Mapped[Optional[str]] = mapped_column(Text)
    call_to_action: Mapped[Optional[str]] = mapped_column(String(80), default="Learn more")
    creative_url: Mapped[Optional[str]] = mapped_column(String(600))
    landing_path: Mapped[str] = mapped_column(String(300), default="/")
    utm_source: Mapped[str] = mapped_column(String(80), default="facebook")
    utm_medium: Mapped[str] = mapped_column(String(80), default="paid_social")
    utm_campaign: Mapped[str] = mapped_column(String(120), default="campaign")
    utm_content: Mapped[Optional[str]] = mapped_column(String(120))
    pixel_event: Mapped[str] = mapped_column(String(60), default="Lead")
    google_ads_campaign_id: Mapped[Optional[str]] = mapped_column(String(80))
    meta_campaign_id: Mapped[Optional[str]] = mapped_column(String(80))
    whatsapp_prefill: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    spend_to_date: Mapped[float] = mapped_column(Float, default=0.0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
