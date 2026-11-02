"""Guard: BDD wire-discipline — error handling goes through the wire, not test-side.

Six complementary checks, locking in the universal-wire-dispatch invariant after the
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
   ``ctx.get("wire_error_envelope")`` / ``ctx.get("synthesized_error_envelope")``, or (c)
   hand-rolled access to an envelope PROTOCOL POSITION (``errors`` / ``adcp_error``). All
   three forms bypass the single sanctioned envelope-parsing mechanism
   (``tests/harness/transport.py``'s own docstring: "step definitions must not hand-roll
   envelope parsing") and none is caught by Check B, which only looks for the two named
   ``_get_error_code``/``_get_error_dict`` helpers, not these inline forms.

D. **No private circuit-breaker state reached from a step** (arrange/assert-side). A step must
   not touch ``<service>._circuit_breakers``. Breaker state is process-local, so a step indexing
   that dict is unfalsifiable across any process boundary (it grades a test double, not a
   delivery). Seeding goes through the harness env's breaker accessors — the one place allowed to
   touch the private dict — and every state READ an assertion depends on goes through the
   production public API ``WebhookDeliveryService.get_circuit_breaker_state``. Allowlist is
   permanently EMPTY.

E. **No provenance-stripped ``ctx["response"]`` read** (assertion-side). Documented at its
   definition below: a copy of the payload cannot tell a Then whether it holds a wire fact or an
   in-process reconstruction. Steps read the dispatch's own ``TransportResult``.

F. **No hand-rolled wire-envelope access** (access-pattern, not symbol-name). Check B only
   fires when a step ALSO calls the reconstruction helpers, and Check C keys on the ``ctx``
   read; a step that hand-rolls ``getattr(result, "wire_error_envelope", None)`` (or
   ``result.wire_error_envelope``) instead of routing through the single guarded accessor
   (``tests/bdd/steps/_outcome_helpers.py``) sails through both, because it never
   touches the symbols they look for. This was Finding 7: six sites duplicated the guard
   logic (loud-raise-on-missing / IMPL-synthesized-fallback) that the accessor centralizes.
   ``_outcome_helpers.py`` (defines the accessors) and ``_dispatch.py`` (the harness's sole
   producer that mirrors the field into ``ctx``'s convenience keys) are the only sanctioned
   direct readers; everywhere else must call the accessor.

All allowlists can only SHRINK. Each entry documents the production gap or tracked follow-up
that keeps it. A separate exact-match pin (below) holds the harness's own breaker write seam.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist

_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"
_TESTS_ROOT = _STEPS_DIR.parent.parent


def _derive_wire_references() -> frozenset[str]:
    """The sanctioned wire primitives, DERIVED from the modules that define them.

    A hand-typed tuple is a derived identifier that rots silently: it listed five
    names while seven primitives existed, so a step calling one of the other two
    counted as NOT using the wire. Importing the artifacts means a primitive added
    to the harness is recognized here the day it lands, with no second list to
    remember.
    """
    from tests.bdd.steps.generic import then_error
    from tests.harness.transport import TransportResult

    # ERROR-wire only. The SUCCESS-path helpers (wire_field/wire_dict/wire_lookup/
    # wire_absent, TransportResult.wire_response) must NOT count: _uses_wire gates the
    # reconstructed-error exemptions, so admitting them would exempt a step that reads a
    # success field through a sanctioned helper and still hand-rolls its ERROR read.
    # The hand-typed tuple this replaces was too narrow; a bare "wire" substring is too
    # wide in the opposite direction.
    names = {name for name in dir(TransportResult) if "wire_error" in name}
    names.add("assert_wire_error")
    names |= {name for name in vars(then_error) if name.startswith("_wire_") and callable(getattr(then_error, name))}
    return frozenset(names)


_WIRE_REFERENCES = _derive_wire_references() | frozenset(
    {
        # The reader pair that replaced the hand-rolled `wire or synthesized`
        # fallback. A step migrated onto it still references the wire — without
        # these names it would lose the marker and trip Check B.
        "error_envelope",
        "error_envelope_or_none",
    }
)

# -- Check A: test-side error construction ------------------------------------
# Keyed by "<relative path> <enclosing func> <ErrorClass>" (NOT line numbers — those
# shift on unrelated edits). Each remaining entry is a 33r0-reclassified production gap.
# EMPTY, and it must stay a set() — a bare {} is an empty DICT and the comparison against it
# raises TypeError rather than failing a check.
#
# (Retired) _SyntheticError in uc006 _promote_creative_errors_to_ctx: its own removal criterion
# fired. The comment here used to read "production emits unstructured per-creative errors (no
# machine code). Remove when sync_creatives emits structured per-creative codes." Production now
# does exactly that — src/core/tools/creatives/_processing.py builds every per-creative entry with
# build_error_object(), so the entry carries a code and the test no longer synthesizes one.
# (Retired) The null-date phantom (uc019 _create_media_buy_with_null_dates): the scenario was
# retired (schema-impossible + not spec-graded) and resolve_canonical_status now guards the null
# edge, so no test-side error construction remains here.
_ERROR_CONSTRUCTION_ALLOWLIST: set[str] = set()

# -- Check B: reconstructed-only error assertions -----------------------------
_RECONSTRUCTED_ASSERTION_ALLOWLIST: set[str] = set()

# -- Check C: hand-rolled envelope/error parsing -------------------------------
# SEEDED (not grown) when the scan stopped being bound to the @then decorator and
# gained the protocol-position form: every entry below is PRE-EXISTING on
# origin/main and was invisible to the older scan, not newly introduced. Measured
# AFTER this PR's own hand-rolls were deleted, so nothing this PR added is parked
# here. Shrink-only from this seed — it may never grow.
# NOW EMPTY. The two remaining entries (uc006_sync_creatives'
# _assert_per_creative_failure and _extract_error_code_and_suggestion) were retired by
# salesagent-3dawm.18/.15: both dug errors[]/adcp_error out of a payload by hand, and
# both now go through the harness readers. `set()` rather than `{}` because a
# brace literal with no elements is a DICT, which makes the guard's set difference
# raise TypeError instead of passing.
_HAND_ROLLED_PARSING_ALLOWLIST: set[str] = set()


# -- Check D: private circuit-breaker state in a step -------------------------
# The private attribute a step may never reach for. Matched as an AST ``Attribute``,
# never as a source token: a token scan also hits the DOCSTRING at
# ``uc004_delivery.py`` that *describes* the process-local limitation, which would make
# the zero allowlist unachievable and the guard unshippable (gra7.3 correction C3).
_PRIVATE_BREAKER_ATTR = "_circuit_breakers"

# ZERO entries, permanently. Every site migrates onto the harness env's breaker accessors
# in the same change that lands this check, so a baseline here would be allowlist growth.
# Keys carry line numbers (unlike checks A/B) precisely BECAUSE the allowlist is empty:
# nothing is ever stored, so nothing can go stale, and the failure names the exact sites.
_PRIVATE_BREAKER_ALLOWLIST: set[str] = set()

# -- Check F: hand-rolled wire-envelope access (access pattern) ---------------
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
    # (Retired on merge) The three uc002_nfr spec-production-gap steps
    # — then_rate_limiting_enforced, then_payload_size_limits and
    # then_budget_validated_against_min_order — carried an entry on main only.
    # This branch's wire-oracle migration already took the direct
    # result.wire_error_envelope read out of all three, so the entries were stale
    # the moment the two sides met, and a stale entry is a carve-out protecting
    # nothing. Removed rather than carried: allowlists only shrink.
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


def _reads_ctx_result(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function reads ``ctx["result"]`` — the actual wire handle.

    Not the bare name ``result``: any local variable spelled ``result`` (and, via
    _func_names' string constants, any f-string mentioning one) used to satisfy
    ``uses_wire``, which made the exemption reachable by accident. Only the
    subscript on ``ctx`` is the TransportResult the dispatcher stashed.
    """
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ctx"
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "result"
        ):
            return True
    return False


def _wire_reference_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Wire primitives the function actually CALLS or reads — never string constants.

    ``_func_names`` deliberately includes string constants (Check A needs them);
    here a mere mention of ``"wire_error_envelope"`` inside a failure message must
    not count as using the wire.
    """
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names & set(_WIRE_REFERENCES)


def _uses_wire(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the function reaches the wire through a sanctioned mechanism."""
    return bool(_wire_reference_names(func)) or _reads_ctx_result(func)


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
            if uses_reconstructed and not _uses_wire(func):
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
    """True if node is ``ctx.get("wire_error_envelope")`` or the retired synthesized key.

    ``synthesized_error_envelope`` is no longer published into ctx (the dispatcher
    write was deleted), so a step reading it can only be reinstating the fallback
    the MCP dispatcher refuses. It stays RECOGNIZED here — and, deliberately, is
    NOT exemptible below — so the shape is reported rather than silently ignored.
    """
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
        and node.args[0].value == "wire_error_envelope"
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
            # A DISJUNCTION of envelope reads is the synthesized-fallback disease and
            # never earns the variable-hop exemption: `ctx.get(wire) or ctx.get(synth)`
            # grades a test-side reconstruction wherever the real wire is missing.
            # Only the single-source form (one read, hopped through a variable) is
            # eligible for the presence-only / assert_envelope_shape exemptions.
            if len(operands) != 1:
                continue
            get_ids = {id(o) for o in operands if _is_ctx_wire_envelope_get(o)}
            if get_ids:
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


#: The individual FUNCTIONS inside the step tree that legitimately read an ``errors``
#: key, keyed exactly like a violation entry ("<rel path> <func>").
#:
#: Function-scoped, never module-scoped: exempting a whole module would hand the step
#: tree's most-imported shared helper file a blanket pass, pre-authorizing the single
#: most attractive place for this disease to relocate to. The real error-envelope
#: primitives (tests/helpers/envelope_assertions.py, tests/harness/transport.py) live
#: OUTSIDE _STEPS_DIR and are never scanned, so they need no exemption at all.
#:
#: The one entry below is a NAME COLLISION, not an envelope read: ``wire_entry_errors``
#: reads the PER-ENTRY ``errors[]`` array inside a SUCCESS envelope (a partial-failure
#: row), which is a different region from the envelope-level ``errors[0]`` Check C
#: targets — and it is itself the sanctioned primitive for that region.
_PRIMITIVE_FUNCTIONS = frozenset(
    {
        "bdd/steps/_outcome_helpers.py wire_entry_errors",
        "bdd/steps/_outcome_helpers.py wire_advisory_errors",
        # The third document the same protocol position appears in: the errors[] of a
        # FAILED response's envelope. Blessed on the same terms as its two siblings --
        # it goes through result.error_envelope() (which raises on a dead wire) and
        # strips `message` via the shared _errors_array. Added because its absence is
        # what made a step module write envelope.get("errors") by hand.
        "bdd/steps/_outcome_helpers.py wire_envelope_errors",
    }
)

#: Envelope keys whose location in the wire shape is the harness's business.
_PROTOCOL_POSITION_KEYS = frozenset({"errors", "adcp_error"})


def _reads_protocol_position_by_hand(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function digs an envelope protocol position out of a dict itself."""
    for node in _own_nodes(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in _PROTOCOL_POSITION_KEYS
            and not (isinstance(node.func.value, ast.Name) and node.func.value.id == "ctx")
        ):
            return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in _PROTOCOL_POSITION_KEYS
            and not (isinstance(node.value, ast.Name) and node.value.id == "ctx")
        ):
            return True
    return False


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
    (c) hand-rolled access to an envelope's PROTOCOL POSITIONS -- ``x.get("errors")``
        / ``x["errors"]`` / ``x.get("adcp_error")`` -- the canonical
        ``(envelope.get("errors") or [{}])[0]`` shape. Form (b) alone missed this the
        moment the ctx read moved into a helper and only the INDEXING stayed behind.
        Where the spec puts a field is the harness's business
        (tests/helpers/envelope_assertions.locate_envelope_error and the readers on
        TransportResult); a step re-deriving it is a second answer to the same
        question. The individual functions that ARE the sanctioned primitive for a
        region are exempted by the scanner (see _PRIMITIVE_FUNCTIONS), not here --
        this detector grades every function it is handed.
    """
    uses_wire = _uses_wire(func)
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
    return _reads_protocol_position_by_hand(func)


def _find_hand_rolled_envelope_parsing() -> set[str]:
    """Find step-module functions that hand-roll error/envelope parsing (see
    _hand_rolled_calls_in_func for the two forms) instead of using
    ctx['result'].assert_wire_error(...) or the _wire_code/_wire_suggestion helpers.

    EVERY function in the step tree is scanned, not only @then-decorated ones. The
    decorator was never the mechanism: a @then delegating to a module-local
    ``_envelope(ctx)`` hand-rolls exactly as much as one that inlines it, and
    binding enforcement to the decorator is what let the disease relocate one call
    frame down while this guard's allowlist read empty. Scanning every function is
    both simpler than a call-graph walk and strictly more complete (a helper
    calling a helper, or one reached from a non-@then entry point, still slips a
    one-level walk).
    """
    found: set[str] = set()
    for rel, tree in _iter_step_modules():
        for func in _enclosing_functions(tree):
            entry = f"{rel} {func.name}"
            if entry in _PRIMITIVE_FUNCTIONS:
                continue
            if _hand_rolled_calls_in_func(func):
                found.add(entry)
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
            "'error_code', ...) or ctx.get('wire_error_envelope')). "
            "Use ctx['result'].assert_wire_error(code, recovery=...) instead "
            "(tests/harness/transport.py) -- the single sanctioned envelope-parsing mechanism. "
            "See then_error_code / then_declaration_rejected for the reference pattern."
        ),
    )


#: Two hand-rolling helpers in ONE file — the shape that distinguishes a function-scoped
#: exemption from a module-scoped one. With a module-wide carve-out both disappear.
_TWO_HELPER_MUTATION_MODULE = """
def _sanctioned_reader(ctx):
    body = ctx.get("wire_error_envelope")
    return (body.get("errors") or [{}])[0]


def _unsanctioned_neighbour(ctx):
    body = ctx.get("wire_error_envelope")
    return (body.get("errors") or [{}])[0]
"""


def test_exemption_is_function_scoped_not_module_scoped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Exempting one function must NOT exempt its neighbours in the same file.

    The property the exemption exists to have, and the one a staleness check cannot
    reach: staleness asks "does this entry still flag something", scope asks "does it
    flag ONLY that something". They coincide on today's tree — ``wire_entry_errors`` is
    the only Check C hit in its module — so a regression to a module-wide carve-out
    would be invisible without this. It would not stay invisible: the day a second
    function in the step tree's most-imported helper module hand-rolls, a module-wide
    exemption swallows it silently.
    """
    rel = "bdd/steps/domain/uc999_mutation_fixture.py"
    monkeypatch.setattr(sys.modules[__name__], "_PRIMITIVE_FUNCTIONS", frozenset({f"{rel} _sanctioned_reader"}))
    reported = _scan_mutated_tree(monkeypatch, tmp_path, _TWO_HELPER_MUTATION_MODULE)

    names = {entry.split()[-1] for entry in reported}
    assert "_sanctioned_reader" not in names, f"the exempted function was reported anyway: {sorted(reported)}"
    assert "_unsanctioned_neighbour" in names, (
        "a NON-exempt function in the same file was not reported — the exemption is being "
        f"applied module-wide, not per function: {sorted(reported)}"
    )


def test_primitive_function_exemptions_are_not_stale() -> None:
    """Every Check C exemption must name a function that WOULD otherwise be flagged.

    A stale exemption is invisible: it silently protects nothing while reading as a
    sanctioned carve-out, and if the function is later renamed the carve-out quietly
    covers no one. Same shrink-only discipline the allowlists get.
    """
    module = sys.modules[__name__]

    exempted = set(_PRIMITIVE_FUNCTIONS)
    assert exempted, "the exemption set is empty — delete the mechanism rather than keeping it dead"
    original = module._PRIMITIVE_FUNCTIONS
    try:
        module._PRIMITIVE_FUNCTIONS = frozenset()
        without_exemptions = _find_hand_rolled_envelope_parsing()
    finally:
        module._PRIMITIVE_FUNCTIONS = original

    # Through the shared helper, never a hand-rolled set subtraction: its "stale entries"
    # mode is exactly this check, and a second copy of the diff is what
    # test_architecture_no_handrolled_allowlist_diff.py exists to prevent. Intersecting
    # first scopes the comparison to the exemption set — other functions legitimately
    # flag without being exempt, and they are Check C's business, not this test's.
    assert_violations_match_allowlist(
        without_exemptions & exempted,
        exempted,
        fix_hint=(
            "An exemption entry flags nothing (renamed, deleted, or no longer parsing). "
            "Remove it — exemptions only shrink."
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


def test_presence_check_via_variable_is_not_flagged() -> None:
    """Meta-test: `envelope = ctx.get("wire_error_envelope"); assert envelope is not None`
    is exempt -- a presence-only check reached through one variable hop, same as the
    direct form. The variable-hop exemption itself is preserved; what changes below is
    that the SYNTHESIZED-fallback disjunction no longer qualifies for it.
    """
    src = """
@then("something")
def then_something(ctx):
    envelope = ctx.get("wire_error_envelope")
    assert envelope is not None
    assert_envelope_shape(envelope, "FOO", recovery="correctable")
"""
    tree = ast.parse(src)
    func = _enclosing_functions(tree)[0]
    assert _hand_rolled_calls_in_func(func) is False


def test_synthesized_fallback_disjunction_is_flagged() -> None:
    """Meta-test (INVERTED): `ctx.get(wire) or ctx.get(synthesized)` is the DISEASE.

    This shape was the guard's blessed sample, which is precisely how the guard came to
    teach the pattern it exists to forbid: the disjunction accepts a test-side
    reconstruction wherever the real wire is missing, so every consumer of the resulting
    variable grades the synthesized envelope on a transport that captured nothing. With
    the ``synthesized_error_envelope`` ctx key gone, the fallback operand has no
    legitimate reading left and the whole expression must be reported.
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
    assert _hand_rolled_calls_in_func(func) is True


def test_local_variable_named_result_is_not_a_wire_reference() -> None:
    """Meta-test: ``uses_wire`` must require ``ctx["result"]``, not the bare name.

    ``_func_names`` collects every Name id, so ANY local called ``result`` -- a loop
    variable, a parsed response, an adapter return -- exempted the function from Check C.
    A false-negative gate: nothing in the tree exploits it today, which is exactly why it
    has to be closed before the widened scan starts relying on ``uses_wire``.
    """
    src = """
@then("something")
def then_something(ctx):
    result = ctx["response"]
    actual = getattr(ctx["error"], "error_code", None)
    assert actual == result
"""
    tree = ast.parse(src)
    func = _enclosing_functions(tree)[0]
    assert _hand_rolled_calls_in_func(func) is True


def test_string_constant_result_is_not_a_wire_reference() -> None:
    """Meta-test: the string ``"result"`` keyed off something other than ``ctx``.

    ``_func_names`` collects string Constants too, so ANY ``"result"`` literal --
    a payload key, a dict lookup, a status word -- satisfied ``uses_wire``. The
    wire reference is the ``ctx["result"]`` Subscript, not the token. Same
    false-negative class as the bare ``result`` name above.
    """
    src = """
@then("something")
def then_something(ctx):
    body = ctx["response"].model_dump()
    actual = getattr(ctx["error"], "error_code", None)
    assert actual == body["result"]
"""
    tree = ast.parse(src)
    func = _enclosing_functions(tree)[0]
    assert _hand_rolled_calls_in_func(func) is True


def test_wire_first_with_reconstructed_fallback_stays_exempt() -> None:
    """Meta-test (preserved): a genuine wire read still exempts the fallback getattr.

    Tightening ``uses_wire`` must not start flagging the documented wire-first pattern
    (see then_error.py then_error_code) -- the point is to stop counting incidental
    names, not to remove the exemption.
    """
    src = """
@then("something")
def then_something(ctx):
    code = _wire_code(ctx)
    if code is None:
        code = getattr(ctx["error"], "error_code", None)
    assert code == "FOO"
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


_MUTATION_MODULE = """
from pytest_bdd import then


def _envelope(ctx):
    return ctx.get("wire_error_envelope")


def _message(ctx):
    envelope = _envelope(ctx)
    return (envelope.get("errors") or [{}])[0].get("message") or ""


@then("something clean")
def then_something_clean(ctx):
    ctx["result"].assert_wire_error("FOO", recovery="terminal")
"""


def _scan_mutated_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str) -> set[str]:
    """Point the scanner at a synthetic step tree and return what it reports.

    Mutating a real step module would leave a permanent violation in the tree; a
    throwaway module tests the SCANNER (which functions it visits) rather than only
    the detector (`_hand_rolled_calls_in_func`), which is where the blind spot lived.
    """
    steps = tmp_path / "bdd" / "steps" / "domain"
    steps.mkdir(parents=True)
    (steps / "uc999_mutation_fixture.py").write_text(source, encoding="utf-8")
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_STEPS_DIR", tmp_path / "bdd" / "steps")
    monkeypatch.setattr(module, "_TESTS_ROOT", tmp_path)
    return _find_hand_rolled_envelope_parsing()


def test_non_then_helper_is_scanned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Meta-test (INVERTED): a module-local helper that hand-rolls IS reported.

    This previously asserted the opposite -- that a helper without a @then decorator is
    out of scope, "the discipline is on the assertion site". It is not: a @then that
    delegates to a module-local ``_envelope(ctx)`` hand-rolls envelope parsing exactly as
    much as one that inlines it, and routing through a helper was how the pattern stayed
    invisible while Check C's allowlist read empty. Every function in a step module is now
    scanned; a helper may hand-roll only if no @then can reach it, which the scan does not
    (and need not) try to prove.
    """
    reported = _scan_mutated_tree(monkeypatch, tmp_path, _MUTATION_MODULE)
    names = {entry.split()[-1] for entry in reported}
    assert "_envelope" in names, f"the hand-rolling helper was not reported: {sorted(reported)}"
    assert "_message" in names, f"the helper's hand-rolling consumer was not reported: {sorted(reported)}"
    assert "then_something_clean" not in names, f"the sanctioned @then was falsely reported: {sorted(reported)}"


def test_mutation_a_hand_rolling_helper_reddens_the_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Mutation self-test: the guard itself must FAIL on the mutated tree.

    ``_find_hand_rolled_envelope_parsing`` reporting the helper is necessary but not
    sufficient -- the allowlist comparison is what turns a report into a failing build.
    A guard with no mutation self-test is not done: it can pass forever on a scan that
    silently visits nothing.
    """
    reported = _scan_mutated_tree(monkeypatch, tmp_path, _MUTATION_MODULE)
    with pytest.raises(AssertionError):
        assert_violations_match_allowlist(reported, _HAND_ROLLED_PARSING_ALLOWLIST, fix_hint="mutation self-test")


def _private_breaker_hits(tree: ast.Module) -> dict[str, list[int]]:
    """Map enclosing function name -> sorted line numbers of ``x._circuit_breakers`` access.

    ``ast.Attribute`` only. A string mentioning ``_circuit_breakers`` — a docstring
    explaining the process-local limitation, a comment, a log line — parses to a
    ``Constant``, never an ``Attribute``, and is therefore invisible here. That is the
    whole reason this check is structural rather than a token scan.
    """
    owner_of: dict[int, str] = {}
    for func in _enclosing_functions(tree):
        for node in _own_nodes(func):
            owner_of[id(node)] = func.name

    hits: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == _PRIVATE_BREAKER_ATTR:
            hits.setdefault(owner_of.get(id(node), "<module>"), []).append(node.lineno)
    return {name: sorted(lines) for name, lines in hits.items()}


def _find_private_breaker_access() -> set[str]:
    """Find every step-side read/write of a service's private ``_circuit_breakers`` dict."""
    found: set[str] = set()
    for rel, tree in _iter_step_modules():
        for func_name, lines in _private_breaker_hits(tree).items():
            found.add(f"{rel} {func_name}:{','.join(str(n) for n in lines)}")
    return found


def test_no_private_circuit_breaker_state_in_steps() -> None:
    """gra7.3: steps must not index ``service._circuit_breakers``; go through the env accessors."""
    assert_violations_match_allowlist(
        _find_private_breaker_access(),
        _PRIVATE_BREAKER_ALLOWLIST,
        fix_hint=(
            "A BDD step reaches into a service's private _circuit_breakers dict. Breaker state is "
            "process-local, so that read is unfalsifiable across a process boundary — it grades a "
            "test double, not a delivery. SEED through the harness env's breaker accessors "
            "(tests/harness/_mixins.py circuit-breaker mixin — the only place allowed to touch the "
            "private dict); READ through the production public API "
            "WebhookDeliveryService.get_circuit_breaker_state (via the env's breaker_snapshot); and "
            "where the scenario claims deliveries happen, assert the delivery EFFECT (an attempt "
            "reached the origin), not the state enum alone. The allowlist is permanently empty."
        ),
    )


# ---------------------------------------------------------------------------
# The harness's own write seam, pinned.
#
# The circuit-breaker mixin declares that the write side of the breaker "lives
# here, in the harness, and NOWHERE else". The check above enforces the "nowhere
# else" half over tests/bdd/steps/. Nothing enforced the "here" half: the mixin
# could grow a sixth affordance for faking breaker state and no test would
# notice — which is how `record_breaker_successes` came to let a scenario claim
# it had delivered N reports while the system delivered nothing. The scan covers
# the whole harness because `env` in a step is a subclass: pinning only the mixin
# catches a rename and misses the shape moving one file over.
#
# This is an exact-match pin, so ADDING a writer fails and so does REMOVING one
# without updating the set. Each name below is a deliberate seam, not debt: the
# set may only shrink as scenarios migrate onto real deliveries.
# ---------------------------------------------------------------------------

_BREAKER_SEAM_METHODS: set[str] = {
    "_mixins.py::seed_breaker_failures",
    "_mixins.py::set_breaker_state",
    "_mixins.py::elapse_breaker_timeout",
    "_mixins.py::drive_breaker_transition",
}


def _methods_touching_the_breaker_seam() -> set[str]:
    """``file::function`` for everything under tests/harness/ that reaches the private breaker.

    The whole harness, not just the mixin, and every function, not just class
    bodies: ``env`` in a step IS a ``CircuitBreakerEnv``, so the natural home for
    a new faking affordance is the subclass one file over — and a module-level
    helper needs no class at all. Both ``_breaker_for`` and the ``_circuit_breakers``
    dict it wraps are matched, because the historical spelling of this defect
    poked the dict directly.
    """
    harness = _TESTS_ROOT / "harness"
    found: set[str] = set()
    for py_file in sorted(harness.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if fn.name == "_breaker_for":
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Attribute) and node.attr in {"_breaker_for", "_circuit_breakers"}:
                    found.add(f"{py_file.name}::{fn.name}")
    return found


def test_the_harness_breaker_write_seam_does_not_grow() -> None:
    """The mixin's breaker affordances are exactly the pinned set."""
    assert_violations_match_allowlist(
        _methods_touching_the_breaker_seam(),
        _BREAKER_SEAM_METHODS,
        fix_hint=(
            "A new method in the circuit-breaker mixin reaches the private breaker. Before adding "
            "one, check whether the scenario should DELIVER instead of seeding: a helper that fakes "
            "N successes lets a Then grade arithmetic the delivery layer never ran, so production "
            "can stop recording successes and the scenario stays green. Seeding is legitimate only "
            "for reaching a STARTING state a test cannot afford to spend real failures on."
        ),
    )


# ── Check E: no step reads the provenance-stripped ctx["response"] ────────────
#
# The dispatch seams stopped writing that key: a copy of the payload cannot tell
# a Then whether it holds a wire fact or an in-process reconstruction, which is
# how a self-grading transport stayed green. Worse, the key had THREE writers
# with three meanings — dispatch, modules calling production directly, and one
# step stashing a REQUEST under it — so a reader could not know what it had.
#
# Steps read the dispatch's own TransportResult via require_payload /
# payload_or_none. Modules that still call production directly stash under the
# explicitly-named ctx["self_dispatched_response"], which the shared accessors
# know about by name.
_CTX_RESPONSE_KEY = "response"

# Shrink-only. Every entry is a module whose When calls production DIRECTLY
# rather than dispatching, with the GitHub issue tracking its migration. When a
# module migrates its entry goes; nothing may be added.
# EMPTY, and it stays that way. Every module migrated; the two that still call
# production directly (uc011's _list_accounts_impl, FIXME(#1880)) stash under the
# explicitly-named ctx["self_dispatched_response"], which the shared accessors
# know by name — so they need no exemption from this check at all.
_CTX_RESPONSE_ALLOWLIST: set[str] = set()


def _ctx_response_hits(tree: ast.AST) -> dict[str, list[int]]:
    """Subscript AND .get access to ctx["response"], per enclosing function.

    ALL FIVE access forms condition C2 made binding — subscript, ``ctx.get``,
    ``_require*(ctx, "response")``, ``"response" in ctx`` and ``ctx.pop``.
    Covering only the first two would leave a future step able to re-open the
    retired key through the shared accessor, which is exactly the escape the
    design review named.

    Subscript and ``ctx.get`` deliberately: a subscript-only check would miss the ~218
    ``ctx.get("response")`` reads this lane migrated and would pass on an almost
    entirely unmigrated tree. A string mentioning the key in a docstring parses
    to ast.Constant and is invisible to both, which is what makes the pinned set
    achievable.
    """
    hits: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        lines: list[int] = []
        for child in ast.walk(node):
            # ctx["response"]
            if (
                isinstance(child, ast.Subscript)
                and isinstance(child.value, ast.Name)
                and child.value.id == "ctx"
                and isinstance(child.slice, ast.Constant)
                and child.slice.value == _CTX_RESPONSE_KEY
            ):
                lines.append(child.lineno)
            # _require(ctx, "response") / any _require*(ctx, "response") helper —
            # the escape route pass 2 named: a future step could re-open the key
            # through the shared accessor rather than by subscript.
            elif (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id.startswith("_require")
                and len(child.args) >= 2
                and isinstance(child.args[0], ast.Name)
                and child.args[0].id == "ctx"
                and isinstance(child.args[1], ast.Constant)
                and child.args[1].value == _CTX_RESPONSE_KEY
            ):
                lines.append(child.lineno)
            # "response" in ctx — a membership test is a read of the same key
            elif (
                isinstance(child, ast.Compare)
                and isinstance(child.left, ast.Constant)
                and child.left.value == _CTX_RESPONSE_KEY
                and any(isinstance(op, ast.In) for op in child.ops)
                and any(isinstance(c, ast.Name) and c.id == "ctx" for c in child.comparators)
            ):
                lines.append(child.lineno)
            # ctx.pop("response") — a clear of a key nothing writes any more is
            # dead code that reads as live state management
            elif (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "pop"
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "ctx"
                and child.args
                and isinstance(child.args[0], ast.Constant)
                and child.args[0].value == _CTX_RESPONSE_KEY
            ):
                lines.append(child.lineno)
            # ctx.get("response")
            elif (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "get"
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "ctx"
                and child.args
                and isinstance(child.args[0], ast.Constant)
                and child.args[0].value == _CTX_RESPONSE_KEY
            ):
                lines.append(child.lineno)
        if lines:
            hits[node.name] = sorted(set(lines))
    return hits


def _find_ctx_response_access() -> set[str]:
    found: set[str] = set()
    steps_root = _STEPS_DIR
    for path in sorted(steps_root.rglob("*.py")):
        rel = path.relative_to(steps_root).as_posix()
        if rel == "_outcome_helpers.py":
            continue  # the accessors themselves; they are the sanctioned readers
        if _ctx_response_hits(ast.parse(path.read_text())):
            found.add(rel)
    return found


class TestNoProvenanceStrippedResponseCopy:
    def test_steps_read_the_dispatch_result_not_a_payload_copy(self):
        """No step module reads ctx["response"] in any of its five access forms."""
        assert_violations_match_allowlist(
            {(module,) for module in _find_ctx_response_access()},
            {(module,) for module in _CTX_RESPONSE_ALLOWLIST},
            fix_hint=(
                "A Then reading ctx['response'] cannot tell a wire fact from an in-process "
                "reconstruction. Read the dispatch's TransportResult instead — require_payload(ctx) "
                "when a payload is required, payload_or_none(ctx) when the step branches on which "
                "path ran. Modules whose When calls production directly stash under "
                "ctx['self_dispatched_response'], which those accessors know by name."
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
