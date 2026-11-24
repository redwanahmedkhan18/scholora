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
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict

from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.content import AdCampaign
from app.models.crm import AnalyticsEvent, DataVerification, Lead
from app.models.education import Program, ProgramIntake, Professor, Scholarship, University
from app.services import notifications, retrieval

log = logging.getLogger("scholara.tasks")

VERIFIED_MODELS = [University, Program, Scholarship, Professor, ProgramIntake]


# ------------------------------------------------------------------- jobs
def verification_sweep() -> Dict[str, Any]:
    """Anything past its review date stops claiming to be VERIFIED."""
    db = SessionLocal()
    try:
        today = datetime.utcnow()
        flipped = 0
        for model in VERIFIED_MODELS:
            rows = db.execute(
                select(model).where(
                    model.review_due_at.is_not(None),
                    model.review_due_at < today,
                    model.confidence == "VERIFIED",
                )
            ).scalars().all()
            for row in rows:
                row.confidence = "NEEDS_REVIEW"
                db.add(DataVerification(
                    entity_type=model.__tablename__, entity_id=row.id,
                    field_name="*", field_value="review window elapsed",
                    source_url=getattr(row, "source_url", None),
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
    """Alert staff about application deadlines inside the warning windows."""
    db = SessionLocal()
    try:
        today = date.today()
        sent = 0
        intakes = db.execute(
            select(ProgramIntake).where(
                ProgramIntake.application_deadline.is_not(None),
                ProgramIntake.application_deadline >= today,
                ProgramIntake.application_deadline <= today + timedelta(days=max(windows)),
            )
        ).scalars().all()

        for intake in intakes:
            days = (intake.application_deadline - today).days
            if days not in windows:
                continue
            program = db.get(Program, intake.program_id)
            notifications.broadcast_to_role(
                db, "SENIOR_COUNSELOR", "DEADLINE_APPROACHING",
                days=days, program=program.name if program else f"programme #{intake.program_id}",
                intake=intake.label, deadline=intake.application_deadline.isoformat(),
                entity_type="program_intake", entity_id=intake.id,
                action_url="/admin/program-intakes",
            )
            sent += 1

        scholarships = db.execute(
            select(Scholarship).where(
                Scholarship.deadline.is_not(None),
                Scholarship.deadline >= today,
                Scholarship.deadline <= today + timedelta(days=60),
            )
        ).scalars().all()
        for sch in scholarships:
            days = (sch.deadline - today).days
            if days not in (60, 30, 14):
                continue
            notifications.broadcast_to_role(
                db, "COUNSELOR", "DEADLINE_APPROACHING", days=days, program=sch.name,
                intake="scholarship", deadline=sch.deadline.isoformat(),
                entity_type="scholarship", entity_id=sch.id, action_url="/admin/scholarships",
            )
            sent += 1

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
            rows = db.execute(
                select(model.confidence, func.count(model.id)).group_by(model.confidence)
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


JOBS: Dict[str, Callable[[], Dict[str, Any]]] = {
    "verification_sweep": verification_sweep,
    "deadline_reminders": deadline_reminders,
    "data_quality_digest": data_quality_digest,
    "reindex_knowledge": reindex_knowledge,
    "campaign_metrics": campaign_metrics,
    "check_source_links": check_source_links,
}

# interval in seconds between runs for the in-process scheduler
SCHEDULE: Dict[str, int] = {
    "check_source_links": 7 * 24 * 3600,
    "verification_sweep": 6 * 3600,
    "deadline_reminders": 12 * 3600,
    "data_quality_digest": 7 * 24 * 3600,
    "reindex_knowledge": 3600,
    "campaign_metrics": 1800,
}


# -------------------------------------------------------------- celery wiring
celery_app = None
if settings.celery_broker_url:  # pragma: no cover - needs a broker
    try:
        from celery import Celery
        from celery.schedules import crontab

        celery_app = Celery("scholara", broker=settings.celery_broker_url,
                            backend=settings.celery_broker_url)
        for name, fn in JOBS.items():
            celery_app.task(name=f"scholara.{name}")(fn)

        celery_app.conf.beat_schedule = {
            "verification-sweep": {"task": "scholara.verification_sweep", "schedule": crontab(hour=2, minute=0)},
            "deadline-reminders": {"task": "scholara.deadline_reminders", "schedule": crontab(hour=7, minute=0)},
            "data-quality-digest": {"task": "scholara.data_quality_digest",
                                     "schedule": crontab(hour=6, minute=0, day_of_week=1)},
            "reindex-knowledge": {"task": "scholara.reindex_knowledge", "schedule": crontab(minute=0)},
            "campaign-metrics": {"task": "scholara.campaign_metrics", "schedule": crontab(minute="*/30")},
            # Weekly is enough: institutional URLs rot over months, and a polite checker
            # should not hammer other people's servers.
            "check-source-links": {"task": "scholara.check_source_links",
                                    "schedule": crontab(hour=3, minute=30, day_of_week=0)},
        }
    except Exception as exc:  # broker library missing
        log.warning("Celery unavailable (%s); falling back to the in-process scheduler", exc)
        celery_app = None


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
    if celery_app is not None or not settings.scheduler_enabled or _scheduler is not None:
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
