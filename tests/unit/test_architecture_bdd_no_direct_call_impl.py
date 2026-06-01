"""Guard: BDD When/Given steps must not call call_impl() or _impl() directly.

Transport dispatch should go through dispatch_request() → env.call_via() so that
parametrized scenarios actually execute across all 4 transports (IMPL/A2A/MCP/REST).

Direct call_impl() bypasses transport dispatch and runs IMPL regardless of the
ctx["transport"] value. This is only allowed when marked with a TRANSPORT-BYPASS
comment explaining why (e.g., cross-cutting list under sync env).

Scanning approach: AST — find functions decorated with @when(...) or @given(...)
in tests/bdd/steps/ and check for .call_impl( or _impl( calls without
TRANSPORT-BYPASS comment.

beads: salesagent-ec0
"""

from __future__ import annotations

import ast
from pathlib import Path

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"

# Functions that legitimately bypass transport dispatch.
# Each entry: (filename_stem, function_name).
# This allowlist can only shrink — never add new entries.
_ALLOWLIST: set[tuple[str, str]] = {
    # FIXME(salesagent-ec0): cross-cutting list under sync env —
    # AccountSyncEnv can't dispatch list_accounts requests
    ("uc011_accounts", "when_list_accounts_unfiltered"),
    ("uc011_accounts", "when_list_sandbox_filter"),
}


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
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            # Check for env.call_impl(...)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "call_impl":
                return True
            # Check for _xxx_impl(...)
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
    """Find When/Given steps with direct call_impl/_impl calls."""
    violations = []
    for py_file in sorted(_BDD_STEPS_DIR.rglob("*.py")):
        if py_file.name.startswith("__"):
            continue
        source = py_file.read_text()
        source_lines = source.splitlines()
        tree = ast.parse(source, filename=str(py_file))
        relative = py_file.relative_to(_BDD_STEPS_DIR.parent.parent)
        file_stem = py_file.stem

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_when_or_given_decorated(node):
                continue
            if not _has_direct_impl_call(node):
                continue
            # Check for TRANSPORT-BYPASS comment
            if _has_transport_bypass_comment(source_lines, node):
                continue
            # Check allowlist
            if (file_stem, node.name) in _ALLOWLIST:
                continue
            violations.append(f"{relative}:{node.lineno} {node.name}")

    return violations


class TestBddNoDirectCallImpl:
    """Structural guard: When/Given steps must use dispatch_request(), not call_impl()."""

    def test_no_direct_call_impl_in_steps(self):
        """Every @when/@given step must dispatch through dispatch_request().

        Direct .call_impl() or _impl() calls bypass transport parametrization,
        causing scenarios tagged [mcp] or [a2a] to silently run IMPL.
        Use TRANSPORT-BYPASS comment for legitimate exceptions.
        """
        violations = _scan_bdd_steps()
        assert not violations, (
            f"Found {len(violations)} step(s) with direct call_impl/_impl calls "
            f"(use dispatch_request or add TRANSPORT-BYPASS comment):\n" + "\n".join(f"  {v}" for v in violations)
        )

    def test_allowlist_no_stale_entries(self):
        """Verify every allowlisted function still exists and still bypasses."""
        for file_stem, func_name in _ALLOWLIST:
            # Find the file
            matches = list(_BDD_STEPS_DIR.rglob(f"{file_stem}.py"))
            assert matches, f"Allowlisted file '{file_stem}.py' not found"
            source = matches[0].read_text()
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
