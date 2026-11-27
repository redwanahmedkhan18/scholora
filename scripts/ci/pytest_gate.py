#!/usr/bin/env python3
"""Turn a pytest log into a CI verdict, and refuse to let a required test pass by skipping.

Why this exists
---------------
``SCHOLARA_REQUIRE_PG_TESTS`` / ``SCHOLARA_REQUIRE_REDIS_TESTS`` (``tests/conftest.py``) already
make a live-service skip into a failed *run*. They cannot see the opposite mistake: a job that
claims to test PostgreSQL or Redis while quietly running a profile in which those tests were never
collected (a path filter, a marker expression, a test file renamed). pytest exits 0 in that case,
and a green tick would then mean less than the log says.

So this gate reads the log and asserts three things a green exit code does not:

1. the run finished and produced a summary line;
2. nothing failed or errored;
3. every skip names one of the reason strings the job is allowed to skip for. A job that requires
   Redis may skip PostgreSQL tests (they belong to another job) but nothing else - and a skip for
   any other reason fails here.

It also enforces a minimum number of passing tests per step, so a suite that silently collects
nothing is an error rather than a fast, successful no-op.

    python3 scripts/ci/pytest_gate.py pytest.log --min-passed 16
    python3 scripts/ci/pytest_gate.py full.log --allow-skip-env SCHOLARA_REDIS_URL --min-passed 1000

Exit codes: 0 the log is green, 1 the log is not, 2 the log could not be read.
Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import List, Optional

#: pytest's final line, e.g. ``1062 passed, 4 warnings in 119.01s (0:01:59)``.
SUMMARY = re.compile(
    r"^(?P<body>(?:\d+ (?:passed|failed|error|errors|skipped|deselected|xfailed|xpassed|warning|warnings)"
    r"(?:, )?)+) in \d+\.\d+s",
    re.MULTILINE,
)
#: A per-test skip line, as printed by ``-rs``: ``SKIPPED [1] tests/x.py:12: reason``.
SKIP_LINE = re.compile(r"^SKIPPED \[\d+\] (?P<where>[^:]+):\d+: (?P<reason>.*)$", re.MULTILINE)


def counts(body: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for number, word in re.findall(r"(\d+) (\w+)", body):
        key = {"error": "errors", "warning": "warnings"}.get(word, word)
        found[key] = found.get(key, 0) + int(number)
    return found


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", help="the captured pytest output")
    parser.add_argument("--allow-skip-env", action="append", default=[], metavar="NAME",
                        help="a skip reason that may mention this variable; repeatable")
    parser.add_argument("--min-passed", type=int, default=1,
                        help="fail if fewer tests than this passed (default 1)")
    args = parser.parse_args(argv)

    try:
        with open(args.log, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        print(f"::error::could not read {args.log}: {exc}")
        return 2

    matches = SUMMARY.findall(text)
    if not matches:
        print(f"::error::{args.log} has no pytest summary line - the run did not finish")
        print(text[-4000:])
        return 1
    body = SUMMARY.search(text)
    assert body is not None
    tally = counts(body.group("body"))
    print(f"summary: {tally}")

    problems: List[str] = []
    for outcome in ("failed", "errors"):
        if tally.get(outcome):
            problems.append(f"{tally[outcome]} {outcome}")

    passed = tally.get("passed", 0)
    if passed < args.min_passed:
        problems.append(f"only {passed} tests passed, expected at least {args.min_passed}")

    skips = SKIP_LINE.findall(text)
    unexpected = [
        f"{where}: {reason}"
        for where, reason in skips
        if not any(name in reason for name in args.allow_skip_env)
    ]
    if unexpected:
        problems.append(f"{len(unexpected)} skip(s) for an unexpected reason")
        for line in unexpected:
            print(f"  unexpected skip: {line}")
    elif skips:
        print(f"{len(skips)} skip(s), every one for an allowed reason "
              f"({', '.join(args.allow_skip_env) or 'none allowed'})")

    if problems:
        print(f"::error::pytest gate failed: {'; '.join(problems)}")
        return 1
    print(f"gate ok: {passed} passed, {len(skips)} allowed skip(s), nothing failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
