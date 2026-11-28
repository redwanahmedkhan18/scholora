"""C2-11 — an invalid governed write must be a 422, not a 500, and not a catch-all.

The governed path (``PUT /admin/programs/{id}``) applies an edit, re-registers the field, and writes
the change row inside the service — each step calls ``flush()``. The invalid-value mapping only
wrapped the ``commit()`` that followed, so PostgreSQL refusing ``"not-a-number"`` for a numeric
column escaped as a 500. Nothing was ever persisted (the transaction rolls back), so only the answer
was wrong: a client error reported as a server fault, which hides real faults among mistyped ones.

What must hold:

* the real PostgreSQL route answers 422 for wrong-typed input, whatever step raises;
* nothing is written: the value, the register, the change row and the audit entry all stay as they
  were, and a later valid edit still works;
* a genuine infrastructure failure (the database going away) or an integrity failure is **not**
  reported as client input — those keep propagating as server errors.

The mapping is deliberately narrow (see ``admin._client_value_error``), and the tests below check
both directions: what it converts, and what it refuses to convert.
"""
from __future__ import annotations

import os

import pytest
import sqlalchemy.exc as sa_exc
from app.api.v1.routers.admin import _client_value_error, _refuse_client_values
from app.core.security import create_access_token
from app.models.content import Faq
from app.models.crm import AuditLog, CatalogueChange, DataVerification, User
from app.models.education import Country, Program, University
from fastapi import HTTPException
from sqlalchemy import select

from skip_policy import skip_pg_only
from task2_helpers import new_session, utc

OFFICIAL = "TIER_1_OFFICIAL_UNIVERSITY"
SOURCE = "https://c2-11.example.edu/fees"


def _staff(role: str = "DATA_MANAGER") -> dict:
    db = new_session()
    try:
        user = User(email=f"c211-{os.urandom(4).hex()}@scholara.test",
                    full_name=f"C2-11 {role}", password_hash="x", role=role, is_active=True)
        db.add(user)
        db.commit()
        return {"Authorization": f"Bearer {create_access_token(str(user.id), role)}",
                "email": user.email}
    finally:
        db.close()


def _parse_error(message: str, *, sqlstate: str | None) -> sa_exc.ProgrammingError:
    """A driver programming error with (server) or without (client-side adaptation) a SQLSTATE."""
    orig = Exception(message)
    orig.sqlstate = sqlstate            # type: ignore[attr-defined]
    return sa_exc.ProgrammingError("INSERT INTO t (v) VALUES (%s)", {}, orig)


def _statement_error(orig: BaseException) -> sa_exc.StatementError:
    """A bind failure as SQLAlchemy raises it, carrying the exception that caused it."""
    return sa_exc.StatementError("could not bind parameter", "INSERT INTO t (v) VALUES (%s)",
                                 {}, orig)


def _data_error(message: str = "invalid input syntax for type double precision") -> sa_exc.DataError:
    return sa_exc.DataError("UPDATE t SET v = %s", {}, Exception(message))


def _program(db, *, confidence: str = "NEEDS_REVIEW") -> tuple[University, Program]:
    country = db.execute(select(Country).where(Country.iso2 == "US")).scalars().first()
    slug = f"c211-{os.urandom(4).hex()}"
    university = University(name=f"C2-11 {slug}", slug=slug, country_id=country.id,
                            city="Testville", confidence="NEEDS_REVIEW", source_type=OFFICIAL,
                            source_url=SOURCE, retrieved_at=utc(-1))
    db.add(university)
    db.flush()
    program = Program(university_id=university.id, name=f"C2-11 Programme {slug}",
                      slug=f"p-{slug}", degree_level="MASTER", currency="USD",
                      tuition_annual=100.0, duration_months=24, overview="C2-11 fixture.",
                      is_published=True, confidence=confidence, source_type=OFFICIAL,
                      source_url=SOURCE, retrieved_at=utc(-1), review_due_at=utc(90))
    db.add(program)
    db.flush()
    return university, program


def _drop(db, university, program):
    for row in db.execute(
        select(DataVerification).where(
            DataVerification.entity_type == "program", DataVerification.entity_id == program.id)
    ).scalars():
        db.delete(row)
    for row in db.execute(
        select(CatalogueChange).where(
            CatalogueChange.entity_type == "program", CatalogueChange.entity_id == program.id)
    ).scalars():
        db.delete(row)
    db.commit()
    db.delete(program)
    db.delete(university)
    db.commit()


# --------------------------------------------------------------- what the mapping converts
def test_client_value_errors_are_recognised():
    assert _client_value_error(_data_error()) is True
    assert _client_value_error(_parse_error("cannot adapt type 'dict' using placeholder '%s'",
                                            sqlstate=None)) is True
    assert _client_value_error(_statement_error(TypeError("bad value"))) is True


def test_infrastructure_and_integrity_errors_are_not_recognised():
    """These are defects or conflicts. Answering 422 would hide them."""
    assert _client_value_error(sa_exc.OperationalError("SELECT 1", {}, Exception("gone"))) is False
    assert _client_value_error(sa_exc.IntegrityError("INSERT", {}, Exception("duplicate"))) is False
    # A programming error that came back *from* the server carries a SQLSTATE: our SQL, not the
    # caller's value.
    assert _client_value_error(_parse_error("syntax error at or near", sqlstate="42601")) is False
    assert _client_value_error(_statement_error(
        sa_exc.OperationalError("SELECT 1", {}, Exception("gone")))) is False


def test_refusal_rolls_back_and_answers_422():
    class FakeSession:
        def __init__(self):
            self.rolled_back = False

        def rollback(self):
            self.rolled_back = True

    session = FakeSession()

    def failing():
        raise _data_error()

    with pytest.raises(HTTPException) as raised:
        _refuse_client_values(session, "programs", failing)
    assert raised.value.status_code == 422
    assert "not valid for their field" in raised.value.detail
    assert session.rolled_back is True
    # Nothing about the schema, the column or the SQL may leak into the answer.
    assert "double precision" not in raised.value.detail
    assert "UPDATE" not in raised.value.detail


def test_refusal_does_not_swallow_infrastructure_failures():
    class FakeSession:
        def __init__(self):
            self.rolled_back = False

        def rollback(self):
            self.rolled_back = True

    session = FakeSession()

    def failing():
        raise sa_exc.OperationalError("SELECT 1", {}, Exception("server closed the connection"))

    with pytest.raises(sa_exc.OperationalError):
        _refuse_client_values(session, "programs", failing)
    assert session.rolled_back is False, "an infrastructure failure is not this handler's to undo"


# --------------------------------------------------------------- PostgreSQL: the real route
@pytest.mark.pg
@pytest.mark.parametrize("payload", [
    {"tuition_annual": "not-a-number"},
    {"duration_months": "twelve"},
    {"is_published": "yes"},
    {"tuition_annual": {"amount": 1}},
])
def test_pg_governed_endpoint_answers_422_and_writes_nothing(client, api, payload):
    db = new_session()
    try:
        if db.get_bind().dialect.name != "postgresql":
            skip_pg_only("this is the PostgreSQL behaviour that produced the 500")
        headers = _staff()
        university, program = _program(db)
        db.commit()
        program_id = program.id
        before_claims = db.scalar(
            select(DataVerification).where(
                DataVerification.entity_type == "program",
                DataVerification.entity_id == program_id).limit(1))
        before_changes = db.scalar(
            select(CatalogueChange).where(
                CatalogueChange.entity_type == "program",
                CatalogueChange.entity_id == program_id).limit(1))
        before_audit = db.scalar(select(AuditLog).where(
            AuditLog.entity_type == "programs", AuditLog.entity_id == program_id))

        response = client.put(f"{api}/admin/programs/{program_id}", headers=headers, json=payload)
        assert response.status_code == 422, response.text
        assert "nothing was saved" in response.json()["detail"]
        # No shape of the database is disclosed.
        assert "double precision" not in response.json()["detail"]
        assert "character varying" not in response.json()["detail"]

        # Nothing was written by the refused request.
        db.expire_all()
        assert db.get(Program, program_id).tuition_annual == 100.0
        assert db.get(Program, program_id).duration_months == 24
        assert db.get(Program, program_id).overview == "C2-11 fixture."
        assert before_claims is None and db.scalar(
            select(DataVerification).where(
                DataVerification.entity_type == "program",
                DataVerification.entity_id == program_id).limit(1)) is None
        assert before_changes is None and db.scalar(
            select(CatalogueChange).where(
                CatalogueChange.entity_type == "program",
                CatalogueChange.entity_id == program_id).limit(1)) is None
        assert db.scalar(select(AuditLog).where(
            AuditLog.entity_type == "programs", AuditLog.entity_id == program_id)) is None \
            or before_audit is not None
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


@pytest.mark.pg
def test_pg_valid_governed_edit_still_works_after_a_refusal(client, api):
    """The refusal must not damage the session or the governed workflow."""
    db = new_session()
    try:
        if db.get_bind().dialect.name != "postgresql":
            skip_pg_only("this is the PostgreSQL behaviour that produced the 500")
        headers = _staff()
        university, program = _program(db)
        db.commit()
        program_id = program.id

        refused = client.put(f"{api}/admin/programs/{program_id}", headers=headers,
                             json={"tuition_annual": "not-a-number"})
        assert refused.status_code == 422

        accepted = client.put(f"{api}/admin/programs/{program_id}", headers=headers,
                              json={"tuition_annual": 250.0})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["tuition_annual"] == 250.0

        db.expire_all()
        assert db.get(Program, program_id).tuition_annual == 250.0
        # The governed workflow is intact: the edit is registered as a claim and a change.
        claim = db.scalar(select(DataVerification).where(
            DataVerification.entity_type == "program",
            DataVerification.entity_id == program_id,
            DataVerification.field_name == "tuition_annual"))
        change = db.scalar(select(CatalogueChange).where(
            CatalogueChange.entity_type == "program",
            CatalogueChange.entity_id == program_id))
        assert claim is not None and claim.status == "NEEDS_REVIEW"
        assert change is not None and change.field_name == "tuition_annual"
        assert db.get(Program, program_id).confidence == "NEEDS_REVIEW"
        audit = db.scalar(select(AuditLog).where(
            AuditLog.entity_type == "programs", AuditLog.entity_id == program_id,
            AuditLog.action == "UPDATE"))
        assert audit is not None
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


@pytest.mark.pg
def test_pg_non_governed_endpoint_keeps_its_behaviour(client, api):
    db = new_session()
    try:
        if db.get_bind().dialect.name != "postgresql":
            skip_pg_only("this is the PostgreSQL behaviour that produced the 500")
        headers = _staff()
        faq = db.scalar(select(Faq))
        if faq is None:
            skip_pg_only("the seeded catalogue has no FAQ to edit")
        response = client.put(f"{api}/admin/faqs/{faq.id}", headers=headers,
                              json={"position": "not-an-int"})
        assert response.status_code == 422, response.text
    finally:
        db.rollback()
        db.close()


def test_route_does_not_convert_an_infrastructure_failure(monkeypatch, client, api):
    """A database outage through the governed route is a 503, never a 422."""
    db = new_session()
    try:
        headers = _staff()
        university, program = _program(db)
        db.commit()
        program_id = program.id

        from app.services import catalogue_writes

        def outage(*args, **kwargs):
            raise sa_exc.OperationalError("UPDATE programs SET tuition_annual = %s", {},
                                          Exception("server closed the connection unexpectedly"))

        monkeypatch.setattr(catalogue_writes, "apply_direct_edit", outage)
        response = client.put(f"{api}/admin/programs/{program_id}", headers=headers,
                              json={"tuition_annual": 9.0})
        assert response.status_code == 503, response.text
        assert "not valid for their field" not in response.text
        db.expire_all()
        assert db.get(Program, program_id).tuition_annual == 100.0
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


# --------------------------------------------------- values that could be stored but never served
@pytest.mark.pg
@pytest.mark.parametrize("payload", [
    {"tuition_annual": "NaN"},                       # a string PostgreSQL reads as a float
    {"tuition_annual": 10 ** 400},                   # a number no column can hold
    {"duration_months": 1.5},                        # PostgreSQL would round this to 2
    {"currency": ["USD"]},                           # a list stored as the text "{USD}"
    {"tuition_annual": True},                        # a boolean in a fee column
    {"other_fees": {"lab": float("nan")}},           # non-finite inside a JSON column
])
def test_values_that_could_never_be_served_are_refused(client, api, payload):
    """One accepted ``NaN`` would make every later response for the record fail."""
    db = new_session()
    try:
        if db.get_bind().dialect.name != "postgresql":
            skip_pg_only("this is the PostgreSQL behaviour that stored the value")
        headers = _staff()
        university, program = _program(db)
        db.commit()
        program_id = program.id

        if "other_fees" in payload:
            # A NaN inside a nested value cannot be serialised by the client library, so it is sent
            # as the raw body a hostile caller would write by hand: the server's JSON reader
            # accepts the literal, which is exactly how such a value arrives.
            response = client.put(
                f"{api}/admin/programs/{program_id}",
                headers={**headers, "Content-Type": "application/json"},
                content='{"other_fees": {"lab": NaN}}')
        else:
            response = client.put(f"{api}/admin/programs/{program_id}", headers=headers,
                                  json=payload)
        assert response.status_code == 422, response.text

        db.expire_all()
        row = db.get(Program, program_id)
        assert row.tuition_annual == 100.0
        assert row.duration_months == 24
        assert row.currency == "USD"
        assert "lab" not in str(row.other_fees or {})
        # The record is still readable: nothing unrepresentable reached it.
        assert client.get(f"{api}/admin/programs/{program_id}", headers=headers).status_code == 200
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


@pytest.mark.pg
@pytest.mark.parametrize("payload", [
    {"tuition_annual": "20000"},                     # a numeric string is a normal form value
    {"duration_months": 24.0},                       # an integral float loses nothing
    {"is_published": True},                          # booleans belong in boolean columns
    {"other_fees": {"lab": 250, "library": 100}},    # a mapping belongs in a JSON column
])
def test_legitimate_shapes_are_still_accepted(client, api, payload):
    """The shape rule must refuse corruption without refusing ordinary form values."""
    db = new_session()
    try:
        if db.get_bind().dialect.name != "postgresql":
            skip_pg_only("this is the PostgreSQL behaviour that stored the value")
        headers = _staff()
        university, program = _program(db)
        db.commit()
        response = client.put(f"{api}/admin/programs/{program.id}", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()
