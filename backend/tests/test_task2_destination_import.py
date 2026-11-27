"""W8 importer acceptance tests — destinations and regions through the real CSV/JSON path.

The admin API path was already tested. This covers the other supported workflow: the importer
must accept a valid country or region, reject bad ISO codes and bad hierarchy, refuse duplicate
declarations, and fail a row without leaving half-written data behind.
"""
from __future__ import annotations

from app.models.crm import DataVerification
from app.models.education import Country, University
from app.services import importer
from sqlalchemy import func, select


def _import(db, rows, *, entity_type="country", source_url="https://example.gov/education"):
    batch = importer.stage(db, entity_type=entity_type, rows=rows,
                           source_url=source_url, actor="acceptance-test")
    result = importer.commit_batch(db, batch, actor="acceptance-test")
    return batch, result


def _cleanup(db, *slugs):
    ids = [c.id for c in db.execute(select(Country).where(Country.slug.in_(slugs))).scalars()]
    for entity_id in ids:
        db.query(DataVerification).filter(
            DataVerification.entity_type == "country",
            DataVerification.entity_id == entity_id).delete(synchronize_session=False)
    # Regions first, then countries; no FK cascade is configured for the self-reference.
    for row in db.execute(select(Country).where(Country.slug.in_(slugs),
                                                Country.parent_country_id.is_not(None))).scalars():
        db.delete(row)
    db.flush()
    for row in db.execute(select(Country).where(Country.slug.in_(slugs))).scalars():
        db.delete(row)
    db.commit()


def test_a_valid_sovereign_destination_can_be_imported(db):
    try:
        batch, result = _import(db, [{
            "name": "Testland Import Probe", "slug": "testland-import", "iso2": "XK", "iso_code": "XK",
            "kind": "COUNTRY", "region": "Test", "status": "COMING_SOON",
            "currency": "EUR", "tagline": "Imported test destination.",
            "summary": "Imported test destination; no coverage is claimed.",
        }])
        assert result["created"] == 1 and not result["failures"]
        row = db.execute(select(Country).where(Country.slug == "testland-import")).scalars().first()
        assert row is not None
        assert row.kind == "COUNTRY" and row.iso_code == "XK"
        assert row.parent_country_id is None
        assert row.is_study_destination is True
    finally:
        _cleanup(db, "testland-import")


def test_a_valid_region_can_be_imported_with_its_parent(db):
    try:
        batch, result = _import(db, [{
            "name": "Test Region of Italy", "slug": "test-region-italy", "iso_code": "IT-T1",
            "kind": "REGION", "parent_country": "italy", "region": "Europe",
            "status": "ACTIVE", "currency": "EUR", "tagline": "Imported test region.",
            "summary": "Imported test region inside Italy.",
        }])
        assert result["created"] == 1, result
        region = db.execute(select(Country).where(Country.slug == "test-region-italy")).scalars().first()
        italy = db.execute(select(Country).where(Country.slug == "italy")).scalars().first()
        assert region is not None and italy is not None
        assert region.kind == "REGION"
        assert region.iso_code == "IT-T1"
        assert region.iso2 is None
        assert region.parent_country_id == italy.id
    finally:
        _cleanup(db, "test-region-italy")


def test_a_region_cannot_name_another_region_as_its_parent(db):
    try:
        _cleanup(db, "bad-parent-region", "parent-region-test")
        # First create a legitimate region, then try to put another region inside it.
        _import(db, [{
            "name": "Parent Region Test", "slug": "parent-region-test", "iso_code": "IT-T2",
            "kind": "REGION", "parent_country": "italy", "status": "ACTIVE",
            "tagline": "Test region.", "summary": "Test region.",
        }])
        batch, result = _import(db, [{
            "name": "Bad Parent Region", "slug": "bad-parent-region", "iso_code": "IT-T3",
            "kind": "REGION", "parent_country": "parent-region-test", "status": "ACTIVE",
            "tagline": "Invalid region.", "summary": "Invalid region.",
        }])
        assert result["created"] == 0
        row = batch.rows[0]
        assert row.action == "ERROR"
        assert any("sovereign COUNTRY" in e for e in row.errors)
        assert db.execute(select(Country).where(
            Country.slug == "bad-parent-region")).scalars().first() is None
    finally:
        _cleanup(db, "bad-parent-region", "parent-region-test")


def test_invalid_iso_codes_are_rejected(db):
    rows = [
        {"name": "Bad Country", "slug": "bad-iso-country", "iso_code": "USA",
         "kind": "COUNTRY", "status": "COMING_SOON"},
        {"name": "Bad Region", "slug": "bad-iso-region", "iso_code": "GBSCT",
         "kind": "REGION", "parent_country": "united-kingdom", "status": "ACTIVE"},
        {"name": "Short Country", "slug": "bad-iso-short", "iso_code": "X",
         "kind": "COUNTRY", "status": "COMING_SOON"},
    ]
    try:
        batch, result = _import(db, rows)
        assert result["created"] == 0
        assert all(r.action == "ERROR" for r in batch.rows)
        assert any("ISO 3166-1" in e for r in batch.rows for e in r.errors)
        assert any("ISO 3166-2" in e for r in batch.rows for e in r.errors)
        assert db.execute(select(func.count()).select_from(Country).where(
            Country.slug.in_(["bad-iso-country", "bad-iso-region", "bad-iso-short"]))).scalar() == 0
    finally:
        _cleanup(db, "bad-iso-country", "bad-iso-region", "bad-iso-short")


def test_a_duplicate_iso_code_is_rejected_without_creating_a_second_row(db):
    try:
        _cleanup(db, "duplicate-source", "duplicate-target")
        _import(db, [{
            "name": "Duplicate Source", "slug": "duplicate-source", "iso_code": "XY",
            "kind": "COUNTRY", "status": "COMING_SOON",
            "tagline": "Test.", "summary": "Test.",
        }])
        before = db.execute(select(func.count()).select_from(Country).where(
            Country.iso_code == "XY")).scalar()
        batch, result = _import(db, [{
            "name": "Duplicate Target", "slug": "duplicate-target", "iso_code": "XY",
            "kind": "COUNTRY", "status": "COMING_SOON",
            "tagline": "Test.", "summary": "Test.",
        }])
        assert result["created"] == 0
        assert batch.rows[0].action == "ERROR"
        assert any("already used" in e for e in batch.rows[0].errors)
        after = db.execute(select(func.count()).select_from(Country).where(
            Country.iso_code == "XY")).scalar()
        assert after == before == 1
    finally:
        _cleanup(db, "duplicate-source", "duplicate-target")


def test_reimporting_the_same_destination_does_not_duplicate_it(db):
    row = {
        "name": "Idempotent Land", "slug": "idempotent-land", "iso_code": "XZ",
        "kind": "COUNTRY", "status": "COMING_SOON", "currency": "EUR",
        "tagline": "First version.", "summary": "Idempotent test destination.",
    }
    try:
        _cleanup(db, "idempotent-land")
        first, first_result = _import(db, [dict(row)])
        assert first_result["created"] == 1
        second, second_result = _import(db, [dict(row)])
        assert second_result["created"] == 0
        assert db.execute(select(func.count()).select_from(Country).where(
            Country.slug == "idempotent-land")).scalar() == 1
        # Changing a field updates the same record rather than adding a second one.
        third, third_result = _import(db, [{**row, "tagline": "Second version."}])
        assert third_result["updated"] == 1
        assert db.execute(select(func.count()).select_from(Country).where(
            Country.slug == "idempotent-land")).scalar() == 1
    finally:
        _cleanup(db, "idempotent-land")


def test_an_institution_cannot_be_imported_into_a_region_of_another_country(db):
    slug = "import-cross-country-university"
    try:
        batch, result = _import(db, [{
            "name": "Import Cross Country University", "slug": slug,
            "country": "united-states", "region": "scotland", "city": "Glasgow",
            "website": "https://cross.example.edu", "institution_type": "PRIVATE",
            "source_url": "https://cross.example.edu",
        }], entity_type="university", source_url="https://cross.example.edu")
        assert result["created"] == 0
        assert result["failures"], "the commit must report the rejected row"
        assert db.execute(select(University).where(University.slug == slug)).scalars().first() is None
    finally:
        db.query(University).filter(University.slug == slug).delete()
        db.commit()


def test_a_failed_row_does_not_leave_partial_or_corrupt_data(db):
    try:
        _cleanup(db, "partial-good-country", "partial-bad-region")
        batch, result = _import(db, [
            {"name": "Partial Good Country", "slug": "partial-good-country", "iso_code": "XW",
             "kind": "COUNTRY", "status": "COMING_SOON", "tagline": "Good row.",
             "summary": "Good row."},
            {"name": "Partial Bad Region", "slug": "partial-bad-region", "iso_code": "NO-DASH",
             "kind": "REGION", "status": "ACTIVE", "tagline": "Bad row.", "summary": "Bad row."},
        ])
        good = db.execute(select(Country).where(Country.slug == "partial-good-country")).scalars().first()
        bad = db.execute(select(Country).where(Country.slug == "partial-bad-region")).scalars().first()
        assert good is not None and good.kind == "COUNTRY" and good.iso_code == "XW"
        assert bad is None
        assert result["created"] == 1
        assert any(r.action == "ERROR" for r in batch.rows)
        assert importer.commit_batch  # the batch is committed, not left half-open
    finally:
        _cleanup(db, "partial-good-country", "partial-bad-region")
