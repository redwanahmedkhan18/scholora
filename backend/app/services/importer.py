"""
Staged data import with field-level provenance and conflict detection.

The platform's integrity rules say every important field carries a source, and that
*conflicting sources are surfaced, not silently overwritten*. A bulk loader that wrote
straight into `universities` would violate both, so this module stages instead:

    upload  ->  parse  ->  match existing  ->  diff each field  ->  classify  ->  human review  ->  commit

Classification per row:

* ``CREATE``    - no existing record with that natural key.
* ``UPDATE``    - exists, and the incoming values differ from unverified/empty current ones.
* ``CONFLICT``  - exists, the current value is **VERIFIED** and carries its own source, and
                  the incoming value disagrees. Never auto-applied. A reviewer picks a side,
                  and the losing value is preserved in `DataVerification.conflict_with`.
* ``UNCHANGED`` - nothing to do.

Committing writes a `DataVerification` row per changed field, so every value on the site can
be traced to the URL it came from and the person who accepted it.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.base import CONFIDENCE_STATES, SOURCE_TIERS
from app.models.crm import DataVerification, ImportBatch, ImportRow
from app.models.education import (
    Country,
    Professor,
    Program,
    ProgramIntake,
    ProgramRequirement,
    Scholarship,
    University,
    UniversityRanking,
    destination_code_error,
)

# How a faculty address came to be known. "UNAVAILABLE" is a first-class answer and the
# correct one whenever a university does not publish the address - far better than a
# guess that looks plausible.
EMAIL_STATUSES = {"OFFICIAL_UNIVERSITY_EMAIL", "PUBLIC_PROFESSIONAL_EMAIL", "UNAVAILABLE"}

# --------------------------------------------------------------------------- schemas



SCHEMAS: Dict[str, Dict[str, Any]] = {
    "country": {
        "model": Country,
        "label": "Countries",
        "key": "slug",
        "required": ["name", "slug"],
        "fields": [
            "name", "slug", "iso2", "iso_code", "kind", "parent_country",
            "is_study_destination", "flag_emoji", "region", "tagline", "summary",
            "why_study", "currency", "tuition_min", "tuition_max",
            "living_cost_min", "living_cost_max", "visa_summary", "work_rights",
            "post_study_work", "student_life", "status", "is_featured", "priority",
            "application_available", "popular_subjects", "intakes",
            "source_url", "source_type", "confidence",
        ],
        "numeric": ["tuition_min", "tuition_max", "living_cost_min", "living_cost_max", "priority"],
        "boolean": ["is_featured", "application_available", "is_study_destination"],
        "lists": ["popular_subjects", "intakes"],
        # A REGION row names its sovereign country here; a COUNTRY row leaves it blank. This is
        # how Scotland can be imported as `kind=REGION, iso_code=GB-SCT, parent_country=united-kingdom`.
        "resolve": {"parent_country": ("parent_country_id", Country)},
    },
    "university": {
        "model": University,
        "label": "Universities",
        "key": "slug",
        "required": ["name", "slug", "country"],
        "fields": [
            "name", "slug", "country", "region", "city", "campus", "logo_url", "institution_type",
            "established_year", "website", "international_website", "application_portal",
            "overview", "accreditation", "student_count", "international_students",
            "partner_status", "is_featured", "is_published", "priority",
            "source_url", "source_type", "confidence",
        ],
        "numeric": ["established_year", "student_count", "international_students", "priority"],
        "boolean": ["is_featured", "is_published"],
        # country_id is the ownership link (GB for a Scottish institution); region_id is the
        # optional subdivision inside it (GB-SCT). The model rejects a region of another country.
        "resolve": {"country": ("country_id", Country), "region": ("region_id", Country)},
    },
    "program": {
        "model": Program,
        "label": "Programmes",
        "key": "slug",
        "required": ["name", "slug", "university", "degree_level"],
        "fields": [
            "name", "slug", "university", "degree_level", "award", "discipline",
            "overview", "duration_months", "credits", "study_mode", "language",
            "currency", "tuition_annual", "tuition_total", "application_fee",
            "living_cost_annual", "scholarship_available", "career_outcomes",
            "subject_tags", "specializations",
            "is_published", "source_url", "source_type", "confidence",
        ],
        "numeric": ["duration_months", "credits", "tuition_annual", "tuition_total",
                    "application_fee", "living_cost_annual"],
        "boolean": ["scholarship_available", "is_published"],
        "lists": ["subject_tags", "specializations"],
        "resolve": {"university": ("university_id", University)},
    },
    "scholarship": {
        "model": Scholarship,
        "label": "Scholarships",
        "key": "slug",
        "required": ["name", "slug"],
        "fields": [
            "name", "slug", "provider", "country", "degree_levels", "amount_type",
            "amount_value", "percentage", "currency", "covers_tuition", "covers_living",
            "stipend", "eligibility_summary", "eligible_nationalities", "disciplines",
            "min_cgpa", "cgpa_scale", "min_ielts",
            "deadline", "application_method", "is_published",
            "source_url", "source_type", "confidence",
        ],
        # 'stipend' is VARCHAR - it holds phrases like "JPY 117,000 per month".
        "numeric": ["amount_value", "percentage", "min_cgpa", "min_ielts"],
        "boolean": ["covers_tuition", "covers_living", "is_published"],
        "lists": ["degree_levels", "disciplines", "eligible_nationalities"],
        "resolve": {"country": ("country_id", Country)},
    },
    "professor": {
        "model": Professor,
        "label": "Faculty",
        "key": "slug",
        "required": ["full_name", "slug", "university"],
        "fields": [
            "full_name", "slug", "university", "title",
            "profile_url", "photo_url", "email", "email_status", "email_source_url",
            "office", "google_scholar", "orcid", "researchgate", "linkedin",
            "personal_site", "bio", "research_interests", "research_keywords",
            "lab_name", "lab_url", "funding_notes", "accepting_phd",
            "supervision_notes", "h_index",
            "source_url", "source_type", "confidence",
        ],
        "numeric": ["h_index"],
        "lists": ["research_interests", "research_keywords"],
        "resolve": {"university": ("university_id", University)},
    },
    "program_requirement": {
        "child_of": True,
        "discriminators": [],
        "model": ProgramRequirement,
        "label": "Programme requirements",
        "key": "program",
        "required": ["program"],
        "fields": [
            "program", "min_qualification", "min_cgpa", "cgpa_scale", "degree_equivalency",
            "study_gap_policy", "max_study_gap_years", "work_experience_years",
            "ielts_overall", "ielts_band_min", "toefl_ibt", "pte_academic", "duolingo",
            "moi_accepted", "gre_required", "gre_score", "gmat_required", "gmat_score",
            "relevant_majors", "required_subjects", "prerequisites", "other_english_tests",
            "documents", "notes", "source_url", "source_type", "confidence",
        ],
        # gre_score / gmat_score are VARCHAR - they carry section breakdowns like "320 (Q160)".
        "numeric": ["min_cgpa", "max_study_gap_years", "work_experience_years",
                    "ielts_overall", "ielts_band_min", "toefl_ibt", "pte_academic",
                    "duolingo"],
        "boolean": ["moi_accepted", "gre_required", "gmat_required"],
        "lists": ["relevant_majors", "required_subjects", "prerequisites",
                  "other_english_tests", "documents"],
        "resolve": {"program": ("program_id", Program)},
    },
    "program_intake": {
        "child_of": True,
        "discriminators": ["label"],
        "model": ProgramIntake,
        "label": "Programme intakes",
        "key": "program",
        "required": ["program", "label"],
        "fields": [
            "program", "label", "term", "month", "year", "status",
            "application_opens", "application_deadline", "class_start", "notes",
            "source_url", "source_type", "confidence",
        ],
        "numeric": ["month", "year"],
        "boolean": [],
        "resolve": {"program": ("program_id", Program)},
    },
    "university_ranking": {
        "child_of": True,
        "discriminators": ["provider", "edition_year"],
        "model": UniversityRanking,
        "label": "University rankings",
        "key": "university",
        "required": ["university", "provider", "edition_year"],
        "fields": [
            "university", "provider", "edition_year", "rank_overall", "rank_national",
            "rank_regional", "subject", "rank_subject",
            "source_url", "source_type", "confidence",
        ],
        # Only edition_year is a real number. Every rank_* column is VARCHAR on purpose:
        # published tables are full of "601-800", "=42" and "1001+".
        "numeric": ["edition_year"],
        "boolean": [],
        "resolve": {"university": ("university_id", University)},
    },
}

# Provenance columns exist on University/Program/Scholarship but not on Country. They are
# accepted on every entity regardless: when the model has no such column the value still
# travels into the DataVerification record, which is where provenance really lives.
PROVENANCE_FIELDS = {"source_url", "source_type", "confidence"}


def entity_types() -> List[Dict[str, Any]]:
    return [
        {
            "key": key,
            "label": spec["label"],
            "natural_key": spec["key"],
            "required": spec["required"],
            "fields": spec["fields"],
        }
        for key, spec in SCHEMAS.items()
    ]


def template_csv(entity_type: str) -> str:
    """A header row plus one commented example, so an operator can start from a real file."""
    spec = _spec(entity_type)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(spec["fields"])
    example = {
        "name": "Example University", "slug": "example-university", "country": "united-kingdom",
        "source_url": "https://www.example.ac.uk/about", "source_type": "TIER_1_OFFICIAL_UNIVERSITY",
        "confidence": "VERIFIED",
    }
    writer.writerow([example.get(f, "") for f in spec["fields"]])
    return buf.getvalue()


# --------------------------------------------------------------------------- parsing
def _spec(entity_type: str) -> Dict[str, Any]:
    spec = SCHEMAS.get((entity_type or "").strip().lower())
    if not spec:
        raise ValueError(f"unknown entity type '{entity_type}'; expected one of {', '.join(SCHEMAS)}")
    return spec


def parse_payload(raw: str, filename: str = "") -> List[Dict[str, Any]]:
    """Accept CSV or JSON (a list, or {"items": [...]}) and return plain dicts."""
    text = (raw or "").strip()
    if not text:
        return []
    looks_json = text[0] in "[{" or filename.lower().endswith(".json")
    if looks_json:
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("items") or data.get("data") or [data]
        if not isinstance(data, list):
            raise ValueError("JSON must be a list of records, or {\"items\": [...]}")
        return [dict(r) for r in data if isinstance(r, dict)]
    reader = csv.DictReader(io.StringIO(text))
    return [{(k or "").strip(): v for k, v in row.items()} for row in reader]


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value if value != "" else None
    return value



def _json_columns(model) -> set:
    """Columns that store JSON. A scalar string in one of these is a latent crash: the
    frontend calls .map() on it. Derived from the model so the schemas cannot drift."""
    out = set()
    for column in model.__table__.columns:
        if "JSON" in str(column.type).upper() or column.type.__class__.__name__.startswith("JSON"):
            out.add(column.name)
    return out


def _max_length(model, field: str) -> Optional[int]:
    """Character limit of a String column, or None when the type is unbounded."""
    column = model.__table__.columns.get(field)
    return getattr(getattr(column, "type", None), "length", None) if column is not None else None


def list_fields(spec: Dict[str, Any]) -> set:
    """Fields that must end up as lists: anything declared, plus every JSON column."""
    return set(spec.get("lists", [])) | _json_columns(spec["model"])


class CoercionError(ValueError):
    """A cell cannot be converted to the column's type without guessing at its meaning."""


def _coerce_number(field: str, value: Any) -> Any:
    """
    Convert a spreadsheet cell to a number, or refuse.

    The previous implementation stripped every non-digit and parsed whatever survived.
    That is fine for "€12,000" but catastrophic elsewhere, because the leftovers still
    parse: a GRE cell of "320 (Q160)" became the number 320160, and "601-800" raised a
    raw ValueError mid-import. One silently invented a score that nobody had ever
    recorded - the exact failure this platform exists to prevent, committed by our own
    loader.

    So: strip only the things that genuinely decorate a number - currency symbols and
    codes, thousands separators, percent signs, the "=42" used for tied rankings,
    surrounding whitespace. Whatever remains must be a single complete number. If it is
    not, raise, and let the row be reported as an ERROR a human can look at. Guessing is
    not an option here.
    """
    text = str(value).strip()
    if not text:
        return None

    cleaned = re.sub(r"(?i)^(=|~|approx\.?|about|circa|ca\.)\s*", "", text)
    cleaned = re.sub(r"[%\s\u00a0]", "", cleaned)
    cleaned = re.sub(r"(?i)^[a-z]{0,3}[$€£¥₹৳]?\s*|[$€£¥₹৳]", "", cleaned)
    cleaned = re.sub(r"(?<=\d),(?=\d{3}\b)", "", cleaned)   # thousands separators only

    if cleaned in {"", "-", ".", "+"}:
        return None
    try:
        return float(cleaned) if "." in cleaned else int(cleaned)
    except ValueError:
        raise CoercionError(
            f"'{field}': cannot read {text!r} as a number. If this is a range, a tie or a "
            f"composite score, it belongs in a text column - it will not be guessed at."
        ) from None


def _coerce(spec: Dict[str, Any], field: str, value: Any) -> Any:
    if value is None:
        return None
    if field in spec.get("boolean", []):
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
    if field in spec.get("numeric", []):
        return _coerce_number(field, value)
    if field in list_fields(spec) and isinstance(value, str):
        parts = [v.strip() for v in value.split("|") if v.strip()]
        return [p.upper() for p in parts] if field == "degree_levels" else parts
    if field == "deadline" and isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return value


def _resolve_reference(db: Session, model, token: str) -> Optional[Any]:
    """Look a referenced record up by slug first, then by name - operators type both."""
    token = str(token).strip()
    found = db.execute(select(model).where(model.slug == token)).scalars().first()
    if found:
        return found
    return db.execute(select(model).where(func.lower(model.name) == token.lower())).scalars().first()


# --------------------------------------------------------------------------- staging
def stage(db: Session, *, entity_type: str, rows: List[Dict[str, Any]], filename: str = "",
          source_url: str = "", source_type: str = "TIER_1_OFFICIAL_UNIVERSITY",
          notes: str = "", actor: str = "system") -> ImportBatch:
    """Validate and diff every row, persist the batch, commit nothing to live tables."""
    spec = _spec(entity_type)
    model = spec["model"]

    if source_type and source_type not in SOURCE_TIERS:
        raise ValueError(f"source_type must be one of {', '.join(SOURCE_TIERS)}")

    reference = _next_reference(db)
    batch = ImportBatch(
        reference=reference, entity_type=entity_type.lower(), filename=filename or None,
        source_url=source_url or None, source_type=source_type,
        retrieved_at=datetime.now(timezone.utc), notes=notes or None,
        status="STAGED", row_count=len(rows), uploaded_by=actor,
    )
    db.add(batch)
    db.flush()

    tally = {"CREATE": 0, "UPDATE": 0, "CONFLICT": 0, "UNCHANGED": 0, "ERROR": 0}

    for index, raw_row in enumerate(rows, start=1):
        payload: Dict[str, Any] = {}
        errors: List[str] = []      # blocking: the row cannot be applied
        warnings: List[str] = []    # advisory: the row is fine, but worth telling the operator

        for field in spec["fields"]:
            if field in raw_row:
                try:
                    payload[field] = _coerce(spec, field, _clean(raw_row[field]))
                except CoercionError as exc:
                    errors.append(str(exc))

        # A value longer than its column is a commit-time database error, which is the
        # worst place to find out: the row is already half-applied and the operator sees
        # a psycopg traceback instead of a sentence. Catch it here, where it is just an
        # ERROR row naming the field and the limit.
        for field, value in payload.items():
            limit = _max_length(model, field)
            if limit and isinstance(value, str) and len(value) > limit:
                errors.append(
                    f"'{field}': {len(value)} characters, but the column holds {limit}. "
                    f"Shorten it or move the detail to a longer field."
                )

        # A faculty email has to be attributable to a page that published it. The spec
        # is unambiguous that addresses are never inferred, and the realistic way an
        # invented address enters a dataset is not malice but a spreadsheet column
        # someone filled in by pattern - firstname.lastname@university.edu. An address
        # with no status and no source URL is exactly that, so it is refused here.
        if entity_type == "professor" and (payload.get("email") or "").strip():
            status = (payload.get("email_status") or "").strip().upper()
            if status not in EMAIL_STATUSES:
                errors.append(
                    "'email' was given without a valid 'email_status' "
                    f"({', '.join(sorted(EMAIL_STATUSES))}). Faculty addresses must say "
                    "where they came from; they are never inferred."
                )
            elif status != "UNAVAILABLE" and not (payload.get("email_source_url") or "").strip():
                errors.append(
                    "'email' needs an 'email_source_url' naming the page that published "
                    "it. An address nobody can trace back is treated as invented."
                )

        # An extra column is not a reason to reject a row - exports routinely carry
        # columns we do not model. Say so and carry on.
        unknown = [k for k in raw_row if k and k not in spec["fields"]]
        if unknown:
            warnings.append(f"ignored unknown columns: {', '.join(sorted(unknown)[:6])}")

        for field in spec["required"]:
            if payload.get(field) in (None, ""):
                errors.append(f"missing required field '{field}'")

        # Provenance is mandatory for anything that will be shown as fact.
        row_source = payload.get("source_url") or source_url
        if not row_source:
            errors.append("no source_url on the row or the batch")
        confidence = (payload.get("confidence") or "").upper()
        if confidence and confidence not in CONFIDENCE_STATES:
            errors.append(f"confidence must be one of {', '.join(CONFIDENCE_STATES)}")
        # Several models (Country) have no confidence column; the value still travels
        # into the provenance record, so warn rather than reject.
        if confidence and not hasattr(spec["model"], "confidence"):
            warnings.append(f"{spec['label']} has no confidence column; "
                            "the value is recorded on the provenance entry only")

        # Resolve foreign references (country slug -> country_id, ...).
        resolved: Dict[str, Any] = {}
        for token_field, (column, ref_model) in (spec.get("resolve") or {}).items():
            token = payload.get(token_field)
            if token:
                target = _resolve_reference(db, ref_model, token)
                if target is None:
                    errors.append(f"{token_field} '{token}' does not exist yet")
                else:
                    resolved[column] = target.id

        # Destination hierarchy rules are enforced at staging too, so an operator sees the
        # problem on the row instead of discovering it as a savepoint failure at commit.
        if entity_type == "country":
            kind = str(payload.get("kind") or "COUNTRY").upper()
            code = str(payload.get("iso_code") or payload.get("iso2") or "").upper().strip()
            code_error = destination_code_error(kind, code)
            if code_error:
                errors.append(code_error)
            if kind not in ("COUNTRY", "REGION"):
                errors.append("'kind' must be COUNTRY or REGION")
            if kind == "REGION" and payload.get("iso2"):
                errors.append("a REGION must not carry iso2; use the subdivision iso_code only")
            parent_id = resolved.get("parent_country_id")
            if kind == "REGION":
                if not parent_id:
                    errors.append("a REGION needs 'parent_country' naming its sovereign country")
                else:
                    parent = db.get(Country, parent_id)
                    if parent is None or (parent.kind or "COUNTRY") != "COUNTRY":
                        errors.append("a REGION's parent_country must be a sovereign COUNTRY, not another region")
            elif parent_id:
                errors.append("a COUNTRY cannot have parent_country; use kind=REGION for a region")
            if code:
                duplicate = db.execute(
                    select(Country).where(func.upper(Country.iso_code) == code)
                ).scalars().first()
                if duplicate is not None and duplicate.slug != (payload.get("slug") or ""):
                    errors.append(
                        f"iso_code '{code}' is already used by '{duplicate.slug}'; "
                        "a destination cannot be imported twice"
                    )

        natural = payload.get(spec["key"])
        existing = None
        if natural and spec.get("child_of"):
            # Requirements, intakes and rankings have no slug of their own: they are
            # identified by their parent plus, where a parent can hold several, a
            # discriminator. Match on the resolved foreign key, not on a column named
            # after the CSV header.
            fk_column, _ = spec["resolve"][spec["key"]]
            parent_id = resolved.get(fk_column)
            if parent_id:
                clauses = [getattr(model, fk_column) == parent_id]
                for extra_key in spec.get("discriminators", []):
                    clauses.append(getattr(model, extra_key) == payload.get(extra_key))
                existing = db.execute(select(model).where(*clauses)).scalars().first()
                natural = " / ".join(
                    [str(natural)] + [str(payload.get(k)) for k in spec.get("discriminators", [])])
        elif natural:
            existing = db.execute(
                select(model).where(getattr(model, spec["key"]) == natural)
            ).scalars().first()

        diff, action = _diff(spec, existing, payload, resolved)
        if errors:
            action = "ERROR"

        tally[action] = tally.get(action, 0) + 1
        if action in {"UPDATE", "CONFLICT"} and existing is not None:
            # W13: the *proposed* value is recorded here, when it is known, not only if it is
            # later applied. A conflict is the case that matters most - the queue must be able to
            # show "we were told the fee is 31,000 and nobody has decided" even though nothing
            # was written to the catalogue.
            _stage_changes(db, batch, spec, existing, diff, action)
        db.add(ImportRow(
            batch_id=batch.id, line_number=index, natural_key=natural,
            action=action, target_id=existing.id if existing else None,
            payload={**payload, **resolved}, diff=diff, errors=errors,
            message="; ".join(warnings) or None,
            # Clean creates and updates are pre-approved; conflicts and errors are not.
            decision="APPROVE" if action in {"CREATE", "UPDATE"} else "SKIP",
        ))

    batch.created_rows = tally.get("CREATE", 0)
    batch.updated_rows = tally.get("UPDATE", 0)
    batch.conflict_rows = tally.get("CONFLICT", 0)
    batch.skipped_rows = tally.get("UNCHANGED", 0) + tally.get("ERROR", 0)
    db.commit()
    db.refresh(batch)
    return batch


def _diff(spec: Dict[str, Any], existing, payload: Dict[str, Any],
          resolved: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """Field-by-field comparison. Returns (diff, action)."""
    if existing is None:
        return ({f: {"current": None, "incoming": v, "conflict": False}
                 for f, v in payload.items() if v is not None}, "CREATE")

    current_confidence = (getattr(existing, "confidence", "") or "").upper()
    protected = current_confidence == "VERIFIED"

    diff: Dict[str, Any] = {}
    changed = conflicted = False
    skip = set((spec.get("resolve") or {}).keys())

    for field, incoming in payload.items():
        if incoming is None or field in skip:
            continue
        # Country has no confidence/source columns; those values live only on the
        # provenance record. Comparing them against a missing attribute would report a
        # change on every replay and make the loader non-idempotent.
        if not hasattr(existing, field):
            continue
        current = getattr(existing, field, None)
        if isinstance(current, datetime):
            current = current.isoformat()
        if _same(current, incoming):
            continue
        # A verified value with its own source is never quietly replaced. Provenance
        # columns themselves are exempt - updating a source URL is the point.
        is_conflict = bool(protected and field not in PROVENANCE_FIELDS
                           and getattr(existing, "source_url", None))
        diff[field] = {"current": _jsonable(current), "incoming": _jsonable(incoming),
                       "conflict": is_conflict}
        changed = True
        conflicted = conflicted or is_conflict

    for column, value in resolved.items():
        if getattr(existing, column, None) != value:
            diff[column] = {"current": getattr(existing, column, None), "incoming": value,
                            "conflict": False}
            changed = True

    if conflicted:
        return diff, "CONFLICT"
    return (diff, "UPDATE") if changed else (diff, "UNCHANGED")


def _same(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    return str(a).strip() == str(b).strip()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


def batches_uploaded_by(db: Session, *, entity_type: str, entity_id: int,
                        email: str) -> List[ImportBatch]:
    """Committed batches by this uploader that wrote this record.

    Used to keep an importer from approving its own upload. Returns an empty list when the
    reviewer has never uploaded this record, which is the normal case.
    """
    if not email:
        return []
    stmt = (select(ImportBatch)
            .join(ImportRow, ImportRow.batch_id == ImportBatch.id)
            .where(ImportBatch.entity_type == entity_type,
                   ImportBatch.status == "COMMITTED",
                   ImportRow.target_id == entity_id,
                   func.lower(ImportBatch.uploaded_by) == email.strip().lower())
            .distinct())
    return list(db.execute(stmt).scalars())


def _next_reference(db: Session) -> str:
    """The next batch reference, derived from the highest existing suffix.

    It used to be ``count() + 1``, so deleting any batch made the next reference collide with a
    live one and the load failed with a unique-constraint error that said nothing about the
    cause. Reading the highest suffix is monotonic: it never goes backwards when a row is removed.
    The unique index still protects against two simultaneous stagings.
    """
    year = datetime.now(timezone.utc).year
    prefix = f"IMP-{year}-"
    highest = 0
    for reference in db.execute(
            select(ImportBatch.reference).where(ImportBatch.reference.like(f"{prefix}%"))
    ).scalars():
        suffix = str(reference)[len(prefix):]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:04d}"


# --------------------------------------------------------------------------- commit
def commit_batch(db: Session, batch: ImportBatch, actor: str = "system") -> Dict[str, Any]:
    """Apply every APPROVE row and record provenance for each field written."""
    if batch.status == "COMMITTED":
        raise ValueError("this batch has already been committed")
    if batch.status == "DISCARDED":
        raise ValueError("this batch was discarded")

    spec = _spec(batch.entity_type)
    model = spec["model"]
    created = updated = verifications = 0
    failures: List[str] = []

    for row in sorted(batch.rows, key=lambda r: r.line_number):
        if row.decision != "APPROVE" or row.applied:
            continue
        try:
            # One savepoint per row. The previous version called db.rollback() in the
            # except branch, which threw away every row already flushed in this batch
            # while leaving their counters incremented - so a load could report "4
            # created, 0 errors" when two of the four no longer existed. A failing row
            # must cost exactly itself.
            with db.begin_nested():
                target = db.get(model, row.target_id) if row.target_id else None
                is_new = target is None
                if target is None:
                    target = model()
                    db.add(target)

                writable = {k: v for k, v in (row.payload or {}).items()
                            if v is not None and k not in (spec.get("resolve") or {})}
                for field, value in writable.items():
                    if hasattr(target, field):
                        setattr(target, field, value)

                # Provenance stamped from the row, falling back to the batch.
                if hasattr(target, "source_url") and not writable.get("source_url"):
                    target.source_url = batch.source_url
                if hasattr(target, "source_type") and not writable.get("source_type"):
                    target.source_type = batch.source_type
                if hasattr(target, "retrieved_at"):
                    target.retrieved_at = batch.retrieved_at or datetime.now(timezone.utc)

                # W12: an upload cannot certify itself. This used to honour a `confidence`
                # column in the CSV, so a spreadsheet asserting VERIFIED produced a record the
                # read-time policy would present as official - with no human in the loop and
                # nothing but the uploader's own claim behind it. Whatever the file says, a
                # committed value arrives NEEDS_REVIEW, and the verification dates are cleared
                # so the previous human verification is not silently grafted onto a new figure.
                touched_fields = _material_fields(spec, writable)
                if touched_fields:
                    # The proposal was recorded when it was staged; applying it records the
                    # decision, so one change has one row and one state.
                    _approve_staged_changes(db, spec, batch, row, target, touched_fields, actor)
                if hasattr(target, "confidence") and touched_fields:
                    previous_confidence = (getattr(target, "confidence", "") or "").upper()
                    previous_verified_at = getattr(target, "verified_at", None)
                    if is_new or previous_confidence == "VERIFIED" \
                            or previous_verified_at is not None:
                        target.confidence = "NEEDS_REVIEW"
                        if hasattr(target, "verified_at"):
                            target.verified_at = None
                        if hasattr(target, "verified_by"):
                            target.verified_by = None
                    if not is_new:
                        # The assignment above is an ORM delta: when this session already holds
                        # NEEDS_REVIEW for the row - the normal case for a record that is still
                        # awaiting review - it emits no column at all, so a concurrent approval
                        # that committed in the meantime survives and the record goes on claiming
                        # VERIFIED over a register that derives NEEDS_REVIEW. That is the same
                        # trust divergence as the direct-edit path (Campaign 10), so the lowering
                        # is written unconditionally as a statement. It can only ever withhold.
                        withholding: Dict[str, Any] = {"confidence": "NEEDS_REVIEW"}
                        if hasattr(target, "verified_at"):
                            withholding["verified_at"] = None
                        if hasattr(target, "verified_by"):
                            withholding["verified_by"] = None
                        db.execute(
                            update(model)
                            .where(model.id == target.id)
                            .values(**withholding)
                            .execution_options(synchronize_session=False)
                        )

                db.flush()
                written = _record_provenance(db, batch, row, target, actor)

            # Remember which record this row wrote. Without it a created row has no target, so
            # "who uploaded this entity" cannot be answered - which is what the review path
            # needs in order to refuse self-certification.
            if row.target_id is None:
                row.target_id = target.id

            # Counters move only once the savepoint has survived, so the tally can never
            # claim a write that was undone.
            created += 1 if is_new else 0
            updated += 0 if is_new else 1
            verifications += written
            row.applied = True
            row.message = "created" if is_new else "updated"
        except Exception as exc:                                     # noqa: BLE001
            row.message = f"failed: {type(exc).__name__}: {exc}"[:400]
            row.action = "ERROR"
            failures.append(f"line {row.line_number}: {row.message}")

    batch.status = "COMMITTED"
    batch.committed_by = actor
    batch.committed_at = datetime.now(timezone.utc)
    db.commit()
    return {"created": created, "updated": updated, "verifications": verifications,
            "failures": failures, "reference": batch.reference}


def _stage_changes(db: Session, batch: ImportBatch, spec: Dict[str, Any], existing,
                   diff: Dict[str, Any], action: str) -> None:
    """Record a pending, reviewable change for each materially different field."""
    from app.models.crm import CatalogueChange
    from app.services import catalogue_changes

    entity_type = batch.entity_type
    for field, change in (diff or {}).items():
        if field in PROVENANCE_FIELDS:
            continue
        previous = change.get("current")
        incoming = change.get("incoming")
        if previous is None and incoming is None:
            continue
        kind = catalogue_changes.classify(field, previous, incoming)
        if kind in (catalogue_changes.NO_CHANGE, catalogue_changes.FORMAT_ONLY):
            continue
        # Re-staging the same proposal must not pile up versions.
        duplicate = db.execute(select(CatalogueChange).where(
            CatalogueChange.entity_type == entity_type,
            CatalogueChange.entity_id == existing.id,
            CatalogueChange.field_name == field,
            CatalogueChange.status == "PENDING",
            CatalogueChange.new_value == str(incoming)[:600],
        )).scalars().first()
        if duplicate is not None:
            continue
        catalogue_changes.record(
            db, entity_type=entity_type, entity_id=existing.id, field_name=field,
            previous=previous, new=incoming, source_url=batch.source_url,
            source_type=batch.source_type, retrieved_at=batch.retrieved_at,
            actor=batch.uploaded_by or "system", batch_id=batch.id,
            classification=kind,
        )


def _material_fields(spec: Dict[str, Any], writable: Dict[str, Any]) -> List[str]:
    """Writable fields whose value the record did not already hold.

    Provenance columns are excluded: re-stating a source URL is not a change to a fact.
    """
    return [f for f in writable if f not in PROVENANCE_FIELDS]


def _approve_staged_changes(db: Session, spec: Dict[str, Any], batch: ImportBatch,
                            row: ImportRow, target, fields: List[str], actor: str) -> None:
    """Mark the pending proposals this row applied as approved by the committing reviewer."""
    from app.models.crm import CatalogueChange
    from app.services import catalogue_changes

    if getattr(target, "id", None) is None:
        db.flush()          # a created record has no identifier until it is flushed
    entity_type = batch.entity_type
    for field in fields:
        change = (row.diff or {}).get(field) or {}
        incoming = change.get("incoming")
        pending = db.execute(select(CatalogueChange).where(
            CatalogueChange.entity_type == entity_type,
            CatalogueChange.entity_id == target.id,
            CatalogueChange.field_name == field,
            CatalogueChange.status == "PENDING",
        ).order_by(CatalogueChange.id.desc())).scalars().first()
        if pending is not None and str(incoming)[:600] == (pending.new_value or ""):
            catalogue_changes.decide(db, pending, status="APPROVED", actor=actor,
                                     note="applied by import batch %s" % batch.reference)
            continue
        # No staged proposal (a field the diff did not record): fall back to recording the
        # change now, so no applied value is left without a version. A brand-new record has no
        # previous value to preserve - its provenance lives in the field register - so a create
        # is not a "change" and does not belong in the review queue.
        if row.action == "CREATE":
            continue
        previous = change.get("current")
        if previous is None and incoming is None:
            continue
        catalogue_changes.record(
            db, entity_type=entity_type, entity_id=target.id, field_name=field,
            previous=previous, new=incoming, source_url=batch.source_url,
            source_type=batch.source_type, retrieved_at=batch.retrieved_at,
            actor=batch.uploaded_by or actor, batch_id=batch.id,
        )


def _record_provenance(db: Session, batch: ImportBatch, row: ImportRow,
                       target, actor: str) -> int:
    """One DataVerification per changed field, so every value traces to a URL."""
    written = 0
    source_url = (row.payload or {}).get("source_url") or batch.source_url
    source_type = (row.payload or {}).get("source_type") or batch.source_type
    now = datetime.now(timezone.utc)

    for field, change in (row.diff or {}).items():
        if field in PROVENANCE_FIELDS:
            continue
        conflict_note = None
        # W12: the uploader's own `confidence` claim never becomes VERIFIED here. A human
        # decision in the review path is the only thing that can.
        status = "NEEDS_REVIEW"
        if change.get("conflict"):
            # The superseded value is kept, never discarded.
            conflict_note = json.dumps({"previous_value": change.get("current"),
                                        "previous_source": getattr(target, "source_url", None),
                                        "resolved_by": actor,
                                        "resolved_at": now.isoformat()})
            status = "CONFLICTING"

        db.add(DataVerification(
            entity_type=batch.entity_type, entity_id=target.id, field_name=field,
            # Full value, never a prefix: the derivation compares this with the stored column, and
            # a clipped record could never agree with a long value (C2-10).
            field_value=str(change.get("incoming")),
            source_url=source_url, source_type=source_type,
            retrieved_at=batch.retrieved_at or now, verified_at=now if status == "VERIFIED" else None,
            verified_by=actor if status == "VERIFIED" else None,
            status=status, conflict_with=conflict_note,
        ))
        written += 1
    return written
