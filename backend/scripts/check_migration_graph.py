#!/usr/bin/env python3
"""Verify the Alembic revision graph against the head the release is supposed to be at.

Why this is a script and not five lines of shell
------------------------------------------------
The CI job it replaces checked *the number of head markers* and then grepped the
``alembic history`` text for a list of revision ids. Both are text heuristics: a squashed
revision, a duplicated id, or a history that is two disconnected islands all survive a grep
that only asks "does this string appear somewhere".

This reads the revision files themselves, builds the graph, and asserts its shape:

1. every revision identifier is unique;
2. exactly one head exists;
3. that head is the expected head;
4. the published baseline is present;
5. every required revision is present;
6. every head is the sole head (no second branch);
7. there is exactly one root, and every revision reaches it (no disconnected islands);
8. the chain from the expected head reaches the published baseline, so the baseline can be
   upgraded through the whole recovered chain;
9. no revision names a parent that does not exist.

Standard library only, no database, no import of the application. That keeps it fast and lets
the negative tests drive it with synthetic revision directories.

Exit codes: 0 the graph is exactly the expected shape, 1 it is not.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


class GraphError(Exception):
    """A structural problem with the revision graph."""


def _literal(node: Optional[ast.expr]) -> object:
    if node is None:  # an annotated assignment with no value
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def parse_revisions(versions: Path) -> Tuple[Dict[str, Path], Dict[str, Optional[Tuple[str, ...]]]]:
    """Return ``{revision: path}`` and ``{revision: down_revision tuple or None}``.

    ``down_revision`` may be ``None``, a string, or a tuple/list of strings in a merge
    revision; all three are normalised to a tuple (``None`` for a root).
    """
    revisions: Dict[str, Path] = {}
    parents: Dict[str, Optional[Tuple[str, ...]]] = {}
    for path in sorted(versions.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            raise GraphError(f"{path.name} is not valid Python: {exc}") from exc
        found: Dict[str, object] = {}
        for node in tree.body:
            targets: Sequence[ast.expr]
            value: Optional[ast.expr]
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in ("revision", "down_revision"):
                    found[target.id] = _literal(value)

        revision = found.get("revision")
        if not isinstance(revision, str):
            # A revision file that does not declare an id is not part of the graph; Alembic
            # would refuse it too. Name it rather than skipping silently.
            raise GraphError(f"{path.name} does not declare a revision identifier")
        if revision in revisions:
            raise GraphError(
                f"revision {revision} is declared twice: {revisions[revision].name} and {path.name}"
            )
        revisions[revision] = path
        declared = found.get("down_revision")
        if declared is None:
            parents[revision] = None
        elif isinstance(declared, str):
            parents[revision] = (declared,)
        elif isinstance(declared, (tuple, list)) and all(isinstance(x, str) for x in declared):
            parents[revision] = tuple(declared)
        else:
            raise GraphError(f"{path.name} has an uninterpretable down_revision: {declared!r}")
    if not revisions:
        raise GraphError(f"no revision files found under {versions}")
    return revisions, parents


def heads(revisions: Dict[str, Path], parents: Dict[str, Optional[Tuple[str, ...]]]) -> List[str]:
    referenced = {p for value in parents.values() if value for p in value}
    return sorted(r for r in revisions if r not in referenced)


def roots(parents: Dict[str, Optional[Tuple[str, ...]]]) -> List[str]:
    return sorted(r for r, value in parents.items() if not value)


def check(
    versions: Path,
    expect_head: str,
    baseline: str,
    required: Sequence[str],
) -> List[str]:
    """Return a list of human-readable problems; empty means the graph is correct."""
    problems: List[str] = []
    revisions, parents = parse_revisions(versions)

    # 9. A parent that is not itself a revision is a dangling pointer, which Alembic reports
    #    confusingly much later (or never, if the head is reached first).
    for revision, value in parents.items():
        for parent in value or ():
            if parent not in revisions:
                problems.append(
                    f"{revision} names parent {parent}, which is not a revision file"
                )

    # 6/2. Heads.
    found_heads = heads(revisions, parents)
    if len(found_heads) != 1:
        problems.append(f"the graph has {len(found_heads)} heads, expected exactly 1: {found_heads}")

    # 3. The head must be the expected one. Report both values so the fix is obvious.
    if expect_head not in revisions:
        problems.append(f"the expected head {expect_head} is not a revision in the graph")
    elif found_heads != [expect_head]:
        problems.append(
            f"the head is {found_heads[0] if found_heads else 'none'}, expected {expect_head}"
        )

    # 4. The published baseline must still exist, so the chain is anchored where main is.
    if baseline not in revisions:
        problems.append(f"the published baseline {baseline} is not a revision in the graph")

    # 5. Every required revision must still be present. A squashed or dropped revision can
    #    leave a single head and still pass a head-only check, which is the whole reason this
    #    list exists.
    for revision in required:
        if revision not in revisions:
            problems.append(f"required revision {revision} is missing from the graph")

    # 7. One root, and every revision can reach it. Two roots mean two independent chains,
    #    even if only one of them currently presents a head.
    found_roots = roots(parents)
    if len(found_roots) != 1:
        problems.append(f"the graph has {len(found_roots)} roots, expected exactly 1: {found_roots}")

    unreachable: List[str] = []
    for revision in revisions:
        seen = set()
        current: Optional[str] = revision
        while current is not None:
            if current in seen:
                problems.append(f"the graph contains a cycle through {current}")
                current = None
                break
            seen.add(current)
            value = parents.get(current)
            if value is None:
                break
            # A merge revision has several parents; following the first is enough to prove the
            # revision is connected to a root, and multi-root merges are caught above.
            current = value[0] if value else None
        if not seen & set(found_roots):
            unreachable.append(revision)
    if unreachable:
        problems.append(
            f"{len(unreachable)} revision(s) do not reach a root: {sorted(unreachable)[:8]}"
        )

    # 8. Walking down from the expected head must pass through the published baseline, which is
    #    what makes "upgrade the baseline to head" meaningful.
    if baseline in revisions and expect_head in revisions:
        chain: List[str] = []
        cursor: Optional[str] = expect_head
        while cursor is not None and cursor not in chain:
            chain.append(cursor)
            value = parents.get(cursor)
            cursor = value[0] if value else None
        if baseline not in chain:
            problems.append(
                f"the chain from {expect_head} does not reach the published baseline {baseline}"
            )

    return problems


def describe(versions: Path, expect_head: str, baseline: str, required: Sequence[str]) -> str:
    revisions, parents = parse_revisions(versions)
    found_heads = heads(revisions, parents)
    found_roots = roots(parents)
    lines = [
        f"revision files : {len(revisions)}",
        f"roots          : {found_roots}",
        f"heads          : {found_heads}",
        f"expected head  : {expect_head}",
        f"baseline       : {baseline}",
        f"required       : {len(required)} revision(s)",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--versions", required=True, type=Path,
                        help="the directory holding the Alembic revision files")
    parser.add_argument("--expect-head", required=True,
                        help="the single revision id the graph must end at")
    parser.add_argument("--baseline", required=True,
                        help="the revision published main is at; must remain in the chain")
    parser.add_argument("--require", action="append", default=[], metavar="REVISION",
                        help="a revision that must remain present; repeatable")
    args = parser.parse_args(argv)

    try:
        print(describe(args.versions, args.expect_head, args.baseline, args.require))
        problems = check(args.versions, args.expect_head, args.baseline, args.require)
    except GraphError as exc:
        print(f"::error::migration graph is unreadable: {exc}")
        return 1

    if problems:
        for problem in problems:
            print(f"::error::{problem}")
        print(f"migration graph check failed: {len(problems)} problem(s)")
        return 1

    print(f"migration graph ok: one head ({args.expect_head}), one root, "
          f"baseline {args.baseline} present, all {len(args.require)} required revisions present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
