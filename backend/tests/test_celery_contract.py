"""The Celery delivery contract.

These are configuration assertions, not behavioural tests, and they exist because the failure
they prevent is silent. With the default ``task_acks_late=False`` the broker is told the message
is handled the moment a worker starts it. Kill that worker mid-task and the message is gone:
observed against a real Redis broker as ``queue=0``, ``unacked=0``, and the task never executing
again - which for a deadline reminder means a counsellor is simply never told.

The same reasoning applies to the redelivery window. A message a crashed worker still held is
restored only after the broker's ``visibility_timeout``; left at its one-hour default, recovery of
a five-minute job is up to an hour late.

Acking late is only safe if every job may run twice. ``test_every_scheduled_job_is_safe_to_run_twice``
in ``test_tasks.py`` is the other half of this contract, and both files were changed together.
"""
from __future__ import annotations

import pytest
from app.services import tasks


@pytest.fixture(scope="module")
def app():
    """Build the application the way production builds it, without contacting a broker.

    Previously this skipped unless ``CELERY_BROKER_URL`` was configured, which meant the tests
    that exist to protect these settings never ran in CI - the guard was unguarded. Configuring
    an application is not connecting to one, so ``build_celery_app`` gives the real settings from
    any environment. The broker URL points at a closed port on loopback: nothing here dispatches a
    task, and if one ever did, it would fail immediately rather than reach anything real.
    """
    return tasks.build_celery_app("redis://127.0.0.1:1/0", "")


def test_the_worker_acknowledges_only_after_the_task_finishes(app):
    assert app.conf.task_acks_late is True, (
        "acks_late=False means a worker crash loses the task it was running")
    assert app.conf.task_reject_on_worker_lost is True, (
        "without this the broker keeps the message reserved instead of requeueing it")


def test_a_hung_task_is_bounded(app):
    assert app.conf.task_time_limit, "a task with no time limit can hold a worker slot for ever"
    assert app.conf.task_soft_time_limit
    assert app.conf.task_soft_time_limit < app.conf.task_time_limit, (
        "the soft limit must fire first so a task can clean up before it is killed")


def test_redelivery_after_a_crash_is_not_hours_late(app):
    visibility = (app.conf.broker_transport_options or {}).get("visibility_timeout")
    assert visibility, "the broker's default visibility timeout is an hour"
    assert int(visibility) <= 60 * 60


def test_redelivery_cannot_happen_while_the_task_is_still_running(app):
    """visibility_timeout must outlast the hard time limit, or work is duplicated, not recovered.

    Reproduced against a real broker: with a visibility timeout shorter than the task, a
    replacement worker's startup restore handed the same message to a second worker while the
    first was still running - one task id, two overlapping executions. Recovery and duplication
    are the same mechanism separated only by this ordering.
    """
    visibility = int((app.conf.broker_transport_options or {}).get("visibility_timeout") or 0)
    hard_limit = int(app.conf.task_time_limit or 0)
    assert hard_limit > 0, "without a hard limit no visibility timeout can be correct"
    assert visibility > hard_limit + 60, (
        f"visibility_timeout={visibility}s does not outlast task_time_limit={hard_limit}s: a "
        "task running past the visibility timeout can be delivered to a second worker")


def test_a_worker_does_not_prefetch_a_queue_full_of_messages(app):
    assert app.conf.worker_prefetch_multiplier == 1, (
        "prefetched messages are held in memory by a worker that may be killed")


def test_every_registered_task_carries_the_same_contract(app):
    """A single task with different settings would silently opt out of the guarantees above."""
    for name in app.tasks.keys():
        if not name.startswith("scholara."):
            continue
        task = app.tasks[name]
        assert task.acks_late is not False, f"{name} overrides acks_late"
