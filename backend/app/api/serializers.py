"""Explicit serializers: public payloads never leak internal/commercial fields (§54)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional


def _dt(v) -> Optional[str]:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def provenance(o) -> Dict[str, Any]:
    return {
        "source_url": getattr(o, "source_url", None),
        "source_type": getattr(o, "source_type", None),
        "verified_at": _dt(getattr(o, "verified_at", None)),
        "verified_by": getattr(o, "verified_by", None),
        "review_due_at": _dt(getattr(o, "review_due_at", None)),
        "confidence": getattr(o, "confidence", "UNVERIFIED"),
    }


def _faculty_ref(dept) -> Optional[Dict[str, Any]]:
    """Compact parent-division reference for nesting inside a department payload."""
    f = getattr(dept, "faculty", None)
    if not f:
        return None
    return {"id": f.id, "name": f.name, "slug": f.slug, "kind": f.kind}


def faculty(f, with_departments: bool = True) -> Dict[str, Any]:
    """An academic division. `kind` is FACULTY/SCHOOL/COLLEGE/INSTITUTE for grouping only -
    the display name already contains the word the university itself uses."""
    out: Dict[str, Any] = {
        "id": f.id, "name": f.name, "slug": f.slug, "kind": f.kind,
        "dean_name": f.dean_name, "dean_title": f.dean_title,
        "website": f.website, "email": f.email, "overview": f.overview,
        "research_areas": f.research_areas or [],
        "position": f.position,
        "provenance": provenance(f),
    }
    if with_departments:
        out["departments"] = [
            {"id": d.id, "name": d.name, "slug": d.slug, "website": d.website,
             "overview": d.overview, "research_areas": d.research_areas or []}
            for d in sorted(f.departments, key=lambda d: d.name)
        ]
        out["department_count"] = len(out["departments"])
    return out


def site_settings(s) -> Dict[str, Any]:
    return {
        "brand_name": s.brand_name, "brand_short": s.brand_short, "tagline": s.tagline,
        "logo_url": s.logo_url, "primary_color": s.primary_color, "accent_color": s.accent_color,
        "about_short": s.about_short, "office_address": s.office_address, "office_hours": s.office_hours,
        "phones": s.phones, "emails": s.emails, "map_embed_url": s.map_embed_url,
        "whatsapp": {"enabled": s.whatsapp_enabled, "number": s.whatsapp_number,
                     "default_message": s.whatsapp_default_message},
        "facebook": {"page_url": s.facebook_page_url, "page_id": s.facebook_page_id,
                     "page_plugin_enabled": s.facebook_page_plugin_enabled,
                     "messenger_url": s.messenger_url, "messenger_enabled": s.messenger_enabled},
        # Booking options the admin controls. The public form renders exactly what is here,
        # so adding a service or dropping a time slot is a CMS edit, not a deploy.
        "appointments": {
            "service_types": s.appointment_service_types or [],
            "modes": s.appointment_modes or [],
            "slots": s.appointment_slots or [],
            "lead_days": s.appointment_lead_days or 0,
        },
        "social": {"instagram": s.instagram_url, "linkedin": s.linkedin_url, "youtube": s.youtube_url,
                   "tiktok": s.tiktok_url, "x": s.x_url},
        "marketing": {"ga4_measurement_id": s.ga4_measurement_id, "gtm_container_id": s.gtm_container_id,
                      "google_ads_conversion_id": s.google_ads_conversion_id,
                      "google_ads_conversion_label": s.google_ads_conversion_label,
                      "meta_pixel_id": s.meta_pixel_id, "tiktok_pixel_id": s.tiktok_pixel_id,
                      "consent_required": s.analytics_consent_required},
        "seo": {"google_site_verification": s.google_site_verification,
                "meta_domain_verification": s.meta_domain_verification,
                "bing_verification": s.bing_verification,
                "default_title": s.default_seo_title, "default_description": s.default_seo_description,
                "default_og_image": s.default_og_image, "site_url": s.site_url,
                "organization_type": s.organization_type, "founding_year": s.founding_year},
        "rotation": {"hero_seconds": s.hero_rotation_seconds, "hero_enabled": s.hero_rotation_enabled,
                     "time_scheduling": s.hero_time_scheduling_enabled, "story_seconds": s.story_rotation_seconds},
    }


def slide(s) -> Dict[str, Any]:
    return {
        "id": s.id, "key": s.key, "eyebrow": s.eyebrow, "title": s.title, "subtitle": s.subtitle,
        "description": s.description, "highlights": s.highlights, "background_media": s.background_media,
        "mobile_media": s.mobile_media, "media_type": s.media_type, "gradient": s.gradient,
        "theme_color": s.theme_color, "country_id": s.country_id, "focus": s.focus, "focus_ref": s.focus_ref,
        "cta_label": s.cta_label, "cta_url": s.cta_url, "secondary_cta_label": s.secondary_cta_label,
        "secondary_cta_url": s.secondary_cta_url, "stats": s.stats,
        "duration_seconds": s.duration_seconds, "priority": s.priority, "status": s.status,
        "start_at": _dt(s.start_at), "end_at": _dt(s.end_at),
        "day_start_hour": s.day_start_hour, "day_end_hour": s.day_end_hour, "campaign_id": s.campaign_id,
    }


def section(s) -> Dict[str, Any]:
    return {"key": s.key, "title": s.title, "subtitle": s.subtitle, "position": s.position,
            "is_enabled": s.is_enabled, "config": s.config}


def country(c, counts: Optional[Dict[str, int]] = None, full: bool = False) -> Dict[str, Any]:
    data = {
        "id": c.id, "name": c.name, "slug": c.slug, "iso2": c.iso2, "flag_emoji": c.flag_emoji,
        "region": c.region, "tagline": c.tagline, "summary": c.summary, "hero_image": c.hero_image,
        "card_image": c.card_image, "accent_color": c.accent_color, "status": c.status,
        "is_featured": c.is_featured, "priority": c.priority, "currency": c.currency,
        "tuition_min": c.tuition_min, "tuition_max": c.tuition_max,
        "living_cost_min": c.living_cost_min, "living_cost_max": c.living_cost_max,
        "popular_subjects": c.popular_subjects, "intakes": c.intakes,
        "application_available": c.application_available,
        "counts": counts or {},
    }
    if full:
        data.update({
            "why_study": c.why_study, "visa_summary": c.visa_summary, "visa_resources": c.visa_resources,
            "work_rights": c.work_rights, "post_study_work": c.post_study_work, "student_life": c.student_life,
            "faqs": c.faqs,
            "seo": {"title": c.seo_title, "description": c.seo_description, "keywords": c.seo_keywords,
                    "og_image": c.og_image},
        })
    return data


def ranking(r) -> Dict[str, Any]:
    return {"provider": r.provider, "edition_year": r.edition_year, "rank_overall": r.rank_overall,
            "rank_national": r.rank_national, "rank_regional": r.rank_regional, "subject": r.subject,
            "rank_subject": r.rank_subject, "label": f"{r.provider} {r.edition_year}",
            "provenance": provenance(r)}


def university(u, full: bool = False, program_count: Optional[int] = None) -> Dict[str, Any]:
    data = {
        "id": u.id, "name": u.name, "slug": u.slug, "city": u.city, "campus": u.campus,
        "logo_url": u.logo_url, "hero_image": u.hero_image, "institution_type": u.institution_type,
        "established_year": u.established_year, "website": u.website,
        "country": {"id": u.country.id, "name": u.country.name, "slug": u.country.slug,
                    "flag_emoji": u.country.flag_emoji} if u.country else None,
        "is_featured": u.is_featured,
        "rankings": [ranking(r) for r in sorted(u.rankings, key=lambda x: -x.edition_year)][:4],
        "program_count": program_count,
        "provenance": provenance(u),
    }
    if full:
        data.update({
            "overview": u.overview, "accreditation": u.accreditation, "student_count": u.student_count,
            "international_students": u.international_students, "student_life": u.student_life,
            "accommodation": u.accommodation, "faqs": u.faqs,
            "international_website": u.international_website, "application_portal": u.application_portal,
            "seo": {"title": u.seo_title, "description": u.seo_description, "og_image": u.og_image},
        })
    return data


def requirement(r) -> Optional[Dict[str, Any]]:
    if not r:
        return None
    return {
        "academic": {"min_qualification": r.min_qualification, "min_cgpa": r.min_cgpa, "cgpa_scale": r.cgpa_scale,
                     "relevant_majors": r.relevant_majors, "required_subjects": r.required_subjects,
                     "prerequisites": r.prerequisites, "degree_equivalency": r.degree_equivalency,
                     "study_gap_policy": r.study_gap_policy, "max_study_gap_years": r.max_study_gap_years,
                     "work_experience_years": r.work_experience_years,
                     "research_experience": r.research_experience},
        "english": {"ielts_overall": r.ielts_overall, "ielts_band_min": r.ielts_band_min, "toefl_ibt": r.toefl_ibt,
                    "pte_academic": r.pte_academic, "duolingo": r.duolingo, "cambridge": r.cambridge,
                    "moi_accepted": r.moi_accepted, "other_accepted_tests": r.other_english_tests},
        "tests": {"gre_required": r.gre_required, "gre_score": r.gre_score,
                  "gmat_required": r.gmat_required, "gmat_score": r.gmat_score},
        "documents": r.documents, "notes": r.notes, "provenance": provenance(r),
    }


def intake(i) -> Dict[str, Any]:
    return {"id": i.id, "label": i.label, "term": i.term, "month": i.month, "year": i.year, "status": i.status,
            "application_opens": _dt(i.application_opens), "application_deadline": _dt(i.application_deadline),
            "class_start": _dt(i.class_start), "notes": i.notes, "provenance": provenance(i)}


def program(p, full: bool = False) -> Dict[str, Any]:
    data = {
        "id": p.id, "name": p.name, "slug": p.slug, "degree_level": p.degree_level, "award": p.award,
        "discipline": p.discipline, "subject_tags": p.subject_tags, "duration_months": p.duration_months,
        "credits": p.credits, "study_mode": p.study_mode, "language": p.language, "campus": p.campus,
        "currency": p.currency, "tuition_annual": p.tuition_annual, "tuition_total": p.tuition_total,
        "application_fee": p.application_fee, "deposit": p.deposit, "insurance_annual": p.insurance_annual,
        "living_cost_annual": p.living_cost_annual, "other_fees": p.other_fees,
        "scholarship_available": p.scholarship_available, "is_featured": p.is_featured,
        "university": {"id": p.university.id, "name": p.university.name, "slug": p.university.slug,
                       "logo_url": p.university.logo_url, "city": p.university.city,
                       "country": {"id": p.university.country.id, "name": p.university.country.name,
                                   "slug": p.university.country.slug,
                                   "flag_emoji": p.university.country.flag_emoji} if p.university.country else None},
        "intakes": [intake(i) for i in sorted(p.intakes, key=lambda x: (x.year or 9999, x.month or 99))],
        "requirement_summary": {
            "min_cgpa": p.requirement.min_cgpa if p.requirement else None,
            "ielts_overall": p.requirement.ielts_overall if p.requirement else None,
        },
        "provenance": provenance(p),
    }
    if full:
        data.update({
            "overview": p.overview, "specializations": p.specializations,
            "career_outcomes": p.career_outcomes, "application_procedure": p.application_procedure,
            "research_areas": p.research_areas,
            "department": {"name": p.department.name,
                           "faculty": _faculty_ref(p.department),
                           "website": p.department.website} if p.department else None,
            "requirements": requirement(p.requirement),
            "seo": {"title": p.seo_title, "description": p.seo_description, "og_image": p.og_image},
        })
    return data


def scholarship(s) -> Dict[str, Any]:
    return {
        "id": s.id, "name": s.name, "slug": s.slug, "provider": s.provider,
        "degree_levels": s.degree_levels, "disciplines": s.disciplines, "amount_type": s.amount_type,
        "amount_value": s.amount_value, "percentage": s.percentage, "currency": s.currency,
        "covers_tuition": s.covers_tuition, "covers_living": s.covers_living, "stipend": s.stipend,
        "eligibility_summary": s.eligibility_summary, "eligible_nationalities": s.eligible_nationalities,
        "min_cgpa": s.min_cgpa, "cgpa_scale": s.cgpa_scale, "min_ielts": s.min_ielts,
        "other_criteria": s.other_criteria, "deadline": _dt(s.deadline),
        "application_method": s.application_method, "is_featured": s.is_featured,
        "university": {"id": s.university.id, "name": s.university.name, "slug": s.university.slug} if s.university else None,
        "country": {"id": s.country.id, "name": s.country.name, "slug": s.country.slug,
                    "flag_emoji": s.country.flag_emoji} if s.country else None,
        "provenance": provenance(s),
    }


def professor(p, full: bool = False, matched: Optional[List[str]] = None,
              evidence: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    email_visible = p.email_status in ("OFFICIAL_UNIVERSITY_EMAIL", "PUBLIC_PROFESSIONAL_EMAIL")
    data = {
        "id": p.id, "slug": p.slug, "full_name": p.full_name, "title": p.title, "photo_url": p.photo_url,
        "university": {"id": p.university.id, "name": p.university.name, "slug": p.university.slug,
                       "country": {"name": p.university.country.name, "slug": p.university.country.slug,
                                   "flag_emoji": p.university.country.flag_emoji} if p.university.country else None},
        "department": {"name": p.department.name,
                       "faculty": _faculty_ref(p.department)} if p.department else None,
        "research_interests": p.research_interests, "research_keywords": p.research_keywords,
        "lab_name": p.lab_name, "lab_url": p.lab_url, "accepting_phd": p.accepting_phd,
        "email": p.email if email_visible else None,
        "email_status": p.email_status, "email_source_url": p.email_source_url,
        "matched_interests": matched or [], "evidence": evidence or [],
        "provenance": provenance(p),
    }
    if full:
        data.update({
            "bio": p.bio, "office": p.office, "profile_url": p.profile_url,
            "google_scholar": p.google_scholar, "orcid": p.orcid, "researchgate": p.researchgate,
            "linkedin": p.linkedin, "personal_site": p.personal_site, "funding_notes": p.funding_notes,
            "supervision_notes": p.supervision_notes, "h_index": p.h_index,
            "publications": [{"title": x.title, "venue": x.venue, "year": x.year, "url": x.url, "doi": x.doi}
                             for x in sorted(p.publications, key=lambda y: -(y.year or 0))],
        })
    return data


def service(s, full: bool = False) -> Dict[str, Any]:
    data = {"id": s.id, "title": s.title, "slug": s.slug, "icon": s.icon, "summary": s.summary,
            "category": s.category, "is_featured": s.is_featured, "priority": s.priority,
            "hero_image": s.hero_image, "status": s.status}
    if full:
        data.update({"description": s.description, "process": s.process, "benefits": s.benefits,
                     "documents": s.documents, "faqs": s.faqs, "countries": s.countries,
                     "seo": {"title": s.seo_title, "description": s.seo_description}})
    return data


def success_story(s, full: bool = False) -> Dict[str, Any]:
    data = {"id": s.id, "student_name": s.student_name, "slug": s.slug, "headline": s.headline,
            "photo_url": s.photo_url, "university_name": s.university_name, "program_name": s.program_name,
            "degree_level": s.degree_level, "intake": s.intake, "year": s.year,
            "scholarship": s.scholarship, "visa_result": s.visa_result, "is_featured": s.is_featured,
            "country": {"id": s.country_id} if s.country_id else None,
            "testimonial": (s.testimonial or "")[:260] if not full else s.testimonial,
            "published_at": _dt(s.published_at)}
    if full:
        data.update({"journey": s.journey, "video_url": s.video_url, "admission_result": s.admission_result,
                     "seo": {"title": s.seo_title, "description": s.seo_description}})
    return data


def event(e, full: bool = False) -> Dict[str, Any]:
    data = {"id": e.id, "title": e.title, "slug": e.slug, "speaker": e.speaker, "start_at": _dt(e.start_at),
            "end_at": _dt(e.end_at), "venue": e.venue, "mode": e.mode, "capacity": e.capacity,
            "banner_url": e.banner_url, "status": e.status, "is_featured": e.is_featured,
            "country_id": e.country_id, "university_id": e.university_id,
            "registration_url": e.registration_url}
    if full:
        data["description"] = e.description
    return data


def blog_post(b, full: bool = False) -> Dict[str, Any]:
    data = {"id": b.id, "title": b.title, "slug": b.slug, "excerpt": b.excerpt, "cover_image": b.cover_image,
            "author": b.author, "category": b.category, "tags": b.tags,
            "reading_minutes": b.reading_minutes, "published_at": _dt(b.published_at)}
    if full:
        data.update({"body": b.body, "seo": {"title": b.seo_title, "description": b.seo_description}})
    return data


def faq(f) -> Dict[str, Any]:
    return {"id": f.id, "question": f.question, "answer": f.answer, "category": f.category, "scope": f.scope}


def campaign(c, internal: bool = False) -> Dict[str, Any]:
    data = {"id": c.id, "name": c.name, "slug": c.slug, "channel": c.channel, "objective": c.objective,
            "status": c.status, "start_at": _dt(c.start_at), "end_at": _dt(c.end_at),
            "headline": c.headline, "primary_text": c.primary_text, "call_to_action": c.call_to_action,
            "creative_url": c.creative_url, "landing_path": c.landing_path,
            "utm": {"source": c.utm_source, "medium": c.utm_medium, "campaign": c.utm_campaign,
                    "content": c.utm_content},
            "pixel_event": c.pixel_event, "whatsapp_prefill": c.whatsapp_prefill}
    if internal:
        data.update({"daily_budget": c.daily_budget, "total_budget": c.total_budget, "currency": c.currency,
                     "audience": c.audience, "spend_to_date": c.spend_to_date, "clicks": c.clicks,
                     "meta_campaign_id": c.meta_campaign_id,
                     "google_ads_campaign_id": c.google_ads_campaign_id, "notes": c.notes})
    return data


def lead(record) -> Dict[str, Any]:
    return {"id": record.id, "reference": record.reference, "full_name": record.full_name,
            "phone": record.phone, "email": record.email,
            "message": record.message, "interest_country": record.interest_country,
            "interest_degree": record.interest_degree,
            "interest_subject": record.interest_subject, "intake": record.intake, "source": record.source,
            "campaign_id": record.campaign_id, "utm": record.utm, "stage": record.stage,
            "priority": record.priority,
            "assigned_to": record.assigned_to, "notes": record.notes, "created_at": _dt(record.created_at)}


def appointment(a) -> Dict[str, Any]:
    return {"id": a.id, "reference": a.reference, "full_name": a.full_name, "phone": a.phone, "email": a.email,
            "service_type": a.service_type, "counselor_name": a.counselor_name,
            "scheduled_date": _dt(a.scheduled_date), "scheduled_time": a.scheduled_time, "mode": a.mode,
            "status": a.status, "notes": a.notes, "created_at": _dt(a.created_at)}
