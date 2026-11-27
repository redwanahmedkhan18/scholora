"""Background job wiring and safe execution (§64).

The jobs read ``SessionLocal`` from this module at call time, so a test can point it at a
private in-memory database. Nothing here touches the shared test database or the network.
"""
from __future__ import annotations

import app.models  # noqa: F401  (registers every table before create_all)
import pytest
from app.core.database import Base
from app.services import tasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def isolated_tasks(monkeypatch):
    """Point the job module at an empty, fully-migrated in-memory database."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    return engine


def test_every_scheduled_job_is_a_registered_job():
    assert set(tasks.SCHEDULE) <= set(tasks.JOBS)
    assert set(tasks.JOBS) == {
        "verification_sweep", "deadline_reminders", "data_quality_digest",
        "reindex_knowledge", "campaign_metrics", "check_source_links",
        # W7: drains the durable object-deletion queue left by a profile erasure.
        "process_pending_deletions",
    }


def test_unknown_job_name_is_rejected():
    with pytest.raises(KeyError):
        tasks.run_job("does_not_exist")


def test_jobs_run_on_an_empty_database_without_error(isolated_tasks):
    sweep = tasks.run_job("verification_sweep")
    assert sweep["flipped"] == 0

    reminders = tasks.run_job("deadline_reminders")
    assert reminders["notifications"] == 0

    digest = tasks.run_job("data_quality_digest")
    assert isinstance(digest, dict)

    campaigns = tasks.run_job("campaign_metrics")
    assert campaigns["campaigns_updated"] == 0


# ------------------------------------------------------- time-driven reminders are not duplicates
def _seed_deadline(isolated_tasks, days_ahead: int = 30):
    """One programme intake whose deadline falls exactly inside a warning window."""
    from datetime import date, timedelta

    from app.models.crm import User
    from app.models.education import Country, Program, ProgramIntake, University

    factory = sessionmaker(bind=isolated_tasks, expire_on_commit=False)
    with factory() as db:
        country = Country(name="Canada", slug="canada", iso2="CA")
        db.add(country)
        db.flush()
        university = University(name="Probe University", slug="probe-university",
                                country_id=country.id)
        db.add(university)
        db.flush()
        program = Program(university_id=university.id, name="Probe MSc", slug="probe-msc",
                          degree_level="MASTER", discipline="Computer Science")
        db.add(program)
        db.flush()
        db.add(ProgramIntake(program_id=program.id, label="Probe Intake",
                             application_deadline=date.today() + timedelta(days=days_ahead)))
        db.add(User(email="counsellor@probe.invalid", full_name="Counsellor",
                    role="SENIOR_COUNSELOR", is_active=True, password_hash="x"))
        db.commit()


def _reminder_rows(isolated_tasks):
    from app.models.crm import Notification
    from sqlalchemy import select

    factory = sessionmaker(bind=isolated_tasks, expire_on_commit=False)
    with factory() as db:
        return list(db.scalars(
            select(Notification).where(Notification.trigger == "DEADLINE_APPROACHING")).all())


def test_deadline_reminders_are_not_duplicated_within_a_day(isolated_tasks):
    """Reproduces the defect: two runs sent every counsellor the same reminder twice.

    Both the in-process scheduler (12h) and two Celery beat instances can run this job more than
    once before the window moves, and each notification is also delivered by email.
    """
    _seed_deadline(isolated_tasks)

    first = tasks.deadline_reminders()
    second = tasks.deadline_reminders()

    assert first["notifications"] == 1, first
    assert second["notifications"] == 0, (
        f"a second run in the same day must not send the reminder again, got {second}")
    assert len(_reminder_rows(isolated_tasks)) == 1


def test_deduplication_does_not_silence_the_next_milestone(isolated_tasks):
    """The window is a milestone, not a daily nag: dedupe must not swallow the 14-day reminder.

    Yesterday's 30-day reminder must not block today's, and a deadline that has moved to 29 days
    (not a window) must send nothing at all - the reminder fires per window, once.
    """
    from datetime import date, datetime, timedelta, timezone

    from app.models.crm import Notification
    from app.models.education import ProgramIntake

    _seed_deadline(isolated_tasks)
    tasks.deadline_reminders()

    factory = sessionmaker(bind=isolated_tasks, expire_on_commit=False)
    with factory() as db:
        reminder = db.query(Notification).first()
        # Timestamps are stored as naive UTC (models/base.py: utcnow), so "yesterday" has to be
        # computed on the same clock. datetime.now() is local time: east of UTC it lands *inside*
        # today's UTC day and the reminder looks like it was sent minutes ago, which made this
        # test pass on a UTC runner and fail on a developer machine in Dhaka.
        reminder.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        intake = db.query(ProgramIntake).first()
        intake.application_deadline = date.today() + timedelta(days=29)   # no longer a window
        db.commit()

    between_windows = tasks.deadline_reminders()
    assert between_windows["notifications"] == 0, (
        "29 days is not a warning window; only 30/14/7/3 are milestones")

    with factory() as db:
        intake = db.query(ProgramIntake).first()
        intake.application_deadline = date.today() + timedelta(days=14)   # the next milestone
        db.commit()

    next_milestone = tasks.deadline_reminders()
    assert next_milestone["notifications"] == 1, (
        "the 14-day reminder must still be sent; dedupe is per reminder, not a daily silence")


def test_event_driven_notifications_are_not_deduplicated():
    """The dedupe belongs to time-driven reminders, not to events that legitimately repeat."""
    import inspect

    from app.services import notifications

    signature = inspect.signature(notifications.broadcast_to_role)
    assert signature.parameters["dedupe_since"].default is None, (
        "event callers (application status, document upload) must keep firing per event")


# --------------------------------------------------------- redelivery safety (acks_late contract)
def test_every_scheduled_job_is_safe_to_run_twice(isolated_tasks):
    """Acking late means any job may be delivered twice; none may duplicate its side effects.

    This is the invariant the Celery contract in ``test_celery_contract.py`` depends on: if a job
    is not idempotent, requeueing it after a crash creates the duplicate instead of preventing the
    loss.
    """

    from app.models.crm import Notification
    from app.models.education import ProgramIntake
    from sqlalchemy import func, select

    _seed_deadline(isolated_tasks)                       # makes deadline_reminders do real work
    factory = sessionmaker(bind=isolated_tasks, expire_on_commit=False)

    def counts():
        with factory() as db:
            return {
                "notifications": db.scalar(select(func.count(Notification.id))),
                "intakes": db.scalar(select(func.count(ProgramIntake.id))),
            }

    for name in sorted(tasks.JOBS):
        tasks.run_job(name)
    once = counts()
    for name in sorted(tasks.JOBS):
        tasks.run_job(name)
    twice = counts()

    assert twice["notifications"] == once["notifications"], (
        f"a second delivery of the schedule created new notifications: {once} -> {twice}")
    assert twice["intakes"] == once["intakes"]


def test_the_digest_is_not_repeated_within_a_day(isolated_tasks):
    """The weekly digest is a summary: redelivery must not page a reviewer twice for it."""
    from app.models.crm import Notification, User
    from app.models.education import Program
    from sqlalchemy import select

    _seed_deadline(isolated_tasks)          # gives the digest a catalogue record to report on
    factory = sessionmaker(bind=isolated_tasks, expire_on_commit=False)
    with factory() as db:
        db.add(User(email="dm@probe.invalid", full_name="Data Manager", role="DATA_MANAGER",
                    is_active=True, password_hash="x"))
        program = db.query(Program).first()
        program.confidence = "CONFLICTING"   # the digest groups the catalogue's own confidence
        db.commit()

    tasks.data_quality_digest()
    tasks.data_quality_digest()

    with factory() as db:
        rows = db.scalars(select(Notification).where(Notification.trigger == "DATA_CONFLICT")).all()
    assert len(rows) == 1, f"the conflict digest must be sent once a day, got {len(rows)}"
