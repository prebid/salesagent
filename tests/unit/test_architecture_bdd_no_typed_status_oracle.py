"""Guard: then_response_status and then_operation_succeeds must read the wire, not the
harness-reconstructed TYPED payload.

Narrow, function-name-keyed scan — sibling to
``test_architecture_bdd_no_typed_sandbox_or_array_oracle.py`` (reuses its detection logic via
direct import, not a re-implementation), deliberately NOT a widening of Check C
(``test_architecture_bdd_wire_discipline.py``'s ``_TYPED_PACKAGE_ORACLE_ALLOWLIST`` /
``_find_typed_package_oracles``, which is scoped to UC-004's package/delivery ``_SHAPE_ATTRS``
and was confirmed, by direct detector inspection, to never watch a ``status`` read at all).

Targets exactly the 2 functions salesagent-oyiv.9 migrated onto ``wire_dict(ctx)``
(``tests/bdd/steps/_outcome_helpers.py``):

  1. ``tests/bdd/steps/generic/then_success.py::then_response_status`` (branch 1 only — the
     ``if "status" in resp_fields:`` arm). Branch 2's ``_STATUSLESS_SUCCESS_ATTRS`` fallback is
     out of this ticket's scope and still reads the typed payload legitimately; it is a known,
     accepted gap tracked by Check C's own allowlist entry, not this guard's.
  2. ``tests/bdd/steps/domain/uc026_package_media_buy.py::then_operation_succeeds``

The allowlist can only SHRINK. ``then_operation_succeeds`` is fully migrated and starts at zero
tolerance — no exception is legitimate for it. ``then_response_status`` carries ONE seeded
allowlist entry: this scan is whole-function (it cannot see "branch 1 clean, branch 2 still
typed" — a single function scoped to two independent code paths), and branch 1's read is what
this ticket migrated; branch 2's ``_STATUSLESS_SUCCESS_ATTRS`` fallback still legitimately reads
the typed payload, deferred until UC-006's two dormant sandbox scenarios graduate (same gap
Check C's own allowlist entry for this function already tracks). Remove the entry once branch 2
migrates too.
"""

from __future__ import annotations

import tests.unit.test_architecture_bdd_no_typed_sandbox_or_array_oracle as sibling_guard
import tests.unit.test_architecture_bdd_wire_discipline as wire_discipline
from tests.unit._architecture_helpers import assert_violations_match_allowlist

_TESTS_ROOT = wire_discipline._TESTS_ROOT

_TARGET_FUNCS: dict[str, tuple[str, ...]] = {
    "bdd/steps/generic/then_success.py": ("then_response_status",),
    "bdd/steps/domain/uc026_package_media_buy.py": ("then_operation_succeeds",),
}

_ALLOWLIST: set[str] = {
    # FIXME: branch 2 (_STATUSLESS_SUCCESS_ATTRS fallback) still reads the typed payload —
    # deferred until UC-006's two dormant sandbox scenarios graduate (xpass-graduation.md).
    # Branch 1 (the arm this ticket migrated) is clean; this guard cannot see the distinction
    # since it scans the whole function. Remove once branch 2 also migrates to wire_dict(ctx).
    "bdd/steps/generic/then_success.py then_response_status",
}


def _find_violations() -> set[str]:
    return sibling_guard.find_typed_field_violations(_TARGET_FUNCS)


def test_no_typed_status_oracle() -> None:
    """then_response_status and then_operation_succeeds must read wire_dict(ctx), never a value
    off ctx['response']/_require_response(ctx)."""
    assert_violations_match_allowlist(
        _find_violations(),
        _ALLOWLIST,
        fix_hint=(
            "One of the 2 status-oracle targets reads a field value off the typed "
            "ctx['response'] payload (getattr(var, field, ...) or var.attr) instead of "
            "wire_dict(ctx)/wire_field(ctx, ...). Migrate the read, then remove the entry from "
            "_ALLOWLIST in this file — it can only shrink."
        ),
    )


def test_meta_scan_set_matches_two_targets() -> None:
    """Pin the scan set at exactly the 2 functions this ticket's scope covers.

    An empty/misconfigured _TARGET_FUNCS would make the guard above vacuously pass — this
    catches that independent of the allowlist's own content.
    """
    all_targets = {f"{rel} {name}" for rel, names in _TARGET_FUNCS.items() for name in names}
    assert len(all_targets) == 2, f"Expected exactly 2 target functions, got {len(all_targets)}: {sorted(all_targets)}"
    for rel in _TARGET_FUNCS:
        assert (_TESTS_ROOT / rel).is_file(), f"{rel} does not exist under {_TESTS_ROOT}"


def test_detector_flags_a_typed_payload_read_positive_control() -> None:
    """Positive control (reuses the sibling guard's own verified detector — see that file's
    identical meta-test for the underlying AST-walk proof): a function binding
    ctx['response'] then reading a field off it via getattr must be flagged."""
    func = sibling_guard._parse_single_function(
        "def then_example(ctx):\n"
        "    resp = ctx.get('response')\n"
        "    value = getattr(resp, 'status', None)\n"
        "    assert value is not None\n"
    )
    typed_vars = wire_discipline._typed_source_var_names(func)
    assert typed_vars == {"resp"}, f"Expected 'resp' recognized as a typed-response local, got {typed_vars}"
    assert sibling_guard._reads_typed_field(func, typed_vars), (
        "Detector failed to flag a function that reads 'status' off a typed ctx['response'] "
        "local via getattr — this is exactly the disease pattern this guard eliminates."
    )


def test_detector_does_not_flag_a_wire_read_negative_control() -> None:
    """Negative control: a function reading the same field via wire_dict(ctx) (no
    ctx['response']/_require_response local at all) must NOT be flagged."""
    func = sibling_guard._parse_single_function(
        "def then_example(ctx):\n"
        "    wire = wire_dict(ctx)\n"
        "    value = wire.get('status')\n"
        "    assert value is not None\n"
    )
    typed_vars = wire_discipline._typed_source_var_names(func)
    assert typed_vars == set(), f"Expected no typed-response local bound, got {typed_vars}"
    assert not sibling_guard._reads_typed_field(func, typed_vars), (
        "Detector flagged a function that only reads wire_dict(ctx) — it has no typed "
        "ctx['response']/_require_response(ctx) source at all, so this is a false positive."
    )
