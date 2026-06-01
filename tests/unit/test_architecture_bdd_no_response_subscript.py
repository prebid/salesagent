"""Guard: BDD Then steps must not read diagnostic ctx keys by subscript.

``resp = ctx["response"]`` raises a bare ``KeyError`` when the When step failed
to populate a response (e.g. production raised, so only ``ctx["error"]`` is set).
A bare KeyError gives no diagnostic — the test output is just ``KeyError:
'response'`` with no hint that the operation errored instead of returning. The
mirror image is ``ctx["error"]`` on a success path: ``KeyError: 'error'`` with
no hint that the operation succeeded when an error was expected.

Then steps must instead use the shared ``_require_response(ctx)`` /
``_require_error(ctx)`` helpers (or the generic ``_require(ctx, key)``), which
fail with a message naming the missing key and surfacing the recorded outcome.
Reading by ``ctx.get(key)`` is also fine — only the bare subscript is the
antipattern.

``env`` is intentionally NOT guarded here: the harness guarantees it and the
``no-silent-env`` guard already requires ``ctx["env"]`` (a hard failure on a
missing env is desired). Entity keys populated by Given steps (tenant, package,
creatives, ...) are out of scope for this guard — see salesagent (qlsx-b).

Writes (``ctx["response"] = ...`` in When steps) are fine — only reads are the
antipattern. This guard is scoped to ``@then`` functions, matching where the
diagnostic matters; When steps that set and immediately read know it is present.

beads: salesagent-o15b (response), salesagent-qlsx (error + generalization)
"""

from __future__ import annotations

import ast

from tests.unit._bdd_guard_helpers import iter_then_functions

# ctx keys whose bare-subscript read in a @then step loses diagnostic context.
# Each maps to the helper that should replace it. To extend the guard to a new
# key, add it here and convert every existing reader in the SAME change — this
# guard has no per-key allowlist (allowlists only shrink, never grow).
_GUARDED_KEYS: dict[str, str] = {
    "response": "_require_response(ctx)",
    "error": "_require_error(ctx)",
}


def _subscript_read_keys(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return the set of guarded ctx keys the function reads via subscript.

    A subscript node ``ctx["error"]`` is a read everywhere except as the direct
    target of an assignment (``ctx["error"] = ...``), which ast models with a
    ``Store`` context on the Subscript.
    """
    found: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Subscript):
            continue
        if isinstance(node.ctx, ast.Store):
            continue
        value = node.value
        slice_ = node.slice
        if (
            isinstance(value, ast.Name)
            and value.id == "ctx"
            and isinstance(slice_, ast.Constant)
            and slice_.value in _GUARDED_KEYS
        ):
            found.add(slice_.value)
    return found


def _scan_guarded_subscripts() -> list[str]:
    """Return ``key — ctx["X"] should be helper`` lines for each violation."""
    violations: list[str] = []
    for key, func in iter_then_functions():
        for ctx_key in sorted(_subscript_read_keys(func)):
            violations.append(f'{key} — ctx["{ctx_key}"] should be {_GUARDED_KEYS[ctx_key]}')
    return violations


class TestBddNoResponseSubscript:
    """Structural guard: Then steps must not subscript-read guarded ctx keys."""

    def test_no_guarded_ctx_subscript_in_then(self):
        """Then steps must use the _require_* helpers, not ctx[key] subscripts."""
        violations = _scan_guarded_subscripts()
        assert not violations, (
            f"Found {len(violations)} Then step(s) reading a guarded ctx key by subscript "
            f"(use the matching _require_* helper for a diagnostic AssertionError instead of a "
            f"bare KeyError):\n  " + "\n  ".join(violations)
        )
