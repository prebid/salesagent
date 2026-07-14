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


def _all_feature_scenarios(compile_bdd):
    """``(feature file, @T- id tag)`` for EVERY scenario in the compiled features."""
    out = []
    for feature_path in sorted(FEATURES_DIR.glob("*.feature")):
        feature = compile_bdd.parse_feature_file(feature_path.read_text())
        for scenario in feature.scenarios:
            sid = compile_bdd._extract_id_from_tags(scenario.tags)
            if sid:
                out.append((feature_path.name, f"@{sid}"))
    return out


def _traceability_scenarios():
    """``(feature file, @T- id tag)`` for every row in the traceability inventory."""
    data = yaml.safe_load(TRACEABILITY_PATH.read_text()) or {}
    return [
        (row["adcp_feature"], f"@{row['adcp_scenario_id']}")
        for rows in (data.get("mappings") or {}).values()
        for row in rows
    ]


def _untracked_scenarios(compile_bdd):
    """Compiled scenarios with NO traceability row at all — completely SOURCE-LESS.

    A scenario present in a feature file but absent from ``bdd-traceability.yaml`` has
    no adcp-req (or hand-edit) provenance, so ``compile_bdd.py --merge`` classifies it
    ``LEGACY-DELETE``. This closes the reviewer's blind spot: a scenario missing its
    marker, registry entry, AND traceability row is invisible to any check that starts
    from markers or from traceability — but it IS visible here, by comparing the
    compiled feature files against the traceability inventory."""
    tracked = set(_traceability_scenarios())
    return [s for s in _all_feature_scenarios(compile_bdd) if s not in tracked]


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
    """Registry ↔ marked SOURCE-LESS scenarios must match exactly.

    Direction 1 (marked source-less ⊆ registry): a hand-edited source-less
    scenario missing from the registry would be dropped by ``--merge`` while the
    classify test above stays green — it only checks scenarios already
    registered. Direction 2 (registry ⊆ marked): a stale entry whose scenario
    was renamed or lost its marker must surface, not silently pass.

    Scoped to the source-less candidate universe (same derivation as the
    completeness test below): a marked scenario that HAS a proper adcp-req
    source (upstream hand-edits arriving via main merges) is protected by its
    marker in ``classify_scenario_pair`` and needs no registry entry — the
    registry is exactly the source-less set, so the two tests can't demand
    contradictory registrations for it.
    """
    compile_bdd = _load_compiler()
    candidates = set(_handmaintained_candidates_from_traceability()) | set(_untracked_scenarios(compile_bdd))

    assert_violations_match_allowlist(
        found=set(_marked_scenarios_on_disk(compile_bdd)) & candidates,
        allowlist=set(HAND_MAINTAINED_SCENARIOS),
        fix_hint=(
            "A hand-edited SOURCE-LESS scenario (an @hand-edited tag or # HAND-EDITED comment "
            "under tests/bdd/features/, with a spec-artifact upstream_ref or no traceability row) "
            "must appear in HAND_MAINTAINED_SCENARIOS, and every registry entry must still exist "
            "and carry its marker. Add new ones; drop or re-mark stale ones."
        ),
    )


def test_source_less_scenarios_are_marked_and_registered():
    """Independent-source completeness — every SOURCE-LESS compiled scenario must be
    registered (and, via the bijection above, marked).

    The marker-derived checks above cannot see a scenario missing BOTH a marker and a
    registry entry. This check derives candidates two independent ways so no orphan
    hides:

      * spec-grounded — a traceability row whose ``upstream_refs`` point at a spec
        artifact (``*.mdx``/``*.yaml``/``*.json``/URL), which has no adcp-req render; and
      * untracked — a compiled scenario with NO traceability row at all.

    Both are ``LEGACY-DELETE`` risks, so the union must equal the registry. This closes
    the reviewer's adversarial case: an unmarked scenario with no registry entry AND no
    traceability row is invisible to marker- or traceability-only scans, but appears here
    as an untracked candidate.
    """
    compile_bdd = _load_compiler()
    candidates = set(_handmaintained_candidates_from_traceability()) | set(_untracked_scenarios(compile_bdd))

    assert_violations_match_allowlist(
        found=candidates,
        allowlist=set(HAND_MAINTAINED_SCENARIOS),
        fix_hint=(
            "A source-less scenario — grounded in a spec artifact (upstream_refs → *.mdx/*.yaml/"
            "*.json) OR with no traceability row at all — would be LEGACY-DELETE'd by "
            "compile_bdd.py --merge. It must carry an @hand-edited / # HAND-EDITED marker AND be "
            "registered in HAND_MAINTAINED_SCENARIOS (or be given a proper adcp-req source + "
            "traceability row)."
        ),
    )
