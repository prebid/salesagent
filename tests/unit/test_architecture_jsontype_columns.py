"""Guard: JSON columns must use JSONType, not plain JSON.

Replaces .pre-commit-hooks enforce-jsontype grep hook (PR 4 of #1234).
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import iter_call_expressions, parse_module, repo_root


@pytest.mark.arch_guard
def test_json_columns_use_jsontype() -> None:
    repo = repo_root()
    violations: list[str] = []
    db_dir = repo / "src" / "core" / "database"
    for path in db_dir.rglob("*.py"):
        rel = str(path.relative_to(repo))
        tree = parse_module(path)
        for call in iter_call_expressions(tree):
            func_name: str | None = None
            if isinstance(call.func, ast.Name):
                func_name = call.func.id
            elif isinstance(call.func, ast.Attribute):
                func_name = call.func.attr
            if func_name not in {"Column", "mapped_column"}:
                continue
            if not call.args:
                continue
            first_arg = call.args[0]
            uses_plain_json = (isinstance(first_arg, ast.Name) and first_arg.id == "JSON") or (
                isinstance(first_arg, ast.Attribute) and first_arg.attr == "JSON"
            )
            if uses_plain_json:
                violations.append(f"{rel}:{call.lineno} — use JSONType, not JSON")
    assert not violations, "\n".join(violations)
