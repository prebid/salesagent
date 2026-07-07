"""Guard: BDD step functions must not have identical implementations.

When multiple step functions share the exact same body (after stripping
docstrings), it signals a DRY violation — they should be collapsed into a
single regex/parametrized step or share a common helper.

Scanning approach: AST — collect all @given/@when/@then decorated functions in
``tests/bdd/steps/``, normalize their bodies, and flag groups of 3+ identical
implementations. (2 is tolerable for partition/boundary pairs.)

beads: beads-m6r
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"

# Threshold: flag when N or more functions share the same body
_DUPLICATE_THRESHOLD = 3

# Steps exempt from the 3+ identical-body scan (load-bearing: each suppresses a
# cluster that would otherwise fail test_no_excessive_duplicate_step_bodies).
# Allowlist can only shrink — remove entries when the duplicate cluster is gone.
# Non-load-bearing entries removed per #1560 review; audit tracked in #1561.
_ALLOWED_DUPLICATES: set[str] = {
    # FIXME(#1561): pass-body / duplicate-body stubs in uc019/uc026 pending implementation
    "when_query_by_refs",
    "given_request_with_buyer_ref",
    "given_resubmit_buyer_ref",
    "given_buyer_owns_mb_with_ref_and_id",
    "given_buyer_owns_mb_with_buyer_ref",
    "given_cross_buy_request",
    "given_buyer_owns_pkg_by_buyer_ref",
    "given_partition_buyer_ref",
    "given_boundary_buyer_ref",
}


def _is_step_decorated(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function is decorated with @given, @when, or @then."""
    step_names = {"given", "when", "then"}
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call):
            func_node = dec.func
            if isinstance(func_node, ast.Name) and func_node.id in step_names:
                return True
        if isinstance(dec, ast.Name) and dec.id in step_names:
            return True
    return False


def _normalize_body(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Produce a canonical string representation of the function body.

    Strips the docstring (first Expr with str Constant), then dumps
    remaining statements as AST. This means two functions with
    identical logic but different docstrings will match.
    """
    stmts = list(func.body)
    # Strip docstring
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        stmts = stmts[1:]

    if not stmts:
        return "<empty>"

    return ast.dump(ast.Module(body=stmts, type_ignores=[]))


def _iter_step_functions() -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, int]]:
    """Yield (step_func_node, repo_relative_path, lineno) for every @given/@when/@then under bdd/steps/."""
    for py_file in sorted(_BDD_STEPS_DIR.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        relative = str(py_file.relative_to(_BDD_STEPS_DIR.parent.parent))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_step_decorated(node):
                yield node, relative, node.lineno


def _scan_bdd_steps() -> list[tuple[str, list[str]]]:
    """Find groups of step functions with identical bodies.

    Returns list of (normalized_body_preview, [func locations]) for groups
    exceeding the threshold.
    """
    body_to_funcs: dict[str, list[str]] = {}

    for node, relative, lineno in _iter_step_functions():
        if node.name in _ALLOWED_DUPLICATES:
            continue
        body_key = _normalize_body(node)
        loc = f"{relative}:{lineno} {node.name}"
        body_to_funcs.setdefault(body_key, []).append(loc)

    return [(key[:80], funcs) for key, funcs in body_to_funcs.items() if len(funcs) >= _DUPLICATE_THRESHOLD]


class TestBddNoDuplicateSteps:
    """Structural guard: step functions must not have identical bodies."""

    @pytest.mark.arch_guard
    def test_no_excessive_duplicate_step_bodies(self):
        """No more than 2 step functions should share the same implementation.

        Groups of 3+ identical bodies indicate a DRY violation that should
        be collapsed into a regex step or shared helper.
        """
        duplicates = _scan_bdd_steps()
        if not duplicates:
            return

        lines = []
        for preview, funcs in duplicates:
            lines.append(f"\n  {len(funcs)} identical bodies (body: {preview}):")
            for f in funcs:
                lines.append(f"    {f}")

        assert not duplicates, (
            f"Found {len(duplicates)} group(s) of step functions with identical bodies "
            f"(threshold: {_DUPLICATE_THRESHOLD}+):" + "".join(lines)
        )

    @pytest.mark.arch_guard
    def test_allowed_duplicate_entries_still_exist(self) -> None:
        """Every _ALLOWED_DUPLICATES entry must still name a live BDD step function.

        Scope: rename/delete detection only — does not assert an entry is
        load-bearing for the 3+ identical-body scan. Non-load-bearing audit:
        #1561.
        """
        step_names = {node.name for node, _, _ in _iter_step_functions()}
        missing = sorted(name for name in _ALLOWED_DUPLICATES if name not in step_names)
        assert not missing, (
            f"Stale _ALLOWED_DUPLICATES entries ({len(missing)}) — step removed/renamed, "
            f"remove from allowlist:\n" + "\n".join(f"  {name}" for name in missing)
        )
