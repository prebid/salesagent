"""Structural guard: admin-client dependency_overrides use the scoped context manager (L0-22, Agent B Risk #13).

When a test obtains an admin client via ``env.get_admin_client()`` and needs
to inject a dependency override, it MUST use the scoped context manager::

    with env.override_dependency(some_dep, lambda: "value"):
        client.get("/admin/...")

Raw assignment is forbidden::

    env.admin_app.dependency_overrides[some_dep] = lambda: "value"   # BANNED

Why: the scoped form installs the override on ``__enter__`` and removes it
on ``__exit__`` — deterministic cleanup regardless of test-body exceptions
or xdist worker reuse. Raw assignment leaks between tests (Agent B Risk
#13, surfaced in ``flask-to-fastapi-deep-audit.md:582-584``). The
harness's ``__exit__`` DOES clear overrides on teardown, but only as a
safety net — the per-test scope is the canonical path.

This guard AST-scans ``tests/`` (only — production code doesn't touch the
admin-client harness) and flags any ``.dependency_overrides[...] = ...``
write through an expression whose attribute chain ends in ``admin_app``.
It also flags the equivalent ``.dependency_overrides.update({...})``
pattern to close the obvious bypass.

Meta-fixture: ``fixtures/test_harness_overrides_isolated_meta_fixture.py.txt``
contains the exact anti-pattern; the guard's scanner MUST flag it when run
against that fixture (asserted by ``test_guard_regex_catches_known_violation``).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    TESTS,
    iter_python_files,
    relpath,
    walk_py_files,
)

META_FIXTURE = FIXTURES_DIR / "test_harness_overrides_isolated_meta_fixture.py.txt"


def _is_admin_app_chain(node: ast.AST) -> bool:
    """Return True iff ``node`` is an attribute expression ending in ``.admin_app``.

    Examples matched:
      - ``env.admin_app``
      - ``self.env.admin_app``
      - ``env1.admin_app`` (any receiver name)
    Examples NOT matched (we don't care about other app references):
      - ``app``
      - ``some_app``
      - ``get_rest_client()``
    """
    return isinstance(node, ast.Attribute) and node.attr == "admin_app"


def _is_dependency_overrides_on_admin_app(node: ast.AST) -> bool:
    """Return True iff ``node`` is ``<...>.admin_app.dependency_overrides``."""
    return isinstance(node, ast.Attribute) and node.attr == "dependency_overrides" and _is_admin_app_chain(node.value)


def _find_violations_in_tree(tree: ast.AST) -> list[int]:
    """Return line numbers of forbidden admin-app dependency_overrides writes.

    Detects two patterns:
      1. Subscript assignment: ``X.admin_app.dependency_overrides[k] = v``
      2. Update call:          ``X.admin_app.dependency_overrides.update({...})``

    The ``override_dependency`` context manager is the only permitted write
    path for admin-app overrides.
    """
    violations: list[int] = []
    for node in ast.walk(tree):
        # Pattern 1: assignment target is a subscript of dependency_overrides.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and _is_dependency_overrides_on_admin_app(target.value):
                    violations.append(node.lineno)
        # Also catch augmented assignment just in case (e.g. |=).
        if isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Subscript) and _is_dependency_overrides_on_admin_app(node.target.value):
                violations.append(node.lineno)
        # Pattern 2: .update({...}) call on dependency_overrides of admin_app.
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in ("update", "setdefault", "pop", "__setitem__")
                and _is_dependency_overrides_on_admin_app(func.value)
            ):
                violations.append(node.lineno)
    return violations


def _scan_roots() -> list[Path]:
    """Return the roots this guard scans.

    Only ``tests/`` is scanned — production code does not use the admin-client
    harness. The guard's own meta-fixture is excluded so the known violation
    there doesn't trip the main assertion.
    """
    return [TESTS]


def _iter_tree_paths() -> list[tuple[Path, ast.AST]]:
    """Yield (path, tree) pairs for the scan, excluding this guard and its fixture."""
    results: list[tuple[Path, ast.AST]] = []
    this_file = Path(__file__).resolve()
    for path, tree in walk_py_files(_scan_roots()):
        if path.resolve() == this_file:
            continue
        results.append((path, tree))
    return results


class TestHarnessOverridesIsolated:
    """Guard: ``env.admin_app.dependency_overrides`` writes must go through override_dependency."""

    def test_no_direct_override_writes_in_tests(self) -> None:
        """No test writes to ``admin_app.dependency_overrides`` outside override_dependency."""
        offenders: list[str] = []
        for path, tree in _iter_tree_paths():
            lines = _find_violations_in_tree(tree)
            for lineno in lines:
                offenders.append(f"{relpath(path)}:{lineno}")

        assert not offenders, (
            "Admin-client dependency_overrides must use env.override_dependency(...) "
            "context manager, not raw assignment. Agent B Risk #13 "
            "(harness overrides leakage). Violations:\n" + "\n".join(f"  - {o}" for o in offenders)
        )

    def test_guard_regex_catches_known_violation(self) -> None:
        """Meta-test: the guard flags the known-bad pattern in the meta-fixture.

        This proves the scanner logic is actually detecting violations — a
        guard that reports 'clean' because its matcher is broken is worse
        than no guard.
        """
        assert META_FIXTURE.exists(), f"Meta-fixture missing: {META_FIXTURE}"
        tree = ast.parse(META_FIXTURE.read_text(encoding="utf-8"))
        violations = _find_violations_in_tree(tree)
        assert violations, (
            f"Meta-fixture {META_FIXTURE.name} contains a known raw-assignment "
            f"violation but the guard did not flag it — scanner logic is broken."
        )

    def test_scan_root_exists(self) -> None:
        """Sanity: the tests/ root is present and scannable."""
        assert TESTS.exists(), "tests/ root missing — guard has no scan surface"
        assert iter_python_files(
            _scan_roots()
        ), "tests/ contains no .py files — guard would silently pass on empty input"
