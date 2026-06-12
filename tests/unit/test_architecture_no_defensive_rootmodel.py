"""Guard: no defensive hasattr(x, 'root') RootModel unwrapping.

Replaces .pre-commit-hooks/check_rootmodel_access.py (PR 4 of #1234).
"""

from __future__ import annotations

import re

import pytest

from tests.unit._architecture_helpers import repo_root

PATTERN = re.compile(r"""hasattr\([^,]+,\s*["']root["']\)""")

ALLOWED_FILES = {
    "tests/unit/test_architecture_no_defensive_rootmodel.py",
}


@pytest.mark.arch_guard
def test_no_defensive_rootmodel_access() -> None:
    repo = repo_root()
    violations: list[str] = []
    scan_roots = [repo / "src", repo / "tests"]
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            rel = str(path.relative_to(repo))
            if rel in ALLOWED_FILES:
                continue
            try:
                lines = path.read_text().splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, 1):
                if "# noqa: rootmodel" in line:
                    continue
                if PATTERN.search(line):
                    violations.append(f"{rel}:{lineno}")
    assert not violations, (
        "Use direct .root access or model_dump(); add '# noqa: rootmodel' if genuinely needed:\n"
        + "\n".join(violations)
    )
