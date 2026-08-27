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

import pytest

from tests.unit._bdd_guard_helpers import iter_bdd_steps

# Threshold: flag when N or more functions share the same body
_DUPLICATE_THRESHOLD = 3

# Steps exempt from the 3+ identical-body scan (load-bearing: each suppresses a
# cluster that would otherwise fail test_no_excessive_duplicate_step_bodies).
# Allowlist can only shrink — remove entries when the duplicate cluster is gone.
# Non-load-bearing entries removed per #1560 review; audit tracked in #1561.
# The uc019/uc026 buyer_ref pass-body/duplicate stubs that #1561 tracked no longer
# exist: the media-buy validation refactor stripped top-level buyer_ref from the
# request contract (pinned 04f59d2d5), and the e2e-harness wiring implemented the
# remaining steps. No pass-body stubs remain, so the allowlist is empty.
_ALLOWED_DUPLICATES: set[str] = set()


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


def _scan_bdd_steps() -> list[tuple[str, list[str]]]:
    """Find groups of step functions with identical bodies.

    Returns list of (normalized_body_preview, [func locations]) for groups
    exceeding the threshold.
    """
    body_to_funcs: dict[str, list[str]] = {}

    for step in iter_bdd_steps():
        if step.name in _ALLOWED_DUPLICATES:
            continue
        body_key = _normalize_body(step.node)
        body_to_funcs.setdefault(body_key, []).append(step.key)

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
        step_names = {step.name for step in iter_bdd_steps()}
        missing = sorted(name for name in _ALLOWED_DUPLICATES if name not in step_names)
        assert not missing, (
            f"Stale _ALLOWED_DUPLICATES entries ({len(missing)}) — step removed/renamed, "
            f"remove from allowlist:\n" + "\n".join(f"  {name}" for name in missing)
        )
