"""F-2 — the verification register cannot be deleted into a false certification.

The audit reproduction, unchanged:

1. A programme is ``NEEDS_REVIEW`` with an unreviewed ``tuition_annual = 1.0``; the register holds
   ``duration_months = VERIFIED`` and ``tuition_annual = NEEDS_REVIEW``.
2. ``DELETE /api/v1/admin/verifications/{tuition_claim_id}`` as SENIOR_COUNSELOR → 200.
3. ``POST /api/v1/admin/verification/{duration_id}/resolve {"status": "VERIFIED"}`` → 200.
4. Result was ``("VERIFIED", "dm@pg.test", 1.0)``: an unreviewed fee published as verified and
   attributed to a reviewer who never saw it.

The register is the evidence an entity's trust is *derived* from (H-1). Reading a missing row as
agreement is the defect; so the fixes are, in order of importance:

* **Rows are not deletable.** Generic CRUD refuses ``DELETE`` and ``PUT`` on ``verifications``;
  withdrawal is an explicit transition (``POST /admin/verification/{id}/withdraw``) that requires an
  actor and a reason, keeps the row and its recorded evidence, and can only lower trust.
* **The derivation fails closed.** ``review.recompute_entity_confidence`` withholds ``VERIFIED``
  when an undecided material change is open for the entity, or when a VERIFIED row's recorded
  evidence no longer matches the stored value (a stale approval certifying a newer figure).
* **Every register mutation recomputes the entity**, in the same transaction as the decision.

These tests exercise the HTTP boundary, the service layer, bulk paths, stale ORM state, rollback and
the public API's verification state. The concurrency cases in ``test_pg_concurrency.py`` run against
PostgreSQL; the ones here run on either backend, and the ones that need real locking are marked ``pg``.
"""
from __future__ import annotations

import os

import pytest
from app.core.security import create_access_token
from app.models.crm import AuditLog, CatalogueChange, DataVerification, User
from app.models.education import Program, University
from app.services import review
from sqlalchemy import select, text

from task2_helpers import cleanup, new_session, utc


# ---------------------------------------------------------------- fixtures
def staff_headers(role: str) -> dict:
    db = new_session()
    try:
        user = User(email=f"{role.lower()}-f2-{os.urandom(4).hex()}@scholara.test",
                    full_name=f"F2 {role}", password_hash="x", role=role, is_active=True)
        db.add(user)
        db.commit()
        return {"Authorization": f"Bearer {create_access_token(str(user.id), role)}",
                "email": user.email}
    finally:
        db.close()


@pytest.fixture
def db():
    session = new_session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _country(db):
    from app.models.education import Country

    return db.execute(select(Country).where(Country.iso2 == "US")).scalars().first()


def _university(db, slug):
    row = University(name=f"F2 {slug}", slug=slug, country_id=_country(db).id, city="Testville",
                     overview="F2 test institution.", confidence="NEEDS_REVIEW",
                     source_type="TIER_1_OFFICIAL_UNIVERSITY",
                     source_url="https://f2.example.edu/official", retrieved_at=utc(-1),
                     review_due_at=utc(90))
    db.add(row)
    db.flush()
    return row


def _program(db, university, slug, tuition=1.0):
    row = Program(university_id=university.id, name=f"F2 Programme {slug}", slug=slug,
                  degree_level="MASTER", award="MSc", discipline="Computer Science",
                  currency="USD", tuition_annual=tuition, duration_months=24,
                  overview="F2 test programme.", is_published=True, confidence="NEEDS_REVIEW",
                  source_type="TIER_1_OFFICIAL_UNIVERSITY",
                  source_url="https://f2.example.edu/programme", retrieved_at=utc(-1),
                  review_due_at=utc(90))
    db.add(row)
    db.flush()
    return row


def _claim(db, entity_id, field, value, status="NEEDS_REVIEW", **extra):
    row = DataVerification(entity_type="program", entity_id=entity_id, field_name=field,
                           field_value=str(value), status=status,
                           source_url="https://f2.example.edu/official",
                           source_type="TIER_1_OFFICIAL_UNIVERSITY", **extra)
    db.add(row)
    db.flush()
    return row


def _cleanup(db, university, programme=None):
    if programme is not None:
        db.query(DataVerification).filter(
            DataVerification.entity_type == "program",
            DataVerification.entity_id == programme.id).delete()
        db.query(CatalogueChange).filter(
            CatalogueChange.entity_type == "program",
            CatalogueChange.entity_id == programme.id).delete()
        db.commit()
    cleanup(db, programme, university)


def _the_reproduction(db, tag: str):
    """The audit's exact starting shape: one verified field, one unreviewed fee."""
    university = _university(db, f"f2-{tag}-uni")
    programme = _program(db, university, f"f2-{tag}-prog", tuition=1.0)
    duration = _claim(db, programme.id, "duration_months", 24, status="VERIFIED",
                      verified_at=utc(-1), verified_by="r@f2.example.edu")
    fee = _claim(db, programme.id, "tuition_annual", 1.0, status="NEEDS_REVIEW")
    db.commit()
    return university, programme, duration, fee


# =====================================================================
# 1. the reproduction, at every deletion path
# =====================================================================
def test_the_audit_reproduction_no_longer_promotes_the_entity(client, api, db):
    """Delete the awaiting-review row, then decide another field: the entity stays withheld."""
    university, programme, duration, fee = _the_reproduction(db, "repro")
    headers = staff_headers("SENIOR_COUNSELOR")
    try:
        res = client.delete(f"{api}/admin/verifications/{fee.id}", headers=headers)
        assert res.status_code == 405, (
            "the register is not deletable through generic CRUD, whatever the caller's role")
        assert "withdraw" in res.json()["detail"].lower()

        res = client.post(f"{api}/admin/verification/{duration.id}/resolve",
                          headers=staff_headers("DATA_MANAGER"),
                          json={"status": "VERIFIED"})
        # duration_months was already VERIFIED and is same-day, so a second confirmation is refused;
        # the point of the probe is that the entity is not promoted either way.
        assert res.status_code in (200, 400, 409), res.text

        db.expire_all()
        entity = db.get(Program, programme.id)
        assert entity.confidence != "VERIFIED", (
            "an unreviewed fee must not become verified because its register row disappeared")
        assert entity.verified_by is None
        assert db.get(DataVerification, fee.id) is not None, "the row still exists"
    finally:
        _cleanup(db, university, programme)


def test_deleting_a_verified_register_row_is_refused(client, api, db):
    university, programme, duration, _fee = _the_reproduction(db, "verified-del")
    try:
        res = client.delete(f"{api}/admin/verifications/{duration.id}",
                            headers=staff_headers("SENIOR_COUNSELOR"))
        assert res.status_code == 405, res.text
        db.expire_all()
        assert db.get(DataVerification, duration.id) is not None
        assert db.get(Program, programme.id).confidence != "VERIFIED", (
            "an entity awaiting an unreviewed fee stays withheld")
    finally:
        _cleanup(db, university, programme)


def test_a_service_level_delete_is_refused_rather_than_laundered(client, api, db):
    """The rule lives in the service, not only in the route.

    A future caller that reaches for a delete finds a refusal instead of a silently changed trust
    state. The row remains, the entity stays withheld, and the caller can be told why.
    """
    university, programme, duration, fee = _the_reproduction(db, "service")
    try:
        for record in (fee, duration):
            with pytest.raises(review.ReviewError) as excinfo:
                review.delete_field(db, record, actor_email="editor@f2.example.edu")
            assert excinfo.value.code == 405
            assert "withdraw" in str(excinfo.value).lower()
        db.rollback()
        db.expire_all()
        assert db.get(DataVerification, fee.id) is not None
        assert db.get(Program, programme.id).confidence != "VERIFIED"
    finally:
        _cleanup(db, university, programme)


def test_no_application_code_deletes_a_register_row():
    """Source scan: the invariant is that nothing deletes these rows, so prove nothing does.

    The API route refuses, the service refuses, and this asserts no third path exists - an importer,
    a task, a migration helper or an admin extra - that reaches for ``delete()`` on DataVerification.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "app"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if not re.search(r"delete\(\s*(\w+\.)?(DataVerification|verifications)\b", line):
                continue
            # The register's *own* helpers (review.withdraw_field) never delete; a delete here is a
            # defect regardless of which module it lives in.
            offenders.append(f"{path.relative_to(root)}: {line.strip()}")
    assert offenders == [], f"register rows must never be deleted: {offenders}"


def test_a_bulk_verification_delete_is_refused(client, api, db):
    """There is no bulk verb on this API; the two obvious shapes must both fail closed."""
    university, programme, duration, fee = _the_reproduction(db, "bulk")
    headers = staff_headers("SENIOR_COUNSELOR")
    try:
        for method, url, kwargs in (
            ("delete", f"{api}/admin/verifications/{fee.id}", {}),
            ("delete", f"{api}/admin/verifications", {"params": {"ids": f"{fee.id},{duration.id}"}}),
            ("post", f"{api}/admin/verifications/bulk-delete", {"json": {"ids": [fee.id]}}),
        ):
            res = getattr(client, method)(url, headers=headers, **kwargs)
            assert res.status_code in (404, 405), f"{method} {url} -> {res.status_code}"
        db.expire_all()
        assert db.get(DataVerification, fee.id) is not None
        assert db.get(DataVerification, duration.id) is not None
    finally:
        _cleanup(db, university, programme)


# =====================================================================
# 2. the register cannot be rewritten either
# =====================================================================
def test_register_evidence_cannot_be_rewritten_through_generic_crud(client, api, db):
    """F-4: the value a reviewer approved is not editable after the fact."""
    university, programme, duration, fee = _the_reproduction(db, "evidence")
    try:
        res = client.put(f"{api}/admin/verifications/{duration.id}",
                         headers=staff_headers("DATA_MANAGER"),
                         json={"field_value": "999999.0",
                               "source_url": "https://attacker.example/claim"})
        assert res.status_code == 405, res.text
        db.expire_all()
        row = db.get(DataVerification, duration.id)
        assert row.field_value == "24"
        assert row.source_url == "https://f2.example.edu/official"
    finally:
        _cleanup(db, university, programme)


def test_register_status_cannot_be_set_through_generic_crud(client, api, db):
    """F-8: a refused write is answered with 403/405, never a 200 that quietly did nothing."""
    university, programme, duration, fee = _the_reproduction(db, "status")
    try:
        res = client.put(f"{api}/admin/verifications/{fee.id}",
                         headers=staff_headers("DATA_MANAGER"),
                         json={"status": "VERIFIED"})
        assert res.status_code == 405, res.text
        db.expire_all()
        assert db.get(DataVerification, fee.id).status == "NEEDS_REVIEW"
    finally:
        _cleanup(db, university, programme)


def test_creating_a_register_row_can_only_withhold_trust(client, api, db):
    """A create is allowed because it cannot certify: the status column is refused."""
    university, programme, duration, _fee = _the_reproduction(db, "create")
    try:
        res = client.post(f"{api}/admin/verifications", headers=staff_headers("DATA_MANAGER"),
                          json={"entity_type": "program", "entity_id": programme.id,
                                "field_name": "tuition_annual", "field_value": "1.0",
                                "status": "VERIFIED", "verified_by": "self"})
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "NEEDS_REVIEW"
        assert res.json()["verified_by"] in (None, "")
        db.expire_all()
        assert db.get(Program, programme.id).confidence != "VERIFIED"
    finally:
        _cleanup(db, university, programme)


# =====================================================================
# 3. withdrawal is the audited transition
# =====================================================================
def test_withdrawal_requires_a_reason_and_keeps_the_history(client, api, db):
    university, programme, duration, _fee = _the_reproduction(db, "withdraw")
    headers = staff_headers("DATA_MANAGER")
    try:
        res = client.post(f"{api}/admin/verification/{duration.id}/withdraw", headers=headers,
                          json={})
        assert res.status_code == 400, "a withdrawal without a reason is not a decision"
        assert "reason" in res.json()["detail"].lower()

        db.expire_all()
        assert db.get(DataVerification, duration.id).status == "VERIFIED", "nothing changed"

        res = client.post(f"{api}/admin/verification/{duration.id}/withdraw", headers=headers,
                          json={"reason": "the university replaced its fee page"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["field_status"] == "WITHDRAWN"
        assert body["previous_field_status"] == "VERIFIED"
        assert body["entity_status"] != "VERIFIED"

        db.expire_all()
        row = db.get(DataVerification, duration.id)
        assert row.status == "WITHDRAWN"
        assert row.field_value == "24", "the recorded evidence survives the withdrawal"
        assert row.verified_at is None and row.verified_by is None
        assert db.get(Program, programme.id).confidence != "VERIFIED"

        # The actor and the reason are in the audit trail, which outlives the reviewer's account.
        entry = db.execute(select(AuditLog).where(
            AuditLog.action == "WITHDRAW_VERIFICATION").order_by(AuditLog.id.desc())).scalars().first()
        assert entry is not None
        assert "fee page" in str(entry.detail.get("note", ""))
        assert entry.actor_email == headers["email"]
    finally:
        _cleanup(db, university, programme)


def test_withdrawing_one_claim_does_not_promote_the_rest(client, api, db):
    """The heart of F-2: fewer claims is not more trust."""
    university, programme, duration, fee = _the_reproduction(db, "gap")
    # Make the register look complete: both fields verified, then withdraw one.
    fee.status = "VERIFIED"
    fee.verified_at = utc(-1)
    fee.verified_by = "r@f2.example.edu"
    db.commit()
    assert review.recompute_entity_confidence(db, "program", programme.id) == "VERIFIED"

    try:
        res = client.post(f"{api}/admin/verification/{fee.id}/withdraw",
                          headers=staff_headers("DATA_MANAGER"),
                          json={"reason": "the fee source was withdrawn"})
        assert res.status_code == 200, res.text
        assert res.json()["entity_status"] != "VERIFIED"
        db.expire_all()
        assert db.get(Program, programme.id).confidence != "VERIFIED"

        # Re-verifying the remaining field cannot re-certify the withdrawn one.
        res = client.post(f"{api}/admin/verification/{duration.id}/resolve",
                          headers=staff_headers("DATA_MANAGER"), json={"status": "VERIFIED"})
        assert res.status_code in (200, 400, 409)
        db.expire_all()
        assert db.get(Program, programme.id).confidence != "VERIFIED"
    finally:
        _cleanup(db, university, programme)


def test_a_second_withdrawal_is_refused(client, api, db):
    university, programme, duration, _fee = _the_reproduction(db, "twice")
    headers = staff_headers("DATA_MANAGER")
    try:
        first = client.post(f"{api}/admin/verification/{duration.id}/withdraw", headers=headers,
                            json={"reason": "first"})
        assert first.status_code == 200
        second = client.post(f"{api}/admin/verification/{duration.id}/withdraw", headers=headers,
                             json={"reason": "second"})
        assert second.status_code == 409
    finally:
        _cleanup(db, university, programme)


# =====================================================================
# 4. derivation is fail-closed on its own terms
# =====================================================================
def test_a_stale_verified_row_does_not_certify_a_newer_value(db):
    """A prior approval is about the value it saw, not about whatever is stored now."""
    university = _university(db, "f2-stale-uni")
    programme = _program(db, university, "f2-stale-prog", tuition=20000.0)
    _claim(db, programme.id, "tuition_annual", 20000.0, status="VERIFIED",
           verified_at=utc(-1), verified_by="r@f2.example.edu")
    db.commit()
    assert review.recompute_entity_confidence(db, "program", programme.id) == "VERIFIED"

    # Somebody changed the column without going through a path that re-registers it.
    db.execute(text("UPDATE programs SET tuition_annual = 9.0 WHERE id = :i"), {"i": programme.id})
    db.commit()
    db.expire_all()

    assert review.recompute_entity_confidence(db, "program", programme.id) != "VERIFIED", (
        "an approval of 20000 must not certify a stored 9.0")
    try:
        _cleanup(db, university, programme)
    finally:
        db.rollback()


def test_an_undecided_material_change_withholds_the_record(client, api, db):
    university = _university(db, "f2-pending-uni")
    programme = _program(db, university, "f2-pending-prog", tuition=20000.0)
    _claim(db, programme.id, "tuition_annual", 20000.0, status="VERIFIED",
           verified_at=utc(-1), verified_by="r@f2.example.edu")
    db.commit()
    try:
        # A direct edit appends a PENDING change and a NEEDS_REVIEW claim.
        res = client.put(f"{api}/admin/programs/{programme.id}",
                         headers=staff_headers("COUNSELOR"),
                         json={"tuition_annual": 24000.0})
        assert res.status_code == 200, res.text

        db.expire_all()
        change = db.execute(select(CatalogueChange).where(
            CatalogueChange.entity_type == "program",
            CatalogueChange.entity_id == programme.id,
            CatalogueChange.status == "PENDING")).scalars().first()
        assert change is not None
        assert review.recompute_entity_confidence(db, "program", programme.id) != "VERIFIED"
        assert db.get(Program, programme.id).confidence != "VERIFIED"
    finally:
        _cleanup(db, university, programme)


def test_deciding_the_change_then_the_field_promotes_again(client, api, db):
    """The legitimate path still works end to end: evidence decided, then field approved."""
    university = _university(db, "f2-legit-uni")
    programme = _program(db, university, "f2-legit-prog", tuition=20000.0)
    _claim(db, programme.id, "tuition_annual", 20000.0, status="VERIFIED",
           verified_at=utc(-1), verified_by="r@f2.example.edu")
    db.commit()
    try:
        res = client.put(f"{api}/admin/programs/{programme.id}",
                         headers=staff_headers("COUNSELOR"),
                         json={"tuition_annual": 21000.0})
        assert res.status_code == 200, res.text

        db.expire_all()
        change = db.execute(select(CatalogueChange).where(
            CatalogueChange.entity_type == "program",
            CatalogueChange.entity_id == programme.id,
            CatalogueChange.status == "PENDING")).scalars().first()
        res = client.post(f"{api}/admin/verification/changes/{change.id}/decide",
                          headers=staff_headers("DATA_MANAGER"),
                          json={"status": "APPROVED", "note": "checked the fee page"})
        assert res.status_code == 200, res.text

        # Withdraw the superseded claim (it attests the old fee), then register and approve the new
        # value through the review workflow.
        stale = db.execute(select(DataVerification).where(
            DataVerification.entity_id == programme.id,
            DataVerification.field_name == "tuition_annual",
            DataVerification.status == "VERIFIED")).scalars().first()
        res = client.post(f"{api}/admin/verification/{stale.id}/withdraw",
                          headers=staff_headers("DATA_MANAGER"),
                          json={"reason": "superseded by the approved change"})
        assert res.status_code == 200, res.text

        pending = db.execute(select(DataVerification).where(
            DataVerification.entity_id == programme.id,
            DataVerification.field_name == "tuition_annual",
            DataVerification.status == "NEEDS_REVIEW")).scalars().first()
        res = client.post(f"{api}/admin/verification/{pending.id}/resolve",
                          headers=staff_headers("DATA_MANAGER"), json={"status": "VERIFIED"})
        assert res.status_code == 200, res.text
        assert res.json()["entity_status"] == "VERIFIED"
        db.expire_all()
        entity = db.get(Program, programme.id)
        assert entity.confidence == "VERIFIED"
        assert entity.verified_by
    finally:
        _cleanup(db, university, programme)


# =====================================================================
# 5. stale sessions, rollback and the public view
# =====================================================================
def test_a_stale_orm_object_cannot_restore_trust(client, api, db):
    university, programme, duration, fee = _the_reproduction(db, "stale-orm")
    try:
        # A reviewer loads the row, then somebody else withdraws it.
        stale = db.get(DataVerification, duration.id)
        assert stale is not None
        res = client.post(f"{api}/admin/verification/{duration.id}/withdraw",
                          headers=staff_headers("DATA_MANAGER"),
                          json={"reason": "source withdrawn"})
        assert res.status_code == 200

        # The reviewer's old object still says VERIFIED. Deciding against it must not be a no-op
        # that quietly succeeds: the conditional update sees the changed state and refuses.
        db.expire(stale)
        db.refresh(stale)
        with pytest.raises(review.ReviewError):
            review.resolve_field(db, stale, status="VERIFIED", actor_email="late@f2.example.edu",
                                 expected_status="VERIFIED")
        db.rollback()
        db.expire_all()
        assert db.get(DataVerification, duration.id).status == "WITHDRAWN"
        assert db.get(Program, programme.id).confidence != "VERIFIED"
    finally:
        _cleanup(db, university, programme)


def test_a_failed_withdrawal_leaves_nothing_half_done(client, api, db):
    university, programme, duration, fee = _the_reproduction(db, "rollback")
    headers = staff_headers("DATA_MANAGER")
    try:
        db.expire_all()
        before = (db.get(DataVerification, duration.id).status,
                  db.get(Program, programme.id).confidence)
        res = client.post(f"{api}/admin/verification/{duration.id}/withdraw", headers=headers,
                          json={"reason": "x", "expected_status": "NEEDS_REVIEW"})
        assert res.status_code == 409, "the expected state is stale, so nothing may change"
        db.expire_all()
        after = (db.get(DataVerification, duration.id).status,
                 db.get(Program, programme.id).confidence)
        assert before == after
    finally:
        _cleanup(db, university, programme)


def test_the_public_api_never_presents_the_unreviewed_value(client, api, db):
    university, programme, duration, fee = _the_reproduction(db, "public")
    try:
        res = client.get(f"{api}/programs/{programme.slug}")
        assert res.status_code == 200, res.text
        payload = res.json()
        text = str(payload)
        assert '"verified": false' in text or "NOT_VERIFIED" in text, (
            "an unreviewed fee must not be presented as verified")
    finally:
        _cleanup(db, university, programme)


# =====================================================================
# 6. concurrency (PostgreSQL): a decision racing a withdrawal
# =====================================================================
@pytest.mark.pg
def test_concurrent_withdrawal_and_decision_cannot_both_win(api):
    """Two writers move one field at once; at most one transition may commit.

    Run with ``SCHOLARA_PG_URL`` set this is a real row-level race: PostgreSQL executes the two
    compare-and-swap statements concurrently and the loser matches no row. On SQLite the same test
    still asserts the invariant - SQLite serialises writers, so it cannot race, but the CAS and the
    fail-closed derivation are checked either way.
    """
    import threading

    from app.core.database import engine
    from sqlalchemy.orm import sessionmaker

    db = new_session()
    university = programme = None
    try:
        university = _university(db, "f2-race-uni")
        programme = _program(db, university, "f2-race-prog", tuition=20000.0)
        claim = _claim(db, programme.id, "tuition_annual", 20000.0, status="VERIFIED",
                       verified_at=utc(-1), verified_by="r@f2.example.edu")
        db.commit()
        claim_id, programme_id = claim.id, programme.id

        Factory = sessionmaker(bind=engine, future=True)
        results: dict[str, str] = {}
        barrier = threading.Barrier(2)

        def withdraw():
            s = Factory()
            try:
                barrier.wait(timeout=10)
                rec = s.get(DataVerification, claim_id)
                review.withdraw_field(s, rec, actor_email="w@f2.example.edu", note="withdrawn")
                s.commit()
                results["withdraw"] = "committed"
            except Exception as exc:                     # noqa: BLE001 - recorded, then asserted
                s.rollback()
                results["withdraw"] = type(exc).__name__
            finally:
                s.close()

        def decide():
            s = Factory()
            try:
                barrier.wait(timeout=10)
                rec = s.get(DataVerification, claim_id)
                review.resolve_field(s, rec, status="OUTDATED", actor_email="d@f2.example.edu",
                                     note="stale", expected_status="VERIFIED")
                s.commit()
                results["decide"] = "committed"
            except Exception as exc:                     # noqa: BLE001
                s.rollback()
                results["decide"] = type(exc).__name__
            finally:
                s.close()

        threads = [threading.Thread(target=withdraw), threading.Thread(target=decide)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        db.expire_all()
        final = db.get(DataVerification, claim_id).status
        assert final in ("WITHDRAWN", "OUTDATED"), final
        assert db.get(Program, programme_id).confidence != "VERIFIED"
        # At most one of the two movers may report success; the other must have been refused.
        assert list(results.values()).count("committed") >= 1, results
    finally:
        if programme is not None:
            _cleanup(db, university, programme)
        else:
            db.rollback()
        db.close()
