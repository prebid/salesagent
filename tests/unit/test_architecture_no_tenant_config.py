"""Guard: no tenant.config column access.

Replaces .pre-commit-hooks no-tenant-config grep hook (PR 4 of #1234).
"""

from __future__ import annotations

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    find_tenant_config_violations,
    format_failure,
    parse_module,
    repo_root,
    src_python_files,
)

_KNOWN_BAD_SNIPPETS = {
    "name_attr": "def f(tenant):\n    return tenant.config",
    "self_attr": "def f(self):\n    return self.tenant.config",
    "name_subscript": "def f(tenant):\n    return tenant['config']",
    "self_subscript": "def f(self):\n    return self.tenant['config']",
}


@pytest.mark.arch_guard
def test_no_tenant_config_access() -> None:
    repo = repo_root()
    violations: list[str] = []
    for path in src_python_files(repo):
        rel = str(path.relative_to(repo))
        if "test_migration" in rel or "postmortem" in rel or "pre-commit" in rel:
            continue
        tree = parse_module(path)
        for lineno in find_tenant_config_violations(tree):
            violations.append(f"{rel}:{lineno}")
    assert not violations, format_failure(
        summary="Use per-field columns, not tenant.config",
        violations=violations,
        fix_hint="Replace tenant.config / tenant['config'] with typed column access.",
        docs_link="docs/development/structural-guards.md",
    )


@pytest.mark.arch_guard
def test_tenant_config_detector_catches_known_bad_snippets() -> None:
    assert_detector_catches_ast_snippets(find_tenant_config_violations, snippets=_KNOWN_BAD_SNIPPETS)
