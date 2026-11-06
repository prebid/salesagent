"""The pinned shared liveness contract, shared by its two graders.

One place, so the structural guard
(``tests/unit/test_architecture_liveness_contract_owner.py``) and the behavioral
grader (``tests/collection/test_architecture_env_route_agreement.py``) cannot drift
apart — the same disease this lane exists to cure.

Authored RED, before implementation: these names ARE the contract the implement
atom builds to. Changing one is changing the contract, and must be recorded in
the bead.
"""

from __future__ import annotations

from pathlib import Path

# The stdlib-only owner. ~20 test modules already import scripts.audit.* at
# module scope, while scripts/audit has exactly ONE import of the test tree and
# it is deliberately deferred inside a function body — an owner under
# tests/helpers/ would invert the direction this lane exists to protect.
# storyboard_spec.py does contain the CLI boundary (argparse, run_cli), but
# IMPORTING a module that defines an entry point does not EXECUTE it; its
# module-level imports are stdlib-only and it does not import conftest.
OWNER = Path("scripts/audit/storyboard_spec.py")

CONFTEST = Path("tests/bdd/conftest.py")
PLUGIN = Path("tests/bdd/scenario_liveness.py")
JOIN = Path("scripts/audit/scenario_liveness_join.py")

# ``resolve_env_route(marker_names, env_routes) -> EnvRoute | None``. The routes
# are INJECTED (the precedent already exists at scenario_liveness_join.build_index)
# so the stdlib-only owner keeps no import-time coupling to the pytest conftest
# that holds the registry. A returned row whose ``xfail_reason`` is set is a
# registered placeholder, not a real wire.
ROUTE_RESOLVER = "resolve_env_route"

# ``parse_ledger_lines(path, *, grammar)``. ONE line scan, grammar injected by
# the caller — the two ledgers have genuinely different grammars over different
# files, so this is one function with two callers, not one function with two
# implementations.
LEDGER_PARSER = "parse_ledger_lines"

# ``derive_marker_names(node) -> frozenset[str]``. The ONE canonical marker-set
# derivation, taking a pytest node (``request.node`` on both sides). Pinned to
# ``iter_markers`` because it is the superset: conftest already derives
# ``{m.name for m in request.node.iter_markers()}`` (auto-applied entity markers
# included) while the plugin derives ``scenario.tags | feature.tags``, a STRICT
# SUBSET. iter_markers is persistable — the plugin has ``request.node`` in
# ``pytest_bdd_before_scenario``.
DERIVATION_ACCESSOR = "derive_marker_names"

# G2 (binding input): under reachability the accessor's home is genuine
# latitude, so the IMPLEMENT atom picks it, RECORDS it in the task,
# and pins it here — the guards cite this value. Constraints already
# established: NOT tests/bdd/conftest.py (the plugin importing conftest is a
# partial-import cycle, conftest.py:47-49) and NOT scripts/audit/storyboard_spec.py
# (stdlib-only, no pytest).
# PICKED AND RECORDED by the implement atom : tests/helpers
# is the existing leaf both sides already import — tests/helpers/ledger.py plays
# exactly this role — and it satisfies both stated constraints (not conftest, not
# the stdlib-only owner).
DERIVATION_ACCESSOR_MODULE: Path | None = Path("tests/helpers/marker_names.py")

# The field the liveness artifact record must carry so the join can resolve the
# same route from the same input (4a: the record carries the marker set — the
# join has no other marker source).
RECORD_MARKER_FIELD = "marker_names"

# Absorbed into resolve_env_route, NOT preserved as callees one call-frame over.
# _detect_uc matters most: it is the UC-bucket lookup the join RE-IMPLEMENTS at
# scenario_liveness_join.py:111-116 — half of the measured disagreement.
ABSORBED_HELPERS = frozenset(
    {
        "_detect_uc",
        "_detect_uc011_harness",
        "_detect_delivery_harness",
        "_is_brand_shorthand_media_buy",
    }
)

FIX_HINT = (
    f"The shared contract has ONE stdlib-only owner both sides import ({OWNER}) — never copied, never re-implemented."
)


def accessor_dotted_module() -> str | None:
    """The pinned accessor as an importable dotted path, or None until G2 is filled."""
    if DERIVATION_ACCESSOR_MODULE is None:
        return None
    return ".".join(DERIVATION_ACCESSOR_MODULE.with_suffix("").parts)
