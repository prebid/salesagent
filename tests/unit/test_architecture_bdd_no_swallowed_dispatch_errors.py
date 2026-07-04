"""Guard: BDD steps must not clear a dispatch error without asserting on it.

Disease pattern (salesagent-927n): a Given/helper dispatches a request and then
"cleans up" with ``ctx.pop("error", None)`` without first asserting the dispatch
succeeded. A failed Given (e.g. a live-server 401 over e2e_rest) is silently
swallowed, the seeded state never exists, and the scenario fails later with a
misleading read-back error — or worse, passes vacuously.

Canonical pattern: between ``dispatch_request(...)`` and ``ctx.pop("error", ...)``
there must be at least one ``assert`` (on ``ctx.get("error")`` or on the
response) so a failed Given fails loudly (No Quiet Failures).

Clearing ``ctx.pop("error", None)`` BEFORE the dispatch (fresh-state pre-clear)
is fine — only a pop AFTER a dispatch with no intervening assert is flagged.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
    iter_call_expressions,
)

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"

# Allowlist can only shrink — never add new violations, fix them instead.
_ALLOWLIST: set[tuple[str, str]] = set()


def _is_ctx_pop_error(node: ast.Call) -> bool:
    """Match ``ctx.pop("error", ...)``."""
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "pop"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "ctx"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "error"
    )


def _is_dispatch_call(node: ast.Call) -> bool:
    """Match ``dispatch_request(...)`` (bare name or attribute call)."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "dispatch_request"
    return isinstance(func, ast.Attribute) and func.attr == "dispatch_request"


def find_swallowed_dispatch_error_violations(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, function_name) for each swallowed-dispatch-error pop.

    A violation is a ``ctx.pop("error", ...)`` that occurs AFTER a
    ``dispatch_request(...)`` call in the same function, with no ``assert``
    statement between the dispatch and the pop.
    """
    violations: list[tuple[int, str]] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        dispatch_lines: list[int] = []
        pop_lines: list[int] = []
        for call in iter_call_expressions(func):
            if _is_dispatch_call(call):
                dispatch_lines.append(call.lineno)
            elif _is_ctx_pop_error(call):
                pop_lines.append(call.lineno)
        assert_lines = [node.lineno for node in ast.walk(func) if isinstance(node, ast.Assert)]
        for pop_line in pop_lines:
            preceding = [d for d in dispatch_lines if d < pop_line]
            if not preceding:
                continue  # pre-clear before any dispatch — fine
            last_dispatch = max(preceding)
            if not any(last_dispatch < a < pop_line for a in assert_lines):
                violations.append((pop_line, func.name))
    return violations


def _scan_bdd_steps() -> list[tuple[str, str]]:
    """Scan every module under tests/bdd/steps (helpers included)."""
    found: list[tuple[str, str]] = []
    for py_file in sorted(_BDD_STEPS_DIR.rglob("*.py")):
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        relative = str(py_file.relative_to(_BDD_STEPS_DIR.parents[1]))
        for _lineno, func_name in find_swallowed_dispatch_error_violations(tree):
            found.append((relative, func_name))
    return found


class TestBddNoSwallowedDispatchErrors:
    """Structural guard: no ctx.pop("error") after dispatch without an assert."""

    @pytest.mark.arch_guard
    def test_no_swallowed_dispatch_errors(self):
        violations = _scan_bdd_steps()
        new = [(p, n) for p, n in violations if (p, n) not in _ALLOWLIST]
        assert not new, (
            f"Found {len(new)} step/helper(s) that clear ctx['error'] after a "
            "dispatch without asserting the dispatch succeeded:\n"
            + "\n".join(f"  {p}:{n}" for p, n in new)
            + "\n\nAssert ctx.get('error') is None (or on the response) before "
            "clearing — a failed Given must fail loudly (salesagent-927n)."
        )

    @pytest.mark.arch_guard
    def test_allowlist_not_stale(self):
        current = set(_scan_bdd_steps())
        assert_violations_match_allowlist(
            current & _ALLOWLIST,
            _ALLOWLIST,
            fix_hint="Remove fixed entries from _ALLOWLIST.",
        )


class TestDetectorMetaTests:
    """Meta-tests: the detector catches known-bad and passes known-good shapes."""

    @pytest.mark.arch_guard
    def test_detector_catches_known_bad(self):
        assert_detector_catches_ast_snippets(
            lambda tree: [line for line, _ in find_swallowed_dispatch_error_violations(tree)],
            snippets={
                "pop-after-dispatch-no-assert": (
                    "def given_seeded(ctx):\n"
                    "    dispatch_request(ctx, req=req)\n"
                    '    ctx.pop("response", None)\n'
                    '    ctx.pop("error", None)\n'
                ),
                "assert-before-dispatch-does-not-count": (
                    "def given_seeded(ctx):\n"
                    "    assert ctx is not None\n"
                    "    dispatch_request(ctx, req=req)\n"
                    '    ctx.pop("error", None)\n'
                ),
            },
        )

    @pytest.mark.arch_guard
    def test_detector_passes_known_good(self):
        good_snippets = {
            "assert-between-dispatch-and-pop": (
                "def given_seeded(ctx):\n"
                "    dispatch_request(ctx, req=req)\n"
                '    assert ctx.get("error") is None\n'
                '    ctx.pop("error", None)\n'
            ),
            "pre-clear-before-dispatch": (
                'def when_sent(ctx):\n    ctx.pop("error", None)\n    dispatch_request(ctx, req=req)\n'
            ),
            "pop-without-any-dispatch": ('def given_state(ctx):\n    ctx.pop("error", None)\n'),
        }
        for label, source in good_snippets.items():
            tree = ast.parse(source, filename=f"<known-good:{label}>")
            assert not find_swallowed_dispatch_error_violations(tree), f"False positive on known-good: {label}"
