"""Guard: allowlist stale-detection must use assert_violations_match_allowlist.

Hand-rolled ``stale = ALLOWLIST - found`` set-diffs duplicate the helper and
drift when one copy is updated. All guard allowlist comparisons route through
``tests.unit._architecture_helpers.assert_violations_match_allowlist``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import repo_root

_HANDROLLED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bstale\s*=\s*\w+\s*-\s*\w+"),
    re.compile(r"\bfixed\s*=\s*\w+\s*-\s*\w+"),
)

_EXEMPT = {
    Path("tests/unit/_architecture_helpers.py"),
    Path("tests/unit/test_architecture_helpers_contract.py"),
    Path("tests/unit/test_architecture_no_handrolled_allowlist_diff.py"),
}


def _find_handrolled_allowlist_diffs() -> list[str]:
    repo = repo_root()
    violations: list[str] = []
    for path in sorted((repo / "tests" / "unit").glob("test_architecture_*.py")):
        rel = path.relative_to(repo)
        if rel in _EXEMPT:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "assert_violations_match_allowlist" in line:
                continue
            for pattern in _HANDROLLED_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{rel}:{lineno}: {line.strip()}")
                    break
    return violations


@pytest.mark.arch_guard
def test_no_handrolled_allowlist_set_diff() -> None:
    """Guard tests must not inline allowlist set-diff logic."""
    violations = _find_handrolled_allowlist_diffs()
    assert not violations, (
        "Hand-rolled allowlist set-diff found — use assert_violations_match_allowlist():\n"
        + "\n".join(f"  {v}" for v in violations)
    )


@pytest.mark.arch_guard
def test_allowlist_diff_guard_catches_known_bad_snippet() -> None:
    """Self-test: the scanner flags inline stale = ALLOWLIST - found."""
    repo = repo_root()
    probe = repo / "tests" / "unit" / "test_architecture_probe_handrolled_allowlist_diff.py"
    probe.write_text("stale = ALLOWLIST - found\n", encoding="utf-8")
    try:
        violations = _find_handrolled_allowlist_diffs()
        assert any("test_architecture_probe_handrolled_allowlist_diff.py" in v for v in violations)
    finally:
        probe.unlink(missing_ok=True)
