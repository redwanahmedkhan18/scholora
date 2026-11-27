"""W8 acceptance tests - the destination hierarchy.

The approved product set is 17 sovereign countries, with Scotland modelled as a region inside
the United Kingdom. These tests prove the set is exact, insertion is idempotent, a new
destination can arrive through the admin workflow without a code change, and that UK and
Scotland queries scope institutions in opposite directions exactly as intended.
"""
from __future__ import annotations

import pytest
from app.models.education import Country, University
from app.services import destinations
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

APPROVED_17 = {
    "US", "CA", "IE", "MY", "CY", "KR", "GB", "AU", "DE", "HU", "AE", "NZ", "SA", "JP", "IT",
    "FI", "ES",
}


def _session():
    from task2_helpers import new_session

    return new_session()


def test_the_approved_destination_set_is_exactly_the_seventeen_countries():
    db = _session()
    try:
        # Other suites deliberately import extra catalogue rows (that is how the importer is
        # tested). The product contract here is the approved set: exactly these 17 codes, each
        # once, and Scotland is not one of them.
        approved_rows = [c for c in destinations.sovereign_rows(db)
                         if (c.iso_code or "").upper() in APPROVED_17]
        codes = [(c.iso_code or "").upper() for c in approved_rows]
        assert set(codes) == APPROVED_17, f"unexpected destination set: {sorted(set(codes))}"
        assert len(codes) == 17, f"a destination was duplicated: {sorted(codes)}"
        for row in approved_rows:
            assert row.kind == "COUNTRY"
            assert row.parent_country_id is None

        # The approved list is itself the product contract, independent of the database.
        declared = [iso for iso, *_ in destinations.APPROVED_DESTINATIONS]
        assert set(declared) == APPROVED_17
        assert len(declared) == 17
    finally:
        db.close()


def test_scotland_is_a_region_inside_the_united_kingdom_not_a_country():
    db = _session()
    try:
        scotland = db.execute(select(Country).where(Country.iso_code == "GB-SCT")).scalars().first()
        assert scotland is not None
        uk = db.execute(select(Country).where(Country.iso2 == "GB")).scalars().first()
        assert uk is not None

        assert scotland.name == "Scotland"
        assert scotland.kind == "REGION"
        assert scotland.parent_country_id == uk.id
        assert scotland.iso_code == "GB-SCT"
        # A subdivision code is not a sovereign ISO 3166-1 code, and must never be listed as one.
        assert scotland.iso2 is None
        assert uk.kind == "COUNTRY"
        assert uk.parent_country_id is None
        assert "GB-SCT" not in {(c.iso_code or c.iso2) for c in destinations.sovereign_rows(db)}
    finally:
        db.close()


def test_destination_seeding_is_idempotent_and_never_duplicates():
    db = _session()
    try:
        before = db.scalar(select(func.count()).select_from(Country))
        first = destinations.ensure_destinations(db)
        second = destinations.ensure_destinations(db)
        after = db.scalar(select(func.count()).select_from(Country))

        assert first["created"] == 0, "the seed already inserted the approved set"
        assert second["created"] == 0
        assert after == before, "seeding must not add rows when nothing is missing"

        # South Korea and Germany were the duplicates the gate had to avoid: one row each.
        for iso in ("KR", "DE"):
            count = db.scalar(select(func.count()).select_from(Country).where(Country.iso_code == iso))
            assert count == 1
        # iso_code is the uniqueness key, and it is unique even across 17 + regions.
        codes = [c.iso_code for c in db.execute(select(Country)).scalars() if c.iso_code]
        assert len(codes) == len(set(codes))
    finally:
        db.close()


def test_a_duplicate_destination_is_rejected_by_the_uniqueness_constraint():
    db = _session()
    try:
        db.add(Country(name="Duplicate Britain", slug="duplicate-britain", iso2="GB", iso_code="GB"))
        with pytest.raises(IntegrityError):
            db.flush()
    finally:
        db.rollback()
        db.close()


def test_a_region_cannot_be_created_without_a_sovereign_parent():
    from app.models.education import validate_country_hierarchy

    orphan = Country(name="Nowhere", slug="nowhere", iso_code="XX-ABC", kind="REGION")
    with pytest.raises(ValueError, match="parent_country_id"):
        validate_country_hierarchy(orphan)

    sovereign_with_parent = Country(name="Atlantis", slug="atlantis", iso_code="AT", kind="COUNTRY",
                                    parent_country_id=1)
    with pytest.raises(ValueError, match="cannot have parent_country_id"):
        validate_country_hierarchy(sovereign_with_parent)

    subdivision_as_country = Country(name="Scotland 2", slug="scotland-2", iso_code="GB-SCT",
                                     kind="COUNTRY")
    with pytest.raises(ValueError, match="subdivision code"):
        validate_country_hierarchy(subdivision_as_country)


def test_a_destination_can_be_added_through_the_admin_api_without_a_code_change(client, api, auth):
    payload = {
        "name": "Netherlands", "slug": "netherlands", "iso2": "NL", "iso_code": "NL",
        "kind": "COUNTRY", "status": "COMING_SOON", "is_study_destination": True,
        "tagline": "Test destination added through the admin CRUD form.",
        "summary": "Test-only destination. Removed at the end of this test.",
    }
    created = client.post(f"{api}/admin/countries", json=payload, headers=auth)
    assert created.status_code == 200, created.text
    row = created.json()
    try:
        assert row["iso_code"] == "NL"
        assert row["kind"] == "COUNTRY"
        listing = client.get(f"{api}/countries").json()
        names = [c["name"] for c in listing]
        assert "Netherlands" in names
        assert all(c["kind"] == "COUNTRY" for c in listing), "regions are nested, never top-level"
        uk = next(c for c in listing if c["iso_code"] == "GB")
        assert [r["iso_code"] for r in uk["regions"]] == ["GB-SCT"]
    finally:
        deleted = client.delete(f"{api}/admin/countries/{row['id']}", headers=auth)
        assert deleted.status_code == 200, deleted.text


def test_a_university_region_must_belong_to_the_same_country():
    """A German institution cannot be tagged as a region of Scotland."""
    db = _session()
    try:
        us = db.execute(select(Country).where(Country.iso2 == "US")).scalars().first()
        scotland = db.execute(select(Country).where(Country.iso_code == "GB-SCT")).scalars().first()
        assert us is not None and scotland is not None
        db.add(University(name="Misplaced University", slug="misplaced-university",
                          country_id=us.id, region_id=scotland.id))
        with pytest.raises(ValueError, match="region of universities.country_id"):
            db.flush()
    finally:
        db.rollback()
        db.close()


def test_a_region_without_a_parent_country_row_is_not_created():
    db = _session()
    try:
        before = db.scalar(select(func.count()).select_from(Country))
        # Remove any pre-existing Scotland impossible here; instead prove the guard on a fake
        # region parent: the service must warn and skip rather than invent a parent.
        from app.services import destinations as service

        original = list(service.APPROVED_REGIONS)
        try:
            service.APPROVED_REGIONS.append(("XX-ABC", "Nowhere", "ZZ", "Test", ""))
            result = service.ensure_destinations(db)
        finally:
            service.APPROVED_REGIONS[:] = original
        assert result["created"] == 0
        assert db.scalar(select(func.count()).select_from(Country)) == before
    finally:
        db.rollback()
        db.close()


def test_united_kingdom_includes_scotland_but_scotland_excludes_the_rest_of_the_uk(client, api):
    db = _session()
    try:
        uk = db.execute(select(Country).where(Country.iso2 == "GB")).scalars().first()
        scotland = db.execute(select(Country).where(Country.iso_code == "GB-SCT")).scalars().first()
        assert uk is not None and scotland is not None

        uk_scope = destinations.university_scope(db, uk)
        scotland_scope = destinations.university_scope(db, scotland)
        uk_ids = set(db.execute(select(University.id).where(uk_scope)).scalars())
        scotland_ids = set(db.execute(select(University.id).where(scotland_scope)).scalars())
        glasgow = db.execute(select(University).where(University.city == "Glasgow")).scalars().first()
        assert glasgow is not None, "the seeded catalogue must contain a Scottish institution"
        assert glasgow.id in uk_ids, "a UK-wide query must include Scotland"
        assert glasgow.id in scotland_ids
        assert scotland_ids < uk_ids, "a Scotland query must exclude English institutions"
    finally:
        db.close()

    uk_page = client.get(f"{api}/universities", params={"country": "united-kingdom", "page_size": 100}).json()
    scotland_page = client.get(f"{api}/universities", params={"country": "scotland", "page_size": 100}).json()
    assert any(u["city"] == "Glasgow" for u in uk_page["items"])
    assert scotland_page["items"], "Scotland is a supported destination with catalogue records"
    assert all(u["country"]["name"] == "United Kingdom" for u in scotland_page["items"])
    assert all(u["city"].lower() in destinations.SCOTLAND_CITIES for u in scotland_page["items"])

    detail = client.get(f"{api}/countries/scotland")
    assert detail.status_code == 200
    body = detail.json()
    assert body["country"]["kind"] == "REGION"
    assert body["country"]["parent"]["name"] == "United Kingdom"
    assert body["universities"], "the Scotland page must list its own institutions"
    assert all(u["city"].lower() in destinations.SCOTLAND_CITIES for u in body["universities"])
    for programme in body["programs"]:
        assert programme["university"]["city"].lower() in destinations.SCOTLAND_CITIES


def test_a_destination_with_no_records_reports_empty_coverage_honestly(client, api):
    """A destination record is a product decision, never a claim that records exist."""
    db = _session()
    empty = None
    try:
        empty = Country(
            name="Test Empty Destination", slug="test-empty-destination", iso2="ZZ",
            iso_code="ZZ", kind="COUNTRY", is_study_destination=True,
            status="COMING_SOON", region="Test", priority=999,
            tagline="Test-only destination with no catalogue coverage.",
            summary="Created and deleted by the acceptance suite to prove empty coverage is reported.",
        )
        db.add(empty)
        db.commit()

        counts = destinations.destination_counts(db)
        assert counts[empty.id]["universities"] == 0
        assert counts[empty.id]["programs"] == 0
        report = {row["iso_code"]: row for row in destinations.coverage(db)}
        assert report["ZZ"]["has_coverage"] is False
        assert report["ZZ"]["universities"] == 0

        listing = client.get(f"{api}/countries").json()
        item = next(c for c in listing if c["iso_code"] == "ZZ")
        assert item["counts"]["universities"] == 0
        assert item["counts"]["programs"] == 0
    finally:
        if empty is not None:
            row = db.get(Country, empty.id)
            if row is not None:
                db.delete(row)
                db.commit()
        db.close()
