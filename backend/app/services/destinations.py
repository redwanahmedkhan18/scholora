"""Study destinations: sovereign countries plus the regions inside them.

The catalogue is data, not code. A new destination is an ordinary ``countries`` row, so it can
be added through the admin CRUD endpoints or the importer without shipping a release. This
module only holds the *approved* minimum set (so a fresh database can be bootstrapped and a
test can prove the set is complete), the resolution rules the assistant and the public filters
share, and an honest coverage report.

Why a region is not a country
-----------------------------
Scotland is a study destination *inside* the United Kingdom. It has no ISO 3166-1 code, no
separate currency and no separate visa regime, so modelling it as a sovereign country would
put false facts in front of a student. Instead:

* ``countries.kind = 'REGION'`` with ``parent_country_id`` pointing at the United Kingdom,
* ``countries.iso_code = 'GB-SCT'``, an ISO 3166-2 subdivision code, never an ISO 3166-1 one,
* universities stay attached to their sovereign country (``universities.country_id = GB``) and
  additionally carry ``universities.region_id`` when they are in a region.

That is what makes both directions correct: a United Kingdom query includes Scotland for free
(the institutions never left GB), while a Scotland query returns only Scottish institutions.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.education import Country, Program, University

log = logging.getLogger(__name__)

#: The 17 approved sovereign destinations, keyed by ISO 3166-1 alpha-2. Order is the order a
#: student sees them in, so it is the product decision, not an accident of insertion.
APPROVED_DESTINATIONS: List[tuple[str, str, str, str]] = [
    # iso2, name, region grouping, flag
    ("US", "United States", "North America", "\U0001F1FA\U0001F1F8"),
    ("GB", "United Kingdom", "Europe", "\U0001F1EC\U0001F1E7"),
    ("CA", "Canada", "North America", "\U0001F1E8\U0001F1E6"),
    ("AU", "Australia", "Oceania", "\U0001F1E6\U0001F1FA"),
    ("IE", "Ireland", "Europe", "\U0001F1EE\U0001F1EA"),
    ("DE", "Germany", "Europe", "\U0001F1E9\U0001F1EA"),
    ("MY", "Malaysia", "Asia", "\U0001F1F2\U0001F1FE"),
    ("HU", "Hungary", "Europe", "\U0001F1ED\U0001F1FA"),
    ("CY", "Cyprus", "Europe", "\U0001F1E8\U0001F1FE"),
    ("AE", "United Arab Emirates", "Middle East", "\U0001F1E6\U0001F1EA"),
    ("KR", "South Korea", "Asia", "\U0001F1F0\U0001F1F7"),
    ("NZ", "New Zealand", "Oceania", "\U0001F1F3\U0001F1FF"),
    ("SA", "Saudi Arabia", "Middle East", "\U0001F1F8\U0001F1E6"),
    ("JP", "Japan", "Asia", "\U0001F1EF\U0001F1F5"),
    ("IT", "Italy", "Europe", "\U0001F1EE\U0001F1F9"),
    ("FI", "Finland", "Europe", "\U0001F1EB\U0001F1EE"),
    ("ES", "Spain", "Europe", "\U0001F1EA\U0001F1F8"),
]

#: Approved study regions: (iso_code, name, parent iso2, grouping, flag).
APPROVED_REGIONS: List[tuple[str, str, str, str, str]] = [
    ("GB-SCT", "Scotland", "GB", "Europe", "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F"),
]

#: Words a student actually types. Longest first, so "south korea" wins over "korea".
ALIASES: Dict[str, str] = {
    "usa": "US", "u.s.": "US", "u.s.a.": "US", "us": "US", "america": "US",
    "united states of america": "US", "the states": "US",
    "uk": "GB", "u.k.": "GB", "britain": "GB", "great britain": "GB", "england": "GB",
    "united kingdom": "GB",
    "scotland": "GB-SCT", "scottish": "GB-SCT", "edinbur": "GB-SCT",
    "uae": "AE", "emirates": "AE", "dubai": "AE", "abu dhabi": "AE",
    "korea": "KR", "south korea": "KR", "republic of korea": "KR",
    "holland": "NL", "deutschland": "DE",
    "new zealand": "NZ", "aotearoa": "NZ",
    "saudi": "SA", "saudi arabia": "SA",
}

#: Cities that place a university in Scotland. Used only to attach a region to an institution
#: that is already on record; it never creates or edits an institution.
SCOTLAND_CITIES = {
    "glasgow", "edinburgh", "aberdeen", "dundee", "stirling", "st andrews", "saint andrews",
    "inverness", "paisley", "perth", "dunfermline", "falkirk", "livingston", "hamilton",
}


def _norm(token: Optional[str]) -> str:
    return (token or "").strip().lower().replace("_", " ").replace("-", " ")


def sovereign_rows(db: Session) -> List[Country]:
    """Sovereign destinations only - regions are not countries and are never listed as one."""
    return list(
        db.execute(
            select(Country).where(func.coalesce(Country.kind, "COUNTRY") == "COUNTRY")
            .order_by(Country.priority, Country.name)
        ).scalars()
    )


def regions_of(db: Session, country: Country) -> List[Country]:
    if country is None or (country.kind or "COUNTRY") != "COUNTRY":
        return []
    return list(
        db.execute(
            select(Country).where(Country.parent_country_id == country.id).order_by(Country.name)
        ).scalars()
    )


def sovereign_of(db: Session, country: Country) -> Country:
    """The country a destination ultimately belongs to (a country is its own sovereign)."""
    if country is not None and country.parent_country_id:
        parent = db.get(Country, country.parent_country_id)
        if parent is not None:
            return parent
    return country


def resolve_destination(db: Session, token: str) -> Optional[Country]:
    """Resolve a name, slug, ISO code or everyday alias to a destination row.

    Expression order: exact ISO code, ISO 3166-1 alpha-2, slug, exact name, then a substring
    match on the name, and finally the alias table. Returns ``None`` rather than guessing, so
    a caller can say "unknown destination" instead of silently answering about the wrong one.
    """
    raw = _norm(token)
    if not raw:
        return None
    key = raw.upper().replace(" ", "-")
    for column, value in ((Country.iso_code, key), (Country.iso2, raw.upper())):
        row = db.execute(select(Country).where(column == value)).scalars().first()
        if row is not None:
            return row
    slug = raw.replace(" ", "-")
    row = db.execute(select(Country).where(Country.slug == slug)).scalars().first()
    if row is not None:
        return row

    rows = list(db.execute(select(Country)).scalars())
    for row in rows:
        if _norm(row.name) == raw:
            return row
    # Longest alias/named match wins, so "south korea" is never resolved as "korea" by accident.
    for alias, iso in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in raw or raw in alias:
            hit = next((r for r in rows if (r.iso_code or r.iso2 or "").upper() == iso), None)
            if hit is not None:
                return hit
    candidates = [r for r in rows if raw in _norm(r.name)]
    if len(candidates) == 1:
        return candidates[0]
    return None


def destination_family(db: Session, country: Country) -> List[Country]:
    """A destination plus every region inside it - the exact scope of a country-wide query."""
    if country is None:
        return []
    return [country, *regions_of(db, country)]


def family_ids(db: Session, country: Country) -> List[int]:
    return [c.id for c in destination_family(db, country)]


# ------------------------------------------------------------------ coverage
def destination_counts(db: Session) -> Dict[int, Dict[str, int]]:
    """Universities and programmes per destination row, regions counted on their own.

    A destination with no records reports zero. That is deliberate: the record's existence is
    a product decision, and claiming coverage would be a fabricated fact.
    """
    counts: Dict[int, Dict[str, int]] = {
        cid: {"universities": 0, "programs": 0}
        for cid in db.execute(select(Country.id)).scalars()
    }
    uni_by_country = db.execute(
        select(University.country_id, func.count()).group_by(University.country_id)
    ).all()
    for cid, n in uni_by_country:
        if cid is not None and cid in counts:
            counts[cid]["universities"] = int(n)
    uni_by_region = db.execute(
        select(University.region_id, func.count())
        .where(University.region_id.is_not(None)).group_by(University.region_id)
    ).all()
    for region_id, n in uni_by_region:
        if region_id is not None and region_id in counts:
            # A region owns the institutions inside it; the country total stays the sum of
            # everything physically in that country, region or not.
            counts[region_id]["universities"] = int(n)
    prog_by_country = db.execute(
        select(University.country_id, func.count(Program.id))
        .join(Program, Program.university_id == University.id)
        .group_by(University.country_id)
    ).all()
    for cid, n in prog_by_country:
        if cid is not None and cid in counts:
            counts[cid]["programs"] = int(n)
    prog_by_region = db.execute(
        select(University.region_id, func.count(Program.id))
        .join(Program, Program.university_id == University.id)
        .where(University.region_id.is_not(None))
        .group_by(University.region_id)
    ).all()
    for region_id, n in prog_by_region:
        if region_id is not None and region_id in counts:
            counts[region_id]["programs"] = int(n)
    return counts


def with_family_counts(db: Session, counts: Dict[int, Dict[str, int]]) -> Dict[int, Dict[str, int]]:
    """Country figures include their regions, so a UK page is never missing its Scottish rows."""
    out = {cid: dict(v) for cid, v in counts.items()}
    for region in db.execute(
        select(Country).where(Country.parent_country_id.is_not(None))
    ).scalars():
        if region.parent_country_id is None:
            continue
        parent = out.get(region.parent_country_id)
        child = counts.get(region.id)
        if parent and child:
            parent["universities"] += child["universities"]
            parent["programs"] += child["programs"]
    return out


def parent_name(db: Session, country: Country) -> Optional[str]:
    if not country.parent_country_id:
        return None
    parent = db.get(Country, country.parent_country_id)
    return parent.name if parent is not None else None


def coverage(db: Session) -> List[Dict[str, object]]:
    """Honest, per-destination view of what the catalogue actually holds."""
    rows = sorted(
        db.execute(select(Country)).scalars(),
        key=lambda c: ((c.kind or "COUNTRY") != "COUNTRY", c.priority or 100, c.name),
    )
    counts = destination_counts(db)
    return [
        {
            "name": row.name, "slug": row.slug, "iso_code": row.iso_code or row.iso2,
            "kind": row.kind or "COUNTRY",
            "parent": parent_name(db, row),
            "status": row.status,
            "universities": counts.get(row.id, {}).get("universities", 0),
            "programs": counts.get(row.id, {}).get("programs", 0),
            "has_coverage": counts.get(row.id, {}).get("universities", 0) > 0,
        }
        for row in rows
    ]


# ------------------------------------------------------------------ bootstrap
def _make_country(iso2: str, name: str, region: str, flag: str, *, status: str,
                  currency: Optional[str]) -> Country:
    from slugify import slugify  # same helper the seed and admin CRUD use

    return Country(
        name=name, slug=slugify(name), iso2=iso2, iso_code=iso2, kind="COUNTRY",
        parent_country_id=None, is_study_destination=True, flag_emoji=flag, region=region,
        status=status, is_featured=False, priority=400, currency=currency,
        tagline=f"Study destinations in {name} - catalogue records are added per university and programme.",
        summary=(f"Scholara is building its {name} catalogue. A destination record means the country is "
                 f"supported by our counselling process; it does not mean every university or programme "
                 f"has been verified yet. Each record carries its own source and verification date."),
    )


def ensure_destinations(db: Session) -> Dict[str, int]:
    """Insert any approved destination that is missing. Idempotent, never overwrites content.

    A row is matched on ISO code (so ``South Korea`` with ``KR`` is never added twice, whatever
    name or slug it was created with). Existing editorial content is left byte-for-byte alone -
    re-running this after a content edit is a no-op.
    """
    created = 0
    existing = {
        (c.iso_code or c.iso2 or "").upper(): c
        for c in db.execute(select(Country)).scalars()
        if (c.iso_code or c.iso2)
    }
    for iso2, name, region, flag in APPROVED_DESTINATIONS:
        if iso2 in existing:
            row = existing[iso2]
            if not row.iso_code:
                row.iso_code = iso2
            continue
        # Currency is only filled where it is unambiguous; a missing value stays unknown.
        currency = {"US": "USD", "GB": "GBP", "CA": "CAD", "AU": "AUD", "NZ": "NZD",
                    "AE": "AED", "SA": "SAR", "JP": "JPY", "KR": "KRW", "MY": "MYR",
                    "HU": "HUF"}.get(iso2, "EUR")
        row = _make_country(iso2, name, region, flag, status="COMING_SOON", currency=currency)
        row.priority = 300 + len(existing)
        db.add(row)
        db.flush()
        existing[iso2] = row
        created += 1

    uk = existing.get("GB")
    for iso_code, name, parent_iso, region, flag in APPROVED_REGIONS:
        if iso_code in existing:
            continue
        parent = existing.get(parent_iso)
        if parent is None:
            log.warning("cannot create region %s: parent %s missing", name, parent_iso)
            continue
        from slugify import slugify

        row = Country(
            name=name, slug=slugify(name), iso2=None, iso_code=iso_code, kind="REGION",
            parent_country_id=parent.id, is_study_destination=True, flag_emoji=flag,
            region=region, status="ACTIVE", is_featured=False, priority=350,
            currency=parent.currency,
            tagline=f"{name} is a study region within the {parent.name}.",
            summary=(f"{name} is part of the {parent.name}: the same student visa, currency and "
                     f"post-study rules apply. It is listed separately so universities and "
                     f"programmes in {name} can be found and compared on their own."),
        )
        db.add(row)
        db.flush()
        existing[iso_code] = row
        created += 1

    attached = _attach_scottish_institutions(db, existing.get("GB-SCT")) if uk is not None else 0
    if created or attached:
        db.commit()
    return {"created": created, "regions_attached": attached}


def _attach_scottish_institutions(db: Session, scotland: Optional[Country]) -> int:
    """Point institutions in Scottish cities at the region row, without touching ownership.

    ``country_id`` stays ``GB`` - the institution is still in the United Kingdom - so no
    existing URL, filter or total changes. Only the region link is added.
    """
    if scotland is None:
        return 0
    changed = 0
    rows = db.execute(
        select(University).where(University.region_id.is_(None))
    ).scalars()
    for uni in rows:
        if (uni.city or "").strip().lower() in SCOTLAND_CITIES:
            uni.region_id = scotland.id
            changed += 1
    return changed


def university_scope(db: Session, country: Country):
    """SQL predicate selecting the institutions a destination query should see.

    A sovereign country returns everything physically in it - including institutions that also
    belong to one of its regions, which is how a United Kingdom search keeps Scotland's
    universities. A region returns only its own, which is how a Scotland search excludes
    English, Welsh and Northern Irish institutions.
    """
    if country is None:
        return None
    if (country.kind or "COUNTRY") == "REGION":
        return University.region_id == country.id
    return University.country_id.in_(family_ids(db, country))


def universities_in(db: Session, country: Country) -> List[int]:
    predicate = university_scope(db, country)
    if predicate is None:
        return []
    return list(db.execute(select(University.id).where(predicate)).scalars())
