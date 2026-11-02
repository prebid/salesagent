"""Guard: BDD step modules must not call call_impl() or _impl() directly.

Transport dispatch should go through dispatch_request() → env.call_via() so that
parametrized scenarios actually execute across all wire transports (A2A/MCP/REST
+ e2e).

Direct call_impl() bypasses transport dispatch and runs IMPL regardless of the
ctx["transport"] value. This is only allowed when marked with a TRANSPORT-BYPASS
comment explaining why (e.g., cross-cutting list under sync env).

Scanning approach: AST — find EVERY function in tests/bdd/steps/ (not only the
@when/@given-decorated ones) and check for .call_impl( or _impl( calls without a
TRANSPORT-BYPASS comment. Scanning only the decorated entry points was a blind
spot: a Then-side module-local helper that calls ``_list_accounts_impl`` grades
IMPL on every transport just as thoroughly as a @when would, and was invisible to
this guard — which is how uc011's ``_persisted_subscribers`` bypass appeared
after the allowlist had been frozen. The decorator is recorded in the violation
label (step vs helper), not used to decide whether to look.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import iter_call_expressions

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"

# Functions that legitimately bypass transport dispatch.
# Each entry: (filename_stem, function_name).
# This allowlist can only shrink — never add new entries. It is EMPTY: the
# list_accounts verb on AccountSyncEnv removed the last reason to hold one.
_ALLOWLIST: set[tuple[str, str]] = set()


def _is_when_or_given_decorated(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function is decorated with @when(...) or @given(...)."""
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call):
            func_node = dec.func
            if isinstance(func_node, ast.Name) and func_node.id in ("when", "given"):
                return True
        if isinstance(dec, ast.Name) and dec.id in ("when", "given"):
            return True
    return False


def _has_direct_impl_call(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function body calls .call_impl() or any _impl() function directly."""
    for node in iter_call_expressions(func):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "call_impl":
            return True
        if isinstance(node.func, ast.Name) and node.func.id.endswith("_impl"):
            return True
    return False


def _has_transport_bypass_comment(source_lines: list[str], func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if the function body has a TRANSPORT-BYPASS comment."""
    # Check lines within the function body
    start = func.lineno  # 1-indexed
    end = func.end_lineno or start
    for line_no in range(start, end + 1):
        if line_no <= len(source_lines) and "TRANSPORT-BYPASS" in source_lines[line_no - 1]:
            return True
    return False


def _scan_bdd_steps() -> list[str]:
    """Find any step-module function with direct call_impl/_impl calls."""
    violations = []
    for py_file in sorted(_BDD_STEPS_DIR.rglob("*.py")):
        if py_file.name.startswith("__"):
            continue
        source = py_file.read_text(encoding="utf-8")
        source_lines = source.splitlines()
        tree = ast.parse(source, filename=str(py_file))
        relative = py_file.relative_to(_BDD_STEPS_DIR.parent.parent)
        file_stem = py_file.stem

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _has_direct_impl_call(node):
                continue
            # Check for TRANSPORT-BYPASS comment
            if _has_transport_bypass_comment(source_lines, node):
                continue
            # Check allowlist
            if (file_stem, node.name) in _ALLOWLIST:
                continue
            kind = "step" if _is_when_or_given_decorated(node) else "helper"
            violations.append(f"{relative}:{node.lineno} {node.name} ({kind})")

    return violations


class TestBddNoDirectCallImpl:
    """Structural guard: step modules must use dispatch_request(), not call_impl()."""

    @pytest.mark.arch_guard
    def test_no_direct_call_impl_in_steps(self):
        """Every function in a step module must reach production through a transport.

        Direct .call_impl() or _impl() calls bypass transport parametrization,
        causing scenarios tagged [mcp] or [a2a] to silently run IMPL. Module-local
        helpers count: a Then-side read-back helper that calls an _impl grades the
        in-process function on every transport, so the row proves nothing about the
        wire the buyer sees. Use a TRANSPORT-BYPASS comment for legitimate exceptions.
        """
        violations = _scan_bdd_steps()
        assert not violations, (
            f"Found {len(violations)} function(s) with direct call_impl/_impl calls "
            f"(use dispatch_request, or env.call_via(...) reading the returned "
            f"TransportResult from a Then-side helper):\n" + "\n".join(f"  {v}" for v in violations)
        )

    @pytest.mark.arch_guard
    def test_allowlist_no_stale_entries(self):
        """Verify every allowlisted function still exists and still bypasses."""
        for file_stem, func_name in _ALLOWLIST:
            # Find the file
            matches = list(_BDD_STEPS_DIR.rglob(f"{file_stem}.py"))
            assert matches, f"Allowlisted file '{file_stem}.py' not found"
            source = matches[0].read_text(encoding="utf-8")
            tree = ast.parse(source)
            found = False
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                    found = True
                    assert _has_direct_impl_call(node), (
                        f"Stale allowlist: {file_stem}.{func_name} no longer calls call_impl/_impl. "
                        "Remove from _ALLOWLIST."
                    )
                    break
            assert found, f"Stale allowlist: {file_stem}.{func_name} not found. Remove from _ALLOWLIST."
