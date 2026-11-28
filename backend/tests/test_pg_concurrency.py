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
import itertools
import os
import threading
import time
from typing import Any, Callable, List

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from skip_policy import PG_ONLY_PREFIX

PG_URL = os.environ.get("SCHOLARA_PG_URL", "").strip()

#: The `pg` marker is what makes a skip detectable: `SCHOLARA_REQUIRE_PG_TESTS=1` (set by the
#: PostgreSQL CI job) turns any skipped `pg` test into a failed run, because a job that proves
#: concurrency correctness may not prove it by skipping.
pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(
        not PG_URL,
        reason=PG_ONLY_PREFIX + "SCHOLARA_PG_URL is not set: "
        "PostgreSQL concurrency verification did not run",
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


# =====================================================================
# H-1 - a direct catalogue edit under real concurrency
# =====================================================================
def _verified_program_row(db: Session, *, tuition: float = 20000.0):
    """A VERIFIED programme inside the pgcamp schema."""
    from datetime import datetime, timedelta, timezone

    from app.models.education import Country, Program, University

    country = db.execute(select(Country).where(
        Country.slug == "pgcamp-land")).scalars().first()
    if country is None:
        country = Country(name="PGCamp Land", slug="pgcamp-land", iso2="PQ", kind="COUNTRY")
        db.add(country)
        db.flush()
    university = db.execute(select(University).where(
        University.slug == "pgcamp-university")).scalars().first()
    if university is None:
        university = University(name="PGCamp University", slug="pgcamp-university",
                                country_id=country.id, city="Testville",
                                confidence="NEEDS_REVIEW")
        db.add(university)
        db.flush()
    programme = db.execute(select(Program).where(
        Program.slug == "pgcamp-msc")).scalars().first()
    if programme is None:
        programme = Program(university_id=university.id, name="PGCamp MSc", slug="pgcamp-msc",
                            degree_level="MASTER", currency="USD")
        db.add(programme)
        db.flush()
    # Each caller needs the same *precondition* - a genuinely verified figure - not a shared row
    # left in whatever state the previous test put it, so the state is re-established every time.
    programme.tuition_annual = tuition
    programme.confidence = "VERIFIED"
    programme.verified_by = "registrar@pgcamp.test"
    programme.verified_at = datetime.now(timezone.utc) - timedelta(days=2)
    programme.review_due_at = datetime.now(timezone.utc) + timedelta(days=200)
    programme.source_url = "https://pgcamp.test/fees"
    programme.source_type = "TIER_1_OFFICIAL_UNIVERSITY"
    # Start every caller from the declared precondition: the record verified, and no leftover
    # register row or undecided change from a previous test that would silently change the derived
    # state. Since F-2 the derivation also withholds a record while a material change is undecided,
    # so a *test* that leaves one behind would be changing the precondition it claims to set.
    from app.models.crm import CatalogueChange, DataVerification

    db.query(DataVerification).filter(
        DataVerification.entity_type == "program",
        DataVerification.entity_id == programme.id).delete(synchronize_session=False)
    db.query(CatalogueChange).filter(
        CatalogueChange.entity_type == "program",
        CatalogueChange.entity_id == programme.id).delete(synchronize_session=False)
    db.commit()
    return programme.id


def test_two_direct_edits_of_one_field_keep_both_changes_and_one_invalidation(factory):
    """Concurrent CRUD edits must not lose each other's history or leave a verified claim."""
    from app.models.crm import CatalogueChange
    from app.models.education import Program
    from app.services import catalogue_writes

    setup = _session(factory)
    try:
        program_id = _verified_program_row(setup)
    finally:
        setup.close()

    def editor(index: int):
        def _run():
            db = _session(factory)
            try:
                row = db.get(Program, program_id)
                summary = catalogue_writes.apply_direct_edit(
                    db, model=Program, row=row,
                    payload={"tuition_annual": 1000.0 + index},
                    actor=f"editor{index}@pg.test", blocked=catalogue_writes.TRUST_FIELDS)
                db.commit()
                return summary["invalidated"]
            finally:
                db.close()
        return _run

    results = run_parallel([editor(1), editor(2)])
    for result in results:
        assert not isinstance(result, Exception), f"an editor raised: {result!r}"

    check = _session(factory)
    try:
        row = check.get(Program, program_id)
        assert row.confidence == "NEEDS_REVIEW"
        assert row.verified_at is None and row.verified_by is None
        changes = check.execute(
            select(CatalogueChange).where(
                CatalogueChange.entity_type == "program",
                CatalogueChange.entity_id == program_id,
                CatalogueChange.field_name == "tuition_annual",
            ).order_by(CatalogueChange.version)).scalars().all()
        # Both edits are recorded, each with its own version: an advisory lock serialises the
        # sequence so neither writer silently overwrites the other's history.
        assert len(changes) == 2, [c.version for c in changes]
        assert [c.version for c in changes] == [1, 2]
        assert {c.new_value for c in changes} == {"1001.0", "1002.0"}
        assert all(c.actor for c in changes)
    finally:
        check.close()


def test_a_commit_after_invalidation_cannot_restore_the_verified_claim(factory):
    """A stale writer holding a VERIFIED row must not resurrect it after the edit committed."""
    from app.models.education import Program
    from app.services import catalogue_writes

    setup = _session(factory)
    try:
        program_id = _verified_program_row(setup, tuition=30000.0)
    finally:
        setup.close()

    # A reader loads the record first - it still looks VERIFIED in its own transaction.
    stale = _session(factory)
    editor = _session(factory)
    try:
        stale_program = stale.get(Program, program_id)
        assert stale_program.confidence == "VERIFIED"

        live = editor.get(Program, program_id)
        catalogue_writes.apply_direct_edit(
            editor, model=Program, row=live, payload={"tuition_annual": 42.0},
            actor="editor@pg.test", blocked=catalogue_writes.TRUST_FIELDS)
        editor.commit()
    finally:
        editor.close()

    try:
        # The stale session re-reads before writing: the database, not its cached copy, decides.
        stale.refresh(stale_program)
        assert stale_program.confidence == "NEEDS_REVIEW", (
            "an editor must not be able to re-publish a figure another edit already withdrew")
        assert stale_program.tuition_annual == 42.0
    finally:
        stale.close()


def test_a_stale_promotion_cannot_re_certify_an_edit_that_already_committed(factory):
    """The race the guarded promotion closes, made deterministic.

    A reviewer's decision derives the entity state from the register, and a direct catalogue edit
    can commit between that derivation and the write. The edit appends a NEEDS_REVIEW register row
    and withdraws the entity's verification; a stale ``VERIFIED`` written afterwards would put the
    unreviewed figure back in front of students. The promotion is conditional in the statement, so
    the database - which can see the edit - refuses it.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.crm import DataVerification
    from app.models.education import Program
    from app.services import catalogue_writes, review

    setup = _session(factory)
    try:
        program_id = _verified_program_row(setup, tuition=25000.0)
        setup.add(DataVerification(
            entity_type="program", entity_id=program_id, field_name="tuition_annual",
            field_value="25000.0", status="VERIFIED",
            verified_by="registrar@pgcamp.test", source_url="https://pgcamp.test/fees",
            source_type="TIER_1_OFFICIAL_UNIVERSITY",
            verified_at=datetime.now(timezone.utc) - timedelta(days=2)))
        setup.commit()
    finally:
        setup.close()

    reviewer = _session(factory)
    try:
        # 1. The reviewer derives "everything is verified" from what it can see right now.
        assert review.recompute_entity_confidence(reviewer, "program", program_id) == "VERIFIED"

        # 2. An edit commits in another transaction while that decision is in flight.
        editor = _session(factory)
        try:
            row = editor.get(Program, program_id)
            catalogue_writes.apply_direct_edit(
                editor, model=Program, row=row, payload={"tuition_annual": 1.0},
                actor="editor@pgcamp.test", blocked=catalogue_writes.TRUST_FIELDS)
            editor.commit()
        finally:
            editor.close()

        # 3. The reviewer writes its stale verdict. The database refuses it.
        review._apply_entity_state(reviewer, "program", program_id, "VERIFIED",
                                   "reviewer@pgcamp.test")
        reviewer.commit()

        final = _session(factory)
        try:
            row = final.get(Program, program_id)
            assert row.confidence != "VERIFIED", (
                "a stale promotion re-certified a value the register says is awaiting review")
            assert row.tuition_annual == 1.0
            assert row.verified_by is None or row.verified_by == "registrar@pgcamp.test"
        finally:
            final.close()
    finally:
        reviewer.close()


def test_a_promotion_still_applies_when_the_register_agrees(factory):
    """The guard must not block the normal case it exists to protect."""
    from datetime import datetime, timedelta, timezone

    from app.models.crm import DataVerification
    from app.models.education import Program
    from app.services import review

    setup = _session(factory)
    try:
        program_id = _verified_program_row(setup, tuition=18000.0)
        setup.query(DataVerification).filter(
            DataVerification.entity_type == "program",
            DataVerification.entity_id == program_id).delete(synchronize_session=False)
        setup.add(DataVerification(
            entity_type="program", entity_id=program_id, field_name="tuition_annual",
            field_value="18000.0", status="VERIFIED",
            verified_by="registrar@pgcamp.test", source_url="https://pgcamp.test/fees",
            source_type="TIER_1_OFFICIAL_UNIVERSITY",
            verified_at=datetime.now(timezone.utc) - timedelta(days=1)))
        setup.commit()
        programme = setup.get(Program, program_id)
        programme.confidence = "NEEDS_REVIEW"
        programme.verified_by = None
        setup.commit()
        assert review.recompute_entity_confidence(setup, "program", program_id) == "VERIFIED"
        review._apply_entity_state(setup, "program", program_id, "VERIFIED",
                                   "reviewer@pgcamp.test")
        setup.commit()
        fresh = setup.get(Program, program_id)
        assert fresh.confidence == "VERIFIED", "an agreeing register must still promote the record"
        assert fresh.verified_by == "reviewer@pgcamp.test"
    finally:
        setup.close()


# =====================================================================
# C2-10 / C2-12 - long evidence and register creation under concurrency
# =====================================================================
def test_a_long_claim_approval_cannot_outrun_a_concurrent_edit(factory):
    """Approving evidence for one value while another is stored must never publish the wrong one."""

    from app.models.crm import DataVerification
    from app.models.education import Program
    from app.services import catalogue_writes, review

    first = ("Original published text. " * 60)
    second = ("Replacement published text. " * 60)
    setup = _session(factory)
    try:
        program_id = _verified_program_row(setup)
        programme = setup.get(Program, program_id)
        programme.overview = first
        setup.add(DataVerification(
            entity_type="program", entity_id=program_id, field_name="overview",
            field_value=first, status="NEEDS_REVIEW", source_url="https://pgcamp.test/fees",
            source_type="TIER_1_OFFICIAL_UNIVERSITY"))
        setup.commit()
        claim_id = setup.execute(select(DataVerification.id).where(
            DataVerification.entity_type == "program",
            DataVerification.entity_id == program_id,
            DataVerification.field_name == "overview")).scalar_one()
        assert review.recompute_entity_confidence(setup, "program", program_id) == "NEEDS_REVIEW"
    finally:
        setup.close()

    def reviewer():
        db = _session(factory)
        try:
            claim = db.get(DataVerification, claim_id)
            review.resolve_field(db, claim, status="VERIFIED", actor_email="dm@pg.test")
            db.commit()
            return "approved"
        finally:
            db.close()

    def editor():
        db = _session(factory)
        try:
            row = db.get(Program, program_id)
            catalogue_writes.apply_direct_edit(db, model=Program, row=row,
                                               payload={"overview": second},
                                               actor="editor@pg.test",
                                               blocked=catalogue_writes.TRUST_FIELDS)
            db.commit()
            return "edited"
        finally:
            db.close()

    results = run_parallel([reviewer, editor])
    assert sorted(r for r in results if isinstance(r, str)) == ["approved", "edited"]

    check = _session(factory)
    try:
        programme = check.get(Program, program_id)
        derived = review.recompute_entity_confidence(check, "program", program_id)
        # The invariant is not "who won" but "the record never claims more than the register says".
        assert programme.confidence == derived, (
            f"entity says {programme.confidence} while the register derives {derived}")
        assert len(programme.overview) in (len(first), len(second))
        if programme.confidence == "VERIFIED":
            verdict = review._evidence_verdict(check, "program", program_id, list(
                check.execute(select(DataVerification).where(
                    DataVerification.entity_type == "program",
                    DataVerification.entity_id == program_id)).scalars()))
            assert verdict["ok"], "a certified record must be described by its own evidence"
    finally:
        check.close()


def test_creating_a_claim_while_a_decision_lands_keeps_the_entity_consistent(factory):
    """C2-12 under concurrency: the refresh and the decision cannot disagree with the register."""

    from app.models.crm import DataVerification
    from app.models.education import Program
    from app.services import review

    setup = _session(factory)
    try:
        program_id = _verified_program_row(setup)
        setup.add(DataVerification(
            entity_type="program", entity_id=program_id, field_name="tuition_annual",
            field_value="20000.0", status="NEEDS_REVIEW",
            source_url="https://pgcamp.test/fees",
            source_type="TIER_1_OFFICIAL_UNIVERSITY"))
        setup.commit()
        claim_id = setup.execute(select(DataVerification.id).where(
            DataVerification.entity_type == "program",
            DataVerification.entity_id == program_id,
            DataVerification.field_name == "tuition_annual")).scalar_one()
    finally:
        setup.close()

    def decider():
        db = _session(factory)
        try:
            claim = db.get(DataVerification, claim_id)
            review.resolve_field(db, claim, status="VERIFIED", actor_email="dm@pg.test")
            db.commit()
            return "decided"
        finally:
            db.close()

    def creator():
        db = _session(factory)
        try:
            # Exactly what the generic create route does: insert, flush, re-derive, commit.
            db.add(DataVerification(
                entity_type="program", entity_id=program_id, field_name="duration_months",
                field_value="24", status="NEEDS_REVIEW", source_url="https://pgcamp.test/fees",
                source_type="TIER_1_OFFICIAL_UNIVERSITY"))
            db.flush()
            review.refresh_entity_state(db, "program", program_id, "dm2@pg.test")
            db.commit()
            return "created"
        finally:
            db.close()

    results = run_parallel([decider, creator])
    assert sorted(r for r in results if isinstance(r, str)) == ["created", "decided"]

    check = _session(factory)
    try:
        programme = check.get(Program, program_id)
        derived = review.recompute_entity_confidence(check, "program", program_id)
        assert programme.confidence == derived
        # The new claim is NEEDS_REVIEW and is current for its field, so the record is withheld.
        assert derived == "NEEDS_REVIEW"
    finally:
        check.close()


# =====================================================================
# C2-10 - the entity/register invariant under every competing order
#
# The failing case was not "who wins". It was that the record could end up claiming VERIFIED over
# a register that derived NEEDS_REVIEW, because (a) a direct edit lowered trust by assigning an
# ORM attribute that was already equal to the target, so no UPDATE was emitted at all, and (b) the
# conditional promotion was evaluated with the statement's own snapshot, which PostgreSQL does not
# re-check after it waits on a row lock. These tests fix the *order* of each interleaving rather
# than hoping for it, and every one of them asserts the invariant on both sides.
# =====================================================================
_RACE_COUNTER = itertools.count(1)


def _race_program(db: Session, *, overview: str):
    """A programme whose entity and register already agree, awaiting one decision.

    Entity NEEDS_REVIEW and one current register row for ``overview``, also NEEDS_REVIEW, so the
    derivation says NEEDS_REVIEW before anything races. Each call gets its own slug, so these tests
    never share a row with each other or with the tests above.
    """
    from app.models.crm import DataVerification
    from app.models.education import Country, Program, University

    tag = next(_RACE_COUNTER)
    country = db.execute(select(Country).where(Country.slug == "pgcamp-land")).scalars().first()
    if country is None:
        country = Country(name="PGCamp Land", slug="pgcamp-land", iso2="PQ", kind="COUNTRY")
        db.add(country)
        db.flush()
    university = db.execute(select(University).where(
        University.slug == "pgcamp-university")).scalars().first()
    if university is None:
        university = University(name="PGCamp University", slug="pgcamp-university",
                                country_id=country.id, city="Testville",
                                confidence="NEEDS_REVIEW")
        db.add(university)
        db.flush()
    programme = Program(university_id=university.id, name=f"Race {tag}",
                        slug=f"pgcamp-race-{tag}", degree_level="MASTER", currency="USD",
                        tuition_annual=20000.0, overview=overview, confidence="NEEDS_REVIEW")
    db.add(programme)
    db.flush()
    db.add(DataVerification(
        entity_type="program", entity_id=programme.id, field_name="overview",
        field_value=overview, status="NEEDS_REVIEW",
        source_url="https://pgcamp.test/overview",
        source_type="TIER_1_OFFICIAL_UNIVERSITY"))
    db.flush()
    claim_id = db.execute(select(DataVerification.id).where(
        DataVerification.entity_id == programme.id,
        DataVerification.field_name == "overview")).scalar_one()
    db.commit()
    return programme.id, claim_id


def _assert_entity_agrees_with_register(db: Session, entity_id: int, where: str):
    """The invariant, asserted the same way everywhere: the record never outclaims its register.

    Also holds the rest of the contract that a race could quietly break - a record that is not
    VERIFIED carries no verification stamp, and a record that *is* VERIFIED is described by its own
    evidence and attributed to the reviewer who decided it.
    """
    from app.models.crm import DataVerification
    from app.models.education import Program
    from app.services import review

    derived = review.recompute_entity_confidence(db, "program", entity_id)
    entity = db.get(Program, entity_id, populate_existing=True)
    assert entity.confidence == derived, (
        f"{where}: entity says {entity.confidence} while the register derives {derived}")
    if entity.confidence == "VERIFIED":
        assert entity.verified_by is not None, f"{where}: VERIFIED with no verifier"
        assert entity.verified_at is not None, f"{where}: VERIFIED with no timestamp"
        verdict = review._evidence_verdict(db, "program", entity_id, list(db.execute(
            select(DataVerification).where(
                DataVerification.entity_type == "program",
                DataVerification.entity_id == entity_id)).scalars()))
        assert verdict["ok"], f"{where}: a certified record is not described by its evidence: {verdict}"
    else:
        assert entity.verified_at is None, (
            f"{where}: {entity.confidence} kept a verification timestamp")
        assert entity.verified_by is None, (
            f"{where}: {entity.confidence} kept a verifier, which attributes it to someone who "
            f"did not approve the stored value")
    return entity, derived


def _wait_for_lock_waiter(engine, *, timeout: float = 15.0) -> bool:
    """Wait until some backend is queued on a row lock in this module's schema.

    A condition wait, not a fixed delay: it returns as soon as the wait appears, and fails the
    test rather than silently continuing if it never does.
    """
    # A row-lock wait is reported as an ungranted *transactionid* lock, not a relation lock, so
    # ask the activity view rather than pg_locks: any other backend stuck on a Lock event is the
    # queue we are waiting for.
    query = text(
        "select count(*) from pg_stat_activity "
        "where datname = current_database() and state = 'active' "
        "and wait_event_type = 'Lock' and pid <> pg_backend_pid()")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as conn:
            if conn.execute(query).scalar_one() > 0:
                return True
        time.sleep(0.02)
    return False


def test_an_edit_built_from_a_stale_row_still_withholds_the_record(factory):
    """The original defect: the invalidation was an ORM assignment that emitted no SQL.

    A record awaiting review already holds NEEDS_REVIEW, which is precisely the target an edit
    assigns, so SQLAlchemy saw no change and wrote no column. An approval that committed in that
    window stayed VERIFIED over unreviewed text. Deterministic: no threads, only a stale row.
    """
    from app.models.crm import DataVerification
    from app.models.education import Program
    from app.services import catalogue_writes, review

    first = "Original published overview. " * 40
    second = "Replacement overview nobody reviewed. " * 40
    setup = _session(factory)
    try:
        program_id, claim_id = _race_program(setup, overview=first)
    finally:
        setup.close()

    editor = _session(factory)
    try:
        stale = editor.get(Program, program_id)
        assert stale.confidence == "NEEDS_REVIEW"

        approver = _session(factory)
        try:
            claim = approver.get(DataVerification, claim_id)
            review.resolve_field(approver, claim, status="VERIFIED",
                                 actor_email="reviewer@pgcamp.test")
            approver.commit()
        finally:
            approver.close()

        result = catalogue_writes.apply_direct_edit(
            editor, model=Program, row=stale, payload={"overview": second},
            actor="editor@pgcamp.test", blocked=catalogue_writes.TRUST_FIELDS)
        editor.commit()
        assert result["invalidated"] is True, "the edit must report that it invalidated the record"
    finally:
        editor.close()

    check = _session(factory)
    try:
        entity, derived = _assert_entity_agrees_with_register(
            check, program_id, "stale-row edit after approval")
        assert entity.overview == second
        assert derived == "NEEDS_REVIEW", (
            "the unseen replacement text must not be certified")
    finally:
        check.close()


def test_an_edit_committed_before_the_approval_writes_withholds_the_record(factory):
    """Order reversed: the edit wins completely, then the stale approval tries to write."""
    from app.models.crm import DataVerification
    from app.models.education import Program
    from app.services import catalogue_writes, review

    first = "Original published overview. " * 40
    second = "Replacement overview nobody reviewed. " * 40
    setup = _session(factory)
    try:
        program_id, claim_id = _race_program(setup, overview=first)
    finally:
        setup.close()

    editor = _session(factory)
    try:
        row = editor.get(Program, program_id)
        catalogue_writes.apply_direct_edit(
            editor, model=Program, row=row, payload={"overview": second},
            actor="editor@pgcamp.test", blocked=catalogue_writes.TRUST_FIELDS)
        editor.commit()
    finally:
        editor.close()

    approver = _session(factory)
    try:
        claim = approver.get(DataVerification, claim_id)
        review.resolve_field(approver, claim, status="VERIFIED",
                             actor_email="reviewer@pgcamp.test")
        approver.commit()
    finally:
        approver.close()

    check = _session(factory)
    try:
        entity, derived = _assert_entity_agrees_with_register(
            check, program_id, "approval after a committed edit")
        assert entity.overview == second
        assert derived == "NEEDS_REVIEW"
    finally:
        check.close()


def test_an_approval_that_waits_on_the_entity_lock_derives_from_the_committed_register(
        factory, engine):
    """The interleaving PostgreSQL's snapshot rule used to hide.

    The edit holds the entity row lock with a NEEDS_REVIEW register row *uncommitted*, and the
    approval queues behind it. When the edit commits, the approval must derive from the register it
    just waited for - not from the snapshot it held when it started.
    """
    from app.models.crm import DataVerification
    from app.models.education import Program
    from app.services import catalogue_writes, review

    first = "Original published overview. " * 40
    second = "Replacement overview nobody reviewed. " * 40
    setup = _session(factory)
    try:
        program_id, claim_id = _race_program(setup, overview=first)
    finally:
        setup.close()

    approval: dict[str, Any] = {}
    started = threading.Event()

    def approve() -> None:
        db = _session(factory)
        try:
            started.set()
            claim = db.get(DataVerification, claim_id)
            review.resolve_field(db, claim, status="VERIFIED",
                                 actor_email="reviewer@pgcamp.test")
            db.commit()
            approval["result"] = "approved"
        except BaseException as exc:              # noqa: BLE001 - reported to the test
            approval["error"] = exc
        finally:
            db.close()

    editor = _session(factory)
    try:
        live = editor.get(Program, program_id)
        catalogue_writes.apply_direct_edit(
            editor, model=Program, row=live, payload={"overview": second},
            actor="editor@pgcamp.test", blocked=catalogue_writes.TRUST_FIELDS)
        # The edit's write holds the entity row lock and is deliberately not committed yet.
        thread = threading.Thread(target=approve)
        thread.start()
        assert started.wait(timeout=10), "the approval thread never started"
        assert _wait_for_lock_waiter(engine), (
            "the approval never queued on the entity row lock, so this test proved nothing")
        editor.commit()
        thread.join(timeout=30)
        assert not thread.is_alive(), "the approval never finished"
    finally:
        editor.close()

    assert "error" not in approval, f"the approval failed: {approval.get('error')!r}"
    check = _session(factory)
    try:
        entity, derived = _assert_entity_agrees_with_register(
            check, program_id, "approval queued behind an uncommitted edit")
        assert entity.overview == second
        assert derived == "NEEDS_REVIEW"
    finally:
        check.close()


def test_competing_approvals_and_an_edit_never_leave_the_record_between_states(factory):
    """Three writers at a barrier: two approvals and one edit, and the invariant still holds."""
    from app.models.crm import DataVerification
    from app.models.education import Program
    from app.services import catalogue_writes, review

    first = "Original published overview. " * 40
    second = "Replacement overview nobody reviewed. " * 40
    setup = _session(factory)
    try:
        program_id, claim_id = _race_program(setup, overview=first)
    finally:
        setup.close()

    def approve(email: str):
        def run():
            db = _session(factory)
            try:
                claim = db.get(DataVerification, claim_id)
                review.resolve_field(db, claim, status="VERIFIED", actor_email=email)
                db.commit()
                return f"approved:{email}"
            finally:
                db.close()
        return run

    def edit():
        db = _session(factory)
        try:
            row = db.get(Program, program_id)
            catalogue_writes.apply_direct_edit(
                db, model=Program, row=row, payload={"overview": second},
                actor="editor@pgcamp.test", blocked=catalogue_writes.TRUST_FIELDS)
            db.commit()
            return "edited"
        finally:
            db.close()

    results = run_parallel([approve("one@pgcamp.test"), approve("two@pgcamp.test"), edit])
    # Exactly one approval may win; the other is *told* it lost, which is the contract. Anything
    # other than that 409 - or an edit that did not complete - is a real failure.
    losers = [r for r in results
              if isinstance(r, BaseException)
              and not (isinstance(r, review.ReviewError) and getattr(r, "code", None) == 409)]
    assert not losers, f"a writer failed unexpectedly: {losers!r}"
    assert "edited" in results, f"the edit did not complete: {results!r}"

    check = _session(factory)
    try:
        entity, derived = _assert_entity_agrees_with_register(
            check, program_id, "two approvals and an edit")
        assert entity.overview in (first, second)
        assert derived in ("VERIFIED", "NEEDS_REVIEW")
    finally:
        check.close()


def test_repeated_approval_edit_races_hold_the_invariant(factory):
    """The same race many times over independent sessions, which is what CI actually exercises."""
    from app.models.crm import DataVerification
    from app.models.education import Program
    from app.services import catalogue_writes, review

    first = "Original published overview. " * 60
    second = "Replacement overview nobody reviewed. " * 60

    for attempt in range(8):
        setup = _session(factory)
        try:
            program_id, claim_id = _race_program(setup, overview=first)
        finally:
            setup.close()

        def approve(claim_id=claim_id):
            db = _session(factory)
            try:
                claim = db.get(DataVerification, claim_id)
                review.resolve_field(db, claim, status="VERIFIED",
                                     actor_email="reviewer@pgcamp.test")
                db.commit()
                return "approved"
            finally:
                db.close()

        def edit(program_id=program_id):
            db = _session(factory)
            try:
                row = db.get(Program, program_id)
                catalogue_writes.apply_direct_edit(
                    db, model=Program, row=row, payload={"overview": second},
                    actor="editor@pgcamp.test", blocked=catalogue_writes.TRUST_FIELDS)
                db.commit()
                return "edited"
            finally:
                db.close()

        results = run_parallel([approve, edit])
        assert sorted(r for r in results if isinstance(r, str)) == ["approved", "edited"], (
            f"attempt {attempt}: a writer did not complete: {results!r}")

        check = _session(factory)
        try:
            entity, derived = _assert_entity_agrees_with_register(
                check, program_id, f"repeated race, attempt {attempt}")
            assert entity.overview in (first, second)
        finally:
            check.close()


def test_a_rolled_back_edit_leaves_the_register_and_entity_agreeing(factory):
    """An edit that never commits must not change what the register says or what the record claims."""
    from app.models.crm import DataVerification
    from app.models.education import Program
    from app.services import catalogue_writes, review

    first = "Original published overview. " * 40
    second = "Abandoned replacement overview. " * 40
    setup = _session(factory)
    try:
        program_id, claim_id = _race_program(setup, overview=first)
    finally:
        setup.close()

    # The edit writes and then rolls back - the row lock is released without any of it landing.
    editor = _session(factory)
    try:
        row = editor.get(Program, program_id)
        catalogue_writes.apply_direct_edit(
            editor, model=Program, row=row, payload={"overview": second},
            actor="editor@pgcamp.test", blocked=catalogue_writes.TRUST_FIELDS)
        editor.flush()
        editor.rollback()
    finally:
        editor.close()

    approver = _session(factory)
    try:
        claim = approver.get(DataVerification, claim_id)
        review.resolve_field(approver, claim, status="VERIFIED",
                             actor_email="reviewer@pgcamp.test")
        approver.commit()
    finally:
        approver.close()

    check = _session(factory)
    try:
        entity, derived = _assert_entity_agrees_with_register(
            check, program_id, "rolled-back edit then approval")
        assert entity.overview == first, "a rolled-back edit must not leave its value behind"
        assert derived == "VERIFIED", "the only surviving claim was approved"
        assert entity.verified_by == "reviewer@pgcamp.test"
    finally:
        check.close()


def test_the_race_keeps_the_register_and_the_audit_trail_consistent(factory):
    """No privileged rows appear, and the withdrawn record points at no verifier."""
    from app.models.crm import DataVerification
    from app.models.education import Program
    from app.services import catalogue_writes, review

    first = "Original published overview. " * 40
    second = "Replacement overview nobody reviewed. " * 40
    setup = _session(factory)
    try:
        program_id, claim_id = _race_program(setup, overview=first)
    finally:
        setup.close()

    editor = _session(factory)
    try:
        stale = editor.get(Program, program_id)
        approver = _session(factory)
        try:
            claim = approver.get(DataVerification, claim_id)
            review.resolve_field(approver, claim, status="VERIFIED",
                                 actor_email="reviewer@pgcamp.test")
            approver.commit()
        finally:
            approver.close()
        catalogue_writes.apply_direct_edit(
            editor, model=Program, row=stale, payload={"overview": second},
            actor="editor@pgcamp.test", blocked=catalogue_writes.TRUST_FIELDS)
        editor.commit()
    finally:
        editor.close()

    check = _session(factory)
    try:
        entity, _derived = _assert_entity_agrees_with_register(
            check, program_id, "audit trail after the race")
        rows = list(check.execute(select(DataVerification).where(
            DataVerification.entity_type == "program",
            DataVerification.entity_id == program_id).order_by(
            DataVerification.id.asc())).scalars())
        # Exactly the two decisions that were taken: the approval and the edit's new claim.
        assert len(rows) == 2, f"expected 2 register rows, found {len(rows)}"
        assert rows[0].status == "VERIFIED"
        assert rows[0].verified_by == "reviewer@pgcamp.test"
        assert rows[1].status == "NEEDS_REVIEW"
        assert rows[1].verified_at is None and rows[1].verified_by is None
        assert entity.verified_by is None, (
            "the record must not be attributed to a reviewer after its value was withdrawn")
    finally:
        check.close()


def test_an_upload_applied_over_a_concurrent_approval_cannot_keep_the_record_verified(factory):
    """The importer had the same defect as the direct edit, in the same shape.

    Its confidence-lowering block was guarded by ``previous_confidence == "VERIFIED"``, evaluated
    from the row this session had loaded. A record that is awaiting review is not VERIFIED, so the
    block was skipped entirely and emitted no SQL; an approval committing in that window left the
    record certifying an imported value nobody reviewed.
    """
    from app.models.crm import DataVerification
    from app.models.education import Program
    from app.services import importer, review

    first = "Original published overview. " * 40
    setup = _session(factory)
    try:
        program_id, claim_id = _race_program(setup, overview=first)
        name = setup.get(Program, program_id).name
        slug = setup.get(Program, program_id).slug
    finally:
        setup.close()

    uploader = _session(factory)
    try:
        stale = uploader.get(Program, program_id)
        assert stale.confidence == "NEEDS_REVIEW"

        approver = _session(factory)
        try:
            claim = approver.get(DataVerification, claim_id)
            review.resolve_field(approver, claim, status="VERIFIED",
                                 actor_email="reviewer@pgcamp.test")
            approver.commit()
        finally:
            approver.close()

        batch = importer.stage(uploader, entity_type="program", rows=[{
            "university": "pgcamp-university", "name": name, "slug": slug,
            "degree_level": "MASTER", "currency": "USD", "tuition_annual": 21000.0,
            "is_published": True, "source_url": "https://pgcamp.test/upload",
            "source_type": "TIER_1_OFFICIAL_UNIVERSITY",
        }], source_url="https://pgcamp.test/upload", actor="uploader@pgcamp.test")
        result = importer.commit_batch(uploader, batch, actor="uploader@pgcamp.test")
        assert not result["failures"], result
        assert result["updated"] == 1, f"the upload did not update the existing row: {result}"
    finally:
        uploader.close()

    check = _session(factory)
    try:
        entity, derived = _assert_entity_agrees_with_register(
            check, program_id, "upload applied over a concurrent approval")
        assert entity.tuition_annual == 21000.0
        assert derived == "NEEDS_REVIEW", (
            "an imported value nobody reviewed must not stay certified")
    finally:
        check.close()
