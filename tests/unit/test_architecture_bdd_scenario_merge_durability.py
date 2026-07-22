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

import re
import sys

import yaml

from tests.unit._architecture_helpers import assert_violations_match_allowlist, repo_root

_ROOT = repo_root()
FEATURES_DIR = _ROOT / "tests" / "bdd" / "features"
TRACEABILITY_PATH = _ROOT / "docs" / "test-obligations" / "bdd-traceability.yaml"

# (feature file, scenario id tag) pairs that are hand-maintained pending an
# upstream adcp-req id. Add new hand-edited scenarios here when introduced.
HAND_MAINTAINED_SCENARIOS = [
    # Grounded in transport-errors.mdx "Layer Separation"; upstream obligation
    # tracked in #1574 — until it lands, only @hand-edited keeps it alive.
    ("BR-UC-002-create-media-buy.feature", "@T-UC-002-ext-nl-unsupported"),
]


def _load_compiler():
    scripts_dir = str(_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import compile_bdd

    return compile_bdd


def _is_adcp_req_id(ref: str) -> bool:
    """True if ``ref`` is a bare adcp-req requirement id (``BR-*``/``SR-*``).

    A row whose ``upstream_refs`` carry at least one such id RENDERS from adcp-req, so
    ``compile_bdd.py --merge`` keeps it. Anything else — a spec artifact (``*.mdx``/URL),
    an unmodeled ref shape (``docs/x.md#Anchor``, a bare path), OR an EMPTY list — has no
    adcp-req render and is a LEGACY-DELETE risk. Keying on the ABSENCE of a requirement id
    (rather than the PRESENCE of a recognized artifact form) is what makes the discovery
    exhaustive: an empty or unmodeled ``upstream_refs`` can no longer slip past.
    """
    return bool(re.match(r"^\s*(BR|SR)-", str(ref)))


def _row_has_adcp_req_source(refs) -> bool:
    """True if any of a traceability row's ``upstream_refs`` is an adcp-req requirement id."""
    return any(_is_adcp_req_id(ref) for ref in (refs or []))


def _is_spec_artifact_ref(ref: str) -> bool:
    """True if ``ref`` points at an AdCP spec ARTIFACT (a file or URL) rather than
    an adcp-req requirement id. Retained for the discriminator self-test; the candidate
    derivation now keys on ``_row_has_adcp_req_source`` so empty/unmodeled refs are caught.
    """
    ref = str(ref)
    if "://" in ref:
        return True
    return bool(re.search(r"\.(mdx|ya?ml|json)(\b|#|$)", ref, re.IGNORECASE))


def _is_merge_set_feature(feature_name: str) -> bool:
    """True if the feature file is compiled/merged from adcp-req (so a source-less scenario
    in it is a real LEGACY-DELETE risk), rather than a hand-authored feature the merger never
    reaches. Hand-authored files carry a ``# Hand-authored feature`` header; the merge set is
    everything else (the ``# DO NOT EDIT -- re-run: compile_bdd.py`` outputs)."""
    path = FEATURES_DIR / feature_name
    if not path.exists():
        return False
    return "Hand-authored feature" not in path.read_text()[:500]


def _handmaintained_candidates_from_traceability():
    """Hand-maintained scenarios enumerated from the *independent* traceability
    inventory (``bdd-traceability.yaml``), keyed ``(feature file, @T- id tag)``.

    A candidate is a MERGE-SET scenario with no adcp-req requirement id in its
    ``upstream_refs`` — a spec artifact (``*.mdx``/URL), an unmodeled ref, OR an EMPTY
    list. Such a scenario has no adcp-req render, so ``compile_bdd.py --merge`` classifies
    it ``LEGACY-DELETE`` and drops it unless it carries a hand-edit marker. Keying on the
    ABSENCE of a requirement id (not the presence of a recognized artifact form) catches
    empty/unmodeled refs; scoping to merge-set files excludes hand-authored features the
    merger never touches. Deriving the inventory here — NOT from the on-disk markers — is
    what lets the guard see a scenario missing BOTH a marker and a registry entry.
    """
    data = yaml.safe_load(TRACEABILITY_PATH.read_text()) or {}
    candidates = []
    for rows in (data.get("mappings") or {}).values():
        for row in rows:
            # Source-less = no adcp-req requirement id (catches spec-artifact, unmodeled, AND
            # empty refs), AND the file is in the merge set (a hand-authored feature the merger
            # never touches is not at LEGACY-DELETE risk, so it is not a candidate).
            if _row_has_adcp_req_source(row.get("upstream_refs")):
                continue
            if not _is_merge_set_feature(row["adcp_feature"]):
                continue
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


def test_is_spec_artifact_ref_discriminates_source_from_requirement_id():
    """Self-test for the scenario-discovery discriminator so a silently-degraded
    ``_is_spec_artifact_ref`` (a broken regex, a traceability-schema drift) can't empty the
    candidate set and let the set-equality guards below pass vacuously — the guard "passes when
    the candidate set is empty" only in its stale direction. Mirrors the committed known-bad/good
    detector self-test in ``test_architecture_no_raw_select.py::TestColumnSelectDetection``.
    """
    # POSITIVE — a source-less scenario grounds itself in a spec ARTIFACT (prose/storyboard/schema
    # by path or URL); each must be recognized as a candidate ref.
    for ref in (
        "dist/docs/3.1.1/media-buy/task-reference/create_media_buy.mdx",
        "dist/compliance/3.1.1/uc-004.yaml",
        "dist/compliance/3.1.1/uc-004.yml",
        "schemas/v1/media-buy.json",
        "https://github.com/adcontextprotocol/adcp/blob/main/spec.mdx",
        "docs/foo.mdx#create-media-buy",
    ):
        assert _is_spec_artifact_ref(ref) is True, f"{ref!r} is a spec artifact and must be detected"
    # NEGATIVE — a bare adcp-req requirement id renders from adcp-req (not source-less); must NOT match.
    for ref in ("BR-UC-004-MAIN-01", "SR-RULE-055", "adcp-req-123", "UC-004"):
        assert _is_spec_artifact_ref(ref) is False, f"{ref!r} is a requirement id, not a spec artifact"


def test_source_less_predicate_catches_empty_and_unmodeled_refs():
    """The candidate predicate keys on ABSENCE of an adcp-req id, so an empty or unmodeled
    ``upstream_refs`` (the reviewer's blind spot) is discovered, not only recognized spec-artifact
    extensions. Known-bad/good self-test so a degraded predicate can't empty the candidate set."""
    # adcp-req-sourced rows are NOT source-less (BR-*/SR- present anywhere in the list).
    assert _row_has_adcp_req_source(["BR-UC-004-MAIN-01"]) is True
    assert _row_has_adcp_req_source(["transport-errors.mdx#x", "SR-RULE-055"]) is True
    # Source-less shapes the OLD spec-artifact predicate would have missed:
    assert _row_has_adcp_req_source([]) is False  # empty — the exact miss this fix closes
    assert _row_has_adcp_req_source(["docs/x.md#Anchor"]) is False  # unmodeled extension
    assert _row_has_adcp_req_source(["building/operating/transport-errors"]) is False  # bare path
    assert _row_has_adcp_req_source(["spec.txt"]) is False


def test_merge_set_scoping_excludes_hand_authored_features():
    """Source-less rows in a HAND-AUTHORED feature (the merger never reaches them) are not
    LEGACY-DELETE candidates; source-less rows in a merge-set (# DO NOT EDIT) file are."""
    # The one registered hand-maintained scenario lives in a merge-set file and must be in scope.
    assert _is_merge_set_feature("BR-UC-002-create-media-buy.feature") is True
    # Hand-authored features (their empty-ref rows must NOT become candidates).
    assert _is_merge_set_feature("BR-ADMIN-ACCOUNTS.feature") is False
    assert _is_merge_set_feature("BR-UC-002-account-access.feature") is False


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
