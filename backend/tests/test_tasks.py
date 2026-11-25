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
