"""The demo seed must never be able to empty a production database.

`seed.run(reset=True)` deletes every row in every table (`wipe`) and then inserts the demo
dataset. It is a development tool, but the command that runs it is short enough to be pasted
into the wrong terminal, so the refusal has to live in the code and has to fire *before*
anything is opened or deleted - not after the wipe, and not as a default that a flag can flip.
"""
from __future__ import annotations

import pytest
from app import seed
from app.core.config import get_settings


class _Abort(Exception):
    """Raised by a spy to stop the seed before it touches the database."""


@pytest.fixture
def production(monkeypatch):
    """Run a block with ENVIRONMENT=production, restored afterwards."""
    previous = get_settings().environment
    get_settings().environment = "production"
    try:
        yield
    finally:
        get_settings().environment = previous


@pytest.fixture
def bombs(monkeypatch):
    """Any attempt to open a session, create a schema or wipe is a test failure."""
    def bomb(*args, **kwargs):
        raise AssertionError("the seed touched the database before refusing a production reset")

    monkeypatch.setattr(seed, "wipe", bomb)
    monkeypatch.setattr(seed, "SessionLocal", bomb)
    monkeypatch.setattr(seed.Base.metadata, "create_all", bomb)
    return bomb


def test_a_production_reset_is_refused(production, bombs):
    """Production + reset=True is a hard stop, not a warning and not a silent no-op."""
    with pytest.raises(SystemExit) as excinfo:
        seed.run(reset=True)
    message = str(excinfo.value)
    assert "production" in message.lower()
    assert "reset" in message.lower()
    # The operator is told what to do instead, so the refusal is actionable.
    assert "alembic upgrade head" in message
    assert "mgmt_admin --create-admin" in message


def test_the_refusal_happens_before_anything_is_opened_or_deleted(production, bombs):
    """No session, no create_all, no wipe: the guard is the first statement that runs."""
    with pytest.raises(SystemExit):
        seed.run()  # reset defaults to True
    # `bombs` raises AssertionError if called; reaching here proves none of them ran.
    assert get_settings().is_production is True


def test_a_development_reset_still_wipes(monkeypatch):
    """The guard must not change development behaviour: reset=True still calls wipe()."""
    calls = []

    def spy(db):
        calls.append(db)
        raise _Abort("stop before the demo dataset is inserted")

    monkeypatch.setattr(seed, "wipe", spy)
    monkeypatch.setattr(seed.Base.metadata, "create_all", lambda *a, **k: None)
    with pytest.raises(_Abort):
        seed.run(reset=True)
    assert len(calls) == 1


def test_reset_false_does_not_wipe(monkeypatch):
    """reset=False is the additive path; it must not delete anything."""
    def bomb(*args, **kwargs):
        raise AssertionError("wipe() must not run when reset=False")

    class _Stop(Exception):
        pass

    class _FakeSession:
        def add(self, *args, **kwargs):
            raise _Stop

        def commit(self):
            raise _Stop

        def close(self):
            pass

        def rollback(self):
            pass

    monkeypatch.setattr(seed, "wipe", bomb)
    monkeypatch.setattr(seed.Base.metadata, "create_all", lambda *a, **k: None)
    monkeypatch.setattr(seed, "SessionLocal", lambda: _FakeSession())
    with pytest.raises(_Stop):
        seed.run(reset=False)


def test_the_guard_reads_the_environment_at_call_time(monkeypatch, production, bombs):
    """A process that changes ENVIRONMENT after import is still protected (and then freed)."""
    with pytest.raises(SystemExit):
        seed.run(reset=True)

    calls = []

    def spy(db):
        calls.append(db)
        raise _Abort

    monkeypatch.setattr(seed, "wipe", spy)
    monkeypatch.setattr(seed, "SessionLocal", lambda: object())
    monkeypatch.setattr(seed.Base.metadata, "create_all", lambda *a, **k: None)
    get_settings().environment = "staging"  # not production: the guard steps aside
    with pytest.raises(_Abort):
        seed.run(reset=True)
    assert len(calls) == 1
