"""destination hierarchy, university regions, chunk provenance

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-11-26 10:05:00.000000

Four additive changes, plus the approved destination bootstrap.

1. ``countries`` becomes a two-level tree. ``kind`` is ``COUNTRY`` or ``REGION``;
   ``parent_country_id`` points a region at the country it belongs to; ``iso_code`` holds an
   ISO 3166-1 alpha-2 code for a country (``GB``) and an ISO 3166-2 subdivision code for a
   region (``GB-SCT``). ``is_study_destination`` marks a row that may be offered to a student.
   Scotland is therefore a region inside the United Kingdom - not a sovereign country with a
   currency and a visa regime of its own.

2. ``universities.region_id`` links an institution to a region *without* moving it out of its
   country: ``country_id`` stays ``GB``, so a United Kingdom query keeps returning Scotland's
   universities while a Scotland query excludes the rest of the UK.

3. ``knowledge_chunks.provenance`` records that a passage came from demonstration data, so a
   well-written placeholder paragraph can never be quoted as an official statement.

4. The 17 approved destinations and the Scotland region are inserted idempotently. Nothing is
   invented: a destination with no catalogue records is stored with no fees, and its status
   says so.

The bootstrap data is deliberately literal here rather than imported from
``app.services.destinations``: a migration must describe the data it existed for, and must not
change meaning when product code is edited later.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: iso2, name, grouping, flag, currency. The ten destinations not already present in the demo
#: seed are inserted if missing; the whole approved set is asserted by the tests.
APPROVED_COUNTRIES = (
    ("US", "United States", "North America", "\U0001F1FA\U0001F1F8", "USD"),
    ("GB", "United Kingdom", "Europe", "\U0001F1EC\U0001F1E7", "GBP"),
    ("CA", "Canada", "North America", "\U0001F1E8\U0001F1E6", "CAD"),
    ("AU", "Australia", "Oceania", "\U0001F1E6\U0001F1FA", "AUD"),
    ("IE", "Ireland", "Europe", "\U0001F1EE\U0001F1EA", "EUR"),
    ("DE", "Germany", "Europe", "\U0001F1E9\U0001F1EA", "EUR"),
    ("MY", "Malaysia", "Asia", "\U0001F1F2\U0001F1FE", "MYR"),
    ("HU", "Hungary", "Europe", "\U0001F1ED\U0001F1FA", "HUF"),
    ("CY", "Cyprus", "Europe", "\U0001F1E8\U0001F1FE", "EUR"),
    ("AE", "United Arab Emirates", "Middle East", "\U0001F1E6\U0001F1EA", "AED"),
    ("KR", "South Korea", "Asia", "\U0001F1F0\U0001F1F7", "KRW"),
    ("NZ", "New Zealand", "Oceania", "\U0001F1F3\U0001F1FF", "NZD"),
    ("SA", "Saudi Arabia", "Middle East", "\U0001F1F8\U0001F1E6", "SAR"),
    ("JP", "Japan", "Asia", "\U0001F1EF\U0001F1F5", "JPY"),
    ("IT", "Italy", "Europe", "\U0001F1EE\U0001F1F9", "EUR"),
    ("FI", "Finland", "Europe", "\U0001F1EB\U0001F1EE", "EUR"),
    ("ES", "Spain", "Europe", "\U0001F1EA\U0001F1F8", "EUR"),
)

#: Cities that make an existing institution Scottish. Used only to set ``region_id``; no
#: institution is created, renamed or moved between countries.
SCOTLAND_CITIES = (
    "glasgow", "edinburgh", "aberdeen", "dundee", "stirling", "st andrews", "saint andrews",
    "inverness", "paisley", "perth", "dunfermline", "falkirk", "livingston", "hamilton",
)


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _add_fk(name: str, table: str, ref_table: str, local: list, remote: list) -> None:
    """Add a foreign key where the dialect can, and skip it where it cannot.

    SQLite refuses ALTER of constraints outside batch mode, and a batch copy-and-move would
    rebuild ``countries`` - a table other tables point at - to gain a constraint that only
    documents what the application already enforces. Production (PostgreSQL) gets the real
    constraint. The model declares it either way, so ``create_all`` databases have it too.
    """
    if _is_sqlite():
        return
    op.create_foreign_key(name, table, ref_table, local, remote)


def _drop_fk(name: str, table: str) -> None:
    if _is_sqlite():
        return
    op.drop_constraint(name, table, type_="foreignkey")


def upgrade() -> None:
    bind = op.get_bind()
    sqlite = _is_sqlite()

    # ---- 1. countries as a destination tree --------------------------------
    op.add_column("countries", sa.Column("iso_code", sa.String(length=8), nullable=True))
    op.add_column("countries", sa.Column("kind", sa.String(length=20), nullable=False,
                                         server_default="COUNTRY"))
    op.add_column("countries", sa.Column("parent_country_id", sa.Integer(), nullable=True))
    op.add_column("countries", sa.Column(
        "is_study_destination", sa.Boolean(), nullable=False,
        server_default=sa.text("1") if sqlite else sa.text("true"),
    ))
    op.create_index("ix_countries_iso_code", "countries", ["iso_code"], unique=True)
    op.create_index("ix_countries_kind", "countries", ["kind"])
    op.create_index("ix_countries_parent_country_id", "countries", ["parent_country_id"])
    _add_fk("fk_countries_parent_country_id", "countries", "countries",
            ["parent_country_id"], ["id"])

    # Every existing row is a sovereign country whose ISO 3166-1 code is already in `iso2`.
    bind.execute(sa.text(
        "UPDATE countries SET iso_code = UPPER(iso2) "
        "WHERE iso_code IS NULL AND iso2 IS NOT NULL"
    ))

    # ---- 2. regions on institutions ----------------------------------------
    op.add_column("universities", sa.Column("region_id", sa.Integer(), nullable=True))
    op.create_index("ix_universities_region_id", "universities", ["region_id"])
    _add_fk("fk_universities_region_id", "universities", "countries", ["region_id"], ["id"])

    # ---- 3. provenance on retrieval passages -------------------------------
    op.add_column("knowledge_chunks", sa.Column("provenance", sa.String(length=20), nullable=True))
    op.create_index("ix_knowledge_chunks_provenance", "knowledge_chunks", ["provenance"])

    # ---- 4. approved destination bootstrap ---------------------------------
    _insert_destinations(bind, sqlite)
    _attach_scottish_institutions(bind)


def _insert_destinations(bind, sqlite: bool) -> None:
    """Insert any approved destination that is absent. Idempotent by ISO code or ISO 3166-1."""
    json_empty = "CAST(:empty AS jsonb)" if not sqlite else ":empty"

    def exists(iso2: str) -> bool:
        row = bind.execute(
            # Separate parameters on purpose: reusing one bind for varchar(8) and varchar(2)
            # makes PostgreSQL deduce two different types for it and refuse the statement.
            sa.text("SELECT id FROM countries WHERE iso_code = CAST(:iso8 AS VARCHAR(8)) "
                    "OR iso2 = CAST(:iso2 AS VARCHAR(2)) LIMIT 1"),
            {"iso8": iso2, "iso2": iso2},
        ).first()
        return row is not None

    def slugify(name: str) -> str:
        out = []
        for ch in name.lower():
            if ch.isalnum():
                out.append(ch)
            elif out and out[-1] != "-":
                out.append("-")
        return "".join(out).strip("-")

    for iso2, name, region, flag, currency in APPROVED_COUNTRIES:
        if exists(iso2):
            bind.execute(sa.text(
                "UPDATE countries SET iso_code = CAST(:iso8 AS VARCHAR(8)) "
                "WHERE (iso_code IS NULL) AND iso2 = CAST(:iso2 AS VARCHAR(2))"
            ), {"iso8": iso2, "iso2": iso2})
            continue
        bind.execute(sa.text(
            f"""
            INSERT INTO countries
                (name, slug, iso2, iso_code, kind, parent_country_id, is_study_destination,
                 flag_emoji, region, status, is_featured, priority, currency, tagline, summary,
                 why_study, popular_subjects, intakes, visa_resources, faqs,
                 accent_color, noindex, application_available, created_at, updated_at)
            SELECT CAST(:name AS VARCHAR(120)), CAST(:slug AS VARCHAR(140)),
                   CAST(:iso2 AS VARCHAR(2)), CAST(:iso8 AS VARCHAR(8)), 'COUNTRY', NULL,
                   {"1" if sqlite else "true"},
                   :flag, :region, 'COMING_SOON',
                   {"0" if sqlite else "false"},
                   :priority, :currency, :tagline, :summary,
                   {json_empty}, {json_empty}, {json_empty}, {json_empty}, {json_empty},
                   '#1d4ed8', {"0" if sqlite else "false"}, {"1" if sqlite else "true"},
                   CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            WHERE NOT EXISTS (SELECT 1 FROM countries
                              WHERE iso_code = CAST(:iso8 AS VARCHAR(8))
                                 OR iso2 = CAST(:iso2 AS VARCHAR(2)))
            """
        ), {
            "name": name, "slug": slugify(name), "iso8": iso2, "iso2": iso2,
            "flag": flag, "region": region,
            "priority": 400, "currency": currency,
            "tagline": f"Study destinations in {name} - records are added per university and programme.",
            "summary": (
                f"A {name} destination record means the country is supported by our counselling "
                f"process. It does not mean every university or programme has been verified yet: "
                f"each record carries its own source and verification date."
            ),
            "empty": "[]",
        })

    # Scotland: a REGION inside the United Kingdom, never a sovereign country.
    uk = bind.execute(sa.text(
        "SELECT id FROM countries WHERE iso2 = 'GB' OR iso_code = 'GB' LIMIT 1"
    )).first()
    if uk is None:
        return
    already = bind.execute(sa.text(
        "SELECT id FROM countries WHERE iso_code = 'GB-SCT' LIMIT 1"
    )).first()
    if already is not None:
        return
    bind.execute(sa.text(
        f"""
        INSERT INTO countries
            (name, slug, iso2, iso_code, kind, parent_country_id, is_study_destination,
             flag_emoji, region, status, is_featured, priority, currency, tagline, summary,
             why_study, popular_subjects, intakes, visa_resources, faqs,
             accent_color, noindex, application_available, created_at, updated_at)
        SELECT 'Scotland', 'scotland', NULL, 'GB-SCT', 'REGION', :parent,
               {"1" if sqlite else "true"},
               :flag, 'Europe', 'ACTIVE',
               {"0" if sqlite else "false"},
               350, 'GBP',
               'Scotland is a study region within the United Kingdom.',
               'Scotland is part of the United Kingdom: the same student visa, currency and post-study rules apply. It is listed separately so universities and programmes in Scotland can be found and compared on their own.',
               {json_empty}, {json_empty}, {json_empty}, {json_empty}, {json_empty},
               '#1d4ed8', {"0" if sqlite else "false"}, {"1" if sqlite else "true"},
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (SELECT 1 FROM countries WHERE iso_code = 'GB-SCT')
        """
    ), {"parent": uk[0], "flag": "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F",
        "empty": "[]"})


def _attach_scottish_institutions(bind) -> None:
    """Point institutions in Scottish cities at the region row, without changing their country."""
    region = bind.execute(sa.text(
        "SELECT id FROM countries WHERE iso_code = 'GB-SCT' LIMIT 1"
    )).first()
    if region is None:
        return
    for city in SCOTLAND_CITIES:
        bind.execute(
            sa.text(
                "UPDATE universities SET region_id = :rid "
                "WHERE region_id IS NULL AND LOWER(TRIM(city)) = :city"
            ),
            {"rid": region[0], "city": city},
        )


def downgrade() -> None:
    """Drop what this revision added. Rows and links it created are removed first.

    The destination rows are deleted only when nothing was attached to them, so a downgrade
    can never cascade away a university or a programme that an operator imported later.
    """
    bind = op.get_bind()

    attached = bind.execute(sa.text(
        "SELECT COUNT(*) FROM universities WHERE region_id IS NOT NULL"
    )).scalar()
    if attached:
        bind.execute(sa.text("UPDATE universities SET region_id = NULL"))

    for iso2 in ("SA", "IT", "ES"):
        bind.execute(sa.text(
            "DELETE FROM countries WHERE iso_code = :iso AND iso2 = :iso "
            "AND NOT EXISTS (SELECT 1 FROM universities u WHERE u.country_id = countries.id) "
            "AND NOT EXISTS (SELECT 1 FROM scholarships s WHERE s.country_id = countries.id)"
        ), {"iso": iso2})
    bind.execute(sa.text(
        "DELETE FROM countries WHERE iso_code = 'GB-SCT' "
        "AND NOT EXISTS (SELECT 1 FROM universities u WHERE u.region_id = countries.id)"
    ))

    op.drop_index("ix_knowledge_chunks_provenance", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "provenance")

    _drop_fk("fk_universities_region_id", "universities")
    op.drop_index("ix_universities_region_id", table_name="universities")
    op.drop_column("universities", "region_id")

    _drop_fk("fk_countries_parent_country_id", "countries")
    op.drop_index("ix_countries_parent_country_id", table_name="countries")
    op.drop_index("ix_countries_kind", table_name="countries")
    op.drop_index("ix_countries_iso_code", table_name="countries")
    op.drop_column("countries", "is_study_destination")
    op.drop_column("countries", "parent_country_id")
    op.drop_column("countries", "kind")
    op.drop_column("countries", "iso_code")
