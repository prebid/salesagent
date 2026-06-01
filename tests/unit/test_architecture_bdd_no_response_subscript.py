"""Guard: BDD Then steps must not read ``ctx["response"]`` by subscript.

``resp = ctx["response"]`` raises a bare ``KeyError`` when the When step failed
to populate a response (e.g. production raised, so only ``ctx["error"]`` is set).
A bare KeyError gives no diagnostic — the test output is just ``KeyError:
'response'`` with no hint that the operation errored instead of returning.

Then steps must instead use the shared ``_require_response(ctx)`` helper (or
``ctx.get("response")`` + an explicit ``assert resp is not None, ...``), which
fails with a message naming the missing response and the recorded error.

Writes (``ctx["response"] = ...`` in When steps) are fine — only reads are the
antipattern. This guard is scoped to ``@then`` functions, matching where the
diagnostic matters; When steps that set and immediately read the response know
it is present.

beads: salesagent-o15b
"""

from __future__ import annotations

import ast

from tests.unit._bdd_guard_helpers import iter_then_functions

# Then steps that still read ctx["response"] by subscript. MUST stay empty —
# convert each to _require_response(ctx) rather than allowlisting it.
_ALLOWED_RESPONSE_SUBSCRIPT: set[str] = set()


def _reads_response_subscript(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function reads ``ctx["response"]`` via subscript.

    A subscript node ``ctx["response"]`` is a read everywhere except as the
    direct target of an assignment (``ctx["response"] = ...``), which ast models
    with ``ctx_store`` context on the Subscript.
    """
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
            and slice_.value == "response"
        ):
            return True
    return False


def _scan_response_subscripts() -> list[str]:
    """Return Then-step keys that read ctx["response"] by subscript."""
    return [
        key
        for key, func in iter_then_functions()
        if _reads_response_subscript(func) and key not in _ALLOWED_RESPONSE_SUBSCRIPT
    ]


class TestBddNoResponseSubscript:
    """Structural guard: Then steps must not subscript-read ctx["response"]."""

    def test_no_response_subscript_in_then(self):
        """Then steps must use _require_response(ctx), not ctx["response"]."""
        violations = _scan_response_subscripts()
        assert not violations, (
            f"Found {len(violations)} Then step(s) reading ctx[\"response\"] by subscript "
            f"(use _require_response(ctx) for a diagnostic AssertionError instead of a bare "
            f"KeyError):\n  " + "\n  ".join(violations)
        )
