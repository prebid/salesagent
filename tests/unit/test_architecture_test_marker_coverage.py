"""Guard: every unit test must have at least one entity marker.

Entity markers (delivery, creative, product, media_buy, tenant, auth, adapter,
inventory, schema, admin, architecture, targeting, transport, workflow, policy,
agent, infra) allow running any slice of the test suite by domain:

    pytest -m delivery          # all delivery tests
    pytest -m "creative and unit"  # creative unit tests only

Markers are auto-applied by filename patterns in tests/conftest.py. This guard
ensures no test slips through without classification. If a new test file doesn't
match any pattern, either:
1. Add a filename pattern to _ENTITY_PATTERNS in tests/conftest.py, or
2. Add an explicit @pytest.mark.<entity> decorator to the test.

The _ALLOWED_UNMARKED set is an escape hatch for tests pending classification.
It must shrink over time — adding new entries is a code smell.
"""

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_ENTITY_MARKERS = frozenset(
    {
        "delivery",
        "creative",
        "product",
        "media_buy",
        "tenant",
        "auth",
        "adapter",
        "inventory",
        "schema",
        "admin",
        "architecture",
        "targeting",
        "transport",
        "workflow",
        "policy",
        "agent",
        "infra",
    }
)

# Tests here are pending entity classification — list must only shrink.
# Format: "tests/unit/test_file.py::TestClass::test_name" or "tests/unit/test_file.py::test_name"
_ALLOWED_UNMARKED: set[str] = set()


def _collect_unmarked_tests() -> list[str]:
    """Run pytest --collect-only with a negated marker expression to find unmarked tests.

    Returns a list of test node IDs that have no entity marker.
    """
    marker_expr = " or ".join(sorted(_ENTITY_MARKERS))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/",
            "--collect-only",
            "-q",
            "-m",
            f"not ({marker_expr})",
            "--no-header",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=60,
    )

    # Parse output lines. pytest -q outputs lines like:
    #   tests/unit/test_foo.py::test_bar
    #   tests/unit/test_foo.py::TestClass::test_method
    # followed by a blank line and summary like "5 tests collected" or
    # "no tests ran" / "no tests collected"
    unmarked: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        # Skip empty lines, summary lines, and warnings
        if not line:
            continue
        if line.startswith(("=", "-", "no tests", "ERROR")):
            continue
        # Summary line pattern: "N tests collected" or "N deselected"
        if re.match(r"^\d+ (tests?|deselected|selected|warning)", line):
            continue
        # A valid test node ID contains :: separator
        if "::" in line:
            unmarked.append(line)

    return unmarked


def test_all_unit_tests_have_entity_markers():
    """Every unit test must have at least one entity marker for entity-scoped runs.

    Entity markers are auto-applied by filename patterns in tests/conftest.py.
    If this test fails, it means a test file doesn't match any entity pattern.

    Fix options:
    1. Add a filename pattern to _ENTITY_PATTERNS in tests/conftest.py
    2. Add an explicit @pytest.mark.<entity> decorator to the test
    3. Rename the test file to include an entity keyword
    """
    unmarked = _collect_unmarked_tests()

    # Filter out allowed unmarked tests
    new_violations = [t for t in unmarked if t not in _ALLOWED_UNMARKED]

    if new_violations:
        msg_lines = [
            f"Found {len(new_violations)} unit test(s) without any entity marker:",
            "",
        ]
        for test_id in sorted(new_violations):
            msg_lines.append(f"  {test_id}")
        msg_lines.append("")
        msg_lines.append("Every test must have at least one entity marker from:")
        msg_lines.append(f"  {', '.join(sorted(_ENTITY_MARKERS))}")
        msg_lines.append("")
        msg_lines.append("Fix: Add a filename pattern to _ENTITY_PATTERNS in tests/conftest.py,")
        msg_lines.append("or add an explicit @pytest.mark.<entity> decorator to the test.")
        raise AssertionError("\n".join(msg_lines))


def test_allowed_unmarked_entries_still_unmarked():
    """Every _ALLOWED_UNMARKED entry must still be unmarked (stale entry detection).

    When a test gains an entity marker (via pattern or decorator), remove it
    from _ALLOWED_UNMARKED. This test enforces that the allowlist only shrinks.
    """
    if not _ALLOWED_UNMARKED:
        return  # Nothing to check

    unmarked = set(_collect_unmarked_tests())
    stale = _ALLOWED_UNMARKED - unmarked

    if stale:
        msg_lines = [
            "Stale _ALLOWED_UNMARKED entries (tests now have markers — remove from allowlist):",
            "",
        ]
        for test_id in sorted(stale):
            msg_lines.append(f"  {test_id!r},")
        raise AssertionError("\n".join(msg_lines))
