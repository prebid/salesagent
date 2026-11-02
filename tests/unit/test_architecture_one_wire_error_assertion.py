"""Guard: a test holding a ``TransportResult`` has ONE way to assert its error.

``assert_envelope_shape`` is the primitive: it grades a bare envelope dict, a
caught ``AdCPToolError``, ``exc_info.value``, ``response.json()`` — contexts where
no ``TransportResult`` exists. It earns its place and 33 call sites use it that way.

``TransportResult.assert_wire_error`` is the surface for a dispatched result. It
delegates to the primitive and adds three things the primitive cannot have:

* a ``CODE_TABLE`` emittability check, so a code no raise site can emit is rejected
  rather than silently asserted;
* ``recovery`` defaulting to the PINNED classification for the code, so the
  assertion stays non-vacuous without every scenario re-typing the value;
* a loud failure when no envelope was captured at all, instead of comparing
  ``None``.

Passing ``result.wire_error_envelope`` to the primitive skips all three and gives
the codebase two spellings for one assertion. That is the duplication this guard
removes — and it cannot be expressed in the type system, because the primitive's
legitimate targets and ``wire_error_envelope`` are both ``dict``. Hence an AST
check rather than a signature.

NO ALLOWLIST, deliberately: all 11 pre-existing sites were migrated in the same
change that added this, so there is nothing to grandfather. Allowlists only
shrink; an empty one cannot.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parent.parent

# The primitive itself delegates from assert_wire_error; that call is the one
# legitimate TransportResult-adjacent use, and it lives in the harness.
_SELF_DELEGATION = ("harness/transport.py",)


def _offending_calls() -> list[str]:
    bad: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        rel = path.relative_to(TESTS_ROOT).as_posix()
        if rel.endswith(_SELF_DELEGATION):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "assert_envelope_shape":
                continue
            first = ast.unparse(node.args[0])
            if "wire_error_envelope" in first:
                bad.append(f"{rel}:{node.lineno}: assert_envelope_shape({first})")
    return bad


class TestOneWireErrorAssertion:
    @pytest.mark.arch_guard
    def test_transport_result_errors_go_through_assert_wire_error(self) -> None:
        offenders = _offending_calls()
        assert not offenders, (
            "A TransportResult's error envelope was passed to the primitive instead of "
            "asserted through the result:\n  "
            + "\n  ".join(offenders)
            + "\n\nUse `result.assert_wire_error(code, recovery=..., field=..., details=..., "
            "issues=..., retry_after=...)`. It delegates to the same primitive but adds the CODE_TABLE "
            "emittability check, the pinned recovery default, and a loud failure when no "
            "envelope was captured. Do NOT add an allowlist entry — this set is empty."
        )
