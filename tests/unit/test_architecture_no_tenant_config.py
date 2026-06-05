"""Guard: no tenant.config column access.

Replaces .pre-commit-hooks no-tenant-config grep hook (PR 4 of #1234).
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import parse_module, repo_root, src_python_files


@pytest.mark.arch_guard
def test_no_tenant_config_access() -> None:
    repo = repo_root()
    violations: list[str] = []
    for path in src_python_files(repo):
        rel = str(path.relative_to(repo))
        if "test_migration" in rel or "postmortem" in rel or "pre-commit" in rel:
            continue
        tree = parse_module(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "config":
                if isinstance(node.value, ast.Name) and node.value.id == "tenant":
                    violations.append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.Subscript):
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "tenant"
                    and isinstance(node.slice, ast.Constant)
                    and node.slice.value == "config"
                ):
                    violations.append(f"{rel}:{node.lineno}")
    assert not violations, "Use per-field columns, not tenant.config:\n" + "\n".join(violations)
