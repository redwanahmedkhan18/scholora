"""Live Redis behaviour, exercised against a real server.

The SQLite suite runs with ``REDIS_URL`` empty, so it only ever proves the in-process fallback.
That says nothing about the properties the shared cache is actually there for: a counter two
application processes both see, a TTL the server enforces, a key the server really deletes, or a
connection that recovers after a failure. Those are below.

Runs only when ``SCHOLARA_REDIS_URL`` points at a disposable Redis:

    SCHOLARA_REDIS_URL=redis://127.0.0.1:56379/0 pytest tests/test_redis_live.py

Marked ``redis``, and the CI switch ``SCHOLARA_REQUIRE_REDIS_TESTS=1`` turns a skip into a
failure for the same reason the PostgreSQL switch exists: a skipped test and a passing test look
identical in a summary.
"""
from __future__ import annotations

import os

import pytest
from app.core import cache as cache_module
from app.core.cache import Cache

from skip_policy import REDIS_ONLY_PREFIX

REDIS_URL = os.environ.get("SCHOLARA_REDIS_URL", "").strip()

pytestmark = [
    pytest.mark.redis_live,
    pytest.mark.skipif(
        not REDIS_URL,
        reason=REDIS_ONLY_PREFIX + "SCHOLARA_REDIS_URL is not set: "
        "live Redis verification did not run",
    ),
]


@pytest.fixture()
def live():
    """A cache bound to the disposable server, with its own key namespace."""
    client = Cache(url=REDIS_URL)
    assert client.backend == "redis", (
        f"the facade did not select Redis (backend={client.backend!r}, "
        f"last_error={client.last_error!r}); this test would otherwise prove nothing"
    )
    created: list[str] = []
    yield client, created
    for key in created:              # leave the server as we found it
        try:
            client.delete(key)
        except Exception:            # noqa: BLE001 - cleanup must not mask a test failure
            pass


def test_the_connection_is_real_not_a_fallback(live):
    client, _ = live
    assert client.configured
    assert client.ping() is True


def test_a_counter_is_shared_by_two_independent_client_instances(live):
    """The property an in-process cache cannot provide: two processes, one budget."""
    first, created = live
    second = Cache(url=REDIS_URL)
    key = "live:shared-counter"
    created.append(key)

    first.incr_with_ttl(key, 60)
    first.incr_with_ttl(key, 60)
    count, ttl = second.incr_with_ttl(key, 60)

    assert count == 3, f"the second instance must see the first instance's writes, got {count}"
    assert 0 < ttl <= 60


def test_the_server_enforces_the_ttl(live):
    """A TTL that only exists in Python would never expire after a restart."""
    client, created = live
    key = "live:ttl"
    created.append(key)
    client.set(key, "value", ttl=30)
    # `set` accepts no ttl accessor here on purpose: the TTL that matters is the one the server
    # holds, so it is read from the server itself rather than from a Python shadow copy.
    assert client._client is not None
    server_ttl = client._client.ttl(client._k(key))
    assert 0 < server_ttl <= 30, f"the server holds ttl={server_ttl}"

    # Written straight to the server with a short expiry: if the facade read its own memory the
    # value would still be visible after the server forgot it.
    assert client._client is not None                # assert the server handle is real
    client._client.set(client._k("live:expiring"), "gone-soon", px=80)
    created.append("live:expiring")
    assert client.get("live:expiring") == "gone-soon"
    import time

    time.sleep(0.25)
    assert client.get("live:expiring") is None, "the server must expire the key, not Python"


def test_delete_removes_the_key_on_the_server(live):
    client, created = live
    key = "live:delete"
    created.append(key)
    client.set(key, "value")
    assert client.get(key) == "value"
    client.delete(key)
    assert client.get(key) is None
    # A second delete is not an error; erasure may legitimately repeat one.
    client.delete(key)


def test_json_round_trip_survives_a_second_reader(live):
    client, created = live
    key = "live:json"
    created.append(key)
    client.set_json(key, {"countries": ["Canada", "Germany"], "n": 2})
    other = Cache(url=REDIS_URL)
    assert other.get_json(key) == {"countries": ["Canada", "Germany"], "n": 2}


def test_the_key_prefix_keeps_environments_apart(monkeypatch):
    """Two deployments sharing a Redis must not share a rate-limit budget."""
    a = Cache(url=REDIS_URL)
    a._prefix = "deploy-a:"
    b = Cache(url=REDIS_URL)
    b._prefix = "deploy-b:"
    try:
        a.set("live:prefixed", "a-value")
        assert b.get("live:prefixed") is None, "a second prefix must not read another's key"
        assert a.get("live:prefixed") == "a-value"
    finally:
        a.delete("live:prefixed")


def test_a_dead_server_degrades_to_memory_without_raising(monkeypatch):
    """Rate limiting must keep working, per process, when Redis cannot be reached.

    This is the availability half of the Redis contract. The safety half is structural: sessions,
    lockouts and MFA state live in the database, so a cache outage cannot weaken authentication -
    it can only make rate limiting per-process instead of cluster-wide.
    """
    dead = Cache(url="redis://127.0.0.1:59999/0", )
    assert dead.backend == "memory", "an unreachable server must not be selected"
    assert dead.last_error, "the reason must be recorded rather than swallowed"

    count, ttl = dead.incr_with_ttl("live:dead", 10)
    assert count == 1 and 1 <= ttl <= 10, f"count={count} ttl={ttl}"
    dead.set("live:dead", "x")
    assert dead.get("live:dead") == "x"
    assert dead.ping() is False


def test_a_failing_operation_falls_back_and_then_recovers(live):
    """One failed command must not poison the client for every later command.

    The facade's contract is per-operation: a command that fails is served from the in-process
    cache and the next command tries the server again. A client that latched onto the fallback
    would silently turn a shared rate-limit budget into a per-process one for the rest of the
    process's life.
    """
    client, created = live
    key = "live:recover"
    created.append(key)
    real_client = client._client
    assert real_client is not None

    class _Exploding:
        """Raises for every command it is asked to perform."""

        def get(self, *a, **k):
            raise ConnectionError("simulated: server went away")

        def set(self, *a, **k):
            raise ConnectionError("simulated: server went away")

        def delete(self, *a, **k):
            raise ConnectionError("simulated: server went away")

    client._client = _Exploding()
    client.set(key, "in-memory-while-broken")       # falls back, does not raise
    assert client.get(key) == "in-memory-while-broken"

    client._client = real_client                    # the connection comes back
    client.set(key, "on-the-server")
    assert Cache(url=REDIS_URL).get(key) == "on-the-server", (
        "after recovery the client must write to the server, not to its own memory"
    )


def test_the_module_singleton_is_still_bound_to_settings():
    """The constructor change must not have moved the application's own cache."""
    assert cache_module.cache is not None
    assert cache_module.cache.backend in {"redis", "memory"}
