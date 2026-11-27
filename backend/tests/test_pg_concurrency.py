"""PostgreSQL concurrency and transaction semantics (W5 / W7 / W12 / W13).

These tests need a real PostgreSQL server, so they run only when ``SCHOLARA_PG_URL`` points at a
disposable database:

    SCHOLARA_PG_URL='postgresql+psycopg://user:pw@127.0.0.1:55432/db' pytest tests/test_pg_concurrency.py

They are not a substitute for the SQLite suite and the SQLite suite is not a substitute for them:
row locks, ``SKIP LOCKED``, READ COMMITTED re-evaluation and unique-violation behaviour simply do
not exist in SQLite, so a green SQLite run says nothing about the invariants proved here.

Concurrency is produced two ways, both deterministic:

* two independent sessions taking their turns at an explicit barrier, so the interleaving that
  matters (one transaction holding a lock while the other tries) is guaranteed rather than hoped
  for; and
* genuinely parallel threads against independent connections, for the paths where the winner is
  allowed to be either side but exactly one may win.
"""
from __future__ import annotations

import hashlib
import os
import threading
from typing import Any, Callable, List

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

PG_URL = os.environ.get("SCHOLARA_PG_URL", "").strip()

#: The `pg` marker is what makes a skip detectable: `SCHOLARA_REQUIRE_PG_TESTS=1` (set by the
#: PostgreSQL CI job) turns any skipped `pg` test into a failed run, because a job that proves
#: concurrency correctness may not prove it by skipping.
pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(
        not PG_URL,
        reason="SCHOLARA_PG_URL is not set: PostgreSQL concurrency verification did not run",
    ),
]


# =====================================================================
# harness
# =====================================================================
@pytest.fixture(scope="module")
def engine():
    # search_path is set per connection rather than per session: a pool checkout that skipped it
    # would silently address the public schema, which is exactly the kind of harness bug that
    # produces a false negative.
    separator = "&" if "?" in PG_URL else "?"
    eng = create_engine(f"{PG_URL}{separator}options=-csearch_path%3Dpgcamp",
                        pool_size=6, max_overflow=0, future=True)
    from app.core.database import Base

    # A dedicated schema per run keeps this module independent of any other state in the database.
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS pgcamp CASCADE"))
        conn.execute(text("CREATE SCHEMA pgcamp"))
        Base.metadata.create_all(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def factory(engine):
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)


def _session(factory) -> Session:
    return factory()


def run_parallel(factories: List[Callable[[], Any]]) -> List[Any]:
    """Run callables on their own threads and return their results or exceptions.

    A barrier makes them start together; nothing here waits on a sleep.
    """
    barrier = threading.Barrier(len(factories))
    results: List[Any] = [None] * len(factories)

    def _wrap(index: int, fn: Callable[[], Any]) -> None:
        try:
            barrier.wait(timeout=10)
            results[index] = fn()
        except BaseException as exc:                      # noqa: BLE001 - reported to the caller
            results[index] = exc

    threads = [threading.Thread(target=_wrap, args=(i, fn)) for i, fn in enumerate(factories)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def _pg_version(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SHOW server_version")).scalar_one()


# =====================================================================
# W5 - assistant context compare-and-swap
# =====================================================================
def _make_session_row(db: Session, *, sid: str = "pg-cas-sid", generation: int = 1):
    from app.models.crm import AssistantSession

    row = AssistantSession(sid=sid, credential_hash=hashlib.sha256(sid.encode()).hexdigest(),
                           credential_generation=generation, ownership="ANONYMOUS",
                           context_json=None, context_revision=0)
    db.add(row)
    db.commit()
    return row.id


def test_cas_under_real_postgres_has_exactly_one_winner(factory):
    """The lost-update interleaving, made deterministic.

    Both writers read the same revision *before* either writes (the barrier sits between the read
    and the write), so this is precisely the case a compare-and-swap exists for. Under READ
    COMMITTED the second UPDATE blocks on the first writer's row lock and then re-evaluates the
    revision predicate against the committed row, which is the behaviour being proved.
    """
    from app.models.crm import AssistantSession
    from app.services import context_slots

    setup = _session(factory)
    try:
        session_id = _make_session_row(setup, sid="pg-cas-one")
    finally:
        setup.close()

    barrier = threading.Barrier(2, timeout=10)

    def writer(country: str):
        db = _session(factory)
        try:
            row = db.get(AssistantSession, session_id)
            observed = int(row.context_revision or 0)          # both read the same revision
            barrier.wait()                                     # ... before either writes
            ctx = {"v": 1, "slots": {"country": {"value": country, "source": "user"}},
                   "asked": [], "rejected": [], "pending": None}
            won = context_slots.cas_write(db, row, ctx, expected_revision=observed,
                                          generation=1, profile_id=None)
            db.commit()
            return won
        finally:
            db.close()

    results = run_parallel([lambda: writer("Canada"), lambda: writer("Germany")])
    for result in results:
        assert not isinstance(result, Exception), f"a writer raised: {result!r}"
    assert sorted(results) == [False, True], (
        f"exactly one compare-and-swap must win, got {results}")

    check = _session(factory)
    try:
        row = check.get(AssistantSession, session_id)
        assert int(row.context_revision) == 1, (
            "the revision must advance exactly once, not once per attempt")
        assert row.context_json["slots"]["country"]["value"] in ("Canada", "Germany")
        assert len(row.context_json["slots"]) == 1, (
            "the losing writer's context must not be partially merged into the winner's")
    finally:
        check.close()


def test_cas_does_not_issue_an_unguarded_second_write(factory):
    """The identity map must not re-flush the values as a revision-less UPDATE."""
    from app.models.crm import AssistantSession
    from app.services import context_slots

    db = _session(factory)
    try:
        session_id = _make_session_row(db, sid="pg-cas-second-write")
        row = db.get(AssistantSession, session_id)
        ctx = {"v": 1, "slots": {"country": {"value": "Canada", "source": "user"}},
               "asked": [], "rejected": [], "pending": None}
        assert context_slots.cas_write(db, row, ctx, expected_revision=0, generation=1,
                                       profile_id=None) is True
        db.commit()
        db.refresh(row)
        revision_after_first = int(row.context_revision)

        # Touch nothing else, commit again: a synchronised identity map would write the
        # context a second time here, without the revision predicate.
        db.commit()
        db.refresh(row)
        assert int(row.context_revision) == revision_after_first == 1
        assert row.context_json["slots"]["country"]["value"] == "Canada"
    finally:
        db.close()


def test_a_stale_revision_cannot_overwrite_the_newer_context(factory):
    from app.models.crm import AssistantSession
    from app.services import context_slots

    db = _session(factory)
    try:
        session_id = _make_session_row(db, sid="pg-cas-stale")
        row = db.get(AssistantSession, session_id)
        first = {"v": 1, "slots": {"country": {"value": "Canada", "source": "user"}},
                 "asked": [], "rejected": [], "pending": None}
        assert context_slots.cas_write(db, row, first, expected_revision=0, generation=1,
                                       profile_id=None) is True
        db.commit()

        # A writer that still believes the revision is 0 must be refused.
        second = {"v": 1, "slots": {"country": {"value": "Germany", "source": "user"}},
                  "asked": [], "rejected": [], "pending": None}
        assert context_slots.cas_write(db, row, second, expected_revision=0, generation=1,
                                       profile_id=None) is False
        db.commit()
        db.refresh(row)
        assert row.context_json["slots"]["country"]["value"] == "Canada"
        assert int(row.context_revision) == 1

        # A revoked session cannot be written even with the right revision.
        row.revoked_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        db.commit()
        assert context_slots.cas_write(db, row, second, expected_revision=1, generation=1,
                                       profile_id=None) is False
    finally:
        db.close()


# =====================================================================
# W7 - erasure outbox
# =====================================================================
def _queue_key(db: Session, key: str) -> None:
    from app.models.crm import PendingObjectDeletion

    db.add(PendingObjectDeletion(storage_key=key, reason="PROFILE_ERASED"))
    db.commit()


def test_two_workers_never_claim_the_same_outbox_row(factory):
    """Deterministic interleaving: worker A holds its locks while worker B selects."""
    from app.models.crm import PendingObjectDeletion
    from app.services import erasure

    setup = _session(factory)
    try:
        for key in ("students/pg-a.pdf", "students/pg-b.pdf", "students/pg-c.pdf"):
            _queue_key(setup, key)
    finally:
        setup.close()

    worker_a = _session(factory)
    worker_b = _session(factory)
    try:
        rows_a = erasure.claim_pending_object_deletions(worker_a, limit=2)
        keys_a = [r.storage_key for r in rows_a]
        assert len(keys_a) == 2, f"worker A should claim two rows, got {keys_a}"

        # A is holding row locks in an open transaction; B must skip exactly those.
        rows_b = erasure.claim_pending_object_deletions(worker_b, limit=5)
        keys_b = [r.storage_key for r in rows_b]
        assert set(keys_a) & set(keys_b) == set(), (
            f"both workers claimed the same row: {set(keys_a) & set(keys_b)}")

        worker_a.commit()
        worker_b.commit()
    finally:
        worker_a.close()
        worker_b.close()

    check = _session(factory)
    try:
        remaining = {r.storage_key for r in check.execute(
            select(PendingObjectDeletion)).scalars()}
        assert remaining == {"students/pg-a.pdf", "students/pg-b.pdf", "students/pg-c.pdf"}, (
            "claiming must not delete anything: deletion is the worker's separate act")
    finally:
        check.close()


def test_concurrent_workers_each_process_their_own_rows_once(factory):
    """Two real workers, one shared queue: every key is deleted once and counted once."""
    from app.models.crm import PendingObjectDeletion
    from app.services import erasure

    keys = [f"students/pg-concurrent-{i}.pdf" for i in range(6)]
    setup = _session(factory)
    try:
        from app.models.crm import PendingObjectDeletion

        setup.execute(delete(PendingObjectDeletion).where(
            PendingObjectDeletion.storage_key.in_(keys)))
        setup.commit()
        for key in keys:
            _queue_key(setup, key)
    finally:
        setup.close()

    def worker():
        db = _session(factory)
        try:
            # Scoped by key so the count is about this test's six rows, not whatever else the
            # shared schema holds from an earlier test.
            return erasure.process_pending_object_deletions(db, keys=keys, limit=10)
        finally:
            db.close()

    results = run_parallel([worker, worker])
    for result in results:
        assert not isinstance(result, Exception), f"a worker raised: {result!r}"
    deleted = sum(r["objects_deleted"] for r in results)
    assert deleted == 6, f"each queued object must be deleted exactly once, got {deleted}: {results}"

    check = _session(factory)
    try:
        left = [r.storage_key for r in check.execute(
            select(PendingObjectDeletion).where(
                PendingObjectDeletion.storage_key.like("students/pg-concurrent-%"))).scalars()]
        assert left == [], f"the queue must be empty, still holds {left}"
    finally:
        check.close()


def test_a_failed_deletion_stays_queued_and_retryable(factory, monkeypatch):
    from app.core import storage
    from app.models.crm import PendingObjectDeletion
    from app.services import erasure

    db = _session(factory)
    try:
        _queue_key(db, "students/pg-fail.pdf")

        def boom(key):
            raise storage.StorageError("backend unavailable")

        monkeypatch.setattr(storage, "remove_verified", boom)
        result = erasure.process_pending_object_deletions(
            db, keys=["students/pg-fail.pdf"], limit=10)
        assert result["objects_deleted"] == 0
        db.commit()

        row = db.execute(select(PendingObjectDeletion).where(
            PendingObjectDeletion.storage_key == "students/pg-fail.pdf")).scalars().first()
        assert row is not None, "a failed deletion must stay queued for retry"
        assert row.attempts >= 1 and row.last_error

        # Recovery: the queue is retried successfully once the backend works again.
        monkeypatch.setattr(storage, "remove_verified", lambda key: None)
        again = erasure.process_pending_object_deletions(
            db, keys=["students/pg-fail.pdf"], limit=10)
        assert again["objects_deleted"] == 1
        left = db.execute(select(PendingObjectDeletion).where(
            PendingObjectDeletion.storage_key == "students/pg-fail.pdf")).scalars().first()
        assert left is None
    finally:
        db.close()


# =====================================================================
# W12 - concurrent verification decisions
# =====================================================================
def _register(db: Session, *, entity_id: int, field: str = "tuition_annual"):
    from app.models.crm import DataVerification

    row = DataVerification(entity_type="program", entity_id=entity_id, field_name=field,
                           field_value="1", status="NEEDS_REVIEW",
                           source_url="https://pg.example.edu", source_type="TIER_1_OFFICIAL_UNIVERSITY")
    db.add(row)
    db.commit()
    return row.id


def test_two_reviewers_cannot_both_decide_one_field(factory):
    from app.models.crm import DataVerification
    from app.services import review

    setup = _session(factory)
    try:
        field_id = _register(setup, entity_id=800001)
    finally:
        setup.close()

    def decide(status: str, actor: str):
        def _run():
            db = _session(factory)
            try:
                row = db.get(DataVerification, field_id)
                outcome = review.resolve_field(db, row, status=status, actor_email=actor,
                                               note="checked the official page",
                                               expected_status="NEEDS_REVIEW")
                db.commit()
                return outcome["field_status"]
            finally:
                db.close()
        return _run

    results = run_parallel([decide("VERIFIED", "a@pg.test"), decide("OUTDATED", "b@pg.test")])
    winners = [r for r in results if not isinstance(r, Exception)]
    conflicts = [r for r in results if isinstance(r, review.ReviewError)]
    assert len(winners) == 1, f"exactly one decision may succeed, got {results}"
    assert len(conflicts) == 1, f"the loser must be told it lost, got {results}"
    assert getattr(conflicts[0], "code", 0) == 409, (
        f"the loser must receive a conflict, got {conflicts[0]!r}")

    check = _session(factory)
    try:
        row = check.get(DataVerification, field_id)
        assert row.status == winners[0]
    finally:
        check.close()


def test_a_rejected_decision_leaves_the_field_untouched(factory):
    from app.models.crm import DataVerification
    from app.services import review

    db = _session(factory)
    try:
        field_id = _register(db, entity_id=800002, field="name")
        row = db.get(DataVerification, field_id)
        with pytest.raises(review.ReviewError):
            review.resolve_field(db, row, status="OUTDATED", actor_email="a@pg.test",
                                 note="")            # a refusal needs a reason
        db.rollback()
        fresh = db.get(DataVerification, field_id)
        assert fresh.status == "NEEDS_REVIEW" and fresh.verified_at is None
    finally:
        db.close()


def test_three_reviewers_resolving_one_field_leave_exactly_one_decision(factory):
    """Only one of three reviewers may win, and the other two must be told they lost.

    Sequential-but-stale sessions: every reviewer holds the pre-decision view, which is exactly
    the situation the conditional update has to catch - a read-modify-write would let all three
    succeed and leave three audit entries describing three different outcomes.
    """
    from app.models.crm import DataVerification
    from app.services import review

    setup = _session(factory)
    try:
        field_id = _register(setup, entity_id=800030)
    finally:
        setup.close()

    def reviewer(index: int):
        def _run():
            db = _session(factory)
            try:
                row = db.get(DataVerification, field_id)
                try:
                    review.resolve_field(db, row, status="VERIFIED" if index % 2 == 0 else "OUTDATED",
                                         actor_email=f"r{index}@pg.test", note="checked the page",
                                         expected_status="NEEDS_REVIEW")
                    db.commit()
                    return "won"
                except review.ReviewError as exc:
                    db.rollback()
                    assert exc.code == 409, exc
                    return "lost"
            finally:
                db.close()
        return _run

    results = run_parallel([reviewer(0), reviewer(1), reviewer(2)])
    for result in results:
        assert not isinstance(result, Exception), f"a reviewer raised: {result!r}"
    assert results.count("won") == 1, f"exactly one reviewer may decide: {results}"
    assert results.count("lost") == 2, f"the other two must be refused: {results}"


def test_concurrent_catalogue_change_decisions_produce_one_outcome(factory):
    from app.models.crm import CatalogueChange
    from app.services import catalogue_changes

    setup = _session(factory)
    try:
        change = catalogue_changes.record(setup, entity_type="program", entity_id=800003,
                                          field_name="tuition_annual", previous=1, new=2,
                                          actor="importer")
        setup.commit()
        change_id = change.id
    finally:
        setup.close()

    def decide(status: str, actor: str):
        def _run():
            db = _session(factory)
            try:
                row = db.get(CatalogueChange, change_id)
                catalogue_changes.decide(db, row, status=status, actor=actor, note="evidence")
                db.commit()
                return row.status
            finally:
                db.close()
        return _run

    results = run_parallel([decide("APPROVED", "a@pg.test"), decide("REJECTED", "b@pg.test")])
    ok = [r for r in results if not isinstance(r, Exception)]
    failed = [r for r in results if isinstance(r, ValueError)]
    assert len(ok) == 1 and len(failed) == 1, (
        f"one change may be decided once, got {results}")

    check = _session(factory)
    try:
        row = check.get(CatalogueChange, change_id)
        assert row.status == ok[0]
        assert row.decided_by in ("a@pg.test", "b@pg.test")
    finally:
        check.close()


# =====================================================================
# W13 - version sequencing
# =====================================================================
def test_concurrent_versions_are_unique_and_ordered(factory):
    from app.models.crm import CatalogueChange
    from app.services import catalogue_changes

    def writer(index: int):
        def _run():
            db = _session(factory)
            try:
                row = catalogue_changes.record(db, entity_type="program", entity_id=800010,
                                               field_name="tuition_annual",
                                               previous=index, new=index + 100,
                                               actor=f"w{index}@pg.test")
                db.commit()
                return row.version
            finally:
                db.close()
        return _run

    results = run_parallel([writer(1), writer(2), writer(3)])
    for result in results:
        assert not isinstance(result, Exception), f"a writer raised: {result!r}"
    assert sorted(results) == [1, 2, 3], f"versions must be unique and sequential: {results}"

    check = _session(factory)
    try:
        versions = [r.version for r in check.execute(
            select(CatalogueChange).where(CatalogueChange.entity_id == 800010)).scalars()]
        assert sorted(versions) == [1, 2, 3]
    finally:
        check.close()


def test_six_concurrent_writers_still_get_a_dense_sequence(factory):
    """The advisory lock must serialise *all* writers, not merely a pair.

    Two workers exercising a lock proves it exists; six prove it holds. Each writer commits its
    own transaction, so a lost lock shows up as a duplicate version rather than as a hang.
    """
    from app.models.crm import CatalogueChange
    from app.services import catalogue_changes

    def writer(index: int):
        def _run():
            db = _session(factory)
            try:
                row = catalogue_changes.record(db, entity_type="program", entity_id=800020,
                                               field_name="tuition_annual",
                                               previous=index, new=index + 100,
                                               actor=f"w{index}@pg.test")
                db.commit()
                return row.version
            finally:
                db.close()
        return _run

    results = run_parallel([writer(i) for i in range(6)])
    for result in results:
        assert not isinstance(result, Exception), f"a writer raised: {result!r}"
    assert sorted(results) == [1, 2, 3, 4, 5, 6], f"versions must be dense: {results}"

    check = _session(factory)
    try:
        stored = sorted(r.version for r in check.execute(
            select(CatalogueChange).where(CatalogueChange.entity_id == 800020)).scalars())
    finally:
        check.close()
    assert stored == [1, 2, 3, 4, 5, 6], f"the stored history must be dense: {stored}"


def test_a_duplicate_version_cannot_be_inserted_even_by_raw_sql(factory):
    """The uniqueness the sequence relies on is enforced by the schema, not by luck."""
    from app.models.crm import CatalogueChange
    from sqlalchemy.exc import IntegrityError

    db = _session(factory)
    try:
        db.add(CatalogueChange(entity_type="program", entity_id=800011,
                               field_name="tuition_annual", previous_value="1",
                               new_value="2", classification="MATERIAL", status="PENDING",
                               version=1, actor="first"))
        db.commit()
        db.add(CatalogueChange(entity_type="program", entity_id=800011,
                               field_name="tuition_annual", previous_value="2",
                               new_value="3", classification="MATERIAL", status="PENDING",
                               version=1, actor="duplicate"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


# =====================================================================
# server identity, so the report can name what actually ran
# =====================================================================
def test_the_server_is_really_postgresql(engine):
    version = _pg_version(engine)
    assert version.startswith("16."), version
    with engine.connect() as conn:
        assert conn.execute(text("SHOW server_version_num")).scalar_one().startswith("16")
        # SKIP LOCKED is the primitive the outbox relies on; prove the server has it.
        conn.execute(text("SELECT 1 FROM (SELECT 1 AS id FOR UPDATE SKIP LOCKED) s"))


# ------------------------------------------------------- duplicate job delivery (at-least-once)
def test_two_overlapping_sweeps_flip_and_record_each_record_once(factory, monkeypatch):
    """A redelivered or overlapping sweep must not double the provenance or the notification.

    Reproduced before the fix: two concurrent runs both saw the same rows as VERIFIED, both
    flipped them, and both wrote a provenance row and a "N records flipped" notice - 16 rows for
    8 records, two identical notifications (each also emailed).
    """
    from datetime import datetime, timedelta

    from app.models.crm import DataVerification, Notification, User
    from app.models.education import Country, Program, University
    from app.services import tasks
    from sqlalchemy import select

    setup = _session(factory)
    try:
        country = Country(name="Sweepland", slug="sweep-land", iso2="SW")
        setup.add(country)
        setup.flush()
        university = University(name="Sweep University", slug="sweep-university",
                                country_id=country.id)
        setup.add(university)
        setup.flush()
        past = datetime.utcnow() - timedelta(days=30)
        ids = []
        for index in range(3):
            program = Program(university_id=university.id, name=f"Sweep {index}",
                              slug=f"sweep-{index}", degree_level="MASTER",
                              discipline="Computer Science", confidence="VERIFIED",
                              review_due_at=past)
            setup.add(program)
            setup.flush()
            ids.append(program.id)
        setup.add(User(email="sweeper@pg.test", full_name="Sweeper", role="DATA_MANAGER",
                       is_active=True, password_hash="x"))
        setup.commit()
    finally:
        setup.close()

    monkeypatch.setattr(tasks, "SessionLocal", factory)

    results = run_parallel([tasks.verification_sweep, tasks.verification_sweep])
    for result in results:
        assert not isinstance(result, Exception), f"a sweep raised: {result!r}"
    flipped = sum(r["flipped"] for r in results)
    assert flipped == 3, f"each due record must be flipped once, got {flipped}: {results}"

    check = _session(factory)
    try:
        rows = check.execute(select(DataVerification).where(
            DataVerification.entity_type == "programs",
            DataVerification.entity_id.in_(ids),
            DataVerification.field_name == "*")).scalars().all()
        notices = check.execute(select(Notification).where(
            Notification.trigger == "VERIFICATION_DUE")).scalars().all()
    finally:
        check.close()
    assert len(rows) == 3, f"one provenance row per record, got {len(rows)}"
    assert len(notices) == 1, f"one summary notice, got {len(notices)}"


def test_two_overlapping_link_sweeps_both_complete(factory, monkeypatch):
    """The loser of an insert race adopts the winner's row instead of failing the whole task."""
    from datetime import datetime, timezone

    from app.models.crm import DataVerification, SourceCheck
    from app.services import source_verification
    from sqlalchemy import select

    urls = ["https://example.edu/admissions", "https://example.edu/tuition"]
    setup = _session(factory)
    try:
        setup.execute(select(SourceCheck))                 # touch the table so it exists
        for url in urls:
            setup.add(DataVerification(entity_type="programs", entity_id=900100, field_name="fee",
                                       field_value="1", source_url=url, status="VERIFIED",
                                       review_due_at=datetime.now(timezone.utc)))
        setup.commit()
    finally:
        setup.close()

    monkeypatch.setattr(source_verification, "check_url", lambda url: {
        "state": "OK", "http_status": 200, "final_url": url, "note": ""})

    def sweep():
        db = _session(factory)
        try:
            return source_verification.sweep(db, limit=10)
        finally:
            db.close()

    results = run_parallel([sweep, sweep])
    for result in results:
        assert not isinstance(result, Exception), f"a link sweep raised: {result!r}"

    check = _session(factory)
    try:
        stored = check.execute(select(SourceCheck).where(SourceCheck.url.in_(urls))).scalars().all()
    finally:
        check.close()
    assert len(stored) == 2, f"one row per URL however many sweeps ran, got {len(stored)}"
