"""Guard: hand-added BDD scenarios must survive ``compile_bdd.py --merge``.

Compiled feature files are regenerated from the adcp-req sources; a scenario
that exists only locally (no ``BR-*`` adcp-req id) is classified
``LEGACY-DELETE`` and silently dropped on the next ``--merge`` unless it
carries a hand-edit marker — an ``@hand-edited`` tag OR a ``# HAND-EDITED``
comment (``classify_scenario_pair`` / ``_has_hand_edited_marker`` in
``scripts/compile_bdd.py``). This guard pins that marker for every scenario we
deliberately maintain by hand, using the compiler's own classifier — if the
marker is ever lost, this goes red instead of the scenario vanishing.

Two invariants:
  1. Each registered scenario classifies ``LEGACY-PRESERVE`` (marker present).
  2. The registry and the on-disk marked scenarios are a bijection — a future
     hand-added scenario that forgets to register here (or a stale registry
     entry) is caught, closing the "added a marked scenario, forgot to
     register it" gap that (1) alone cannot see.
"""

import pathlib
import sys

from tests.unit._architecture_helpers import assert_violations_match_allowlist

FEATURES_DIR = pathlib.Path(__file__).parent.parent / "bdd" / "features"

# (feature file, scenario id tag) pairs that are hand-maintained pending an
# upstream adcp-req id. Add new hand-edited scenarios here when introduced.
HAND_MAINTAINED_SCENARIOS = [
    # Grounded in transport-errors.mdx "Layer Separation"; upstream obligation
    # tracked in #1574 — until it lands, only @hand-edited keeps it alive.
    ("BR-UC-002-create-media-buy.feature", "@T-UC-002-ext-nl-unsupported"),
]


def _load_compiler():
    scripts_dir = str(pathlib.Path(__file__).parent.parent.parent / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import compile_bdd

    return compile_bdd


def _marked_scenarios_on_disk(compile_bdd):
    """Every scenario under ``features/`` carrying a hand-edit marker, keyed by
    its ``(feature file, @T- id tag)`` — the same key shape as the registry."""
    marked = []
    for feature_path in sorted(FEATURES_DIR.glob("*.feature")):
        feature = compile_bdd.parse_feature_file(feature_path.read_text())
        for scenario in feature.scenarios:
            if not compile_bdd._has_hand_edited_marker(scenario):
                continue
            scenario_id = compile_bdd._extract_id_from_tags(scenario.tags)
            tag = f"@{scenario_id}" if scenario_id else None
            marked.append((feature_path.name, tag))
    return marked


def test_hand_maintained_scenarios_classify_legacy_preserve():
    """Each hand-maintained scenario must classify LEGACY-PRESERVE, not LEGACY-DELETE."""
    compile_bdd = _load_compiler()

    for feature_name, scenario_tag in HAND_MAINTAINED_SCENARIOS:
        feature = compile_bdd.parse_feature_file((FEATURES_DIR / feature_name).read_text())
        matches = [s for s in feature.scenarios if scenario_tag in s.tags]
        assert matches, f"{scenario_tag} not found in {feature_name} — was it dropped by a merge?"
        for scenario in matches:
            bucket = compile_bdd.classify_scenario_pair(scenario, None)
            assert bucket == "LEGACY-PRESERVE", (
                f"{scenario_tag} in {feature_name} classifies {bucket}: the next "
                f"compile_bdd.py --merge would delete it. "
                f"Restore the @hand-edited tag or a # HAND-EDITED comment."
            )


def test_registry_is_bijection_with_marked_scenarios():
    """Registry ↔ on-disk marked scenarios must match exactly.

    Direction 1 (marked ⊆ registry): a hand-edited scenario missing from the
    registry would be dropped by ``--merge`` while the classify test above
    stays green — it only checks scenarios already registered. Direction 2
    (registry ⊆ marked): a stale entry whose scenario was renamed or lost its
    marker must surface, not silently pass.
    """
    compile_bdd = _load_compiler()

    assert_violations_match_allowlist(
        found=set(_marked_scenarios_on_disk(compile_bdd)),
        allowlist=set(HAND_MAINTAINED_SCENARIOS),
        fix_hint=(
            "A hand-edited scenario (an @hand-edited tag or # HAND-EDITED comment under "
            "tests/bdd/features/) must appear in HAND_MAINTAINED_SCENARIOS, and every registry "
            "entry must still exist and carry its marker. Add new ones; drop or re-mark stale ones."
        ),
    )
