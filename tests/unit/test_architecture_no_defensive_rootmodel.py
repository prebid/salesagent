"""Guard: no defensive hasattr(x, 'root') RootModel unwrapping.

Replaces .pre-commit-hooks/check_rootmodel_access.py (PR 4 of #1234).
"""

from __future__ import annotations

import re

import pytest

from tests.unit._architecture_helpers import format_failure, repo_root

PATTERN = re.compile(r"""hasattr\([^,]+,\s*["']root["']\)""")

ALLOWED_FILES = {
    "tests/unit/test_architecture_no_defensive_rootmodel.py",
}

_KNOWN_BAD_SNIPPET = "def f(x):\n    if hasattr(x, 'root'):\n        return x.root"


def _find_rootmodel_violations(repo, *, extra_allowed: set[str] | None = None) -> list[str]:
    allowed = ALLOWED_FILES | (extra_allowed or set())
    violations: list[str] = []
    scan_roots = [repo / "src", repo / "tests"]
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            rel = str(path.relative_to(repo))
            if rel in allowed:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, 1):
                if "# noqa: rootmodel" in line:
                    continue
                if PATTERN.search(line):
                    violations.append(f"{rel}:{lineno}")
    return violations


@pytest.mark.arch_guard
def test_no_defensive_rootmodel_access() -> None:
    violations = _find_rootmodel_violations(repo_root())
    assert not violations, format_failure(
        summary="No defensive hasattr(x, 'root') RootModel unwrapping",
        violations=violations,
        fix_hint="Use direct .root access or model_dump(); add '# noqa: rootmodel' if genuinely needed.",
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_rootmodel_detector_catches_known_bad_snippet(tmp_path) -> None:
    bad_file = tmp_path / "src" / "probe.py"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text(_KNOWN_BAD_SNIPPET, encoding="utf-8")
    violations = _find_rootmodel_violations(tmp_path)
    assert violations, "Detector must flag known-bad hasattr(..., 'root') snippet"
