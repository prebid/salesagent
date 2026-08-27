"""Guard: BDD wire-discipline — error handling goes through the wire, not test-side.

Four complementary checks, locking in the universal-wire-dispatch invariant after the
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
   (``_wire_code`` / ``_wire_suggestion`` / ``assert_wire_error`` / ``wire_error_envelope`` /
   ``ctx['result']``). Reconstruction collapses distinct wire codes onto one exception class
   (yields ``RuntimeError`` for an unmapped code); the wire envelope is the buyer-facing
   contract.

C. **No hand-rolled envelope/error parsing** (assertion-side, PR #1721 review round 2
   #1721 review round 2, F6). An error ``@then`` step must go through
   ``ctx['result'].assert_wire_error(...)`` (or the ``_wire_code``/``_wire_suggestion``
   helpers) rather than either (a) a bare ``getattr(<error>, "error_code", ...)`` on a
   reconstructed exception object, or (b) hand-rolled dict access on
   ``ctx.get("wire_error_envelope")`` / ``ctx.get("synthesized_error_envelope")``. Both
   forms bypass the single sanctioned envelope-parsing mechanism
   (``tests/harness/transport.py``'s own docstring: "step definitions must not hand-roll
   envelope parsing") and neither is caught by Check B, which only looks for the two named
   ``_get_error_code``/``_get_error_dict`` helpers, not these inline forms.

D. **No hand-rolled parsing of the ATTRIBUTE form** (assertion-side,
   salesagent-n78j0.1.5). Check C's matcher is ``isinstance(node, ast.Call)``-gated, so
   it can never see ``ctx['result'].wire_error_envelope``. A step that moved from
   ``ctx.get("wire_error_envelope")`` to the attribute access and KEPT the hand-rolled
   parsing moved the violation past the guard rather than fixing it. Check D matches the
   attribute read with its own predicate, wired into the FINDER only — widening Check C's
   ``_is_ctx_wire_envelope_get`` instead would also widen the exemption calculator that
   consumes it, and the new shape would be matched and immediately excused.

Every allowlist can only SHRINK. Each entry documents the production gap that keeps it.
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
    "assert_wire_error",
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

# -- Check C: hand-rolled envelope/error parsing -------------------------------
_HAND_ROLLED_PARSING_ALLOWLIST: set[str] = set()

# -- Check D: hand-rolled parsing of the ATTRIBUTE form ------------------------
# Check C matches ``ctx.get("wire_error_envelope")`` only. Moving the same
# hand-rolled parsing onto ``ctx["result"].wire_error_envelope`` moved the
# violation PAST the guard rather than fixing it, so the attribute form gets its
# own detector. Ships EMPTY and can only SHRINK: the one live violation the
# detector found (uc002_nfr.then_payload_size_limits, which walked
# result.wire_error_envelope by hand because PAYLOAD_TOO_LARGE is not a pinned
# code) was FIXED to read through _wire_error_object rather than allowlisted —
# a non-pinned code rules out the assertion helper, not the reader.
_ATTRIBUTE_ENVELOPE_PARSING_ALLOWLIST: set[str] = set()


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


def _is_ctx_wire_envelope_get(node: ast.AST) -> bool:
    """True if node is ctx.get("wire_error_envelope" | "synthesized_error_envelope")."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    return (
        isinstance(fn, ast.Attribute)
        and fn.attr == "get"
        and isinstance(fn.value, ast.Name)
        and fn.value.id == "ctx"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in {"wire_error_envelope", "synthesized_error_envelope"}
    )


def _flatten_or_operands(node: ast.AST) -> list[ast.AST]:
    """Flatten `a or b or c` into [a, b, c]; a non-BoolOp node is its own singleton list."""
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        out: list[ast.AST] = []
        for value in node.values:
            out.extend(_flatten_or_operands(value))
        return out
    return [node]


def _exempted_envelope_get_ids(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """id()s of ctx.get(wire-key) Call nodes that are exempt from Check C:

    (a) presence-only: `ctx.get(...) is None` / `ctx.get(...) is not None` -- testing
        whether an envelope exists, not parsing its content. Also covers the
        one-hop-through-a-variable form (`envelope = ctx.get(...) or ctx.get(...);
        assert envelope is not None`).
    (b) piped into assert_envelope_shape(...) -- the sanctioned mechanism
        tests/CLAUDE.md documents for this exact call shape -- directly, or via
        the same one-hop-through-a-variable form.
    (c) inside an f-string (ast.JoinedStr) -- a diagnostic/failure-message
        interpolation can't influence pass/fail, so it isn't "parsing".
    """
    exempt: set[int] = set()
    # varname -> ctx.get(wire-key) call ids that feed it (via `x = A or B or ...`)
    var_sources: dict[str, set[int]] = {}
    for node in _own_nodes(func):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            operands = _flatten_or_operands(node.value)
            get_ids = {id(o) for o in operands if _is_ctx_wire_envelope_get(o)}
            if get_ids and len(get_ids) == len(operands):
                var_sources[node.targets[0].id] = get_ids
        if isinstance(node, ast.JoinedStr):
            exempt.update(id(n) for n in ast.walk(node) if _is_ctx_wire_envelope_get(n))

    def _feeding_ids(expr: ast.AST) -> set[int]:
        if _is_ctx_wire_envelope_get(expr):
            return {id(expr)}
        if isinstance(expr, ast.Name):
            return var_sources.get(expr.id, set())
        return set()

    for node in _own_nodes(func):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Is, ast.IsNot)):
            operands = [node.left, *node.comparators]
            if any(isinstance(o, ast.Constant) and o.value is None for o in operands):
                for o in operands:
                    exempt.update(_feeding_ids(o))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "assert_envelope_shape"
            and node.args
        ):
            exempt.update(_feeding_ids(node.args[0]))
    return exempt


def _hand_rolled_calls_in_func(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if func's own body (not nested defs) hand-rolls error/envelope parsing.

    Two forms:
    (a) getattr(<name>, "error_code", ...) as the SOLE mechanism -- a bare
        reconstructed-exception read that doesn't go through the named
        _get_error_code/_get_error_dict helpers (which Check B already catches)
        but has the identical disease. Exempt (mirrors Check B's uses_wire logic)
        when the function ALSO references a wire indicator -- a documented
        wire-first-with-IMPL-fallback pattern (see then_error_code).
    (b) ctx.get("wire_error_envelope") / ctx.get("synthesized_error_envelope") --
        reading the envelope dict directly instead of through assert_wire_error,
        except the two exemptions in _exempted_envelope_get_ids.
    """
    names = _func_names(func)
    uses_wire = bool(set(_WIRE_REFERENCES) & names) or "result" in names
    exempt_get_ids = _exempted_envelope_get_ids(func)

    for node in _own_nodes(func):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (
            isinstance(fn, ast.Name)
            and fn.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "error_code"
            and not uses_wire
        ):
            return True
        if _is_ctx_wire_envelope_get(node) and id(node) not in exempt_get_ids:
            return True
    return False


def _find_hand_rolled_envelope_parsing() -> set[str]:
    """Find @then steps that hand-roll error/envelope parsing (see
    _hand_rolled_calls_in_func for the two forms) instead of using
    ctx['result'].assert_wire_error(...) or the _wire_code/_wire_suggestion helpers.
    """
    found: set[str] = set()
    for rel, tree in _iter_step_modules():
        for func in _enclosing_functions(tree):
            if _is_then(func) and _hand_rolled_calls_in_func(func):
                found.add(f"{rel} {func.name}")
    return found


def test_no_hand_rolled_envelope_parsing() -> None:
    """#1721 review round 2 (F6): error @then steps must use assert_wire_error(...),
    never a bare getattr(error, 'error_code') or ctx.get('wire_error_envelope')/
    ctx.get('synthesized_error_envelope') hand-roll.
    """
    assert_violations_match_allowlist(
        _find_hand_rolled_envelope_parsing(),
        _HAND_ROLLED_PARSING_ALLOWLIST,
        fix_hint=(
            "An error Then-step hand-rolls envelope/error parsing (bare getattr(error, "
            "'error_code', ...) or ctx.get('wire_error_envelope'/'synthesized_error_envelope')). "
            "Use ctx['result'].assert_wire_error(code, recovery=..., message_substr=...) instead "
            "(tests/harness/transport.py) -- the single sanctioned envelope-parsing mechanism. "
            "See then_error_code / then_declaration_rejected for the reference pattern."
        ),
    )


_WIRE_ENVELOPE_ATTRS = {"wire_error_envelope", "synthesized_error_envelope"}


def _is_wire_envelope_attribute(node: ast.AST) -> bool:
    """True if node is an ATTRIBUTE read of a wire envelope (``result.wire_error_envelope``).

    Deliberately a SEPARATE predicate from :func:`_is_ctx_wire_envelope_get`, not a
    widening of it. That one is consumed twice — by the Check-C finder AND by
    :func:`_exempted_envelope_get_ids` — so teaching it the attribute form would
    make the new shape matched and, via the one-hop ``var_sources`` exemption,
    immediately excused: a re-introduced violation would sail through. This
    predicate is wired into the Check-D finder only, with its own narrow,
    direct-form exemptions below.

    ``ast.Attribute`` is also structurally unreachable for the Check-C matcher,
    whose first line is ``isinstance(node, ast.Call)``.
    """
    return isinstance(node, ast.Attribute) and node.attr in _WIRE_ENVELOPE_ATTRS


def _exempted_envelope_attribute_ids(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """id()s of wire-envelope ATTRIBUTE reads that are exempt from Check D.

    Mirrors Check C's exemptions, but DIRECT-FORM ONLY (no one-variable hop):

    (a) presence-only -- ``result.wire_error_envelope is not None``, the wire-first
        guard in front of ``result.assert_wire_error(...)`` (then_error
        then_validation_error, uc019 then_real_validation_error, uc026 then_outcome);
    (b) piped straight into ``assert_envelope_shape(...)``, the sanctioned helper;
    (c) inside an f-string -- a diagnostic interpolation cannot influence pass/fail.

    The hop-through-a-variable form is deliberately NOT exempt here: reading the
    envelope into a local and then walking it is exactly the disease, and every
    sanctioned site in the tree uses the direct compare.
    """
    exempt: set[int] = set()
    for node in _own_nodes(func):
        if isinstance(node, ast.JoinedStr):
            exempt.update(id(n) for n in ast.walk(node) if _is_wire_envelope_attribute(n))
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Is, ast.IsNot)):
            operands = [node.left, *node.comparators]
            if any(isinstance(o, ast.Constant) and o.value is None for o in operands):
                exempt.update(id(o) for o in operands if _is_wire_envelope_attribute(o))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "assert_envelope_shape":
            exempt.update(id(a) for a in node.args if _is_wire_envelope_attribute(a))
    return exempt


def _hand_rolled_attribute_reads_in_func(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if func's own body reads a wire envelope via attribute access, unexempted."""
    exempt = _exempted_envelope_attribute_ids(func)
    return any(_is_wire_envelope_attribute(n) and id(n) not in exempt for n in _own_nodes(func))


def _find_attribute_envelope_parsing() -> set[str]:
    """Find @then steps that hand-roll parsing off ``<x>.wire_error_envelope``."""
    found: set[str] = set()
    for rel, tree in _iter_step_modules():
        for func in _enclosing_functions(tree):
            if _is_then(func) and _hand_rolled_attribute_reads_in_func(func):
                found.add(f"{rel} {func.name}")
    return found


def test_no_attribute_form_envelope_parsing() -> None:
    """Check D: moving a hand-roll from ctx.get('wire_error_envelope') onto
    ctx['result'].wire_error_envelope moves the violation past Check C, not away.
    """
    assert_violations_match_allowlist(
        _find_attribute_envelope_parsing(),
        _ATTRIBUTE_ENVELOPE_PARSING_ALLOWLIST,
        fix_hint=(
            "An error Then-step reads <x>.wire_error_envelope / .synthesized_error_envelope "
            "and parses it by hand. Use ctx['result'].assert_wire_error(code, recovery=..., "
            "message_substr=...) (tests/harness/transport.py). A bare presence check "
            "(`result.wire_error_envelope is not None`) in front of assert_wire_error is "
            "exempt; walking the dict is not."
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


# -- Check C meta-tests ---------------------------------------------------------


def test_positive_getattr_error_code_is_detected() -> None:
    """Meta-test: a bare getattr(error, 'error_code', ...) in a @then step is caught."""
    src = """
@then("something")
def then_something(ctx):
    error = ctx["error"]
    actual = getattr(error, "error_code", None)
    assert actual == "FOO"
"""
    tree = ast.parse(src)
    func = _enclosing_functions(tree)[0]
    assert _is_then(func)
    assert _hand_rolled_calls_in_func(func) is True


def test_positive_ctx_get_wire_envelope_is_detected() -> None:
    """Meta-test: hand-rolled content extraction from ctx.get('wire_error_envelope') is
    caught -- reading CONTENT (not just checking presence, and not piping straight into
    assert_envelope_shape) is the actual disease.
    """
    src = """
@then("something")
def then_something(ctx, token):
    envelope = ctx.get("wire_error_envelope")
    message = (envelope.get("errors") or [{}])[0].get("message") or ""
    assert token in message
"""
    tree = ast.parse(src)
    func = _enclosing_functions(tree)[0]
    assert _hand_rolled_calls_in_func(func) is True


def test_negative_presence_check_via_variable_is_not_flagged() -> None:
    """Meta-test: `envelope = ctx.get(A) or ctx.get(B); assert envelope is not None` is
    exempt -- a presence-only check reached through one variable hop, same as the direct
    form (see then_version_details_supported_versions for the real-code shape).
    """
    src = """
@then("something")
def then_something(ctx):
    envelope = ctx.get("wire_error_envelope") or ctx.get("synthesized_error_envelope")
    assert envelope is not None
    assert_envelope_shape(envelope, "FOO", recovery="correctable")
"""
    tree = ast.parse(src)
    func = _enclosing_functions(tree)[0]
    assert _hand_rolled_calls_in_func(func) is False


def test_negative_assert_wire_error_is_not_flagged() -> None:
    """Meta-test: the sanctioned ctx['result'].assert_wire_error(...) pattern is excluded."""
    src = """
@then("something")
def then_something(ctx):
    ctx["result"].assert_wire_error("FOO", recovery="terminal")
"""
    tree = ast.parse(src)
    func = _enclosing_functions(tree)[0]
    assert _hand_rolled_calls_in_func(func) is False


def test_regex_slip_getattr_other_attribute_is_not_flagged() -> None:
    """Meta-test: getattr(x, 'some_other_field', ...) (not 'error_code') is NOT a false positive."""
    src = """
@then("something")
def then_something(ctx):
    value = getattr(ctx["result"], "some_other_field", None)
    assert value is not None
"""
    tree = ast.parse(src)
    func = _enclosing_functions(tree)[0]
    assert _hand_rolled_calls_in_func(func) is False


# -- Check D meta-tests ---------------------------------------------------------


def test_positive_attribute_form_envelope_parsing_is_detected() -> None:
    """Meta-test: the shape Check C could never see -- hand-rolled parsing off
    ``ctx["result"].wire_error_envelope`` -- is caught by Check D.
    """
    src = """
@then("something")
def then_something(ctx, token):
    envelope = ctx["result"].wire_error_envelope
    code = envelope.get("errors", [{}])[0].get("code", "")
    assert code == token
"""
    func = _enclosing_functions(ast.parse(src))[0]
    assert _hand_rolled_attribute_reads_in_func(func) is True


def test_check_c_matcher_cannot_see_the_attribute_form() -> None:
    """Meta-test: Check C's matcher is Call-only, so the attribute form is invisible
    to it -- which is why Check D exists as a separate detector rather than a
    widening of _is_ctx_wire_envelope_get (that would auto-exempt via var_sources).
    """
    src = """
@then("something")
def then_something(ctx):
    envelope = ctx["result"].wire_error_envelope
    assert envelope.get("errors")
"""
    func = _enclosing_functions(ast.parse(src))[0]
    assert _hand_rolled_calls_in_func(func) is False
    assert _hand_rolled_attribute_reads_in_func(func) is True


def test_negative_attribute_presence_guard_then_assert_wire_error_is_not_flagged() -> None:
    """Meta-test: the sanctioned wire-first form (presence compare, then
    assert_wire_error) is exempt -- the real shape at then_error.then_validation_error,
    uc019.then_real_validation_error and uc026.then_outcome.
    """
    src = """
@then("something")
def then_something(ctx):
    result = ctx.get("result")
    if result is not None and result.wire_error_envelope is not None:
        result.assert_wire_error("VALIDATION_ERROR")
        return
    assert ctx.get("error") is not None
"""
    func = _enclosing_functions(ast.parse(src))[0]
    assert _hand_rolled_attribute_reads_in_func(func) is False


def test_negative_attribute_piped_into_assert_envelope_shape_is_not_flagged() -> None:
    """Meta-test: piping the attribute straight into assert_envelope_shape is exempt."""
    src = """
@then("something")
def then_something(ctx):
    assert_envelope_shape(ctx["result"].wire_error_envelope, "FOO", recovery="correctable")
"""
    func = _enclosing_functions(ast.parse(src))[0]
    assert _hand_rolled_attribute_reads_in_func(func) is False


def test_regex_slip_unrelated_attribute_is_not_flagged() -> None:
    """Meta-test: an attribute with a different name is not a Check D false positive."""
    src = """
@then("something")
def then_something(ctx):
    body = ctx["result"].wire_response
    assert body.get("status") == "ok"
"""
    func = _enclosing_functions(ast.parse(src))[0]
    assert _hand_rolled_attribute_reads_in_func(func) is False


def test_regex_slip_non_then_step_is_not_scanned() -> None:
    """Meta-test: a helper function (no @then decorator) using the hand-rolled pattern is
    not scanned by _find_hand_rolled_envelope_parsing() -- only @then steps are (helpers
    like _get_error() are allowed to hand-roll; the discipline is on the assertion site).
    """
    src = """
def _helper(ctx):
    return ctx.get("wire_error_envelope")

@then("something")
def then_something(ctx):
    ctx["result"].assert_wire_error("FOO", recovery="terminal")
"""
    tree = ast.parse(src)
    funcs = {f.name: f for f in _enclosing_functions(tree)}
    assert _hand_rolled_calls_in_func(funcs["_helper"]) is True
    assert _is_then(funcs["_helper"]) is False
    assert _hand_rolled_calls_in_func(funcs["then_something"]) is False
