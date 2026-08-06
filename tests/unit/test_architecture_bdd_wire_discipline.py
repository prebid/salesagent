"""Guard: BDD wire-discipline — error handling goes through the wire, not test-side.

Two complementary checks, locking in the universal-wire-dispatch invariant after the
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

C. **No typed-payload package/delivery oracle** (success-path, assertion-side). The
   success-path twin of B. An oracle-shaped function (``@then`` step, ``assert_``/``_assert``
   helper, or ``when_`` step) must not grade ``media_buy_deliveries`` / ``by_package`` /
   ``totals`` content off the harness-reconstructed TYPED payload (``ctx["response"]`` /
   ``_require_response(ctx)``) without reading the wire via ``_wire_packages`` / ``wire_dict``
   / ``wire_field``. On a2a/mcp/rest the typed payload is reconstructed FROM the wire, so its
   fields are already coerced to their declared types: a boolean truncation flag serialized as
   the string ``"true"`` reconstructs to ``True`` and the oracle passes on a non-conformant
   wire. (A *missing* field is still caught — it reconstructs to ``None`` — so the blind spot
   is coercion, not absence.) Demonstrated: with the flags emitted as strings, all six
   truncation rows stayed green until ``then_field_true`` / ``then_field_false`` /
   ``then_packages_limited`` moved onto ``_wire_packages``.

   Re-keyed on the READ (a typed-response variable dereferenced for one of
   the three field names above, or a call to a helper that itself does so — resolved
   behaviorally within the module, not by a fixed helper-name list) rather than on the literal
   symbol ``_collect_all_packages``, so a sibling reaching the same data by any other route
   cannot be invisible to it. Exemption is per READ SITE (the specific typed-sourced variable),
   not per function: a ``wire_dict``/``wire_field`` call elsewhere in the same function does
   not exempt a *different* variable's typed read — see ``_find_typed_package_oracles_in``.
   ``hasattr(x, "media_buy_deliveries")`` (a domain-routing type sniff, not a grading read) is
   deliberately not matched.

All three allowlists can only SHRINK. Each entry documents the production gap that keeps it.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tests.unit._architecture_helpers import assert_violations_match_allowlist
from tests.unit.test_architecture_bdd_no_orphan_ctx_reads import _ORACLE_PREFIXES

_TESTS_ROOT = Path(__file__).resolve().parents[1]
_STEPS_DIR = _TESTS_ROOT / "bdd"

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

# -- Check C: typed-payload package/delivery oracles (success path) -----------
# Keyed by "<relative path> <function>". RETIRED, not shrunk: the prior
# 6-entry allowlist (all UC-004 package oracles reading _collect_all_packages) is gone —
# superset proof in test_architecture_bdd_wire_discipline_rekeyed_meta.py confirms the
# re-keyed detector flags all 6 against their pre-migration bodies. One entry is seeded
# deliberately (architect review MEDIUM-4): a cross-UC generic step, not
# UC-004-scoped work, tracked separately.
_TYPED_PACKAGE_ORACLE_ALLOWLIST: set[str] = {
    # FIXME(#1778): then_response_status grades media_buy_deliveries presence (among other
    # statusless-response types) off the typed payload via a runtime field-name lookup
    # (_STATUSLESS_SUCCESS_ATTRS), so a boolean/enum coercion regression on that path is
    # invisible to it. Cross-UC generic step (shared by every "the response status should be
    # ..." phrasing, not UC-004-scoped) — migrating it is a separately tracked follow-up, not
    # this guard's job. Seeded here explicitly rather than narrowing the detector's scan set back
    # to uc004_delivery.py, which would reintroduce the file-keying this re-key exists to end.
    "bdd/steps/generic/then_success.py then_response_status",
}


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


def _uses_ctx_result_subscript(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function reads ``ctx["result"]``/``ctx.get("result")`` anywhere in its body.

    Tighter than a bare ``"result" in names`` token match, which a local variable, kwarg, or
    string literal spelled ``result`` could satisfy without the function ever touching the real
    ``TransportResult`` — this requires the exact ctx-subscript-or-``.get`` shape the wire path
    actually uses.
    """
    return any(_ctx_key_read_matches(node, lambda k: k == "result") for node in _own_nodes(func))


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
            uses_wire = bool(set(_WIRE_REFERENCES) & names) or _uses_ctx_result_subscript(func)
            if uses_reconstructed and not uses_wire:
                found.add(f"{rel} {func.name}")
    return found


_SHAPE_ATTRS = {
    "media_buy_deliveries",
    "by_package",
    "totals",
    "by_placement",
    "reporting_period",
    "aggregated_totals",
}
# NOTE: "errors" is deliberately NOT in this set — it is a generic field name shared by
# many unrelated response types across other UCs (uc003/uc006/uc011/uc019/then_error.py),
# so including it would pull those out-of-scope oracles into Check C's UC-004-scoped
# view (sweep verification). uc004_delivery.py's own errors-field
# oracles (then_no_errors_field, then_has_errors_field) were migrated to wire_dict
# directly, independent of guard coverage; the tree-wide errors-field disease is
# tracked separately, not this guard's scope.
_WIRE_READER_NAMES = {"_wire_packages", "wire_dict", "wire_field", "wire_packages"}


def _ctx_key_read_matches(node: ast.expr, predicate: Callable[[Any], bool]) -> bool:
    """True if ``node`` is ``ctx[<key>]`` or ``ctx.get(<key>)`` and ``predicate(key)`` holds.

    Shared by :func:`_is_typed_response_expr` (``key == "response"``) and the sandbox/array
    guard's ``_is_cached_typed_source_expr`` (``key in cache_keys``) — both need the identical
    "is this a ctx-subscript-or-.get read, and does the literal key match?" AST shape, differing
    only in the predicate applied to the key.
    """
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ctx"
        and isinstance(node.slice, ast.Constant)
        and predicate(node.slice.value)
    ):
        return True
    if isinstance(node, ast.Call):
        f = node.func
        if (
            isinstance(f, ast.Attribute)
            and f.attr == "get"
            and isinstance(f.value, ast.Name)
            and f.value.id == "ctx"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and predicate(node.args[0].value)
        ):
            return True
    return False


def _is_typed_response_expr(node: ast.expr) -> bool:
    """``ctx["response"]`` / ``ctx.get("response")`` / ``_require_response(ctx)`` — a direct
    typed-payload source expression (or an ``or``-chain of these)."""
    if _ctx_key_read_matches(node, lambda v: v == "response"):
        return True
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name) and f.id == "_require_response":
            return True
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return any(_is_typed_response_expr(v) for v in node.values)
    return False


def _typed_source_var_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Local names bound to a typed-response source anywhere in ``func``'s own body.

    Handles plain assignment (``resp = ctx.get("response")``) and walrus binding
    (``if (resp := ctx.get("response")) is not None:``) — sweep verification R4.
    """
    names: set[str] = set()
    for node in _own_nodes(func):
        if isinstance(node, ast.Assign) and _is_typed_response_expr(node.value):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        if (
            isinstance(node, ast.NamedExpr)
            and _is_typed_response_expr(node.value)
            and isinstance(node.target, ast.Name)
        ):
            names.add(node.target.id)
    return names


def _module_level_shape_containing_names(tree: ast.Module) -> set[str]:
    """Module-level names bound to a tuple/list/set literal containing >=1 SHAPE attribute
    string — e.g. ``_STATUSLESS_SUCCESS_ATTRS = ("formats", "media_buy_deliveries", ...)``.

    Closes the field-name-as-loop-variable evasion: ``for attr in _SOME_ATTRS: getattr(resp,
    attr)`` reaches the same data as a literal ``resp.media_buy_deliveries`` but no 3-arg
    ``getattr`` constant-matching predicate alone can see it, since the field name is a
    runtime variable, not a literal at the read site.
    """
    names: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if not isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            continue
        if any(isinstance(e, ast.Constant) and e.value in _SHAPE_ATTRS for e in value.elts):
            for t in targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def _expand_typed_vars_through_iteration(
    func: ast.FunctionDef | ast.AsyncFunctionDef, seed: set[str], shape_containers: set[str]
) -> tuple[set[str], set[str]]:
    """Grow ``seed`` to include for-loop/comprehension targets that iterate a SHAPE attribute
    off an already-typed variable (``for d in resp.media_buy_deliveries`` binds ``d``).

    Returns ``(expanded_typed_vars, shape_valued_names)`` — the second set collects loop
    targets that iterate a module-level ``shape_containers`` name (``for attr in
    _STATUSLESS_SUCCESS_ATTRS`` binds ``attr`` to a SHAPE attribute string at runtime, on
    some iterations, even though ``attr`` is not itself a typed object).
    """
    expanded = set(seed)
    shape_valued: set[str] = set()
    changed = True
    while changed:
        changed = False
        targets_and_iters: list[tuple[ast.expr, ast.expr]] = []
        for node in _own_nodes(func):
            if isinstance(node, ast.For):
                targets_and_iters.append((node.target, node.iter))
            for n in ast.walk(node):
                if isinstance(n, ast.comprehension):
                    targets_and_iters.append((n.target, n.iter))
        for target, it in targets_and_iters:
            if (
                isinstance(it, ast.Attribute)
                and it.attr in _SHAPE_ATTRS
                and isinstance(it.value, ast.Name)
                and it.value.id in expanded
                and isinstance(target, ast.Name)
                and target.id not in expanded
            ):
                expanded.add(target.id)
                changed = True
            if (
                isinstance(it, ast.Name)
                and it.id in shape_containers
                and isinstance(target, ast.Name)
                and target.id not in shape_valued
            ):
                shape_valued.add(target.id)
                changed = True
    return expanded, shape_valued


def _walks_typed_shape(
    func: ast.FunctionDef | ast.AsyncFunctionDef, typed_vars: set[str], shape_containers: set[str] = frozenset()
) -> bool:
    """True if ``func`` reads a SHAPE attribute off ``typed_vars`` (expanded through
    iteration) or directly off an inline typed-response expression.

    Matches ``.attr`` access, 3-arg ``getattr(x, "attr", default)`` with a literal field
    name, and 2-arg ``getattr(x, name)`` where ``name`` is a loop variable over a
    module-level SHAPE-containing container (see :func:`_module_level_shape_containing_names`).
    Deliberately NOT ``hasattr(x, "attr")``, which is a type-routing probe, not a grading read.
    """
    expanded, shape_valued = _expand_typed_vars_through_iteration(func, typed_vars, shape_containers)
    for node in _own_nodes(func):
        if isinstance(node, ast.Attribute) and node.attr in _SHAPE_ATTRS:
            if isinstance(node.value, ast.Name) and node.value.id in expanded:
                return True
            if _is_typed_response_expr(node.value):
                return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
        ):
            base = node.args[0]
            base_is_typed = (isinstance(base, ast.Name) and base.id in expanded) or _is_typed_response_expr(base)
            if not base_is_typed:
                continue
            field_arg = node.args[1]
            if isinstance(field_arg, ast.Constant) and field_arg.value in _SHAPE_ATTRS:
                return True
            if isinstance(field_arg, ast.Name) and field_arg.id in shape_valued:
                return True
    return False


def _typed_walker_names(tree: ast.Module, shape_containers: set[str]) -> set[str]:
    """Helper functions in ``tree`` that themselves read a SHAPE attribute off one of their
    OWN parameters OR a local typed-response binding — behavioral detection, so a future
    helper doing the same walk under any name (not just ``_collect_all_packages``) is caught
    automatically — and do not name a wire reader anywhere in their own body.

    Seeds from ``params | _typed_source_var_names(func)`` (sweep verification R3): a
    non-oracle-prefixed helper that binds ``ctx["response"]`` to a LOCAL (not a parameter)
    and walks it was previously invisible as both walker and oracle, since only parameters
    were seeded — the mirror image of the documented "already-typed parameter" limitation.
    """
    walkers: set[str] = set()
    for func in _enclosing_functions(tree):
        seed = {a.arg for a in func.args.args} | _typed_source_var_names(func)
        if not seed:
            continue
        if _walks_typed_shape(func, seed, shape_containers) and not (_WIRE_READER_NAMES & _func_names(func)):
            walkers.add(func.name)
    return walkers


def _find_typed_package_oracles_in(rel: str, tree: ast.Module) -> set[str]:
    """Per-module re-keyed Check C — flag oracle-shaped functions reaching UC-004
    package/delivery content off the typed response, directly or via a typed-walker helper.

    Keyed on the READ SITE (a typed-sourced variable's provenance), not on a fixed helper
    name or on function-wide wire-mention presence: a ``wire_dict`` call for one branch does
    not exempt a *different* typed-sourced variable's read elsewhere in the same function.

    KNOWN LIMITATION (not tracked, by design — closing it needs interprocedural call-site
    argument tracing this syntactic guard does not do): a helper that receives an ALREADY
    typed-extracted collection as a plain parameter (e.g. ``_assert_placements_sorted_by(
    packages: list[Any], ...)``, where the caller passed the result of a typed walker call)
    is not detected, because its own body never touches ``ctx`` or a typed-source expression
    directly. Its CALLERS are still caught (they dereference ``ctx["response"]``/call the
    walker directly), so migrating them forces the same fix transitively.
    """
    shape_containers = _module_level_shape_containing_names(tree)
    walkers = _typed_walker_names(tree, shape_containers)
    found: set[str] = set()
    for func in _enclosing_functions(tree):
        if not func.name.startswith(_ORACLE_PREFIXES):
            continue
        typed_vars = _typed_source_var_names(func)
        direct_hit = _walks_typed_shape(func, typed_vars, shape_containers)
        calls_walker = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in walkers for n in _own_nodes(func)
        )
        if direct_hit or calls_walker:
            found.add(f"{rel} {func.name}")
    return found


def _find_typed_package_oracles() -> set[str]:
    """Repo-wide union of :func:`_find_typed_package_oracles_in` over every step module."""
    found: set[str] = set()
    for rel, tree in _iter_step_modules():
        found |= _find_typed_package_oracles_in(rel, tree)
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


def test_uses_ctx_result_subscript_requires_real_ctx_result_shape() -> None:
    """Regression guard: a bare local/kwarg/string-literal named ``result`` must NOT count as
    wire-grounding (salesagent-oyiv.2) — only a genuine ``ctx["result"]``/``ctx.get("result")``
    read does.
    """

    def _first_func(source: str) -> ast.FunctionDef:
        tree = ast.parse(source)
        return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))

    bare_local = _first_func("def then_x(ctx):\n    result = _get_error_code(ctx['error'])\n    assert result == 'X'\n")
    assert not _uses_ctx_result_subscript(bare_local), (
        "a local variable merely named 'result' must not satisfy the ctx['result'] wire check"
    )

    bare_kwarg = _first_func("def then_x(ctx, result='unused'):\n    assert _get_error_code(ctx['error'])\n")
    assert not _uses_ctx_result_subscript(bare_kwarg), (
        "a kwarg named 'result' must not satisfy the ctx['result'] wire check"
    )

    string_literal = _first_func("def then_x(ctx):\n    assert _get_error_code(ctx['error']) == 'result'\n")
    assert not _uses_ctx_result_subscript(string_literal), (
        "a string literal 'result' must not satisfy the ctx['result'] wire check"
    )

    real_subscript = _first_func("def then_x(ctx):\n    ctx['result'].assert_wire_error('X')\n")
    assert _uses_ctx_result_subscript(real_subscript), "a genuine ctx['result'] subscript must be recognized"

    real_get = _first_func("def then_x(ctx):\n    r = ctx.get('result')\n    r.assert_wire_error('X')\n")
    assert _uses_ctx_result_subscript(real_get), "a genuine ctx.get('result') read must be recognized"


def test_no_typed_payload_package_oracle() -> None:
    """Success-path oracles must grade the wire, not the coerced typed payload."""
    assert_violations_match_allowlist(
        _find_typed_package_oracles(),
        _TYPED_PACKAGE_ORACLE_ALLOWLIST,
        fix_hint=(
            "An oracle-shaped function grades media_buy_deliveries/by_package/totals off "
            "ctx['response'] or _require_response(ctx) (the typed payload reconstructed from the "
            "wire, whose fields are already coerced to their declared types), directly or via a "
            "helper that does the same walk. Read wire_dict(ctx) / wire_field(ctx, f) / "
            "wire_packages(ctx) instead — they read the buyer's actual serialized body and inherit "
            "a raise-on-missing-body guard. See then_field_true / then_field_false / "
            "then_packages_limited in uc004_delivery.py for the target shape."
        ),
    )


def test_meta_the_scan_set_is_not_empty() -> None:
    """``_iter_step_modules`` must resolve to the real BDD step tree.

    An empty or misconfigured scan set is indistinguishable from "no violations" for every
    check in this file — a typo in ``_STEPS_DIR`` would silently disable all three guards
    while every other test here kept passing.
    """
    scanned = _iter_step_modules()
    assert len(scanned) >= 10, (
        f"_iter_step_modules() found only {len(scanned)} modules under {_STEPS_DIR} — the guard "
        "is scanning the wrong tree"
    )
    rels = {rel for rel, _ in scanned}
    assert "bdd/steps/domain/uc004_delivery.py" in rels, (
        "uc004_delivery.py is not in the scan set; it carries the package/delivery oracles Check C polices"
    )
    assert "bdd/steps/generic/then_success.py" in rels, (
        "generic/then_success.py is not in the scan set; it carries the cross-UC then_response_status oracle"
    )
    assert "bdd/test_uc018_list_creatives.py" in rels, (
        "salesagent-oyiv.6 regression: the top-level tests/bdd/test_uc*.py scenario-binding "
        "modules must be in scan scope too — a @then oracle landing directly in one of them "
        "(not in tests/bdd/steps/) was previously invisible to Checks A/B/C"
    )
