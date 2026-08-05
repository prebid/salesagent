"""Superset proof for the re-keyed typed-payload package-oracle detector.

This detector replaces Check C in ``test_architecture_bdd_wire_discipline.py``. The
old detector keys on the literal symbol ``_collect_all_packages`` — so the migration
that deletes that helper also empties the detector's match set, and the guard would go
green while ~40 typed-payload reads remain permanently invisible to it. The ticket's
allowlist is therefore described as *retired, not shrunk*.

That claim needs a receipt: the replacement detector must be a strict superset of the
one it replaces. This module is that receipt. It runs the re-keyed detector against a
frozen snapshot of the SIX functions that are in ``_TYPED_PACKAGE_ORACLE_ALLOWLIST``
today, in their PRE-migration form, and requires every one of them to be flagged —
plus four negative controls that must not be.

The snapshot (``_fixtures/uc004_pre_migration_oracles.py.txt``) is a verbatim copy at
commit 567e9a3c9, kept as data rather than code because the migration deletes these
bodies from the tree. Without it the proof becomes unrunnable the moment it matters.

Required seam (does not exist yet — this is the TDD-red spec for step 1 of the
implementation plan): the re-keyed detector must expose a per-module entry point

    _find_typed_package_oracles_in(rel: str, tree: ast.Module) -> set[str]

returning ``"<rel> <func>"`` keys, with the repo-wide
``_find_typed_package_oracles()`` becoming the union of it over
``_iter_step_modules()``. A detector reachable only as a whole-tree walk cannot be
evaluated against bodies that are no longer in the tree.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import tests.unit.test_architecture_bdd_wire_discipline as wire_discipline

_SNAPSHOT = Path(__file__).parent / "_fixtures" / "uc004_pre_migration_oracles.py.txt"
_SNAPSHOT_REL = "bdd/steps/domain/uc004_delivery.py"

# The six entries of _TYPED_PACKAGE_ORACLE_ALLOWLIST, by bare function name. The
# re-keyed detector must flag every one against its pre-migration body, or the
# replacement loses coverage the old detector had.
_MUST_FLAG = (
    "then_geo_system",
    "then_placement_sorted",
    "then_placement_sorted_fallback",
    "then_packages_include_field",
    "then_packages_include_breakdown",
    "then_packages_include_two",
)

# Functions in the same snapshot that must NOT be flagged: two already migrated onto
# the wire reader, one Given-side fixture mutator (not an assertion), one that reads
# the webhook POST wire and never touches ctx["response"]. These pin that the wider
# detector is discriminating rather than "flags everything in the module".
_MUST_NOT_FLAG = (
    "then_field_true",
    "then_packages_limited",
    "_inject_placement_data",
    "then_webhook_payload_has_metrics",
)


def _rekeyed_detector():
    """Return the per-module re-keyed detector, failing loudly if step 1 has not landed."""
    detector = getattr(wire_discipline, "_find_typed_package_oracles_in", None)
    assert detector is not None, (
        "The re-keyed detector has not landed: "
        "test_architecture_bdd_wire_discipline.py exposes no "
        "_find_typed_package_oracles_in(rel, tree) -> set[str]. The re-keyed detector "
        "must be evaluable against a single parsed module so the superset proof can run "
        "it over pre-migration bodies that no longer exist in the tree."
    )
    return detector


def _flagged_names() -> set[str]:
    tree = ast.parse(_SNAPSHOT.read_text(encoding="utf-8"), filename=str(_SNAPSHOT))
    found = _rekeyed_detector()(_SNAPSHOT_REL, tree)
    return {key.split(" ", 1)[1] for key in found}


def test_snapshot_holds_every_currently_allowlisted_oracle() -> None:
    """The frozen snapshot must actually contain what the proof claims to prove.

    Mirrors ``test_meta_the_scan_set_is_not_empty`` in the guard itself: a proof run
    against an empty or drifted corpus passes for the wrong reason.
    """
    tree = ast.parse(_SNAPSHOT.read_text(encoding="utf-8"), filename=str(_SNAPSHOT))
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = set(_MUST_FLAG + _MUST_NOT_FLAG) - defined
    assert not missing, f"Frozen snapshot is missing control functions: {sorted(missing)}"

    # The six _MUST_FLAG functions were migrated onto
    # wire_packages(ctx) and the old allowlist entries removed — retired, not merely
    # shrunk (the superset proof above is what makes that claim provable). Before
    # migration this assertion pinned "the live allowlist equals the snapshot's positive
    # set" (drift guard while both existed side by side). Post-migration the meaningful
    # invariant flips: none of the six may still be in the LIVE allowlist under any name
    # — reappearing there would mean the migration was reverted or the functions were
    # silently re-allowlisted instead of fixed.
    allowlisted = {entry.split(" ", 1)[1] for entry in wire_discipline._TYPED_PACKAGE_ORACLE_ALLOWLIST}
    reappeared = allowlisted & set(_MUST_FLAG)
    assert not reappeared, (
        f"{sorted(reappeared)} migrated onto wire_packages(ctx) but "
        f"reappeared in the live Check C allowlist — migration reverted or re-allowlisted "
        f"instead of fixed."
    )


@pytest.mark.parametrize("func_name", _MUST_FLAG)
def test_rekeyed_detector_flags_every_pre_migration_allowlisted_oracle(func_name: str) -> None:
    """Superset proof: the replacement detector catches everything Check C caught."""
    assert func_name in _flagged_names(), (
        f"{func_name} is in _TYPED_PACKAGE_ORACLE_ALLOWLIST — the old detector caught it — "
        "but the re-keyed detector does not flag its pre-migration body. Retiring the "
        "allowlist on a detector that is not a superset silently drops guard coverage."
    )


@pytest.mark.parametrize("func_name", _MUST_NOT_FLAG)
def test_rekeyed_detector_does_not_flag_wire_graded_or_non_assertion_functions(func_name: str) -> None:
    """The wider detector must stay discriminating, not flag the whole module."""
    assert func_name not in _flagged_names(), (
        f"{func_name} was flagged by the re-keyed detector. It either already reads the "
        "wire or is not an assertion at all — flagging it means the detector keys on "
        "field-name mentions rather than on the read, which is the defect being fixed."
    )
