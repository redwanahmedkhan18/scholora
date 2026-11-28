"""The one place a skip reason is allowed to be written.

A skip is only legitimate when it names the service that is genuinely absent *and* the test
carries the marker for that service. Encoding that in one module, rather than in a dozen
``pytest.skip("the PostgreSQL behaviour ...")`` strings, is what lets
``scripts/ci/pytest_gate.py`` decide a policy instead of guessing at prose.

Rules
-----
* A test marked ``@pytest.mark.pg`` skips through :func:`skip_pg_only`.
* A test marked ``@pytest.mark.redis_live`` skips through :func:`skip_redis_only`.
* Nothing else may call ``pytest.skip`` with a free-text reason: the gate rejects any skip
  whose reason does not begin with one of these prefixes.

The prefix is checked; the text after it is not, so the wording may stay descriptive without
widening the policy.
"""
from __future__ import annotations

import pytest

#: Reason prefix for a test that needs PostgreSQL and has no server in this profile.
PG_ONLY_PREFIX = "pg-only: "
#: Reason prefix for a test that needs live Redis and has no server in this profile.
REDIS_ONLY_PREFIX = "redis-only: "

#: Marker name to the prefix its skips must use.
PREFIXES = {"pg": PG_ONLY_PREFIX, "redis_live": REDIS_ONLY_PREFIX}


def skip_pg_only(reason: str) -> None:
    """Skip a ``@pytest.mark.pg`` test because this profile has no PostgreSQL server."""
    pytest.skip(PG_ONLY_PREFIX + reason)


def skip_redis_only(reason: str) -> None:
    """Skip a ``@pytest.mark.redis_live`` test because this profile has no Redis server."""
    pytest.skip(REDIS_ONLY_PREFIX + reason)
