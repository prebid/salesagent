"""Guard: hand-added BDD scenarios must survive ``compile_bdd.py --merge``.

Compiled feature files are regenerated from the adcp-req sources; a scenario
that exists only locally (no ``BR-*`` adcp-req id) is classified
``LEGACY-DELETE`` and silently dropped on the next ``--merge`` unless it
carries the ``@hand-edited`` marker (``classify_scenario_pair`` in
``scripts/compile_bdd.py``). This test pins that marker for every scenario we
deliberately maintain by hand, using the compiler's own classifier — if the
tag is ever lost, this goes red instead of the scenario vanishing.
"""

import pathlib
import sys

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
                f"compile_bdd.py --merge would delete it. Restore the @hand-edited tag."
            )
