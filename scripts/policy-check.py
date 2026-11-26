#!/usr/bin/env python3
"""Fail-closed check that no prohibited path can reach a commit or a publication.

Policy (permanent): the repository-root ``docs/`` directory and every file whose basename begins
with ``.env`` are never committed or published. This script proves that for the three places it can
be violated — the index, the ignore rules, and an arbitrary commit range — and it fails *closed*: a
Git command that does not answer is an error, never a clean result.

    python3 scripts/policy-check.py                       # index + staged + ignore rules
    python3 scripts/policy-check.py --range origin/main..HEAD

Exit codes: 0 clean, 1 violation found, 2 the check could not be performed.

Standard library only, and NUL-separated output throughout, so a path containing spaces, tabs or
non-ASCII characters is handled exactly like any other path.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Iterable, List, Sequence, Tuple

OK, VIOLATION, ERROR = 0, 1, 2

#: Paths whose behaviour is asserted directly, so a broken ignore rule cannot hide behind an
#: empty file list. Each entry is (path, expected_to_be_ignored).
IGNORE_PROBES: Sequence[Tuple[str, bool]] = (
    ("docs/01-product-requirements.md", True),
    ("docs/deployment/ENVIRONMENT_MATRIX.md", True),
    ("docs/anything-added-later.md", True),
    ("backend/.env", True),
    ("backend/.env.example", True),
    ("frontend/.env.example", True),
    (".env", True),
    (".env.local", True),
    ("README.md", False),
    ("ARCHITECTURE.md", False),
    ("backend/app/main.py", False),
    ("frontend/src/lib/api.ts", False),
)


class InspectionError(RuntimeError):
    """Raised when Git cannot answer, so the caller exits 2 rather than reporting success."""


def git(*args: str) -> bytes:
    """Run a Git command and return raw bytes, or raise :class:`InspectionError`."""
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
    except OSError as exc:                     # git missing entirely
        raise InspectionError(f"could not run git: {exc}") from exc
    if result.returncode != 0:
        raise InspectionError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return result.stdout


def nul_paths(raw: bytes) -> List[str]:
    return [chunk.decode("utf-8", "surrogateescape") for chunk in raw.split(b"\0") if chunk]


def is_prohibited(path: str) -> bool:
    """True for the whole root ``docs/`` tree and any basename beginning ``.env``.

    Matching is case-insensitive on purpose: Git on a case-insensitive filesystem (macOS and
    Windows by default) treats ``.ENV.example`` and ``.env.example`` as the same file, so a
    case-sensitive rule would enforce the policy on Linux and leak the file elsewhere.
    """
    lowered = path.lower()
    basename = lowered.rsplit("/", 1)[-1]
    return lowered == "docs" or lowered.startswith("docs/") or basename.startswith(".env")


def check_tracked() -> List[str]:
    return [p for p in nul_paths(git("ls-files", "-z")) if is_prohibited(p)]


def check_staged() -> List[str]:
    """Staged *additions and modifications* matter; a staged deletion of a prohibited path is
    the remediation itself and must not be reported as a violation."""
    offenders: List[str] = []
    fields = nul_paths(git("diff", "--cached", "--name-status", "-z", "--no-renames"))
    if len(fields) % 2:
        raise InspectionError(
            f"git diff --cached returned {len(fields)} fields, which is not status/path pairs")
    for index in range(0, len(fields), 2):
        status, path = fields[index], fields[index + 1]
        if is_prohibited(path) and not status.startswith("D"):
            offenders.append(path)
    return offenders


def check_ignore() -> List[str]:
    problems: List[str] = []
    for path, expected in IGNORE_PROBES:
        probe = subprocess.run(
            # The probes must describe *this repository's* rules. Without pinning core.excludesFile
            # a global exclude file, or a developer's own config, could satisfy every probe while
            # the published .gitignore still permits docs/ - the guard would pass on a lie.
            ["git", "-c", "core.excludesFile=/dev/null",
             "check-ignore", "-q", "--no-index", "--", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False,
        )
        if probe.returncode not in (0, 1):     # 0 ignored, 1 not ignored
            raise InspectionError(
                f"git check-ignore failed for {path!r}: "
                f"{probe.stderr.decode('utf-8', 'replace').strip()[:200]}")
        ignored = probe.returncode == 0
        if ignored != expected:
            problems.append(f"{path} is {'ignored' if ignored else 'not ignored'}, expected the opposite")
    return problems


def _commit_changes(commit: str) -> List[Tuple[str, str]]:
    """(status, path) for every path one commit touches, root commits and both sides of renames."""
    fields = nul_paths(git("diff-tree", "--no-commit-id", "--name-status", "-r", "-z", "-M",
                           "--root", commit))
    changes: List[Tuple[str, str]] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if not status:
            raise InspectionError(f"git diff-tree returned an empty status for {commit[:7]}")
        arity = 2 if status[0] in ("R", "C") else 1
        if index + arity > len(fields):
            raise InspectionError(
                f"git diff-tree output for {commit[:7]} is truncated after status {status!r}")
        if arity == 2:
            changes.append((status, fields[index]))
            changes.append((status, fields[index + 1]))
        else:
            changes.append((status, fields[index]))
        index += arity
    return changes


def check_range(spec: str) -> List[str]:
    """Inspect **every commit** in the range, not the net difference.

    A net diff is not proof: a commit can add ``docs/private.md`` and a later commit can delete it,
    leaving the published *tree* clean while the prohibited file is still in the published
    *history*, which is exactly what policy forbids. Each commit is therefore inspected on its own
    with ``git diff-tree``, which also reports deletions and both sides of a rename.
    """
    if "..." in spec:
        raise InspectionError(f"{spec!r} is a symmetric range: use BASE..HEAD, which names commits")
    if ".." not in spec:
        raise InspectionError(f"{spec!r} is not a range: expected BASE..HEAD")
    base, head = spec.split("..", 1)
    for endpoint in (base, head):
        git("rev-parse", "--verify", "--quiet", f"{endpoint}^{{commit}}")  # raises if unknown

    offenders: List[str] = []
    commits = nul_paths(git("rev-list", "-z", "--reverse", f"{base}..{head}"))
    for commit in commits:
        for status, path in _commit_changes(commit):
            if not is_prohibited(path):
                continue
            # Deleting a prohibited path is the remediation itself, and is the only permissible way
            # for one to appear in a publication range. Introducing or modifying one is not: the
            # introducing commit is caught here even if a later commit deletes the file again.
            if status.startswith("D"):
                continue
            # A rename is reported on both sides, so moving content *out of* docs/ is flagged too:
            # the old path disappears but its contents are published under an allowed name.
            offenders.append(f"{commit[:7]}:{status} {path}")
    return offenders


def report(title: str, offenders: Iterable[str]) -> bool:
    offenders = list(offenders)
    if offenders:
        print(f"FAIL  {title}")
        for path in offenders:
            print(f"        {path}")
        return False
    print(f"ok    {title}")
    return True


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--range", dest="range_spec", metavar="A..B",
                        help="also check a commit range, e.g. origin/main..HEAD")
    args = parser.parse_args(argv)

    try:
        clean = report("no tracked path is under docs/ or named .env*", check_tracked())
        clean &= report("no prohibited path is staged for addition", check_staged())
        clean &= report(".gitignore excludes prohibited paths", check_ignore())
        if args.range_spec:
            clean &= report(f"no prohibited path in any commit of {args.range_spec}",
                            check_range(args.range_spec))
    except InspectionError as exc:
        print(f"ERROR {exc}")
        print("ERROR the policy could not be verified, so this result is not a pass")
        return ERROR

    print("\nRESULT:", "clean" if clean else "violation")
    return OK if clean else VIOLATION


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
