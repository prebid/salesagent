"""Guard: BDD wire-discipline — error handling goes through the wire, not test-side.

Three complementary checks, locking in the universal-wire-dispatch invariant after the
holdouts were migrated:

A. **No test-side error construction** (dispatch-side). A step must NOT
   fabricate the expected error via ``ctx["error"] = SomethingError(...)``. Dispatch the
   malformed/invalid request through the wire so *production* emits the error; assert it via
   ``ctx['result'].assert_wire_error(...)``. (The complementary ``env.call_impl`` bypass is
   enforced by ``test_architecture_bdd_no_direct_call_impl.py`` /
   ``test_architecture_bdd_no_partial_account_call_impl.py`` — there are currently zero
   ``call_impl`` calls in ``tests/bdd/steps/`` after the dlh8/osrl/zh85 migrations.)

B. **No reconstructed-only error assertion** (assertion-side). An error
   ``@then`` step must not assert purely on the lossy reconstructed ``ctx['error']`` via
   ``_get_error_code`` / ``_get_error_dict`` without reading the real wire envelope
   (``_wire_code`` / ``_wire_suggestion`` / ``assert_wire_error`` / ``assert_wire_recovery`` /
   ``assert_wire_is_adcp_envelope`` / ``wire_error_envelope`` / ``ctx['result']``).
   Reconstruction collapses distinct wire codes onto one exception class
   (yields ``RuntimeError`` for an unmapped code); the wire envelope is the buyer-facing
   contract.

C. **No hand-rolled wire-envelope access** (access-pattern, not symbol-name). Check B only
   fires when a step ALSO calls the reconstruction helpers — a step that hand-rolls
   ``getattr(result, "wire_error_envelope", None)`` (or ``result.wire_error_envelope``)
   instead of routing through the single guarded accessor
   (``tests/bdd/steps/_outcome_helpers.py``'s ``wire_error_dict`` /
   ``wire_error_envelope_or_none``) sails through Check B untouched, because it never
   touches the reconstruction symbols Check B looks for. This was Finding 7
   six sites duplicated the guard logic (loud-raise-on-missing /
   IMPL-synthesized-fallback) that the accessor centralizes. ``_outcome_helpers.py`` (defines
   the accessors) and ``_dispatch.py`` (the harness's sole producer that mirrors the field into
   ``ctx``'s convenience keys) are the only sanctioned direct readers; everywhere else must
   call the accessor.

All three allowlists can only SHRINK. Each entry documents the production gap or tracked
follow-up that keeps it.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._architecture_helpers import assert_violations_match_allowlist

_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"
_TESTS_ROOT = _STEPS_DIR.parent.parent

_WIRE_REFERENCES = (
    "_wire_code",
    "_wire_suggestion",
    "_wire_error_object",
    "_wire_result",
    "assert_wire_error",
    "assert_wire_recovery",
    "assert_wire_is_adcp_envelope",
    "wire_error_envelope",
)

# -- Check A: test-side error construction ------------------------------------
# Keyed by "<relative path> <enclosing func> <ErrorClass>" (NOT line numbers — those
# shift on unrelated edits). Each remaining entry is a 33r0-reclassified production gap.
_ERROR_CONSTRUCTION_ALLOWLIST: set[str] = {
    # Production gap: _SyntheticError wraps the REAL production per-creative error
    # string — production emits unstructured per-creative errors (no machine code). Remove
    # when sync_creatives emits structured per-creative codes.
    "bdd/steps/domain/uc006_sync_creatives.py _promote_creative_errors_to_ctx _SyntheticError",
    # (Retired) The null-date phantom (uc019 _create_media_buy_with_null_dates) is gone:
    # the scenario was retired (schema-impossible + not spec-graded) and resolve_canonical_status
    # now guards the null edge, so no test-side error construction remains here.
}

# -- Check B: reconstructed-only error assertions -----------------------------
_RECONSTRUCTED_ASSERTION_ALLOWLIST: set[str] = set()

# -- Check C: hand-rolled wire-envelope access (access pattern) ---------------
# Keyed by "<relative path> <enclosing func>". Pre-existing sites found the moment
# this check started scanning the ACCESS PATTERN instead of Check B's symbol names
# — none introduced by that change. Tracked at
# https://github.com/prebid/salesagent/issues/1995; remove each entry as it migrates
# onto wire_error_dict / wire_error_envelope_or_none (_outcome_helpers.py).
_WIRE_ENVELOPE_ACCESS_ALLOWLIST: set[str] = {
    # FIXME(#1995): result.wire_error_envelope read directly instead of via the
    # guarded accessor.
    "bdd/steps/domain/uc002_create_media_buy.py _assert_error_outcome",
    # FIXME(#1995): result.wire_error_envelope read directly instead of via the
    # guarded accessor.
    "bdd/steps/domain/uc019_query_media_buys.py then_real_validation_error",
    # FIXME(#1995): result.wire_error_envelope read directly instead of via the
    # guarded accessor (spec-production-gap steps — verify the gap assertion still
    # holds once migrated, see issue for the caveat).
    "bdd/steps/domain/uc002_nfr.py then_rate_limiting_enforced",
    "bdd/steps/domain/uc002_nfr.py then_payload_size_limits",
    "bdd/steps/domain/uc002_nfr.py then_budget_validated_against_min_order",
}

# The only two legitimate direct readers of TransportResult.wire_error_envelope:
# _outcome_helpers.py defines the guarded accessors; _dispatch.py's
# _populate_ctx_from_result is the harness's sole producer that mirrors the field
# (and synthesized_error_envelope) into ctx's convenience keys — a passthrough copy,
# not a re-implementation of the accessor's guard/fallback logic.
_ACCESS_PATTERN_EXEMPT_MODULES = frozenset(
    {
        "bdd/steps/_outcome_helpers.py",
        "bdd/steps/generic/_dispatch.py",
    }
)


def _iter_step_modules() -> list[tuple[str, ast.Module]]:
    out = []
    for py_file in sorted(_STEPS_DIR.rglob("*.py")):
        if py_file.name.startswith("__"):
            continue
        rel = str(py_file.relative_to(_TESTS_ROOT))
        out.append((rel, ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))))
    return out


def _enclosing_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _own_nodes(func: ast.FunctionDef | ast.AsyncFunctionDef):
    """Yield nodes in ``func``'s body but NOT inside any nested function definition.

    Prevents attributing a construction in a nested helper to BOTH the helper and
    its enclosing function (which double-counts under a naive ``ast.walk``).
    """
    stack = list(func.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # a nested function owns its own nodes
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _is_then(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in func.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id == "then":
            return True
    return False


def _error_class_name(call: ast.Call) -> str | None:
    """Return the constructed class name if it ends in 'Error', else None."""
    fn = call.func
    name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
    return name if name and name.endswith("Error") else None


def _func_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """All identifiers/attributes referenced in the function body."""
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


def _find_error_construction() -> set[str]:
    """Find ``ctx["error"] = <X>Error(...)`` assignments in any step function."""
    found: set[str] = set()
    for rel, tree in _iter_step_modules():
        for func in _enclosing_functions(tree):
            for node in _own_nodes(func):
                if not isinstance(node, ast.Assign):
                    continue
                # target ctx["error"]
                if not any(
                    isinstance(t, ast.Subscript)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "ctx"
                    and isinstance(t.slice, ast.Constant)
                    and t.slice.value == "error"
                    for t in node.targets
                ):
                    continue
                if isinstance(node.value, ast.Call) and (cls := _error_class_name(node.value)):
                    found.add(f"{rel} {func.name} {cls}")
    return found


def _find_reconstructed_only_assertions() -> set[str]:
    """Find error @then steps using _get_error_code/_get_error_dict without a wire reference."""
    found: set[str] = set()
    for rel, tree in _iter_step_modules():
        # then_error.py DEFINES the helpers — its wire-first steps reference _wire_code; skip
        # the helper-definition file's own _get_* definitions by requiring a @then decorator.
        for func in _enclosing_functions(tree):
            if not _is_then(func):
                continue
            names = _func_names(func)
            uses_reconstructed = bool({"_get_error_code", "_get_error_dict"} & names)
            uses_wire = bool(set(_WIRE_REFERENCES) & names) or "result" in names
            if uses_reconstructed and not uses_wire:
                found.add(f"{rel} {func.name}")
    return found


def test_no_test_side_error_construction() -> None:
    """0wby: steps must not fabricate ctx['error']; dispatch through the wire instead."""
    assert_violations_match_allowlist(
        _find_error_construction(),
        _ERROR_CONSTRUCTION_ALLOWLIST,
        fix_hint=(
            "A BDD step constructs the expected error test-side (ctx['error'] = SomeError(...)). "
            "Dispatch the malformed/invalid request through the wire (raw flat-kwargs for schema-shape "
            "rejections) so production emits it; assert via ctx['result'].assert_wire_error(...). "
            "See zh85 / 33r0 for the pattern."
        ),
    )


def test_no_reconstructed_only_error_assertion() -> None:
    """ztl6.8: error @then steps must read the wire envelope, not only the lossy ctx['error']."""
    assert_violations_match_allowlist(
        _find_reconstructed_only_assertions(),
        _RECONSTRUCTED_ASSERTION_ALLOWLIST,
        fix_hint=(
            "An error Then-step asserts on the reconstructed ctx['error'] (_get_error_code/_get_error_dict) "
            "without reading the wire envelope. Make it wire-first: read _wire_code(ctx)/_wire_suggestion(ctx) "
            "or ctx['result'].assert_wire_error(...) and fall back to the reconstructed exception only for "
            "IMPL/no-wire. See then_error.py then_error_code / then_suggestion_contains."
        ),
    )


def _is_wire_envelope_attr(node: ast.AST) -> bool:
    """Match ``<anything>.wire_error_envelope`` attribute access."""
    return isinstance(node, ast.Attribute) and node.attr == "wire_error_envelope"


def _is_wire_envelope_getattr(node: ast.AST) -> bool:
    """Match ``getattr(<anything>, "wire_error_envelope", ...)``."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"):
        return False
    return any(isinstance(arg, ast.Constant) and arg.value == "wire_error_envelope" for arg in node.args)


def _find_hand_rolled_wire_envelope_access() -> set[str]:
    """Find direct ``TransportResult.wire_error_envelope`` reads outside the guarded accessor.

    Unlike Check B (symbol-name matching on the reconstruction helpers), this matches the
    ACCESS PATTERN itself — attribute access or ``getattr`` on ``wire_error_envelope`` — so a
    step that hand-rolls the read without ever touching the reconstruction symbols still
    trips it. ``_ACCESS_PATTERN_EXEMPT_MODULES`` names the two sanctioned readers; every other
    module is scanned in full, not gated behind ``@then``, because Finding 7's duplication
    lived in plain helper functions (``_wire_code`` et al.), not directly inside
    ``@then``-decorated steps.
    """
    found: set[str] = set()
    for rel, tree in _iter_step_modules():
        if rel in _ACCESS_PATTERN_EXEMPT_MODULES:
            continue
        for func in _enclosing_functions(tree):
            for node in _own_nodes(func):
                if _is_wire_envelope_attr(node) or _is_wire_envelope_getattr(node):
                    found.add(f"{rel} {func.name}")
    return found


def test_no_hand_rolled_wire_envelope_access() -> None:
    """TransportResult.wire_error_envelope has one reader — the guarded accessor."""
    assert_violations_match_allowlist(
        _find_hand_rolled_wire_envelope_access(),
        _WIRE_ENVELOPE_ACCESS_ALLOWLIST,
        fix_hint=(
            "A step reads TransportResult.wire_error_envelope directly (getattr(result, "
            "'wire_error_envelope', ...) or result.wire_error_envelope) instead of routing through the "
            "single guarded accessor in tests/bdd/steps/_outcome_helpers.py: wire_error_dict(ctx) (loud "
            "guard + IMPL-synthesized fallback) or wire_error_envelope_or_none(ctx) (no guard, real "
            "envelope or None — use before delegating to result.assert_wire_error). "
            "See then_error.py's _wire_code / _wire_suggestion / _wire_error_object / then_error_recovery."
        ),
    )
