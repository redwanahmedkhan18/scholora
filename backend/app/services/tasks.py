"""Scheduled background work (§45, §60, §83).

Runs under Celery when CELERY_BROKER_URL is set, otherwise an in-process scheduler
thread executes the same functions. The jobs themselves are plain functions so they
can also be invoked manually from the admin or a cron entry.

Jobs:
  * ``verification_sweep``  — flip records past review_due_at to NEEDS_REVIEW.
  * ``deadline_reminders``  — warn staff about intakes and scholarships closing soon.
  * ``data_quality_digest`` — weekly summary for the data manager.
  * ``reindex_knowledge``   — rebuild the assistant's retrieval corpus.
  * ``campaign_metrics``    — roll up clicks and leads per campaign.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict

from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.content import AdCampaign
from app.models.crm import AnalyticsEvent, DataVerification, Lead
from app.models.education import Professor, Program, ProgramIntake, Scholarship, University
from app.services import notifications, retrieval

log = logging.getLogger("scholara.tasks")

# Heterogeneous registry of tables that carry the verification mixin. The union (rather than a
# bare list of distinct classes, which mypy collapses to ``type[Base]``) keeps the mixin fields
# visible at every use site without resorting to Any.
VerificationModel = type[University] | type[Program] | type[Scholarship] | type[Professor] | type[ProgramIntake]
VERIFIED_MODELS: list[VerificationModel] = [University, Program, Scholarship, Professor, ProgramIntake]


# ------------------------------------------------------------------- jobs
def verification_sweep() -> Dict[str, Any]:
    """Anything past its review date stops claiming to be VERIFIED."""
    db = SessionLocal()
    try:
        today = datetime.utcnow()
        flipped = 0
        for model in VERIFIED_MODELS:
            # `model` is a heterogeneous registry entry; the verification mixin fields live on
            # each concrete class, not on the declarative Base, so read them explicitly.
            review_due_at = model.review_due_at
            confidence = model.confidence
            # Claim the rows in one statement instead of reading then writing them one by one.
            # Two overlapping deliveries of this job (a second worker, a restart, a replacement
            # during a rolling deploy) both used to see the same rows as VERIFIED, both flipped
            # them, and both wrote a provenance row and a notification: reproduced on PostgreSQL
            # as 16 verification rows for 8 records and two identical "N records flipped" notices
            # to every data manager. Whatever this UPDATE returns is exactly what this run owns,
            # so the count, the provenance rows and the notification stay singular.
            claimed = db.execute(
                update(model)
                .where(
                    review_due_at.is_not(None),
                    review_due_at < today,
                    confidence == "VERIFIED",
                )
                .values(confidence="NEEDS_REVIEW")
                .returning(model.id, model.source_url)
                .execution_options(synchronize_session=False)
            ).all()
            for entity_id, source_url in claimed:
                db.add(DataVerification(
                    entity_type=model.__tablename__, entity_id=entity_id,
                    field_name="*", field_value="review window elapsed",
                    source_url=source_url,
                    status="NEEDS_REVIEW",
                    review_due_at=today + timedelta(days=14),
                ))
                flipped += 1
        if flipped:
            db.commit()
            notifications.broadcast_to_role(
                db, "DATA_MANAGER", "VERIFICATION_DUE", count=flipped,
                action_url="/admin/verifications",
            )
        else:
            db.commit()
        log.info("verification_sweep: %s records flipped", flipped)
        return {"job": "verification_sweep", "flipped": flipped}
    finally:
        db.close()


def deadline_reminders(windows: tuple[int, ...] = (30, 14, 7, 3)) -> Dict[str, Any]:
    """Alert staff about application deadlines inside the warning windows.

    Idempotent within a day. The reminder is a *milestone* - "this deadline is 30 days away" -
    so it must reach a counsellor once, and running the job twice (a 12h in-process schedule, two
    beat instances, a manual re-run after a failure) must not send it twice. The windows change
    daily as the deadline approaches, so the next day's run is a different reminder and is not
    suppressed.
    """
    db = SessionLocal()
    try:
        today = date.today()
        day_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        sent = 0
        intakes = db.execute(
            select(ProgramIntake).where(
                ProgramIntake.application_deadline.is_not(None),
                ProgramIntake.application_deadline >= today,
                ProgramIntake.application_deadline <= today + timedelta(days=max(windows)),
            )
        ).scalars().all()

        for intake in intakes:
            if intake.application_deadline is None:  # filtered in SQL; guard for the type checker
                continue
            days = (intake.application_deadline - today).days
            if days not in windows:
                continue
            program = db.get(Program, intake.program_id)
            sent += notifications.broadcast_to_role(
                db, "SENIOR_COUNSELOR", "DEADLINE_APPROACHING",
                days=days, program=program.name if program else f"programme #{intake.program_id}",
                intake=intake.label, deadline=intake.application_deadline.isoformat(),
                entity_type="program_intake", entity_id=intake.id,
                action_url="/admin/program-intakes", dedupe_since=day_start,
            )

        scholarships = db.execute(
            select(Scholarship).where(
                Scholarship.deadline.is_not(None),
                Scholarship.deadline >= today,
                Scholarship.deadline <= today + timedelta(days=60),
            )
        ).scalars().all()
        for sch in scholarships:
            if sch.deadline is None:  # filtered in SQL; guard for the type checker
                continue
            days = (sch.deadline - today).days
            if days not in (60, 30, 14):
                continue
            sent += notifications.broadcast_to_role(
                db, "COUNSELOR", "DEADLINE_APPROACHING", days=days, program=sch.name,
                intake="scholarship", deadline=sch.deadline.isoformat(),
                entity_type="scholarship", entity_id=sch.id, action_url="/admin/scholarships",
                dedupe_since=day_start,
            )

        log.info("deadline_reminders: %s notifications", sent)
        return {"job": "deadline_reminders", "notifications": sent}
    finally:
        db.close()


def data_quality_digest() -> Dict[str, Any]:
    """Weekly snapshot of how trustworthy the database currently is."""
    db = SessionLocal()
    try:
        summary: Dict[str, Any] = {}
        for model in VERIFIED_MODELS:
            confidence = model.confidence
            rows = db.execute(
                select(confidence, func.count(model.id)).group_by(confidence)
            ).all()
            summary[model.__tablename__] = {(c or "UNVERIFIED"): n for c, n in rows}

        conflicts = len(db.execute(
            select(DataVerification).where(DataVerification.status == "CONFLICTING")
        ).scalars().all())

        for entity, states in summary.items():
            for field, count in states.items():
                if field == "CONFLICTING" and count:
                    notifications.broadcast_to_role(
                        db, "DATA_MANAGER", "DATA_CONFLICT", entity=entity, field=f"{count} record(s)",
                        action_url="/admin/verifications",
                        # A digest is a daily-invariant summary: redelivering the task (a crashed
                        # worker, a retry) must not page the same reviewer twice for the same
                        # conflict count.
                        dedupe_since=datetime.now(timezone.utc).replace(
                            hour=0, minute=0, second=0, microsecond=0, tzinfo=None),
                    )
        log.info("data_quality_digest: %s conflicts", conflicts)
        return {"job": "data_quality_digest", "summary": summary, "conflicts": conflicts}
    finally:
        db.close()


def reindex_knowledge() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        counts = retrieval.rebuild_index(db)
        log.info("reindex_knowledge: %s chunks", counts.get("total"))
        return {"job": "reindex_knowledge", **counts}
    finally:
        db.close()


def campaign_metrics() -> Dict[str, Any]:
    """Roll analytics events and leads up onto the campaign records."""
    db = SessionLocal()
    try:
        updated = 0
        for campaign in db.execute(select(AdCampaign)).scalars().all():
            clicks = len(db.execute(
                select(AnalyticsEvent).where(AnalyticsEvent.campaign_id == campaign.id)
            ).scalars().all())
            leads = len(db.execute(
                select(Lead).where(Lead.campaign_id == campaign.id)
            ).scalars().all()) if hasattr(Lead, "campaign_id") else 0
            if campaign.clicks != clicks or getattr(campaign, "leads", None) != leads:
                campaign.clicks = clicks
                if hasattr(campaign, "leads"):
                    campaign.leads = leads
                updated += 1
        db.commit()
        return {"job": "campaign_metrics", "campaigns_updated": updated}
    finally:
        db.close()


def check_source_links() -> Dict[str, Any]:
    """
    Walk the distinct citation URLs and record whether each still resolves.

    Deliberately limited to reachability. It never touches a record's confidence: only a
    named human can say a page still supports the claim we drew from it. Its value is
    catching link rot early - the first run found three citations that had 404'd and were
    silently holding up 39 field claims.
    """
    from app.services import source_verification
    with SessionLocal() as db:
        return {"job": "check_source_links", **source_verification.sweep(db, limit=200)}


def process_pending_deletions(limit: int = 200) -> Dict[str, Any]:
    """Retry stored-object deletions that an erasure queued but could not confirm.

    Erasure removes database rows in a transaction and enqueues the object keys; this job is the
    safety net for a storage backend that was unavailable at that moment. It is idempotent - a
    key that is already gone simply stops being pending - so running it on a schedule is safe.
    """
    from app.services import erasure

    with SessionLocal() as db:
        return {"job": "process_pending_deletions",
                **erasure.process_pending_object_deletions(db, limit=limit)}


JOBS: Dict[str, Callable[[], Dict[str, Any]]] = {
    "verification_sweep": verification_sweep,
    "deadline_reminders": deadline_reminders,
    "data_quality_digest": data_quality_digest,
    "reindex_knowledge": reindex_knowledge,
    "campaign_metrics": campaign_metrics,
    "check_source_links": check_source_links,
    "process_pending_deletions": process_pending_deletions,
}

# interval in seconds between runs for the in-process scheduler
SCHEDULE: Dict[str, int] = {
    "check_source_links": 7 * 24 * 3600,
    "verification_sweep": 6 * 3600,
    "deadline_reminders": 12 * 3600,
    "data_quality_digest": 7 * 24 * 3600,
    "reindex_knowledge": 3600,
    "process_pending_deletions": 300,
    "campaign_metrics": 1800,
}


# -------------------------------------------------------------- celery wiring
def build_celery_app(broker_url: str, result_backend: str = "") -> Any:
    """Build the Celery application without connecting to the broker.

    Upstash TCP URLs are normalised to ``rediss://`` and certificate verification stays
    on. Results are disabled unless CELERY_RESULT_BACKEND is explicitly configured, so a
    missing logical database on Upstash cannot break task execution.
    """
    from celery import Celery
    from celery.schedules import crontab

    from app.core.connectivity import normalize_redis_url

    broker = normalize_redis_url(broker_url)
    backend = normalize_redis_url(result_backend) if result_backend else ""

    app = Celery("scholara", broker=broker, backend=backend or None)
    for name, fn in JOBS.items():
        app.task(name=f"scholara.{name}")(fn)

    app.conf.update(
        task_ignore_result=not bool(backend),
        task_track_started=bool(backend),
        broker_connection_retry_on_startup=True,
        timezone="UTC",
        enable_utc=True,
        # Ack *after* the task finishes, and requeue it if the worker dies mid-run. With the
        # default (ack on receipt) a worker killed while a task is running takes the message with
        # it: observed against a real broker as queue=0, unacked=0, task never executed again
        # (tests/test_celery_contract.py records the settings; the observation is in docs/11).
        # This is only safe because every job is idempotent, which tests/test_tasks.py asserts.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        # A hung task must not hold a worker slot for ever: without a limit a single stuck HTTP
        # call blocks a concurrency slot indefinitely and the queue backs up behind it.
        task_time_limit=15 * 60,
        task_soft_time_limit=13 * 60,
        # How long the broker holds an unacknowledged message before handing it to another
        # worker. The 1 hour default made crash recovery an hour late for a 5-minute job, but
        # too *short* is worse than too long: a value below the hard time limit hands out a
        # message whose task is still running, and two workers then execute it at once. That was
        # reproduced against a real broker - one task id, two overlapping 20 s runs - by starting
        # a replacement worker while the first was still working. The invariant is
        # visibility_timeout > task_time_limit + margin, asserted in tests/test_celery_contract.py.
        broker_transport_options={
            "visibility_timeout": 1800,
            "socket_connect_timeout": int(settings.redis_socket_connect_timeout),
            "socket_timeout": int(settings.redis_socket_timeout),
        },
        # Do not hand a worker more than it is working on: prefetched-but-unstarted messages are
        # exactly what an early ack used to lose.
        worker_prefetch_multiplier=1,
    )
    app.conf.beat_schedule = {
        "verification-sweep": {"task": "scholara.verification_sweep", "schedule": crontab(hour=2, minute=0)},
        "deadline-reminders": {"task": "scholara.deadline_reminders", "schedule": crontab(hour=7, minute=0)},
        "data-quality-digest": {"task": "scholara.data_quality_digest",
                                 "schedule": crontab(hour=6, minute=0, day_of_week=1)},
        "reindex-knowledge": {"task": "scholara.reindex_knowledge", "schedule": crontab(minute=0)},
        # Erasure queues object deletions; this drains the queue when the backend was unavailable.
        "process-pending-deletions": {"task": "scholara.process_pending_deletions",
                                      "schedule": crontab(minute="*/5")},
        "campaign-metrics": {"task": "scholara.campaign_metrics", "schedule": crontab(minute="*/30")},
        # Weekly is enough: institutional URLs rot over months, and a polite checker
        # should not hammer other people's servers.
        "check-source-links": {"task": "scholara.check_source_links",
                                "schedule": crontab(hour=3, minute=30, day_of_week=0)},
    }
    return app


celery_app = None
if settings.celery_broker_url:  # pragma: no cover - needs a broker
    try:
        celery_app = build_celery_app(settings.celery_broker_url, settings.celery_result_backend)
    except Exception as exc:  # broker library missing
        # Log the type only: a broker exception can echo the rediss:// URL, which carries
        # the Upstash password. The scheduler falls back regardless.
        log.warning("Celery unavailable (%s); falling back to the in-process scheduler",
                    type(exc).__name__)
        celery_app = None


def scheduler_mode() -> str:
    """What actually owns the schedule, not merely what was requested."""
    if celery_app is not None:
        return "celery"
    if not settings.scheduler_enabled:
        return "disabled"
    if settings.is_production and not settings.allow_in_process_scheduler_in_production:
        return "disabled"
    return "in-process"


# ---------------------------------------------------- in-process scheduler
class _Scheduler(threading.Thread):
    """Minimal, dependency-free periodic runner for single-node deployments."""

    daemon = True

    def __init__(self) -> None:
        super().__init__(name="scholara-scheduler")
        self._stop = threading.Event()
        self._last: Dict[str, float] = {}

    def run(self) -> None:
        # Let the app finish booting before the first pass.
        self._stop.wait(20)
        while not self._stop.is_set():
            now = time.time()
            for name, interval in SCHEDULE.items():
                if now - self._last.get(name, 0) >= interval:
                    self._last[name] = now
                    try:
                        JOBS[name]()
                    except Exception:
                        log.exception("scheduled job %s failed", name)
            self._stop.wait(60)

    def stop(self) -> None:
        self._stop.set()


_scheduler: _Scheduler | None = None


def start_scheduler() -> bool:
    """Called from the app lifespan. No-op when Celery owns the schedule."""
    global _scheduler
    if _scheduler is not None or scheduler_mode() != "in-process":
        if settings.is_production and celery_app is None:
            log.warning(
                "in-process scheduler is disabled in production; run the Celery worker "
                "and beat services, or set ALLOW_IN_PROCESS_SCHEDULER_IN_PRODUCTION=true "
                "for a deliberately single-instance deployment"
            )
        return False
    _scheduler = _Scheduler()
    _scheduler.start()
    log.info("in-process scheduler started (%s jobs)", len(SCHEDULE))
    return True


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None


def run_job(name: str) -> Dict[str, Any]:
    if name not in JOBS:
        raise KeyError(name)
    return JOBS[name]()
