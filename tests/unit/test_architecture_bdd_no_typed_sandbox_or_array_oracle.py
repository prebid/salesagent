"""Guard: 5 sandbox/array oracle functions must read the wire, not the
harness-reconstructed TYPED payload.

Narrow, function-name-keyed scan — deliberately NOT a widening of Check C
(``test_architecture_bdd_wire_discipline.py``'s ``_TYPED_PACKAGE_ORACLE_ALLOWLIST`` /
``_find_typed_package_oracles``). Check C is scoped to UC-004's package/delivery SHAPE_ATTRS
(``media_buy_deliveries`` / ``by_package`` / ``totals`` / ...) by its own docstring; widening
it to scan every top-level ``tests/bdd/test_uc*.py`` file is tracked separately. This guard
targets exactly the 5 functions migrated onto ``wire_dict(ctx)`` / ``wire_field(ctx, ...)``
(``tests/bdd/steps/_outcome_helpers.py``):

  1. ``tests/bdd/steps/generic/then_success.py::then_response_contains_array``    (Wave A)
  2. ``tests/bdd/steps/domain/uc003_update_media_buy.py::then_response_has_sandbox`` (Wave B)
  3. ``tests/bdd/steps/domain/uc026_package_media_buy.py::_get_packages``          (Wave B)
  4. ``tests/bdd/steps/generic/then_success.py::then_no_real_orders_created``      (Wave B)
  5. ``tests/bdd/steps/generic/then_success.py::then_no_real_api_calls``           (Wave B,
     part 3 of 3 only — the sandbox-flag read; parts 1/2 are out of this ticket's scope and
     legitimately keep reading ``ctx["response"]`` for "did the operation run" / "were
     external mocks exercised" checks, which are not wire-shaped questions)

The disease: each of these reads a field VALUE off a local bound to ``ctx["response"]`` /
``ctx.get("response")`` / ``_require_response(ctx)`` — the harness-reconstructed typed
payload — via ``getattr(var, field, ...)`` or plain attribute access (``var.attr``,
``var.model_dump()``), instead of ``wire_dict(ctx)`` / ``wire_field(ctx, ...)``. On
a2a/mcp/rest the typed payload is reconstructed FROM the wire with fields already coerced to
their declared types, so a wire-format regression (e.g. a bool serialized as the string
``"true"``) can pass green on a non-conformant wire.

This guard is function-name-allowlist-style (a fixed list of exactly 5 targets), not a
tree-wide behavioral walk — it does not attempt to discover new instances of the disease
elsewhere in the corpus (that is Check C's job, and several other tracked migrations').

The allowlist can only SHRINK as each target migrates; it starts at all 5 (TDD red).
"""

from __future__ import annotations

import ast

import tests.unit.test_architecture_bdd_wire_discipline as wire_discipline
from tests.unit._architecture_helpers import assert_violations_match_allowlist

_TESTS_ROOT = wire_discipline._TESTS_ROOT

# rel path (relative to tests/) -> function names this guard polices in that file.
_TARGET_FUNCS: dict[str, tuple[str, ...]] = {
    "bdd/steps/generic/then_success.py": (
        "then_response_contains_array",
        "then_no_real_orders_created",
        "then_no_real_api_calls",
    ),
    "bdd/steps/domain/uc003_update_media_buy.py": ("then_response_has_sandbox",),
    "bdd/steps/domain/uc026_package_media_buy.py": ("_get_packages",),
}

# Empty by design — every one of the 5 targets was a defect that has since been migrated, not
# an accepted/tracked exception. No allowlist entry should ever be added for these 5 (there is
# no legitimate reason to keep reading the typed payload here; unlike Check C, this guard has
# no permanent exceptions).
_ALLOWLIST: set[str] = set()


def _reads_typed_field(func: ast.FunctionDef | ast.AsyncFunctionDef, typed_vars: set[str]) -> bool:
    """True if ``func`` extracts a field VALUE off a typed-response var.

    Unlike Check C's ``_walks_typed_shape`` (restricted to a fixed ``_SHAPE_ATTRS`` set),
    this is field-name-agnostic: these 5 targets read arbitrary field names (``"sandbox"``,
    ``"packages"``, a Gherkin-supplied array-field variable) that no fixed literal set can
    enumerate. Matches:

    - ``getattr(var, field[, default])`` where ``var`` is a typed-source local (or an inline
      typed-response expression, e.g. ``getattr(_require_response(ctx), field, None)``).
    - Any attribute access ``var.attr`` off a typed-source local (covers ``resp.model_dump()``
      as well as a plain ``resp.sandbox``/``resp.packages`` read).

    Deliberately NOT matched: ``isinstance(var, ...)`` / ``var is not None`` — those are
    presence/type-routing checks, not value extraction, and several targets legitimately keep
    a presence guard even after migrating the value read itself.
    """
    for node in wire_discipline._own_nodes(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
        ):
            base = node.args[0]
            if (isinstance(base, ast.Name) and base.id in typed_vars) or wire_discipline._is_typed_response_expr(base):
                return True
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in typed_vars:
                return True
            if wire_discipline._is_typed_response_expr(node.value):
                return True
    return False


def find_typed_field_violations(target_funcs: dict[str, tuple[str, ...]]) -> set[str]:
    """Shared scan for function-name-allowlist-style typed-payload-oracle guards.

    For each ``(rel path, func names)`` pair, parse the file, find each named function, and
    flag it if it reads a field VALUE off a typed-response local (see ``_reads_typed_field``).
    Reused by sibling guards scoped to a different fixed function list (e.g.
    ``test_architecture_bdd_no_typed_status_oracle.py``) so the scan loop itself — not just the
    field-read detector — has one implementation.
    """
    found: set[str] = set()
    for rel, func_names in target_funcs.items():
        path = _TESTS_ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        by_name = {f.name: f for f in wire_discipline._enclosing_functions(tree)}
        for func_name in func_names:
            func = by_name.get(func_name)
            assert func is not None, (
                f"{rel}::{func_name} not found in the tree — target function was renamed, "
                "moved, or deleted. Update the guard's target-funcs mapping to match."
            )
            typed_vars = wire_discipline._typed_source_var_names(func)
            if typed_vars and _reads_typed_field(func, typed_vars):
                found.add(f"{rel} {func_name}")
    return found


def _find_violations() -> set[str]:
    return find_typed_field_violations(_TARGET_FUNCS)


def test_no_typed_sandbox_or_array_oracle() -> None:
    """These 5 functions must read wire_dict(ctx)/wire_field(ctx,
    ...), never a value off ctx['response']/_require_response(ctx)."""
    assert_violations_match_allowlist(
        _find_violations(),
        _ALLOWLIST,
        fix_hint=(
            "One of the 5 targets still reads a field value off "
            "the typed ctx['response'] payload (getattr(var, field, ...) or var.attr) instead "
            "of wire_dict(ctx)/wire_field(ctx, ...). Migrate the read, then remove the entry "
            "from _ALLOWLIST in this file — it can only shrink."
        ),
    )


def test_meta_scan_set_matches_five_targets() -> None:
    """Pin the scan set at exactly the 5 functions this ticket's scope covers.

    An empty/misconfigured _TARGET_FUNCS would make the guard above vacuously pass —
    this catches that independent of the allowlist's own content.
    """
    all_targets = {f"{rel} {name}" for rel, names in _TARGET_FUNCS.items() for name in names}
    assert len(all_targets) == 5, f"Expected exactly 5 target functions, got {len(all_targets)}: {sorted(all_targets)}"
    for rel in _TARGET_FUNCS:
        assert (_TESTS_ROOT / rel).is_file(), f"{rel} does not exist under {_TESTS_ROOT}"


# ── Detector meta-tests (positive/negative controls on the detection logic itself) ──
# An AST-walk guard has no "regex-slip" failure mode (that risk is specific to
# string/regex matching), so a positive + a negative control on the underlying
# helpers is the applicable bar here — proving the detector actually discriminates
# a typed-payload read from a wire read, independent of what the live tree currently
# contains (which only proves "today's tree is clean", not "the detector would catch
# a regression").


def _parse_single_function(source: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    (func,) = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    return func


def test_detector_flags_a_typed_payload_read_positive_control() -> None:
    """Positive control: a function binding ctx['response'] then reading a field off
    it via getattr must be flagged — this is the exact disease shape being eliminated."""
    func = _parse_single_function(
        "def then_example(ctx):\n"
        "    resp = ctx.get('response')\n"
        "    value = getattr(resp, 'some_field', None)\n"
        "    assert value is not None\n"
    )
    typed_vars = wire_discipline._typed_source_var_names(func)
    assert typed_vars == {"resp"}, f"Expected 'resp' recognized as a typed-response local, got {typed_vars}"
    assert _reads_typed_field(func, typed_vars), (
        "Detector failed to flag a function that reads a field value off a typed "
        "ctx['response'] local via getattr — this is exactly the disease pattern this guard "
        "eliminates; a detector that misses it provides no regression protection."
    )


def test_detector_does_not_flag_a_wire_read_negative_control() -> None:
    """Negative control: a function reading the same field via wire_dict(ctx) (no
    ctx['response']/_require_response local at all) must NOT be flagged — proves the
    detector discriminates rather than matching on the mere presence of 'getattr'."""
    func = _parse_single_function(
        "def then_example(ctx):\n"
        "    wire = wire_dict(ctx)\n"
        "    value = wire.get('some_field')\n"
        "    assert value is not None\n"
    )
    typed_vars = wire_discipline._typed_source_var_names(func)
    assert typed_vars == set(), f"Expected no typed-response local bound, got {typed_vars}"
    assert not _reads_typed_field(func, typed_vars), (
        "Detector flagged a function that only reads wire_dict(ctx) — it has no typed "
        "ctx['response']/_require_response(ctx) source at all, so this is a false positive."
    )
