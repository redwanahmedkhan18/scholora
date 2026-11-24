"""
The import pipeline is the one place where bulk data can reach live tables, so these tests
guard the integrity rules rather than the happy path: schemas must match the real columns,
provenance must be mandatory, and a value that contradicts a verified source must never be
applied without a human saying so.
"""
import json

from app.models.education import Country, Program, Scholarship, University
import pytest

from app.services import importer


def test_every_schema_field_exists_on_its_model():
    """The schemas were hand-written once and silently drifted from the models. A field
    that does not exist is accepted at upload and then quietly dropped at commit, which is
    the worst possible failure for a data-integrity tool."""
    for key, spec in importer.SCHEMAS.items():
        columns = {c.name for c in spec["model"].__table__.columns}
        allowed = columns | set((spec.get("resolve") or {}).keys()) | importer.PROVENANCE_FIELDS
        unknown = [f for f in spec["fields"] if f not in allowed]
        assert not unknown, f"{key} declares fields that do not exist: {unknown}"
        for required in spec["required"]:
            assert required in allowed, f"{key} requires unknown field {required}"

        if spec.get("child_of"):
            # Requirements, intakes and rankings are keyed on their parent, so the key is a
            # resolve alias rather than a column, and every discriminator must be real.
            assert spec["key"] in (spec.get("resolve") or {}), \
                f"{key} is a child record but its key is not resolvable to a parent"
            for discriminator in spec.get("discriminators", []):
                assert discriminator in columns, \
                    f"{key} discriminates on '{discriminator}', which is not a column"
        else:
            assert spec["key"] in columns, f"{key} keys on '{spec['key']}', which is not a column"


def test_parse_accepts_csv_and_json():
    csv_rows = importer.parse_payload("name,slug\nAlpha,alpha\nBeta,beta", "x.csv")
    assert [r["slug"] for r in csv_rows] == ["alpha", "beta"]
    json_rows = importer.parse_payload(json.dumps({"items": [{"name": "Alpha", "slug": "alpha"}]}), "x.json")
    assert json_rows[0]["name"] == "Alpha"


def test_a_row_without_any_source_is_rejected(db):
    batch = importer.stage(db, entity_type="country", rows=[{"name": "Nowhere", "slug": "nowhere-test"}],
                           source_url="", actor="test")
    row = batch.rows[0]
    assert row.action == "ERROR"
    assert any("source_url" in e for e in row.errors)
    assert db.query(Country).filter(Country.slug == "nowhere-test").first() is None


def test_unknown_columns_warn_but_do_not_block(db):
    batch = importer.stage(db, entity_type="country",
                           rows=[{"name": "Warnland", "slug": "warnland-test", "nonsense": "x"}],
                           source_url="https://example.gov/edu", actor="test")
    row = batch.rows[0]
    assert row.action == "CREATE" and not row.errors
    assert "ignored unknown columns" in (row.message or "")


def test_staging_writes_nothing_to_live_tables(db):
    before = db.query(University).count()
    importer.stage(db, entity_type="university",
                   rows=[{"name": "Ghost University", "slug": "ghost-university-test",
                          "country": "united-states", "source_url": "https://ghost.edu"}],
                   source_url="https://ghost.edu", actor="test")
    assert db.query(University).count() == before
    assert db.query(University).filter(University.slug == "ghost-university-test").first() is None


def test_a_contradiction_with_verified_data_is_flagged_not_applied(db):
    target = db.query(University).filter(University.slug == "university-of-texas-at-arlington").first()
    original_city, original_conf = target.city, target.confidence
    target.confidence = "VERIFIED"
    target.source_url = target.source_url or "https://www.uta.edu"
    db.commit()
    try:
        batch = importer.stage(db, entity_type="university",
                               rows=[{"name": target.name, "slug": target.slug,
                                      "country": "united-states", "city": "Somewhere Else",
                                      "source_url": "https://third-party.example/uta"}],
                               source_url="https://third-party.example/uta",
                               source_type="TIER_7_THIRD_PARTY", actor="test")
        row = batch.rows[0]
        assert row.action == "CONFLICT"
        assert row.decision == "SKIP", "a conflict must never be pre-approved"
        assert row.diff["city"]["conflict"] is True

        # Committing the batch as-is must leave the verified value alone.
        importer.commit_batch(db, batch, actor="test")
        db.refresh(target)
        assert target.city == original_city
    finally:
        target.confidence = original_conf
        db.commit()


def test_commit_records_one_provenance_row_per_changed_field(db):
    from app.models.crm import DataVerification
    slug = "provenance-probe-university"
    batch = importer.stage(db, entity_type="university",
                           rows=[{"name": "Provenance Probe", "slug": slug,
                                  "country": "united-states", "city": "Testville",
                                  "source_url": "https://probe.example/about",
                                  "confidence": "VERIFIED"}],
                           source_url="https://probe.example/about", actor="tester@example.com")
    result = importer.commit_batch(db, batch, actor="tester@example.com")
    try:
        created = db.query(University).filter(University.slug == slug).first()
        assert created is not None and result["created"] == 1
        records = db.query(DataVerification).filter(
            DataVerification.entity_type == "university",
            DataVerification.entity_id == created.id).all()
        assert records, "committing must leave an audit trail of where each value came from"
        assert all(r.source_url == "https://probe.example/about" for r in records)
        assert {r.field_name for r in records} >= {"name", "city"}
    finally:
        db.query(DataVerification).filter(
            DataVerification.entity_type == "university",
            DataVerification.entity_id == db.query(University).filter(
                University.slug == slug).first().id).delete()
        db.query(University).filter(University.slug == slug).delete()
        db.commit()


def test_a_committed_batch_cannot_be_committed_twice(db):
    batch = importer.stage(db, entity_type="country",
                           rows=[{"name": "Doubleland", "slug": "doubleland-test",
                                  "source_url": "https://example.gov/edu"}],
                           source_url="https://example.gov/edu", actor="test")
    importer.commit_batch(db, batch, actor="test")
    try:
        try:
            importer.commit_batch(db, batch, actor="test")
            raise AssertionError("re-committing should have raised")
        except ValueError as exc:
            assert "already been committed" in str(exc)
    finally:
        db.query(Country).filter(Country.slug == "doubleland-test").delete()
        db.commit()


def test_reference_data_loads_and_is_idempotent(db):
    """The real destinations ship as versioned CSVs replayed through this pipeline, so a
    reseed cannot lose them. Replaying must also be safe: the second pass changes nothing."""
    from app.models.crm import DataVerification
    from app.services import reference_data

    for entry in reference_data.available():
        assert entry["exists"], f"{entry['filename']} is missing from the repository"

    first = reference_data.load_all(db, actor="test")
    assert first["errors"] == 0, first["files"]
    assert first["conflicts"] == 0

    for slug in ("singapore", "saudi-arabia", "spain", "turkiye", "austria"):
        country = db.query(Country).filter(Country.slug == slug).first()
        assert country is not None, f"{slug} was not loaded"
        records = db.query(DataVerification).filter(
            DataVerification.entity_type == "country",
            DataVerification.entity_id == country.id).all()
        assert records, f"{slug} carries no provenance"
        assert all(r.source_url for r in records), f"{slug} has a field with no source"

    # Every imported university must resolve to a country and cite an official source.
    for slug in ("national-university-of-singapore", "middle-east-technical-university",
                 "university-of-vienna", "king-fahd-university-of-petroleum-and-minerals"):
        uni = db.query(University).filter(University.slug == slug).first()
        assert uni is not None and uni.country_id is not None
        assert uni.source_url and uni.source_url.startswith("https://")

    second = reference_data.load_all(db, actor="test")
    assert second["created"] == 0 and second["updated"] == 0, \
        "replaying reference data must be a no-op"


def test_json_columns_are_always_imported_as_lists(db):
    """A string written into a JSON list column survives the database and then crashes the
    page that calls .map() on it. This happened once with Country.why_study; the fix was to
    derive list handling from the model, and this test is what stops it coming back."""
    from app.services.importer import list_fields

    for key, spec in importer.SCHEMAS.items():
        derived = list_fields(spec)
        json_columns = {c.name for c in spec["model"].__table__.columns
                        if "JSON" in str(c.type).upper()}
        assert json_columns <= derived, \
            f"{key}: JSON columns {json_columns - derived} would be imported as plain strings"

    batch = importer.stage(db, entity_type="country",
                           rows=[{"name": "Listland", "slug": "listland-test",
                                  "why_study": "First reason|Second reason|Third reason",
                                  "source_url": "https://example.gov/edu"}],
                           source_url="https://example.gov/edu", actor="test")
    importer.commit_batch(db, batch, actor="test")
    try:
        created = db.query(Country).filter(Country.slug == "listland-test").first()
        assert isinstance(created.why_study, list)
        assert created.why_study == ["First reason", "Second reason", "Third reason"]
    finally:
        db.query(Country).filter(Country.slug == "listland-test").delete()
        db.commit()


def test_loaded_countries_render_safe_list_columns(db):
    """Guards the actual shipped data, not just the mechanism."""
    from app.services import reference_data
    reference_data.load_all(db, actor="test")
    for slug in ("singapore", "saudi-arabia", "spain", "turkiye", "austria"):
        country = db.query(Country).filter(Country.slug == slug).first()
        for field in ("why_study", "popular_subjects", "intakes"):
            value = getattr(country, field)
            assert value is None or isinstance(value, list), \
                f"{slug}.{field} is {type(value).__name__}, which will break the country page"


# ------------------------------------------------ schema / column agreement
def test_coercion_buckets_match_the_real_column_types():
    """
    Declaring a VARCHAR column 'numeric' is not a typo you notice - it corrupts data.
    Ranking columns are strings because published tables say "601-800" and "=42"; GRE
    cells carry "320 (Q160)". When those were listed as numeric the importer stripped the
    non-digits and produced 320160: a score nobody ever published.
    """
    from sqlalchemy import Boolean, Float, Integer, Numeric
    from app.models.base import JSONType
    from sqlalchemy import JSON

    problems = []
    for name, spec in importer.SCHEMAS.items():
        columns = {c.name: c for c in spec["model"].__table__.columns}
        for field in spec.get("numeric", []):
            col = columns.get(field)
            if col is not None and not isinstance(col.type, (Float, Integer, Numeric)):
                problems.append(f"{name}.{field} declared numeric but is {col.type}")
        for field in spec.get("boolean", []):
            col = columns.get(field)
            if col is not None and not isinstance(col.type, Boolean):
                problems.append(f"{name}.{field} declared boolean but is {col.type}")
        for field in spec.get("lists", []):
            col = columns.get(field)
            if col is not None and not isinstance(col.type, (JSON, JSONType)):
                problems.append(f"{name}.{field} declared a list but is {col.type}")
    assert not problems, "schema/column type disagreement:\n  " + "\n  ".join(problems)


def test_every_coercible_field_is_actually_importable():
    """A field in a coercion bucket but absent from 'fields' is silently never read."""
    orphans = [
        f"{name}.{field} ({bucket})"
        for name, spec in importer.SCHEMAS.items()
        for bucket in ("numeric", "boolean", "lists")
        for field in spec.get(bucket, [])
        if field not in spec["fields"]
    ]
    assert not orphans, "declared but unreachable:\n  " + "\n  ".join(orphans)


def test_a_number_is_never_invented_from_a_messy_cell():
    """Composite and range values must be refused outright, not mangled into a number."""
    for junk in ("320 (Q160)", "601-800", "12 to 15", "n/a-ish"):
        with pytest.raises(importer.CoercionError):
            importer._coerce_number("gre_score", junk)


def test_genuinely_decorated_numbers_still_parse():
    """Refusing junk must not mean refusing '€12,000'."""
    assert importer._coerce_number("tuition_annual", "€12,000") == 12000
    assert importer._coerce_number("tuition_annual", "12,000") == 12000
    assert importer._coerce_number("percentage", "25%") == 25
    assert importer._coerce_number("rank", "=42") == 42
    assert importer._coerce_number("ielts_overall", "6.5") == 6.5
    assert importer._coerce_number("tuition_annual", "") is None
    assert importer._coerce_number("tuition_annual", "   ") is None


# ------------------------------------------------- commit isolation & honesty
def test_one_bad_row_does_not_destroy_its_neighbours(db):
    """
    commit_batch used to call db.rollback() when a row raised, which discarded every row
    already flushed in that batch while leaving their counters incremented. A real load
    reported "4 created, 0 errors" and two of the four were simply absent afterwards.
    Each row now runs in its own savepoint and costs only itself.
    """
    rows = [
        {"name": "Savepoint Good One", "slug": "savepoint-good-one", "provider": "T",
         "source_url": "https://example.org/a", "source_type": "TIER_4_OFFICIAL_SCHOLARSHIP_PROVIDER"},
        # Same natural key: nothing exists yet at staging time, so both stage as CREATE
        # and the second one collides on the unique slug at flush.
        {"name": "Savepoint Clash", "slug": "savepoint-good-one", "provider": "T",
         "source_url": "https://example.org/b", "source_type": "TIER_4_OFFICIAL_SCHOLARSHIP_PROVIDER"},
        {"name": "Savepoint Good Two", "slug": "savepoint-good-two", "provider": "T",
         "source_url": "https://example.org/c", "source_type": "TIER_4_OFFICIAL_SCHOLARSHIP_PROVIDER"},
    ]
    batch = importer.stage(db, entity_type="scholarship", rows=rows, actor="test")
    db.commit()
    result = importer.commit_batch(db, batch, actor="test")

    survivors = {s.slug for s in db.query(Scholarship)
                 .filter(Scholarship.slug.like("savepoint-%")).all()}
    assert survivors == {"savepoint-good-one", "savepoint-good-two"}, \
        "a failing row took a good row down with it"
    assert len(result["failures"]) == 1
    assert result["created"] == 2, "the tally must match what the database actually holds"


def test_an_over_long_value_is_caught_before_commit(db):
    """
    A value longer than its column used to surface as a psycopg DataError at commit,
    half way through a batch. It is a staging error now, phrased for a human.
    """
    rows = [{"name": "Too Long Stipend", "slug": "too-long-stipend", "provider": "T",
             "stipend": "x" * 400,
             "source_url": "https://example.org/x",
             "source_type": "TIER_4_OFFICIAL_SCHOLARSHIP_PROVIDER"}]
    batch = importer.stage(db, entity_type="scholarship", rows=rows, actor="test")
    row = batch.rows[0]
    assert row.action == "ERROR"
    assert any("160" in e and "stipend" in e for e in row.errors), row.errors


# -------------------------------------------------------------- faculty provenance
def test_a_faculty_email_without_a_stated_origin_is_refused(db):
    """
    The realistic way a fabricated address enters a dataset is a spreadsheet column
    filled in by pattern - firstname.lastname@university.edu - not malice. An address
    with no email_status is exactly that shape, so it never reaches the database.
    """
    rows = [{"full_name": "Dr Test Person", "slug": "dr-test-person-guess",
             "university": "tu-dresden", "email": "test.person@tu-dresden.de",
             "source_url": "https://tu-dresden.de/", "source_type": "TIER_1_OFFICIAL_UNIVERSITY"}]
    batch = importer.stage(db, entity_type="professor", rows=rows, actor="test")
    row = batch.rows[0]
    assert row.action == "ERROR"
    assert any("email_status" in e for e in row.errors), row.errors


def test_a_faculty_email_must_name_the_page_that_published_it(db):
    rows = [{"full_name": "Dr Test Two", "slug": "dr-test-two",
             "university": "tu-dresden", "email": "two@tu-dresden.de",
             "email_status": "OFFICIAL_UNIVERSITY_EMAIL",
             "source_url": "https://tu-dresden.de/", "source_type": "TIER_1_OFFICIAL_UNIVERSITY"}]
    batch = importer.stage(db, entity_type="professor", rows=rows, actor="test")
    assert batch.rows[0].action == "ERROR"
    assert any("email_source_url" in e for e in batch.rows[0].errors)


def test_unavailable_is_a_legitimate_answer(db):
    """Not publishing an address is normal. Recording that fact must be frictionless."""
    rows = [{"full_name": "Dr Test Three", "slug": "dr-test-three",
             "university": "tu-dresden", "email_status": "UNAVAILABLE",
             "research_interests": "hydrology|flood modelling",
             "source_url": "https://tu-dresden.de/", "source_type": "TIER_1_OFFICIAL_UNIVERSITY"}]
    batch = importer.stage(db, entity_type="professor", rows=rows, actor="test")
    row = batch.rows[0]
    assert row.action != "ERROR", row.errors
    assert row.payload["research_interests"] == ["hydrology", "flood modelling"]
