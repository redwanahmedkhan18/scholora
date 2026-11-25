"""Loader that writes the illustrative dataset into the database.

Run:  python -m app.seed
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from slugify import slugify
from sqlalchemy import select

from app import seed_data as D
from app.core import mfa
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.security import hash_password
from app.models.content import (
    AdCampaign,
    BlogPost,
    Event,
    Faq,
    HomepageSection,
    HomepageSlide,
    Service,
    SiteSetting,
    SuccessStory,
)
from app.models.crm import Appointment, DataVerification, Lead, StudentProfile, User
from app.models.education import (
    Country,
    Department,
    Faculty,
    Professor,
    Program,
    ProgramIntake,
    ProgramRequirement,
    Publication,
    ResearchArea,
    Scholarship,
    University,
    UniversityPartnership,
    UniversityRanking,
)

NOW = datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc).replace(tzinfo=None)
REVIEW_DUE = NOW + timedelta(days=90)
SAMPLE = "Sample dataset - replace with a verified source extract"

DOC_SETS = {
    "BACHELOR": ["Passport copy", "SSC certificate and transcript", "HSC certificate and transcript",
                 "English test score or MOI", "Statement of purpose", "Recommendation letter", "Financial documents"],
    "MASTER": ["Passport copy", "Bachelor certificate and transcript", "English test score", "Updated CV",
               "Statement of purpose", "Two recommendation letters", "Experience certificate (if any)",
               "Financial documents"],
    "PHD": ["Passport copy", "Bachelor and Master transcripts", "English test score", "Academic CV",
            "Research proposal", "Three recommendation letters", "Writing sample / publications",
            "Supervisor correspondence (recommended)"],
}

INTAKE_PLAN = {
    "Fall 2027": (9, 2027, date(2026, 9, 1), date(2027, 3, 15), date(2027, 8, 20)),
    "Spring 2027": (1, 2027, date(2026, 6, 1), date(2026, 10, 15), date(2027, 1, 12)),
    "January 2027": (1, 2027, date(2026, 6, 15), date(2026, 11, 1), date(2027, 1, 20)),
    "September 2027": (9, 2027, date(2026, 10, 1), date(2027, 6, 30), date(2027, 9, 15)),
    "May 2027": (5, 2027, date(2026, 9, 1), date(2027, 2, 28), date(2027, 5, 10)),
}

INTAKE_BY_COUNTRY = {
    "United States": ["Fall 2027", "Spring 2027"],
    "United Kingdom": ["September 2027", "January 2027"],
    "Canada": ["September 2027", "January 2027", "May 2027"],
    "Australia": ["Fall 2027", "January 2027"],
    "Ireland": ["September 2027", "January 2027"],
    "Germany": ["September 2027", "May 2027"],
    "Malaysia": ["January 2027", "May 2027", "September 2027"],
    "Hungary": ["September 2027"],
    "Cyprus": ["September 2027", "January 2027"],
    "United Arab Emirates": ["September 2027", "January 2027"],
    "South Korea": ["September 2027", "Spring 2027"],
    "New Zealand": ["Fall 2027", "January 2027"],
}

DEPT_FOR_DISCIPLINE = {
    "Computer Science": "Computer Science", "Artificial Intelligence": "Computer Science",
    "Data Science": "Computer Science", "Cyber Security": "Computer Science",
    "Information Technology": "Information Technology", "Business Administration": "Business School",
    "Business Analytics": "Business School", "Electrical Engineering": "Electrical and Computer Engineering",
    "Mechanical Engineering": "Mechanical Engineering", "Public Health": "School of Health Sciences",
    "Nursing": "School of Nursing", "Pharmacy": "School of Pharmacy", "Medicine": "School of Medicine",
}

CURRENCY_BY_COUNTRY = {"United Kingdom": "GBP", "Canada": "CAD", "Australia": "AUD", "New Zealand": "NZD",
                       "Germany": "EUR", "Ireland": "EUR", "Hungary": "EUR", "Cyprus": "EUR"}


def wipe(db):
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()


def _faculty_kind(name: str) -> str:
    """Faculty / School / College / Institute - taken from the name the university uses."""
    lowered = name.lower()
    for code in ("SCHOOL", "COLLEGE", "INSTITUTE", "ACADEMY"):
        if code.lower() in lowered:
            return code
    return "FACULTY"


def _faculty_name(dept_name: str) -> str:
    """Name the parent faculty without producing "Faculty of School of Nursing".

    Department names in the dataset are a mix of bare disciplines ("Computer Science") and
    names that already carry their own organisational word ("School of Nursing", "Business
    School"). Prefixing every one with "Faculty of" produced visibly wrong text on the
    university page, so names that are already self-describing are used as-is.
    """
    name = dept_name.strip()
    lowered = name.lower()
    if any(word in lowered for word in ("faculty", "school", "college", "institute", "academy")):
        return name
    return f"Faculty of {name}"


def run(reset: bool = True) -> dict:
    random.seed(42)
    # Production schema is owned by Alembic. Tests and zero-config SQLite development may
    # still create their throwaway schema, but seeding must never be an implicit migration.
    if not settings.is_production:
        Base.metadata.create_all(engine)
    db = SessionLocal()
    if reset:
        wipe(db)

    # ------------------------------------------------------------------ settings
    db.add(SiteSetting(
        id=1, brand_name="Scholara", brand_short="Scholara",
        tagline="Global Education Intelligence & Application Platform",
        primary_color="#0f3d8a", accent_color="#f59e0b",
        about_short=("Scholara is an Uttara-based international education consultancy that pairs licensed counselors "
                     "with a verified university, programme, scholarship and faculty database, so Bangladeshi students "
                     "can research a study option properly before they ever speak to anyone."),
        office_address="House 12, Road 7, Sector 4, Uttara, Dhaka 1230, Bangladesh",
        office_hours="Saturday - Thursday, 10:00 - 19:00 (Friday closed)",
        phones=["+880 1700-000000", "+880 2-58900000"],
        emails=["info@scholara.com.bd", "admissions@scholara.com.bd"],
        map_embed_url="https://www.google.com/maps?q=Uttara+Sector+4+Dhaka&output=embed",
        appointment_service_types=D.APPOINTMENT_SERVICE_TYPES,
        appointment_modes=D.APPOINTMENT_MODES,
        appointment_slots=D.APPOINTMENT_SLOTS,
        appointment_lead_days=D.APPOINTMENT_LEAD_DAYS,
        whatsapp_number="+8801700000000", whatsapp_enabled=True,
        whatsapp_default_message="Hello Scholara, I would like to discuss studying abroad.",
        facebook_page_url="https://www.facebook.com/scholara.bd", facebook_page_id="scholara.bd",
        messenger_url="https://m.me/scholara.bd", facebook_page_plugin_enabled=True, messenger_enabled=True,
        instagram_url="https://www.instagram.com/scholara.bd",
        linkedin_url="https://www.linkedin.com/company/scholara",
        youtube_url="https://www.youtube.com/@scholara",
        ga4_measurement_id="G-XXXXXXXXXX", gtm_container_id="", google_ads_conversion_id="AW-XXXXXXXXX",
        google_ads_conversion_label="abcDEFghiJKL", google_site_verification="",
        meta_pixel_id="000000000000000", analytics_consent_required=True,
        default_seo_title="Scholara | Study Abroad Consultancy in Uttara, Dhaka",
        default_seo_description=("Search verified universities, programmes, scholarships, intakes and PhD supervisors. "
                                 "Check your eligibility, estimate your real annual cost and generate a personalised "
                                 "brochure - then talk to a counselor in Uttara or on WhatsApp."),
        site_url="http://localhost:3000", organization_type="EducationalOrganization", founding_year=2016,
        hero_rotation_seconds=30, hero_rotation_enabled=True, hero_time_scheduling_enabled=False,
        story_rotation_seconds=12))
    db.commit()

    # ------------------------------------------------------------------ users
    # ADMIN and SENIOR_COUNSELOR are in MFA_REQUIRED_ROLES, so they are seeded already
    # enrolled with fixed, well-known development secrets. Without this the demo logins
    # would land on the enrolment wall and nobody could get into the back office.
    # These constants are dev-only; real deployments enrol through /admin/auth/mfa/enroll.
    admin_recovery_plain, admin_recovery_hashed = mfa.issue_recovery_codes()
    db.add_all([
        User(email=settings.admin_email.lower(), full_name="Platform Administrator",
             password_hash=hash_password(settings.admin_password), role="ADMIN",
             phone="+8801700000001",
             mfa_secret=D.DEV_MFA_SECRET_ADMIN, mfa_confirmed_at=datetime.now(timezone.utc),
             mfa_recovery_codes=admin_recovery_hashed),
        User(email="senior@scholara.com.bd", full_name="Imrul Kayes",
             password_hash=hash_password("Senior@12345"), role="SENIOR_COUNSELOR", phone="+8801700000003",
             mfa_secret=D.DEV_MFA_SECRET_SENIOR, mfa_confirmed_at=datetime.now(timezone.utc),
             mfa_recovery_codes=mfa.issue_recovery_codes()[1]),
        User(email="counselor@scholara.com.bd", full_name="Farzana Haque",
             password_hash=hash_password("Counselor@123"), role="COUNSELOR", phone="+8801700000002"),
        User(email="data@scholara.com.bd", full_name="Rumana Sultana",
             password_hash=hash_password("Data@12345"), role="DATA_MANAGER", phone="+8801700000004"),
    ])
    db.commit()

    # ------------------------------------------------------------------ countries
    countries = {}
    for i, (name, iso2, flag, region, status, featured, tuition_range, living_range, currency, tagline) in enumerate(D.COUNTRIES):
        c = Country(
            name=name, slug=slugify(name), iso2=iso2, flag_emoji=flag, region=region, status=status,
            is_featured=featured, priority=(i + 1) * 10, tagline=tagline, currency=currency,
            tuition_min=tuition_range[0], tuition_max=tuition_range[1],
            living_cost_min=living_range[0], living_cost_max=living_range[1],
            why_study=D.WHY.get(name, []), popular_subjects=D.SUBJECTS.get(name, []),
            intakes=D.INTAKE_MONTHS.get(name, []), accent_color="#1d4ed8",
            summary=(f"{name} hosts a wide range of institutions that accept Bangladeshi students. This page lists the "
                     f"universities, programmes, scholarships, intakes and cost ranges currently recorded in our "
                     f"database for {name}, with the source and verification date on every record."),
            visa_summary=(f"A student visa for {name} generally requires an offer letter, proof of funds, academic and "
                          f"English documents and a biometric appointment. Requirements are confirmed against the "
                          f"official immigration source at the time of application."),
            visa_resources=[{"label": f"Official {name} student visa information",
                             "url": f"https://www.google.com/search?q={slugify(name)}+official+student+visa"}],
            work_rights="Part-time work rights during study vary by destination and visa subclass.",
            post_study_work="Post-study work options are recorded per destination and confirmed before application.",
            student_life=f"Bangladeshi student communities are active in most major {name} university cities.",
            faqs=[{"q": f"What is the average annual cost of studying in {name}?",
                   "a": (f"Tuition typically ranges from {currency} {tuition_range[0]:,} to {currency} {tuition_range[1]:,} per year, "
                         f"with living costs of roughly {currency} {living_range[0]:,}-{living_range[1]:,}. Every programme page "
                         f"shows a precise estimate for that programme.")},
                  {"q": f"Which intakes are available in {name}?",
                   "a": "Intakes are stored per programme, not per university. See each programme page for its own intake calendar."}],
            seo_title=f"Study in {name} from Bangladesh | Universities, Costs, Scholarships & Intakes",
            seo_description=(f"Verified {name} university list, tuition and living cost ranges, scholarships, intakes and "
                             f"visa guidance for Bangladeshi students. Check your eligibility and talk to a counselor in Uttara."))
        db.add(c)
        countries[name] = c
    db.commit()

    # ------------------------------------------------------------------ universities
    unis = {}
    for name, cname, city, itype, founded, website, partner, featured, rankings in D.UNIVERSITIES:
        u = University(
            name=name, slug=slugify(name), country_id=countries[cname].id, city=city, campus=f"{city} campus",
            institution_type=itype, established_year=founded, website=website,
            international_website=f"{website}/international", application_portal=f"{website}/apply",
            overview=(f"{name} is located in {city}, {cname}. This institution record is maintained by the Scholara "
                      f"data team and links to the official university website for every student-facing figure."),
            accreditation=f"Recognised by the competent authority in {cname}.",
            student_count=random.choice([8000, 12000, 18000, 24000, 32000, 41000]),
            international_students=random.choice([900, 1800, 3200, 5400, 8100]),
            student_life="Student unions, Bangladeshi student associations and career services operate on campus.",
            accommodation="On-campus halls plus verified off-campus options; applications usually open with the offer letter.",
            faqs=[{"q": "Does the university accept Bangladeshi HSC / Bachelor qualifications?",
                   "a": "Yes, subject to the equivalency and minimum CGPA stated on each programme page."},
                  {"q": "Is MOI accepted instead of IELTS?",
                   "a": "This varies by programme. The English requirement block on each programme page records the accepted tests."}],
            partner_status=partner, is_featured=featured, priority=10 if featured else 50,
            source_url=website, source_type="TIER_1_OFFICIAL_UNIVERSITY", retrieved_at=NOW, verified_at=NOW,
            verified_by=SAMPLE, review_due_at=REVIEW_DUE, confidence="NEEDS_REVIEW",
            seo_title=f"{name} - Programmes, Requirements, Tuition & Scholarships",
            seo_description=(f"{name} in {city}, {cname}: verified programme list, entry requirements, English scores, "
                             f"tuition, scholarships and intake calendar with sources."))
        db.add(u)
        unis[name] = u
        db.flush()
        for provider, year, overall, national in rankings:
            db.add(UniversityRanking(university_id=u.id, provider=provider, edition_year=year,
                                     rank_overall=overall, rank_national=national, source_url=website,
                                     source_type="TIER_5_OFFICIAL_RANKING_PROVIDER", verified_at=NOW,
                                     verified_by=SAMPLE, confidence="NEEDS_REVIEW"))
        db.add(UniversityPartnership(
            university_id=u.id, status=partner, representative="Regional manager (South Asia)",
            contact_email="partners@" + website.replace("https://", "").replace("www.", ""),
            region="South Asia", commission_notes="Internal commercial note - never exposed on public endpoints.",
            agreement_reference=f"AGR-{u.id:04d}", agreement_expiry=date(2027, 12, 31)))
    db.commit()

    # ------------------------------------------------------------------ departments + programmes
    for uname, plist in D.PROGRAMS.items():
        u = unis[uname]
        cname = u.country.name
        for pname, level, award, discipline, months, tuition, cgpa, ielts, tags, specs in plist:
            dept_name = DEPT_FOR_DISCIPLINE.get(discipline, discipline)
            # University > Faculty/School > Department > Programme. The division is a real
            # row, created once per university and shared by every department under it.
            fac_name = _faculty_name(dept_name)
            faculty = db.execute(select(Faculty).where(Faculty.university_id == u.id,
                                                       Faculty.name == fac_name)).scalars().first()
            if not faculty:
                faculty = Faculty(
                    university_id=u.id, name=fac_name, slug=slugify(f"{u.name}-{fac_name}"),
                    kind=_faculty_kind(fac_name),
                    dean_name=None, dean_title="Dean",
                    website=f"{u.website}/faculties", email=None,
                    overview=f"{fac_name} at {u.name}.",
                    research_areas=[], position=len(u.faculties) * 10,
                    source_url=u.website, source_type="OFFICIAL_UNIVERSITY_WEBSITE",
                    confidence="UNVERIFIED",
                )
                db.add(faculty)
                db.flush()

            dept = db.execute(select(Department).where(Department.university_id == u.id,
                                                       Department.name == dept_name)).scalars().first()
            if not dept:
                dept = Department(university_id=u.id, name=dept_name, slug=slugify(f"{u.name}-{dept_name}"),
                                  faculty_id=faculty.id, website=f"{u.website}/departments",
                                  overview=f"{dept_name} at {u.name}.",
                                  research_areas=(["artificial intelligence", "data science", "systems"]
                                                  if "Computer" in dept_name else []))
                db.add(dept)
                db.flush()
            currency = CURRENCY_BY_COUNTRY.get(cname, "USD")
            living_mid = int(((countries[cname].living_cost_min or 0)
                              + (countries[cname].living_cost_max or 0)) / 2)
            p = Program(
                university_id=u.id, department_id=dept.id, name=pname, slug=slugify(f"{u.name}-{pname}"),
                degree_level=level, award=award, discipline=discipline, subject_tags=tags, specializations=specs,
                overview=(f"{pname} at {u.name} ({u.city}). Entry requirements, English requirements, fees, intakes and "
                          f"funding are stored at programme level, because they differ between programmes inside the "
                          f"same university."),
                duration_months=months, credits={"BACHELOR": 120, "MASTER": 36, "PHD": 72}.get(level),
                study_mode="Full-time", language="English", campus=u.city, currency=currency,
                tuition_annual=float(tuition), tuition_total=float(tuition) * max(months / 12, 1),
                application_fee=float(random.choice([0, 50, 75, 100])),
                deposit=float(random.choice([0, 1000, 2000])),
                insurance_annual=float(random.choice([400, 900, 1500])),
                other_fees=[{"label": "Student services fee", "amount": 350}],
                living_cost_annual=float(living_mid), scholarship_available=True,
                career_outcomes=("Graduates move into industry roles or further research. Career services and alumni "
                                 "networks are listed on the official programme page."),
                application_procedure=["Create an account on the university application portal",
                                       "Upload academic documents and the English test score",
                                       "Pay the application fee where applicable",
                                       "Submit and track the application status",
                                       "Accept the offer and pay the tuition deposit",
                                       "Receive the visa support document (I-20 / CAS / CoE / LoA)"],
                research_areas=(tags if level == "PHD" else []),
                is_featured=(level in ("MASTER", "PHD") and random.random() < 0.45),
                source_url=f"{u.website}/courses", source_type="TIER_1_OFFICIAL_UNIVERSITY", retrieved_at=NOW,
                verified_at=NOW, verified_by=SAMPLE, review_due_at=REVIEW_DUE, confidence="NEEDS_REVIEW",
                seo_title=f"{pname} at {u.name} | Requirements, Fees, Intakes & Scholarships",
                seo_description=(f"{pname} ({award}) at {u.name}, {u.city}: minimum CGPA {cgpa}, IELTS {ielts}, tuition "
                                 f"{currency} {tuition:,}/year, intakes and scholarships - every figure with a source."))
            db.add(p)
            db.flush()
            db.add(ProgramRequirement(
                program_id=p.id,
                min_qualification={"BACHELOR": "HSC / A-Level or equivalent",
                                   "MASTER": "Four-year bachelor degree (or equivalent)",
                                   "PHD": "Master degree in a relevant discipline"}[level],
                min_cgpa=cgpa, cgpa_scale=4.0,
                relevant_majors=([discipline] if level != "BACHELOR" else []),
                required_subjects=(["Mathematics"] if ("Engineering" in discipline or "Computer" in discipline) else []),
                prerequisites=(["Linear Algebra", "Programming"]
                               if discipline in ("Computer Science", "Artificial Intelligence", "Data Science")
                               and level != "BACHELOR" else []),
                degree_equivalency="Bangladeshi qualifications are assessed against the destination framework.",
                study_gap_policy="Study gaps are considered with supporting documentation.", max_study_gap_years=5,
                work_experience_years=(2 if award == "MBA" else None),
                research_experience=("Documented research output strengthens the application." if level == "PHD" else None),
                ielts_overall=ielts, ielts_band_min=(ielts - 0.5 if ielts >= 6.5 else 5.5),
                toefl_ibt={6.0: 72, 6.5: 79, 7.0: 94}.get(ielts, 79),
                pte_academic={6.0: 50, 6.5: 58, 7.0: 65}.get(ielts, 58),
                duolingo={6.0: 100, 6.5: 110, 7.0: 120}.get(ielts, 110),
                moi_accepted=(cname in ("Malaysia", "Cyprus", "Hungary", "United Arab Emirates")),
                other_english_tests=["Cambridge C1 Advanced"],
                gre_required=(level == "PHD" and cname == "United States"),
                gre_score=("310+ (programme dependent)" if level == "PHD" and cname == "United States" else None),
                gmat_required=(award == "MBA" and cname == "United States"),
                gmat_score=("550+" if award == "MBA" and cname == "United States" else None),
                documents=DOC_SETS[level],
                notes="Requirements are programme-specific and must be confirmed from the official source before applying.",
                source_url=f"{u.website}/entry-requirements", source_type="TIER_1_OFFICIAL_UNIVERSITY",
                retrieved_at=NOW, verified_at=NOW, verified_by=SAMPLE, review_due_at=REVIEW_DUE,
                confidence="NEEDS_REVIEW"))
            for label in INTAKE_BY_COUNTRY.get(cname, ["September 2027"]):
                m, y, opens, deadline, start = INTAKE_PLAN[label]
                db.add(ProgramIntake(program_id=p.id, label=label, term=label.split()[0], month=m, year=y,
                                     status="OPEN", application_opens=opens, application_deadline=deadline,
                                     class_start=start,
                                     notes="Programme-specific intake - confirm before applying.",
                                     source_url=f"{u.website}/key-dates", source_type="TIER_1_OFFICIAL_UNIVERSITY",
                                     verified_at=NOW, verified_by=SAMPLE, confidence="NEEDS_REVIEW"))
    db.commit()

    # ------------------------------------------------------------------ scholarships
    for name, uni_name, cname, levels, amount_type, amount, pct, currency, min_cgpa, min_ielts, summary in D.SCHOLARSHIPS:
        sch_uni = unis.get(uni_name) if uni_name else None
        db.add(Scholarship(
            name=name, slug=slugify(f"{name}-{cname}"), provider=(uni_name or f"Government of {cname}"),
            university_id=(sch_uni.id if sch_uni else None), country_id=countries[cname].id, degree_levels=levels,
            amount_type=amount_type, amount_value=amount, percentage=pct, currency=currency,
            covers_tuition=(amount_type in ("FULL", "PERCENTAGE")), covers_living=(amount_type == "FULL"),
            stipend=("Monthly stipend included" if amount_type == "FULL" else None),
            eligibility_summary=summary, eligible_nationalities=["International", "Bangladesh"],
            min_cgpa=min_cgpa, cgpa_scale=4.0, min_ielts=min_ielts,
            other_criteria=["Must hold an offer letter",
                            "Applies to the first year of study unless stated otherwise"],
            deadline=date(2027, random.choice([1, 2, 3, 4, 5]), random.choice([10, 15, 28])),
            application_method="Apply through the official scholarship page after receiving an offer.",
            is_featured=(amount_type == "FULL" or (pct is not None and pct >= 25)),
            source_url=((sch_uni.website or "") + "/scholarships" if sch_uni and sch_uni.website
                        else "https://stipendiumhungaricum.hu"),
            source_type=("TIER_1_OFFICIAL_UNIVERSITY" if sch_uni else "TIER_4_OFFICIAL_SCHOLARSHIP_PROVIDER"),
            retrieved_at=NOW, verified_at=NOW, verified_by=SAMPLE, review_due_at=REVIEW_DUE,
            confidence="NEEDS_REVIEW",
            seo_title=f"{name} - eligibility, amount and deadline",
            seo_description=f"{name}: published eligibility criteria, award value and deadline for Bangladeshi students."))
    db.commit()

    # ------------------------------------------------------------------ research areas + faculty
    for area, parent, kws in D.RESEARCH_AREAS:
        db.add(ResearchArea(name=area, slug=slugify(area), parent=parent, keywords=kws))
    db.commit()

    for fname, uni_name, dept_kw, title, interests, keywords, lab, accepting, email_status, h in D.FACULTY:
        u = unis[uni_name]
        dept = db.execute(select(Department).where(Department.university_id == u.id,
                                                   Department.name.ilike(f"%{dept_kw}%"))).scalars().first()
        domain = (u.website or "").replace("https://", "").replace("www.", "")
        email = None
        if email_status in ("OFFICIAL_UNIVERSITY_EMAIL", "PUBLIC_PROFESSIONAL_EMAIL"):
            email = slugify(fname.replace("Dr. ", "")).replace("-", ".") + "@" + domain
        prof = Professor(
            university_id=u.id, department_id=(dept.id if dept else None), full_name=fname,
            slug=slugify(f"{fname}-{u.name}"), title=title, email=email, email_status=email_status,
            email_source_url=(f"{u.website}/staff" if email else None), office=f"{u.city} campus",
            profile_url=f"{u.website}/staff/{slugify(fname)}", google_scholar="https://scholar.google.com/",
            orcid="0000-0000-0000-0000",
            bio=(f"{fname} is a {title.lower()} at {u.name} working on {', '.join(interests[:3])}. This is a SAMPLE "
                 f"record that demonstrates the faculty intelligence module; the data team replaces it with a verified "
                 f"extract from the official department page before launch."),
            research_interests=interests, research_keywords=keywords, lab_name=lab,
            lab_url=f"{u.website}/research", accepting_phd=accepting,
            funding_notes="Funding availability varies by year - confirm with the department.",
            supervision_notes="Prospective students should send a CV, transcripts and a short research statement.",
            h_index=h, source_url=f"{u.website}/staff", source_type="TIER_2_OFFICIAL_DEPARTMENT",
            retrieved_at=NOW, verified_at=NOW, verified_by=SAMPLE, review_due_at=REVIEW_DUE,
            confidence="NEEDS_REVIEW")
        db.add(prof)
        db.flush()
        for ptitle, venue, year, kws in D.PUBLICATIONS.get(fname, []):
            db.add(Publication(professor_id=prof.id, title=ptitle, venue=venue, year=year,
                               url="https://doi.org/10.0000/sample-record", keywords=kws))
    db.commit()

    # ------------------------------------------------------------------ services
    for i, (title, icon, summary, category) in enumerate(D.SERVICES):
        db.add(Service(
            title=title, slug=slugify(title), icon=icon, summary=summary, category=category,
            description=(f"{summary} Every step is logged against your student record, so you and your counselor always "
                         f"see the same status, deadlines and outstanding documents."),
            process=[{"step": 1, "title": "Profile review", "detail": "We record your academics, tests, budget and goal."},
                     {"step": 2, "title": "Options report", "detail": "You receive a shortlist with eligibility and cost evidence."},
                     {"step": 3, "title": "Execution", "detail": "Applications, documents and deadlines are tracked to completion."},
                     {"step": 4, "title": "Follow-through", "detail": "Offer, deposit, visa and pre-departure support."}],
            benefits=["Source-backed information", "Programme-level accuracy", "Deadline tracking",
                      "WhatsApp access to your counselor"],
            documents=["Academic transcripts", "Passport copy", "English test score (if available)"],
            faqs=[{"q": "Is this service chargeable?",
                   "a": "Service charges are explained in writing before any work begins."}],
            countries=[c[0] for c in D.COUNTRIES[:6]], is_featured=(i < 6), featured_weekday=(i % 7),
            priority=(i + 1) * 10, status="ACTIVE",
            seo_title=f"{title} | Scholara, Uttara Dhaka", seo_description=summary))
    db.commit()

    # ------------------------------------------------------------------ success stories
    for i, (sname, cname, uni_name, prog, level, intake, year, schol, visa, quote) in enumerate(D.STORIES):
        story_uni = unis.get(uni_name)
        db.add(SuccessStory(
            student_name=sname, slug=slugify(f"{sname}-{uni_name}-{year}"),
            headline=f"How {sname.split()[0]} started {prog} in {cname}",
            country_id=countries[cname].id, university_id=(story_uni.id if story_uni else None), university_name=uni_name,
            program_name=prog, degree_level=level, intake=intake, year=year, admission_result="Offer received",
            scholarship=schol, visa_result=visa, testimonial=quote,
            journey=["Profile assessment", "University shortlist", "Application", "Offer", "Visa", "Departure"],
            consent_reference=f"CONSENT-{2000 + i}", is_featured=(i < 6), is_published=True,
            published_at=NOW - timedelta(days=i * 11),
            seo_title=f"{sname} - {prog} at {uni_name} ({cname}) | Success Story",
            seo_description=(f"{sname} from Bangladesh secured {prog} at {uni_name}, {cname} for {intake}. "
                             f"Read the full application journey.")))
    db.commit()

    # ------------------------------------------------------------------ events
    for title, cname, uni_name, days, mode, venue in D.EVENTS:
        start = NOW + timedelta(days=days, hours=5)
        db.add(Event(title=title, slug=slugify(title),
                     country_id=(countries[cname].id if cname else None),
                     university_id=(unis[uni_name].id if uni_name else None),
                     speaker="Scholara counseling team" + (f" with a {uni_name} delegate" if uni_name else ""),
                     start_at=start, end_at=start + timedelta(hours=3), venue=venue, mode=mode,
                     capacity=random.choice([40, 60, 100, 150]),
                     description=(f"{title}. Bring your academic documents and passport copy for an on-the-spot profile "
                                  f"assessment. Registration is free and seats are limited."),
                     status="UPCOMING", is_featured=(days < 15),
                     seo_title=f"{title} | Scholara Events", seo_description=f"Register for {title} at {venue}."))
    db.commit()

    # ------------------------------------------------------------------ blog
    for i, (title, cat, excerpt, mins) in enumerate(D.POSTS):
        db.add(BlogPost(
            title=title, slug=slugify(title), excerpt=excerpt, category=cat,
            body=(f"## {title}\n\n{excerpt}\n\n"
                  "### Why this matters\n\nRequirements, fees and intake dates change between programmes inside the same "
                  "university, and they change over time. Scholara stores every value at programme level with a source "
                  "link and a verification date, so a counselor and a student always look at the same evidence.\n\n"
                  "### What to do next\n\n1. Build your student profile.\n2. Run the eligibility check against the "
                  "programmes you care about.\n3. Generate the personalised brochure and bring it to your counseling "
                  "session.\n\n> Nothing on this page is an admission or visa guarantee. Always confirm requirements "
                  "from the official university source before paying any fee."),
            author="Scholara Editorial", tags=[cat.lower(), "study abroad", "bangladesh"], reading_minutes=mins,
            is_published=True, published_at=NOW - timedelta(days=i * 6 + 2),
            seo_title=f"{title} | Scholara Blog", seo_description=excerpt))
    db.commit()

    # ------------------------------------------------------------------ FAQs
    for i, (q, a, cat) in enumerate(D.FAQS):
        db.add(Faq(question=q, answer=a, category=cat, scope="GLOBAL", position=(i + 1) * 10))
    db.commit()

    # ------------------------------------------------------------------ ad campaigns
    for (name, channel, objective, status, landing, src, med, camp, headline, text, pixel, budget) in D.CAMPAIGNS:
        db.add(AdCampaign(name=name, slug=slugify(name), channel=channel, objective=objective, status=status,
                          start_at=NOW - timedelta(days=10), end_at=NOW + timedelta(days=50),
                          daily_budget=round(budget / 30, 2), total_budget=budget, currency="BDT",
                          audience="Bangladesh, 18-32, interests: study abroad, IELTS, scholarships",
                          headline=headline, primary_text=text, call_to_action="Learn more", landing_path=landing,
                          utm_source=src, utm_medium=med, utm_campaign=camp, utm_content="primary",
                          pixel_event=pixel,
                          whatsapp_prefill=f"Hello, I saw your {name} campaign and would like more information.",
                          spend_to_date=round(budget * random.uniform(0.2, 0.7), 2),
                          clicks=random.randint(120, 1400)))
    db.commit()

    # ------------------------------------------------------------------ homepage slides and sections
    for key, title, subtitle, desc, cname, highlights, focus, priority in D.SLIDES:
        slide_country = countries.get(cname) if cname else None
        db.add(HomepageSlide(
            key=key,
            eyebrow=("Destination focus" if focus == "COUNTRY" else
                     {"PHD": "Research pathway", "SCHOLARSHIP": "Funding", "STORY": "Proof"}.get(focus, "Scholara")),
            title=title, subtitle=subtitle, description=desc, highlights=highlights, media_type="GRADIENT",
            gradient=D.GRADIENTS.get(cname, "linear-gradient(135deg,#0b1736 0%,#13306e 50%,#f59e0b 100%)"),
            theme_color="#f59e0b", country_id=(slide_country.id if slide_country else None), focus=focus,
            cta_label=(f"Explore {cname}" if cname else {"PHD": "Explore PhD & research",
                                                         "SCHOLARSHIP": "Browse scholarships",
                                                         "STORY": "Read success stories"}.get(focus, "Explore")),
            cta_url=(f"/study-destinations/{slugify(cname)}" if cname else
                     {"PHD": "/phd", "SCHOLARSHIP": "/scholarships", "STORY": "/success-stories"}.get(focus, "/")),
            secondary_cta_label="Check my eligibility", secondary_cta_url="/profile-assessment",
            stats=([{"label": "Verified records", "value": "Source-linked"},
                    {"label": "Updated", "value": "03 Oct 2026"}]),
            duration_seconds=30, priority=priority, status="ACTIVE"))
    for key, title, subtitle, pos, cfg in D.SECTIONS:
        db.add(HomepageSection(key=key, title=title, subtitle=subtitle, position=pos, is_enabled=True, config=cfg))
    db.commit()

    # ------------------------------------------------------------------ sample CRM rows
    for i, (n, ph, em, cn, dl, sub, stage, src) in enumerate(D.LEADS):
        db.add(Lead(reference=f"LEAD-2026-{i + 1:06d}", full_name=n, phone=ph, email=em, interest_country=cn,
                    interest_degree=dl, interest_subject=sub, stage=stage, source=src,
                    utm=({"utm_source": "facebook", "utm_medium": "paid_social", "utm_campaign": "usa_fall_2027"}
                         if src == "FACEBOOK" else {}),
                    message="Looking for programme and scholarship options.",
                    created_at=NOW - timedelta(days=i * 2)))
    db.add(Appointment(reference="APPT-2026-000001", full_name="Tanjina Rahman", phone="+8801711111111",
                       email="tanjina@example.com", service_type="UNIVERSITY_SELECTION",
                       counselor_name="Farzana Haque", scheduled_date=(NOW + timedelta(days=2)).date(),
                       scheduled_time="11:30", mode="OFFLINE", status="CONFIRMED"))
    db.add(StudentProfile(public_token="demo-student", full_name="Tanjina Rahman", email="tanjina@example.com",
                          phone="+8801711111111", nationality="Bangladesh", highest_qualification="Bachelor",
                          major="Computer Science and Engineering", institution="BRAC University", cgpa=3.6,
                          cgpa_scale=4.0, graduation_year=2025, english_test="IELTS", ielts_overall=7.0,
                          ielts_lowest_band=6.5, work_experience_years=2,
                          completed_subjects=["Linear Algebra", "Programming", "Machine Learning"],
                          research_interests=["nlp", "healthcare ai"], budget_per_year=30000, budget_currency="USD",
                          preferred_countries=["United States", "Canada"], target_degree_level="MASTER",
                          target_discipline="Artificial Intelligence", target_intake="Fall 2027",
                          career_goal="AI research engineer"))
    db.commit()

    # ------------------------------------------------------------------ verification queue
    uta = unis["University of Texas at Arlington"]
    sample_program = db.execute(select(Program).where(Program.university_id == uta.id,
                                                      Program.degree_level == "MASTER")).scalars().first()
    if sample_program is None:  # the demo dataset always seeds one; fail loudly, never deref None
        raise RuntimeError("seed: expected a seeded MASTER programme for University of Texas at Arlington")
    uta_site = uta.website or ""
    db.add_all([
        DataVerification(entity_type="program_requirement", entity_id=sample_program.id, field_name="ielts_overall",
                         field_value="6.5", source_url=f"{uta_site}/admissions",
                         source_type="TIER_1_OFFICIAL_UNIVERSITY", retrieved_at=NOW, verified_at=NOW,
                         verified_by="Rumana Sultana", review_due_at=REVIEW_DUE, status="VERIFIED"),
        DataVerification(entity_type="program_requirement", entity_id=sample_program.id, field_name="ielts_overall",
                         field_value="6.0", source_url="https://third-party-aggregator.example.com/uta",
                         source_type="TIER_7_THIRD_PARTY", retrieved_at=NOW, status="CONFLICTING",
                         conflict_with=("Official university source states 6.5; a third-party aggregator states 6.0. "
                                        "The official source takes precedence until re-verified.")),
        DataVerification(entity_type="university", entity_id=uta.id, field_name="tuition_annual", field_value="28000",
                         source_url=f"{uta_site}/tuition", source_type="TIER_1_OFFICIAL_UNIVERSITY",
                         retrieved_at=NOW - timedelta(days=400), verified_at=NOW - timedelta(days=400),
                         verified_by="Rumana Sultana", review_due_at=NOW - timedelta(days=35), status="OUTDATED"),
    ])
    db.commit()

    counts = {
        "countries": len(D.COUNTRIES),
        "universities": len(D.UNIVERSITIES),
        "departments": len(db.execute(select(Department)).scalars().all()),
        "programs": len(db.execute(select(Program)).scalars().all()),
        "intakes": len(db.execute(select(ProgramIntake)).scalars().all()),
        "scholarships": len(D.SCHOLARSHIPS),
        "faculty": len(D.FACULTY),
        "services": len(D.SERVICES),
        "success_stories": len(D.STORIES),
        "events": len(D.EVENTS),
        "posts": len(D.POSTS),
        "faqs": len(D.FAQS),
        "slides": len(D.SLIDES),
        "sections": len(D.SECTIONS),
        "campaigns": len(D.CAMPAIGNS),
        "leads": len(D.LEADS),
    }
    db.close()
    return counts


if __name__ == "__main__":
    print("Seeding the Scholara database ...")
    for key, value in run().items():
        print(f"  {key:18} {value}")
    print("Done.")
