"""Adversarial tests for the two CI gates.

``scripts/ci/check_migration_graph.py`` and ``scripts/ci/pytest_gate.py`` are the things that
decide whether a GitHub Actions run is green. A gate that has never been shown to fail is a
gate nobody should trust, so every test here tries to make one of them accept something it must
reject, and one case proves it still accepts the legitimate configuration.

Nothing here needs a database, a Redis server or a network. The fixtures are strings and
throwaway directories, which is the point: these tests run in every profile.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    assert path.is_file(), f"{path} is missing"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load("c9_pytest_gate", REPO_ROOT / "scripts" / "ci" / "pytest_gate.py")
graph = _load("c9_migration_graph", REPO_ROOT / "backend" / "scripts" / "check_migration_graph.py")


# =====================================================================
# The gate's prefix table must agree with the tests' skip helpers
# =====================================================================
def test_the_gate_prefixes_match_the_skip_policy_module():
    """Two copies of the prefix table are a drift risk; this ties them together."""
    from skip_policy import PREFIXES

    assert gate.SKIP_PREFIXES == PREFIXES, (
        "scripts/ci/pytest_gate.py and tests/skip_policy.py disagree about skip prefixes"
    )


# =====================================================================
# Helpers: build a pytest log and a structured report
# =====================================================================
def _log(passed: int, skipped: int = 0, failed: int = 0, xfailed: int = 0, *,
         skips: tuple[tuple[str, str], ...] = (), summary: bool = True) -> str:
    lines = []
    for where, reason in skips:
        # pytest prints ``SKIPPED [n] path:line: reason``; keep the shape realistic so the
        # parser is tested against what it actually sees.
        lines.append(f"SKIPPED [1] {where}:5: {reason}")
    if summary:
        parts = [f"{passed} passed"]
        if failed:
            parts.append(f"{failed} failed")
        if xfailed:
            parts.append(f"{xfailed} xfailed")
        if skipped:
            parts.append(f"{skipped} skipped")
        lines.append(", ".join(parts) + " in 12.34s")
    return "\n".join(lines) + "\n"


def _report(tmp_path: Path, tests: list[dict]) -> str:
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"schema": 1, "skip_prefixes": gate.SKIP_PREFIXES,
                                "tests": tests}), encoding="utf-8")
    return str(path)


def _run(tmp_path: Path, log_text: str, *extra: str) -> int:
    log = tmp_path / "suite.log"
    log.write_text(log_text, encoding="utf-8")
    return gate.main([str(log), *extra])


# =====================================================================
# The gate must FAIL
# =====================================================================
def test_gate_fails_on_an_unexplained_skip(tmp_path):
    log = _log(40, 1, skips=(("tests/test_x.py", "not implemented yet"),))
    assert _run(tmp_path, log, "--profile", "sqlite", "--min-passed", "10") == 1


def test_gate_fails_on_a_newly_introduced_skip_reason(tmp_path):
    """A skip that merely says 'PostgreSQL' is not justification any more."""
    log = _log(40, 1, skips=(("tests/test_x.py", "needs PostgreSQL"),))
    assert _run(tmp_path, log, "--profile", "sqlite", "--min-passed", "10") == 1


def test_gate_fails_when_a_pg_test_skips_in_the_postgres_profile(tmp_path):
    tests = [
        {"nodeid": "tests/test_pg.py::t", "markers": ["pg"], "outcome": "skipped",
         "reason": "pg-only: no server"},
    ]
    log = _log(1500, 1, skips=(("tests/test_pg.py", "pg-only: no server"),))
    code = _run(tmp_path, log, "--profile", "postgres", "--min-passed", "1000",
                "--report", _report(tmp_path, tests))
    assert code == 1


def test_gate_fails_when_a_redis_test_skips_in_the_redis_profile(tmp_path):
    tests = [
        {"nodeid": "tests/test_redis_live.py::t", "markers": ["redis_live"],
         "outcome": "skipped", "reason": "redis-only: no server"},
    ]
    log = _log(1500, 1, skips=(("tests/test_redis_live.py", "redis-only: no server"),))
    code = _run(tmp_path, log, "--profile", "redis", "--min-passed", "1000",
                "--report", _report(tmp_path, tests))
    assert code == 1


def test_gate_fails_when_the_skip_prefix_does_not_match_the_marker(tmp_path):
    """A 'pg-only' reason on an unmarked test is a disguised skip, not a justified one."""
    tests = [{"nodeid": "tests/test_x.py::t", "markers": [], "outcome": "skipped",
              "reason": "pg-only: I would rather not"}]
    log = _log(40, 1, skips=(("tests/test_x.py", "pg-only: I would rather not"),))
    code = _run(tmp_path, log, "--profile", "sqlite", "--min-passed", "10",
                "--report", _report(tmp_path, tests))
    assert code == 1


def test_gate_fails_on_a_skip_from_a_misspelled_environment_variable(tmp_path):
    """SCHOLARA_PG_URI is not SCHOLARA_PG_URL; the typo must not read as a justification."""
    log = _log(40, 1, skips=(("tests/test_pg.py", "SCHOLARA_PG_URI is not set"),))
    assert _run(tmp_path, log, "--profile", "sqlite", "--min-passed", "10") == 1


def test_gate_fails_when_too_few_tests_ran(tmp_path):
    log = _log(3)
    assert _run(tmp_path, log, "--profile", "sqlite", "--min-passed", "1000") == 1


def test_gate_fails_on_failures_even_with_a_high_pass_count(tmp_path):
    log = _log(1184, 1, failed=1)
    assert _run(tmp_path, log, "--profile", "sqlite", "--min-passed", "1000") == 1


def test_gate_fails_when_the_summary_line_is_missing(tmp_path):
    log = _log(40, summary=False)
    assert _run(tmp_path, log, "--profile", "sqlite", "--min-passed", "1") == 1


def test_gate_fails_when_the_summary_line_is_malformed(tmp_path):
    log = "40 passed, 1 skipped\n"  # no "in N.NNs" tail
    assert _run(tmp_path, log, "--profile", "sqlite", "--min-passed", "1") == 1


def test_gate_fails_on_an_unreadable_log(tmp_path):
    assert gate.main([str(tmp_path / "absent.log"), "--profile", "sqlite"]) == 2


def test_gate_fails_when_an_xfail_hides_a_required_test(tmp_path):
    tests = [{"nodeid": "tests/test_pg.py::t", "markers": ["pg"], "outcome": "passed",
              "reason": "", "xfail": "expected to fail"}]
    log = _log(1500, xfailed=1)
    code = _run(tmp_path, log, "--profile", "postgres", "--min-passed", "1000",
                "--report", _report(tmp_path, tests))
    assert code == 1


def test_gate_fails_when_a_required_marker_test_did_not_pass(tmp_path):
    tests = [{"nodeid": "tests/test_pg.py::t", "markers": ["pg"], "outcome": "skipped",
              "reason": "pg-only: quietly skipped"}]
    log = _log(1500, 1, skips=(("tests/test_pg.py", "pg-only: quietly skipped"),))
    code = _run(tmp_path, log, "--profile", "postgres", "--min-passed", "1000",
                "--report", _report(tmp_path, tests))
    assert code == 1


def test_gate_fails_when_a_required_marker_was_deselected(tmp_path):
    """Everything green, but the pg tests never made it into the run at all."""
    tests = [{"nodeid": "tests/test_api.py::t", "markers": [], "outcome": "passed", "reason": ""}]
    log = _log(1500)
    code = _run(tmp_path, log, "--profile", "postgres", "--min-passed", "1000",
                "--min-marked", "pg=40", "--report", _report(tmp_path, tests))
    assert code == 1


def test_gate_fails_for_a_skipped_migration_test_in_the_strict_profile(tmp_path):
    tests = [{"nodeid": "tests/test_migrations.py::t", "markers": [], "outcome": "skipped",
              "reason": "pg-only: no server"}]
    log = _log(10, 1, skips=(("tests/test_migrations.py", "pg-only: no server"),))
    code = _run(tmp_path, log, "--profile", "strict", "--min-passed", "1",
                "--report", _report(tmp_path, tests))
    assert code == 1


def test_gate_fails_without_a_report_when_a_minimum_is_requested(tmp_path):
    log = _log(1500)
    code = _run(tmp_path, log, "--profile", "postgres", "--min-passed", "1000",
                "--min-marked", "pg=40")
    assert code == 1


# =====================================================================
# The gate must PASS
# =====================================================================
def test_gate_passes_for_permitted_skips_in_the_sqlite_profile(tmp_path):
    tests = [
        {"nodeid": "tests/test_pg.py::t", "markers": ["pg"], "outcome": "skipped",
         "reason": "pg-only: SCHOLARA_PG_URL is not set"},
        {"nodeid": "tests/test_redis_live.py::t", "markers": ["redis_live"], "outcome": "skipped",
         "reason": "redis-only: SCHOLARA_REDIS_URL is not set"},
        {"nodeid": "tests/test_api.py::t", "markers": [], "outcome": "passed", "reason": ""},
    ]
    log = _log(1185, 2)
    code = _run(tmp_path, log, "--profile", "sqlite", "--min-passed", "1000",
                "--min-marked", "pg=1", "--report", _report(tmp_path, tests))
    assert code == 0


def test_gate_passes_for_redis_skips_in_a_profile_without_redis(tmp_path):
    tests = [
        {"nodeid": "tests/test_redis_live.py::t", "markers": ["redis_live"], "outcome": "skipped",
         "reason": "redis-only: SCHOLARA_REDIS_URL is not set"},
        {"nodeid": "tests/test_pg.py::t", "markers": ["pg"], "outcome": "passed", "reason": ""},
    ]
    log = _log(1200, 1)
    code = _run(tmp_path, log, "--profile", "postgres", "--min-passed", "1000",
                "--min-marked", "pg=1", "--report", _report(tmp_path, tests))
    assert code == 0


def test_gate_passes_for_a_full_postgres_profile(tmp_path):
    tests = [
        {"nodeid": f"tests/test_pg.py::t{i}", "markers": ["pg"], "outcome": "passed",
         "reason": ""} for i in range(42)
    ] + [
        {"nodeid": "tests/test_redis_live.py::t", "markers": ["redis_live"], "outcome": "skipped",
         "reason": "redis-only: SCHOLARA_REDIS_URL is not set"},
    ]
    log = _log(1235, 1)
    code = _run(tmp_path, log, "--profile", "postgres", "--min-passed", "1000",
                "--min-marked", "pg=40", "--report", _report(tmp_path, tests))
    assert code == 0


def test_gate_passes_for_a_full_redis_profile(tmp_path):
    tests = [
        {"nodeid": f"tests/test_redis_live.py::t{i}", "markers": ["redis_live"],
         "outcome": "passed", "reason": ""} for i in range(9)
    ] + [
        {"nodeid": "tests/test_pg.py::t", "markers": ["pg"], "outcome": "skipped",
         "reason": "pg-only: SCHOLARA_PG_URL is not set"},
    ]
    log = _log(1200, 16)
    code = _run(tmp_path, log, "--profile", "redis", "--min-passed", "1000",
                "--min-marked", "redis_live=9", "--report", _report(tmp_path, tests))
    assert code == 0


def test_gate_fails_when_a_skip_line_cannot_be_parsed(tmp_path):
    """A skip the parser cannot read must not be silently dropped from the policy."""
    log = "SKIPPED [1] tests/test_x.py: not in the expected shape\n40 passed, 1 skipped in 1.00s\n"
    assert _run(tmp_path, log, "--profile", "sqlite", "--min-passed", "10") == 1


def test_gate_passes_a_synthetic_log_in_log_only_mode(tmp_path):
    """The repository-policy mutation test uses this shape."""
    log = _log(40, 1, skips=(("tests/test_pg.py", "pg-only: SCHOLARA_PG_URL is not set"),))
    assert _run(tmp_path, log, "--profile", "sqlite", "--min-passed", "10") == 0


# =====================================================================
# The migration gate must FAIL on each mutation
# =====================================================================
MIGRATIONS = {
    "a1": ("root", None),
    "b2": ("second", "a1"),
    "c3": ("baseline", "b2"),
    "d4": ("recovered", "c3"),
    "e5": ("head", "d4"),
}
BASELINE = "c3"
HEAD = "e5"
REQUIRED = ("d4",)


def _write_versions(path: Path, migrations: dict[str, tuple[str, str | None]]) -> Path:
    versions = path / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    for revision, (label, down) in migrations.items():
        down_literal = "None" if down is None else f'"{down}"'
        (versions / f"{revision}_{label}.py").write_text(
            '"""synthetic revision for a gate test"""\n\n'
            f'revision = "{revision}"\n'
            f"down_revision = {down_literal}\n"
            "branch_labels = None\n"
            "depends_on = None\n\n\n"
            "def upgrade():\n    pass\n\n\n"
            "def downgrade():\n    pass\n",
            encoding="utf-8",
        )
    return versions


def test_migration_gate_accepts_a_valid_linear_graph(tmp_path):
    versions = _write_versions(tmp_path, MIGRATIONS)
    assert graph.main(["--versions", str(versions), "--expect-head", HEAD,
                       "--baseline", BASELINE, "--require", "d4"]) == 0


def test_migration_gate_fails_on_a_wrong_expected_head(tmp_path):
    versions = _write_versions(tmp_path, MIGRATIONS)
    assert graph.main(["--versions", str(versions), "--expect-head", "d4",
                       "--baseline", BASELINE, "--require", "d4"]) == 1


@pytest.mark.parametrize("extra", ["f6", "g7"])
def test_migration_gate_fails_on_two_heads(tmp_path, extra):
    migrations = dict(MIGRATIONS)
    migrations[extra] = ("branch", "d4")  # d4 now has two children, so two heads
    versions = _write_versions(tmp_path, migrations)
    assert graph.main(["--versions", str(versions), "--expect-head", HEAD,
                       "--baseline", BASELINE, "--require", "d4"]) == 1


def test_migration_gate_fails_when_a_required_revision_is_missing(tmp_path):
    migrations = dict(MIGRATIONS)
    del migrations["d4"]
    migrations["e5"] = ("head", "c3")  # reconnected so only the missing revision is wrong
    versions = _write_versions(tmp_path, migrations)
    assert graph.main(["--versions", str(versions), "--expect-head", HEAD,
                       "--baseline", BASELINE, "--require", "d4"]) == 1


def test_migration_gate_fails_when_the_baseline_is_absent(tmp_path):
    versions = _write_versions(tmp_path, MIGRATIONS)
    assert graph.main(["--versions", str(versions), "--expect-head", HEAD,
                       "--baseline", "c3c3c3c3c3c3"]) == 1


def test_migration_gate_fails_when_the_campaign3_head_is_absent(tmp_path):
    """The head id itself is not a revision: the graph must contain what it names."""
    versions = _write_versions(tmp_path, MIGRATIONS)
    assert graph.main(["--versions", str(versions), "--expect-head", "ffffffffffff",
                       "--baseline", BASELINE]) == 1


def test_migration_gate_fails_on_a_disconnected_history(tmp_path):
    migrations = dict(MIGRATIONS)
    migrations["f6"] = ("island", None)  # a second root, with its own head
    versions = _write_versions(tmp_path, migrations)
    assert graph.main(["--versions", str(versions), "--expect-head", HEAD,
                       "--baseline", BASELINE, "--require", "d4"]) == 1


def test_migration_gate_fails_on_a_dangling_parent(tmp_path):
    migrations = dict(MIGRATIONS)
    migrations["e5"] = ("head", "zz")
    versions = _write_versions(tmp_path, migrations)
    assert graph.main(["--versions", str(versions), "--expect-head", HEAD,
                       "--baseline", BASELINE]) == 1


def test_migration_gate_fails_on_a_duplicate_revision_identifier(tmp_path):
    versions = _write_versions(tmp_path, MIGRATIONS)
    (versions / "d4_duplicate.py").write_text(
        'revision = "d4"\ndown_revision = "c3"\n', encoding="utf-8")
    assert graph.main(["--versions", str(versions), "--expect-head", HEAD,
                       "--baseline", BASELINE]) == 1


def test_migration_gate_fails_when_the_chain_skips_the_baseline(tmp_path):
    """Head is right, baseline exists, but they are on different islands."""
    migrations = {
        "a1": ("root", None),
        "c3": ("baseline", "a1"),
        "d4": ("recovered", None),  # its own root: the baseline cannot reach head
        "e5": ("head", "d4"),
    }
    versions = _write_versions(tmp_path, migrations)
    # Two roots are also reported, but the baseline-unreachable problem is the point.
    problems = graph.check(versions, HEAD, BASELINE, REQUIRED)
    assert any("does not reach the published baseline" in p for p in problems), problems


# =====================================================================
# The real repository graph is valid
# =====================================================================
def test_the_real_alembic_graph_is_the_expected_shape():
    versions = REPO_ROOT / "backend" / "alembic" / "versions"
    problems = graph.check(
        versions,
        expect_head="b4c5d6e7f8a9",
        baseline="a7b8c9d0e1f2",
        required=("b8c9d0e1f2a3", "c9d0e1f2a3b4", "d7e8f9a0b1c2",
                  "d9e0f1a2b3c4", "e1f2a3b4c5d6", "f2a3b4c5d6e7", "a3b4c5d6e7f8"),
    )
    assert problems == [], problems
