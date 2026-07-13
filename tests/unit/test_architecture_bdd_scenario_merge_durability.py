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
import re
import sys

import yaml

from tests.unit._architecture_helpers import assert_violations_match_allowlist

FEATURES_DIR = pathlib.Path(__file__).parent.parent / "bdd" / "features"
TRACEABILITY_PATH = pathlib.Path(__file__).parent.parent.parent / "docs" / "test-obligations" / "bdd-traceability.yaml"

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


def _is_spec_artifact_ref(ref: str) -> bool:
    """True if ``ref`` points at an AdCP spec ARTIFACT (a file or URL) rather than
    an adcp-req requirement id.

    Hand-authored scenarios ground themselves in the spec — prose (``*.mdx``),
    compliance storyboards (``*.yaml``/``*.yml``), or schemas (``*.json``), by
    path or URL. adcp-req-sourced scenarios reference bare requirement ids
    (``BR-*``, ``SR-*``). This is extension-agnostic across spec doc types so a
    ``.yaml``/``.json``-grounded scenario is recognized, not just ``.mdx``.
    """
    ref = str(ref)
    if "://" in ref:
        return True
    return bool(re.search(r"\.(mdx|ya?ml|json)(\b|#|$)", ref, re.IGNORECASE))


def _handmaintained_candidates_from_traceability():
    """Hand-maintained scenarios enumerated from the *independent* traceability
    inventory (``bdd-traceability.yaml``), keyed ``(feature file, @T- id tag)``.

    A candidate is a scenario grounded in an AdCP spec ARTIFACT — its
    ``upstream_refs`` point at a spec file/URL (``*.mdx`` prose, ``*.yaml``
    storyboard, ``*.json`` schema) rather than an adcp-req requirement id
    (``BR-*``/``SR-*``). Such a scenario has no adcp-req render, so
    ``compile_bdd.py --merge`` classifies it ``LEGACY-DELETE`` and drops it unless
    it carries a hand-edit marker. Deriving the inventory here — NOT from the
    on-disk markers — is what lets the guard see a scenario missing BOTH a marker
    and a registry entry (invisible to a marker-only scan), independent of which
    spec-doc extension grounds it.
    """
    data = yaml.safe_load(TRACEABILITY_PATH.read_text()) or {}
    candidates = []
    for rows in (data.get("mappings") or {}).values():
        for row in rows:
            refs = row.get("upstream_refs") or []
            if any(_is_spec_artifact_ref(ref) for ref in refs):
                candidates.append((row["adcp_feature"], f"@{row['adcp_scenario_id']}"))
    return candidates


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


def test_traceability_candidates_are_marked_and_registered():
    """Independent-source completeness — hand-maintained candidates enumerated
    from ``bdd-traceability.yaml`` must each be registered (and, via the
    bijection above, marked).

    The two checks above are marker-derived: they cannot see a scenario missing
    BOTH a marker and a registry entry, because it is in neither the ``marked``
    nor the ``registered`` set. This check derives candidates from the
    *independent* traceability inventory (scenarios grounded in spec prose,
    ``upstream_refs → *.mdx``, which have no adcp-req render and would be
    LEGACY-DELETE'd unmarked), so a spec-grounded scenario that forgot its
    ``@hand-edited`` marker surfaces here as an unregistered candidate.

    ``candidates == registered`` here plus ``marked == registered`` above
    transitively pin ``candidates == marked == registered``: a candidate missing
    only its marker is registered-but-unmarked (caught by the bijection); a
    candidate missing its registry entry is a new violation here.
    """
    assert_violations_match_allowlist(
        found=set(_handmaintained_candidates_from_traceability()),
        allowlist=set(HAND_MAINTAINED_SCENARIOS),
        fix_hint=(
            "A traceability scenario grounded in spec prose (upstream_refs → *.mdx) has no "
            "adcp-req source, so compile_bdd.py --merge would LEGACY-DELETE it unless it carries an "
            "@hand-edited / # HAND-EDITED marker AND is registered in HAND_MAINTAINED_SCENARIOS."
        ),
    )
