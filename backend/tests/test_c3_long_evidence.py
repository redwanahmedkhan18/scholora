"""C2-10 — a long catalogue value must be verifiable, not silently un-promotable.

The defect: evidence was stored in a ``VARCHAR(400)`` column and every writer clipped to fit it.
The derivation compares the recorded evidence with the value actually stored, so any claim about a
value longer than 400 characters recorded a *prefix*, never matched, and withheld the record for
ever — while the reviewer was told the field was approved and nothing explained the difference.
There was no way to register evidence that would agree, because the column could not hold it.

What these tests pin down, at both the service and the HTTP layer:

* the recorded evidence is the value it attests to, in full, at every length around the old
  boundary (390 / 400 / 401 / 592 / 775) and for multibyte text;
* a long value can be approved end to end and the record becomes presentable;
* evidence that genuinely disagrees still withholds, and says so;
* a *legacy* clipped row — written under the old schema — is reported as unanswerable
  (``evidence_truncated``) rather than as a disagreement, and the documented recovery works;
* withdrawal and conflicting decisions never leave a false ``VERIFIED`` behind.

The PostgreSQL-only tests check the physical column type and that a value far beyond the old limit
survives storage. SQLite enforces no length, so it cannot prove that part.
"""
from __future__ import annotations

import os

import pytest
from app.core.security import create_access_token
from app.models.crm import CatalogueChange, DataVerification, User
from app.models.education import Country, Program, University
from app.services import catalogue_changes, catalogue_writes, review
from sqlalchemy import select

from skip_policy import skip_pg_only
from task2_helpers import new_session, utc

SOURCE = "https://c2-10.example.edu/official"
OFFICIAL = "TIER_1_OFFICIAL_UNIVERSITY"
LONG = 775


def _staff(role: str = "DATA_MANAGER") -> dict:
    db = new_session()
    try:
        user = User(email=f"c210-{os.urandom(4).hex()}@scholara.test",
                    full_name=f"C2-10 {role}", password_hash="x", role=role, is_active=True)
        db.add(user)
        db.commit()
        return {"Authorization": f"Bearer {create_access_token(str(user.id), role)}",
                "email": user.email}
    finally:
        db.close()


def _pod(db, *, overview: str = "Short overview.", confidence: str = "VERIFIED"):
    """A programme whose register is complete and consistent, so it starts out presentable."""
    country = db.execute(select(Country).where(Country.iso2 == "US")).scalars().first()
    slug = f"c210-{os.urandom(4).hex()}"
    university = University(name=f"C2-10 {slug}", slug=slug, country_id=country.id,
                            city="Testville", confidence="NEEDS_REVIEW",
                            source_type=OFFICIAL, source_url=SOURCE, retrieved_at=utc(-1))
    db.add(university)
    db.flush()
    program = Program(university_id=university.id, name=f"C2-10 Programme {slug}",
                      slug=f"p-{slug}", degree_level="MASTER", award="MSc",
                      discipline="Computer Science", currency="USD", tuition_annual=1.0,
                      duration_months=24, overview=overview, is_published=True,
                      confidence=confidence,
                      verified_by="seed" if confidence == "VERIFIED" else None,
                      source_type=OFFICIAL, source_url=SOURCE, retrieved_at=utc(-1),
                      review_due_at=utc(90))
    db.add(program)
    db.flush()
    return university, program


def _claim(db, program, field: str, value, status: str = "VERIFIED", verified: bool = True):
    row = DataVerification(entity_type="program", entity_id=program.id, field_name=field,
                           field_value=str(value), status=status,
                           verified_by="seed" if verified and status == "VERIFIED" else None,
                           source_url=SOURCE, source_type=OFFICIAL)
    db.add(row)
    db.flush()
    return row


def _consistent_register(db, program):
    """The two claims a fully verified programme would have: fee and length."""
    _claim(db, program, "tuition_annual", program.tuition_annual)
    _claim(db, program, "duration_months", program.duration_months)


def _settle(db, program_id: int) -> None:
    """Decide every open change and approve every pending field, the way a reviewer would."""
    for change in db.execute(
        select(CatalogueChange).where(
            CatalogueChange.entity_type == "program",
            CatalogueChange.entity_id == program_id,
            CatalogueChange.status == "PENDING",
        )
    ).scalars():
        catalogue_changes.decide(db, change, status="APPROVED", actor="dm@c2-10.test",
                                 note="checked")
    db.commit()
    for row in db.execute(
        select(DataVerification).where(
            DataVerification.entity_type == "program",
            DataVerification.entity_id == program_id,
        ).order_by(DataVerification.id.asc())
    ).scalars():
        if (row.status or "").upper() == "NEEDS_REVIEW":
            review.resolve_field(db, row, status="VERIFIED", actor_email="dm@c2-10.test")
            db.commit()


def _drop(db, university, program):
    for row in db.execute(
        select(DataVerification).where(
            DataVerification.entity_type == "program",
            DataVerification.entity_id == program.id,
        )
    ).scalars():
        db.delete(row)
    for row in db.execute(
        select(CatalogueChange).where(
            CatalogueChange.entity_type == "program",
            CatalogueChange.entity_id == program.id,
        )
    ).scalars():
        db.delete(row)
    db.commit()
    db.delete(program)
    db.delete(university)
    db.commit()


# ------------------------------------------------------------------ evidence is stored in full
@pytest.mark.parametrize("length", [390, 400, 401, 592, 775])
def test_evidence_matches_the_stored_value_at_every_length(length):
    """The recorded value is the value, byte for byte — including past the old 400 limit."""
    db = new_session()
    try:
        university, program = _pod(db)
        _consistent_register(db, program)
        db.commit()
        text = ("A" * (length - 1)) + "Z"      # last character differs from any clip

        catalogue_writes.apply_direct_edit(db, model=Program, row=program,
                                           payload={"overview": text}, actor="editor@c2-10.test",
                                           blocked=catalogue_writes.TRUST_FIELDS)
        db.commit()
        program_id = program.id

        # Re-read in a *new* session: this is what was persisted, not what the ORM remembers.
        fresh = new_session()
        try:
            stored = fresh.get(Program, program_id)
            recorded = fresh.execute(
                select(DataVerification).where(
                    DataVerification.entity_type == "program",
                    DataVerification.entity_id == program_id,
                    DataVerification.field_name == "overview",
                )
            ).scalars().first()
            assert stored.overview == text
            assert recorded.field_value == text, "evidence must not be truncated"
            assert len(recorded.field_value) == length
            assert review.values_agree(stored.overview, recorded.field_value)
            assert review.recompute_entity_confidence(fresh, "program", program_id) == "NEEDS_REVIEW"
        finally:
            fresh.close()

        _settle(db, program_id)
        assert review.recompute_entity_confidence(db, "program", program_id) == "VERIFIED"
        db.refresh(program)
        assert program.confidence == "VERIFIED"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_the_reported_592_and_775_character_cases_are_approvable():
    """The two values the audit reported as stuck: both must reach VERIFIED."""
    db = new_session()
    try:
        university, program = _pod(db)
        _consistent_register(db, program)
        db.commit()
        body = ("Programme fees are reviewed annually against the published schedule. " * 12)
        for length in (592, 775):
            text = body[:length].ljust(length, ".")
            catalogue_writes.apply_direct_edit(db, model=Program, row=program,
                                               payload={"overview": text},
                                               actor="editor@c2-10.test",
                                               blocked=catalogue_writes.TRUST_FIELDS)
            db.commit()
            _settle(db, program.id)
            derivation = review.explain_entity_state(db, "program", program.id)
            assert derivation["state"] == "VERIFIED", (
                f"{length} characters must be verifiable, got {derivation}")
            assert derivation["reason"] is None
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_multibyte_evidence_round_trips_exactly():
    """CJK and emoji count as one character each; nothing is clipped or mangled."""
    db = new_session()
    try:
        university, program = _pod(db)
        _consistent_register(db, program)
        db.commit()
        text = ("奖学金说明：学费按年公布，包含实验费用🎓。" * 40)
        assert len(text) > 400
        catalogue_writes.apply_direct_edit(db, model=Program, row=program,
                                           payload={"overview": text}, actor="editor@c2-10.test",
                                           blocked=catalogue_writes.TRUST_FIELDS)
        db.commit()
        fresh = new_session()
        try:
            recorded = fresh.execute(
                select(DataVerification).where(
                    DataVerification.entity_type == "program",
                    DataVerification.entity_id == program.id,
                    DataVerification.field_name == "overview",
                )
            ).scalars().first()
            assert recorded.field_value == text
            assert fresh.get(Program, program.id).overview == text
        finally:
            fresh.close()
        _settle(db, program.id)
        assert review.recompute_entity_confidence(db, "program", program.id) == "VERIFIED"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_formatting_differences_still_agree():
    """The comparison keeps its old tolerance: a formatting-only difference is not a mismatch."""
    db = new_session()
    try:
        university, program = _pod(db)
        program.overview = "Fees include lab access."
        _claim(db, program, "tuition_annual", program.tuition_annual)
        _claim(db, program, "duration_months", program.duration_months)
        _claim(db, program, "overview", "FEES INCLUDE LAB ACCESS")
        db.commit()
        assert review.recompute_entity_confidence(db, "program", program.id) == "VERIFIED"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


# ------------------------------------------------------------------ withholding is explained
def test_genuine_disagreement_withholds_and_says_why():
    """Approving a claim cannot certify a value it does not describe."""
    db = new_session()
    try:
        university, program = _pod(db)
        program.overview = "The published version."
        _claim(db, program, "tuition_annual", program.tuition_annual)
        _claim(db, program, "duration_months", program.duration_months)
        db.commit()
        assert review.recompute_entity_confidence(db, "program", program.id) == "VERIFIED"

        # A claim that records a value the column does not hold, approved by a reviewer.
        wrong = _claim(db, program, "overview", "A different version entirely.",
                       status="NEEDS_REVIEW", verified=False)
        result = review.resolve_field(db, wrong, status="VERIFIED", actor_email="dm@c2-10.test")
        db.commit()

        assert result["field_status"] == "VERIFIED"
        assert result["entity_status"] == "NEEDS_REVIEW"
        assert result["derivation"]["reason"] == "evidence_mismatch"
        assert result["derivation"]["fields"] == ["overview"]

        derivation = review.explain_entity_state(db, "program", program.id)
        assert derivation["state"] == "NEEDS_REVIEW"
        assert derivation["reason"] == "evidence_mismatch"
        assert derivation["explanation"]
        assert derivation["recovery"]
        db.refresh(program)
        assert program.confidence != "VERIFIED"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_legacy_clipped_evidence_is_reported_as_unanswerable_then_recoverable(client, api):
    """A row written under the old schema is a way out, not a dead end."""
    db = new_session()
    try:
        long_text = ("Claim published before full evidence was stored. " * 20)
        assert len(long_text) > 400
        university, program = _pod(db, overview=long_text)
        _claim(db, program, "tuition_annual", program.tuition_annual)
        _claim(db, program, "duration_months", program.duration_months)
        # Exactly what the old writer produced: the first 400 characters.
        legacy = _claim(db, program, "overview", long_text[:400])
        db.commit()
        program_id = program.id

        derivation = review.explain_entity_state(db, "program", program_id)
        assert derivation["state"] == "NEEDS_REVIEW"
        assert derivation["reason"] == "evidence_truncated"
        assert derivation["fields"] == ["overview"]
        assert "register the field value again" in (derivation["recovery"] or "")

        # The documented recovery: register the value again from the current source, decide it.
        headers = _staff()
        created = client.post(f"{api}/admin/verifications", headers=headers,
                              json={"entity_type": "program", "entity_id": program_id,
                                    "field_name": "overview", "field_value": long_text,
                                    "source_url": SOURCE})
        assert created.status_code == 200, created.text
        assert created.json()["field_value"] == long_text
        assert created.json()["status"] == "NEEDS_REVIEW"

        resolved = client.post(f"{api}/admin/verification/{created.json()['id']}/resolve",
                               headers=headers, json={"status": "VERIFIED"})
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["entity_status"] == "VERIFIED"
        assert resolved.json()["derivation"]["reason"] is None

        db.expire_all()
        assert review.recompute_entity_confidence(db, "program", program_id) == "VERIFIED"
        assert legacy.id is not None          # the history is still there
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_exactly_400_characters_is_compared_not_assumed_truncated():
    """The boundary itself is legitimate data: 400 recorded characters can equal the stored value."""
    db = new_session()
    try:
        exact = ("E" * 400)
        university, program = _pod(db, overview=exact)
        _claim(db, program, "tuition_annual", program.tuition_annual)
        _claim(db, program, "duration_months", program.duration_months)
        _claim(db, program, "overview", exact)
        db.commit()
        derivation = review.explain_entity_state(db, "program", program.id)
        assert derivation["state"] == "VERIFIED"
        assert derivation["reason"] is None

        # ...but a 400-character value that is *not* a prefix of the stored value disagrees.
        program.overview = "Z" * 401
        db.commit()
        derived = review.explain_entity_state(db, "program", program.id)
        assert derived["state"] == "NEEDS_REVIEW"
        assert derived["reason"] == "evidence_mismatch"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_a_genuine_400_character_prefix_is_withheld_and_explained(client, api):
    """A4-01: heuristic detection must not claim more certainty than it has.

    A 400-character claim that is a prefix of a longer stored value is *indistinguishable* from a
    clipped legacy record, so it is reported as ``evidence_truncated`` - the system has no
    "confirmed truncation" state. What must hold either way: the record is withheld (never
    promoted), the caller is given an explanation and a recovery, and the supported recovery
    (register the value again) resolves it.
    """
    db = new_session()
    try:
        headers = _staff()
        long_text = "Prefix evidence " * 60
        assert len(long_text) > 400
        university, program = _pod(db, overview=long_text)
        _claim(db, program, "tuition_annual", program.tuition_annual)
        _claim(db, program, "duration_months", program.duration_months)
        prefix_claim = _claim(db, program, "overview", long_text[:400])
        db.commit()
        program_id = program.id

        result = review.resolve_field(db, prefix_claim, status="VERIFIED",
                                      actor_email="dm@c2-10.test")
        db.commit()
        assert result["field_status"] == "VERIFIED"
        assert result["entity_status"] == "NEEDS_REVIEW", "a prefix must never certify the record"
        derivation = result["derivation"]
        assert derivation["reason"] == "evidence_truncated"
        assert derivation["fields"] == ["overview"]
        assert derivation["explanation"] and derivation["recovery"]

        # The recovery the explanation names actually works.
        created = client.post(f"{api}/admin/verifications", headers=headers,
                              json={"entity_type": "program", "entity_id": program_id,
                                    "field_name": "overview", "field_value": long_text,
                                    "source_url": SOURCE})
        assert created.status_code == 200, created.text
        resolved = client.post(f"{api}/admin/verification/{created.json()['id']}/resolve",
                              headers=headers, json={"status": "VERIFIED"})
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["entity_status"] == "VERIFIED"
        db.expire_all()
        assert review.recompute_entity_confidence(db, "program", program_id) == "VERIFIED"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_withdrawal_of_a_long_claim_never_restores_verified():
    db = new_session()
    try:
        text = ("W" * 700)
        university, program = _pod(db, overview=text)
        _claim(db, program, "tuition_annual", program.tuition_annual)
        _claim(db, program, "duration_months", program.duration_months)
        overview_claim = _claim(db, program, "overview", text)
        db.commit()
        assert review.recompute_entity_confidence(db, "program", program.id) == "VERIFIED"

        review.withdraw_field(db, overview_claim, actor_email="dm@c2-10.test",
                              note="the published page changed")
        db.commit()
        db.refresh(program)
        assert program.confidence == "NEEDS_REVIEW"
        derivation = review.explain_entity_state(db, "program", program.id)
        assert derivation["state"] == "NEEDS_REVIEW"
        assert derivation["reason"] == "fields_not_verified"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


# ------------------------------------------------------------------ HTTP journey
def test_long_overview_is_approvable_over_http(client, api):
    """The reported user journey: edit a 775-character overview, decide it, approve the claim."""
    headers = _staff()
    db = new_session()
    try:
        university, program = _pod(db)
        _consistent_register(db, program)
        db.commit()
        program_id = program.id
        text = ("The programme is delivered over twelve months and includes a placement. " * 12)[:LONG]
        assert len(text) == LONG

        edited = client.put(f"{api}/admin/programs/{program_id}", headers=headers,
                            json={"overview": text})
        assert edited.status_code == 200, edited.text

        claim_id = db.execute(
            select(DataVerification).where(
                DataVerification.entity_type == "program",
                DataVerification.entity_id == program_id,
                DataVerification.field_name == "overview",
            )
        ).scalars().first().id
        change_id = db.execute(
            select(CatalogueChange).where(
                CatalogueChange.entity_type == "program",
                CatalogueChange.entity_id == program_id,
                CatalogueChange.status == "PENDING",
            )
        ).scalars().first().id

        assert client.post(f"{api}/admin/verification/changes/{change_id}/decide", headers=headers,
                           json={"status": "APPROVED"}).status_code == 200
        resolved = client.post(f"{api}/admin/verification/{claim_id}/resolve", headers=headers,
                              json={"status": "VERIFIED"})
        assert resolved.status_code == 200, resolved.text
        body = resolved.json()
        assert body["field_status"] == "VERIFIED"
        assert body["entity_status"] == "VERIFIED", body
        assert body["derivation"]["reason"] is None

        detail = client.get(f"{api}/admin/verification/entity/program/{program_id}",
                            headers=headers)
        assert detail.status_code == 200
        assert detail.json()["derived_status"] == "VERIFIED"
        assert detail.json()["derivation"]["state"] == "VERIFIED"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


def test_review_detail_explains_a_withheld_record_over_http(client, api):
    headers = _staff()
    db = new_session()
    try:
        university, program = _pod(db)
        program.overview = "Published wording."
        _claim(db, program, "tuition_annual", program.tuition_annual)
        _claim(db, program, "duration_months", program.duration_months)
        _claim(db, program, "overview", "Different wording.")
        db.commit()
        detail = client.get(f"{api}/admin/verification/entity/program/{program.id}",
                            headers=headers)
        assert detail.status_code == 200
        derivation = detail.json()["derivation"]
        assert derivation["state"] == "NEEDS_REVIEW"
        assert derivation["reason"] == "evidence_mismatch"
        assert derivation["fields"] == ["overview"]
        assert detail.json()["derived_status"] == "NEEDS_REVIEW"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()


# ------------------------------------------------------------------ PostgreSQL: the real column
@pytest.mark.pg
def test_pg_evidence_column_is_unbounded_text():
    """PostgreSQL must actually store the full value, not a prefix with a quieter limit."""
    from sqlalchemy import text as sql

    url = os.environ.get("SCHOLARA_PG_URL")
    if not url:
        skip_pg_only("SCHOLARA_PG_URL is not set")
    from sqlalchemy import create_engine

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            row = conn.execute(sql(
                "SELECT data_type, character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_name = 'data_verifications' AND column_name = 'field_value'"
            )).first()
            if row is None:
                skip_pg_only("the PostgreSQL database has not been migrated")
            assert row[0] == "text"
            assert row[1] is None, "the evidence column must not impose a length"
    finally:
        engine.dispose()


@pytest.mark.pg
def test_pg_stores_evidence_far_beyond_the_old_limit():
    db = new_session()
    try:
        if db.get_bind().dialect.name != "postgresql":
            skip_pg_only("this test proves PostgreSQL storage")
        university, program = _pod(db)
        _consistent_register(db, program)
        db.commit()
        text = ("P" * 5000)
        catalogue_writes.apply_direct_edit(db, model=Program, row=program,
                                           payload={"overview": text}, actor="editor@c2-10.test",
                                           blocked=catalogue_writes.TRUST_FIELDS)
        db.commit()
        fresh = new_session()
        try:
            recorded = fresh.execute(
                select(DataVerification).where(
                    DataVerification.entity_type == "program",
                    DataVerification.entity_id == program.id,
                    DataVerification.field_name == "overview",
                )
            ).scalars().first()
            assert len(recorded.field_value) == 5000
            assert recorded.field_value == text
            assert fresh.get(Program, program.id).overview == text
        finally:
            fresh.close()
        _settle(db, program.id)
        assert review.recompute_entity_confidence(db, "program", program.id) == "VERIFIED"
        _drop(db, university, program)
    finally:
        db.rollback()
        db.close()
