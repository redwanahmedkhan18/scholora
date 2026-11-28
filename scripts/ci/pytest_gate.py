#!/usr/bin/env python3
"""Turn a pytest log into a CI verdict, and refuse to let a required test pass by skipping.

Why this exists
---------------
``SCHOLARA_REQUIRE_PG_TESTS`` / ``SCHOLARA_REQUIRE_REDIS_TESTS`` (``tests/conftest.py``) already
make a live-service skip into a failed *run*. They cannot see the opposite mistake: a job that
claims to test PostgreSQL or Redis while quietly running a profile in which those tests were never
collected (a path filter, a marker expression, a test file renamed). pytest exits 0 in that case,
and a green tick would then mean less than the log says.

So this gate asserts what a green exit code does not:

1. the run finished and produced a summary line;
2. nothing failed or errored, and enough tests passed;
3. every skip is *structurally* justified: its reason must begin with the canonical prefix for a
   marker that the current profile is allowed to skip, and (when a structured report is supplied)
   the skipping test must actually carry that marker;
4. no test that the current profile *requires* was skipped or xfailed;
5. a profile that requires a service ran at least a stated number of that service's tests.

Profiles
--------
``sqlite``    no service required; ``pg`` and ``redis_live`` tests may skip.
``postgres``  ``pg`` required (may not skip); only ``redis_live`` may skip.
``redis``     ``redis_live`` required (may not skip); only ``pg`` may skip.
``strict``    nothing may skip. Used for a module run whose whole point is to execute.

The legacy behaviour of matching a substring of the human-readable reason is gone on purpose: a
reason that merely mentions PostgreSQL is not evidence that the test needed PostgreSQL. The policy
now keys on the closed prefix set in ``tests/skip_policy.py``, and a reason that does not start
with one of those prefixes is an unexpected skip.

    python3 scripts/ci/pytest_gate.py suite.log --profile postgres \\
        --report suite.json --min-passed 1000 --min-marked pg=40

Exit codes: 0 the log is green, 1 the log is not, 2 the log could not be read.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Dict, List, Optional

#: The canonical prefix for each skippable marker. Must equal ``tests/skip_policy.py``; a unit
#: test asserts that the two agree, so this copy cannot drift unnoticed.
SKIP_PREFIXES = {"pg": "pg-only: ", "redis_live": "redis-only: "}

#: What each profile requires (never allowed to skip) and what it may skip.
PROFILES: Dict[str, Dict[str, frozenset]] = {
    "sqlite": {"required": frozenset(), "allowed": frozenset({"pg", "redis_live"})},
    "postgres": {"required": frozenset({"pg"}), "allowed": frozenset({"redis_live"})},
    "redis": {"required": frozenset({"redis_live"}), "allowed": frozenset({"pg"})},
    "strict": {"required": frozenset(), "allowed": frozenset()},
}

#: pytest's final line, e.g. ``1062 passed, 4 warnings in 119.01s (0:01:59)``.
SUMMARY = re.compile(
    r"^(?P<body>(?:\d+ (?:passed|failed|error|errors|skipped|deselected|xfailed|xpassed|warning|warnings)"
    r"(?:, )?)+) in \d+\.\d+s",
    re.MULTILINE,
)
#: A per-test skip line, as printed by ``-rs``: ``SKIPPED [1] tests/x.py:12: reason``. The number
#: in brackets is how many tests share that location and reason, so the capture is a multiplier.
SKIP_LINE = re.compile(
    r"^SKIPPED \[(?P<count>\d+)\] (?P<where>[^:]+):\d+: (?P<reason>.*)$", re.MULTILINE
)


def counts(body: str) -> Dict[str, int]:
    found: Dict[str, int] = {}
    for number, word in re.findall(r"(\d+) (\w+)", body):
        key = {"error": "errors", "warning": "warnings"}.get(word, word)
        found[key] = found.get(key, 0) + int(number)
    return found


def _prefix_allowed(reason: str, allowed: frozenset) -> Optional[str]:
    """Return the marker whose prefix matches ``reason`` and is allowed here, else None."""
    for marker in sorted(allowed):
        prefix = SKIP_PREFIXES[marker]
        if reason.startswith(prefix):
            return marker
    return None


def check_report(
    report_path: str, profile: str, problems: List[str], notes: List[str],
    min_marked: Dict[str, int],
) -> Optional[int]:
    """Cross-check skips against the tests' real markers. Returns the skip count, or None."""
    spec = PROFILES[profile]
    try:
        with open(report_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        problems.append(f"the structured report {report_path} is unreadable: {exc}")
        return None

    tests = payload.get("tests")
    if not isinstance(tests, list):
        problems.append(f"{report_path} has no 'tests' list")
        return None

    skipped = 0
    marked_seen: Dict[str, int] = {}
    for test in tests:
        nodeid = str(test.get("nodeid", "<unknown>"))
        markers = set(test.get("markers") or ())
        outcome = test.get("outcome")
        reason = str(test.get("reason") or "")
        for marker in markers:
            marked_seen[marker] = marked_seen.get(marker, 0) + 1

        if markers & spec["required"]:
            if outcome != "passed":
                problems.append(
                    f"{nodeid} carries a required marker ({sorted(markers & spec['required'])}) "
                    f"and did not pass: {outcome}"
                )
            if test.get("xfail"):
                problems.append(
                    f"{nodeid} carries a required marker and was xfailed, which hides it: "
                    f"{test['xfail']}"
                )

        if outcome != "skipped":
            continue

        skipped += 1
        required_hit = markers & spec["required"]
        if required_hit:
            problems.append(
                f"{nodeid} was SKIPPED but {sorted(required_hit)} is required in the "
                f"{profile} profile: {reason}"
            )
            continue
        matched = _prefix_allowed(reason, spec["allowed"])
        if matched is None:
            problems.append(f"{nodeid} was SKIPPED for an unexpected reason: {reason}")
        elif matched not in markers:
            problems.append(
                f"{nodeid} used the {matched} skip prefix but does not carry that marker: {reason}"
            )
        else:
            notes.append(f"allowed {matched} skip: {nodeid}")

    for marker, minimum in min_marked.items():
        ran = marked_seen.get(marker, 0)
        if ran < minimum:
            problems.append(
                f"the {profile} profile collected only {ran} {marker} test(s), expected at "
                f"least {minimum} - they were deselected, renamed, or filtered out"
            )
        else:
            notes.append(f"{marker}: {ran} collected (>= {minimum})")

    for marker in spec["required"]:
        if not any(marker in (t.get("markers") or ()) for t in tests):
            problems.append(f"the {profile} profile collected no {marker} test at all")
    return skipped


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", help="the captured pytest output")
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES),
                        help="which service profile this run is supposed to prove")
    parser.add_argument("--report", default=None,
                        help="the JSON written by SCHOLARA_PYTEST_JSON (marker cross-check)")
    parser.add_argument("--min-passed", type=int, default=1,
                        help="fail if fewer tests than this passed (default 1)")
    parser.add_argument("--min-marked", action="append", default=[], metavar="MARKER=N",
                        help="fail unless at least N tests carrying MARKER were collected; "
                             "repeatable (detects deselection in any profile)")
    args = parser.parse_args(argv)

    min_marked: Dict[str, int] = {}
    for item in args.min_marked:
        if "=" not in item:
            parser.error(f"--min-marked expects MARKER=N, got {item!r}")
        marker, _, number = item.partition("=")
        if marker not in SKIP_PREFIXES:
            parser.error(f"--min-marked marker must be one of {sorted(SKIP_PREFIXES)}")
        try:
            min_marked[marker] = int(number)
        except ValueError:
            parser.error(f"--min-marked expects an integer count, got {number!r}")

    try:
        with open(args.log, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        print(f"::error::could not read {args.log}: {exc}")
        return 2

    found = SUMMARY.search(text)
    if found is None:
        print(f"::error::{args.log} has no pytest summary line - the run did not finish")
        print(text[-4000:])
        return 1
    tally = counts(found.group("body"))
    spec = PROFILES[args.profile]
    print(f"profile {args.profile}: requires {sorted(spec['required']) or 'nothing'}, "
          f"may skip {sorted(spec['allowed']) or 'nothing'}")
    print(f"summary: {tally}")

    problems: List[str] = []
    notes: List[str] = []
    for outcome in ("failed", "errors"):
        if tally.get(outcome):
            problems.append(f"{tally[outcome]} {outcome}")

    passed = tally.get("passed", 0)
    if passed < args.min_passed:
        problems.append(f"only {passed} tests passed, expected at least {args.min_passed}")

    # An xfail at all is a bypass in every profile here: none of these runs uses one on purpose.
    if tally.get("xfailed"):
        problems.append(f"{tally['xfailed']} test(s) xfailed - an xfail must not stand in for a pass")

    if args.report:
        skipped = check_report(args.report, args.profile, problems, notes, min_marked)
        if skipped is None:
            skipped = 0
    else:
        # Log-only mode: the prefix is the only structural signal available, and it is still
        # closed - a skip whose prefix belongs to a marker this profile requires is rejected
        # because that marker is not in ``allowed``.
        log_skips = SKIP_LINE.findall(text)
        parsed = sum(int(count) for count, _where, _reason in log_skips)
        skipped = parsed
        for count, where, reason in log_skips:
            marker = _prefix_allowed(reason, spec["allowed"])
            if marker is None:
                problems.append(
                    f"{where} ({count} test(s)) was SKIPPED for an unexpected reason: {reason}")
            else:
                notes.append(f"allowed {marker} skip: {where}")
        # A skip line the parser cannot read is not a skip that did not happen. Reconcile the
        # summary's own count against what was parsed, so a format change fails loudly instead
        # of quietly removing every skip from the policy.
        if tally.get("skipped", 0) != parsed:
            problems.append(
                f"the summary reports {tally.get('skipped', 0)} skipped test(s) but only "
                f"{parsed} could be read from the log - refusing to ignore an unparsed skip"
            )
        for marker, minimum in min_marked.items():
            problems.append(
                f"--min-marked {marker}={minimum} cannot be verified without --report"
            )

    for note in notes:
        print(f"  {note}")

    if problems:
        for problem in problems:
            print(f"::error::{problem}")
        print(f"::error::pytest gate failed: {len(problems)} problem(s)")
        return 1
    print(f"gate ok: {passed} passed, {skipped} allowed skip(s), nothing failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
