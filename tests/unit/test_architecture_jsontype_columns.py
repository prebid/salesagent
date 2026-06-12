"""Guard: JSON columns must use JSONType, not plain JSON.

Replaces .pre-commit-hooks enforce-jsontype grep hook (PR 4 of #1234).
"""

from __future__ import annotations

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    find_plain_json_column_violations,
    format_failure,
    parse_module,
    repo_root,
)

_KNOWN_BAD_SNIPPETS = {
    "bare_json": "from sqlalchemy import JSON\ndef f():\n    mapped_column(JSON)",
    "sa_json": "import sqlalchemy as sa\ndef f():\n    mapped_column(sa.JSON)",
}


@pytest.mark.arch_guard
def test_json_columns_use_jsontype() -> None:
    repo = repo_root()
    violations: list[str] = []
    db_dir = repo / "src" / "core" / "database"
    for path in db_dir.rglob("*.py"):
        rel = str(path.relative_to(repo))
        tree = parse_module(path)
        for lineno in find_plain_json_column_violations(tree):
            violations.append(f"{rel}:{lineno} — use JSONType, not JSON")
    assert not violations, format_failure(
        summary="JSON DB columns must use JSONType, not plain JSON",
        violations=violations,
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_jsontype_detector_catches_known_bad_snippets() -> None:
    assert_detector_catches_ast_snippets(find_plain_json_column_violations, snippets=_KNOWN_BAD_SNIPPETS)
