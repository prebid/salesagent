"""Real-run proof for tests/bdd/scenario_liveness.py.

The pure-logic tests live in ``tests/unit/test_architecture_bdd_scenario_liveness.py``.
This file is the grounding the parent finding demands: the liveness artifact must be
emitted from an ACTUAL BDD run, and a scenario with genuinely unbound steps must be
recorded as such — not asserted against hand-constructed dataclasses.

Reads the artifact the suite's own BDD sessions published (``load_run``), and grades two
slices of it, selected by the ``@storyboard-v3.1`` marker so the scenarios discovered match
what ``scripts/audit/storyboard_coverage_map.covered_storyboards`` claims as covered:

* UC-006's ``uc006-storyboard-routing`` scenarios, whose ``@storyboard-v3.1``
  members all landed real step definitions and now all pass for real — the
  multi-format-sync-status and the four provenance gaps that used to be ledgered
  graduated once production emitted the per-creative ``status`` and the pin's
  ``PROVENANCE_*`` codes. Proves ``steps_bound=True``/live-pass for a whole slice,
  alongside the one member of it that IS still dormant (third test below).
* UC-005's ``@storyboard-v3.1`` scenarios, which carry both sides of the axis:
  the two format-id-roundtrip scenarios pass for real on all three in-process
  transports, and baseline-format-id-object-shape is ledgered against upstream
  adcp#7338. Proves the artifact distinguishes ledgered-xfail from live-pass for
  scenarios that both have their steps bound — not just the steps-bound/unbound
  axis — and proves ``steps_bound=True``/``harness_wired=True`` for a scenario
  that isn't dormant, because a guard that only ever proves the negative case
  isn't a guard.

Which members are ledgered is never frozen into this file: both graders read the
live ``_XFAIL_TAGS`` routing map (``_ledgered_reasons`` below), so a graduation
moves the expectation in the same commit that removes the route.

One further obligation is graded here, because it is not observable from the pure-logic
unit tests:

* **Membership is keyed on the scenario's own ``@T-*`` IDENTITY tag**, with the
  ``@storyboard-v3.1``/``@schema-v3.1`` provenance tags RECORDED as a field
  rather than used as a collection filter. A provenance retag currently
  removes a scenario from the measurement silently; an identity tag cannot
  move under a retag. Every expected id here is DERIVED in-test from the
  feature file's own tags (via the single owner, ``storyboard_spec.
  tagged_scenarios``) — a re-frozen literal id list would be exactly the
  frozen-literal artifact this lane's Core Invariant forbids.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence
from pathlib import Path

from scripts.audit import storyboard_spec
from tests.bdd.scenario_liveness import _IN_PROCESS_NAMES, load_run, sessions_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
FEATURES_DIR = REPO_ROOT / "tests" / "bdd" / "features"

# The marker naming the UC-006 slice this module grades.
UC006_MARKER = "uc006-storyboard-routing"


def _slice_scenarios(feature: str, marker: str) -> list[storyboard_spec.TaggedScenario]:
    """The scenarios a ``-m <marker>`` slice of ``feature`` runs, from the feature's own tags.

    Read through ``storyboard_spec.tagged_scenarios`` — the single owner of the
    tag-line/identity-tag grammar (``_IDENT_TAG_RE``) — so this grader never
    grows a second copy of the regex, and so the expected set is regenerated
    from the tree on every run instead of frozen into this file.
    """
    return [s for s in storyboard_spec.tagged_scenarios(FEATURES_DIR, tag=f"@{marker}") if s.feature == feature]


def _measured_scenarios() -> dict[str, dict]:
    """What the real run measured, projected onto the in-process transports.

    ``load_run`` refuses an artifact that does not account for every BDD module, so a
    partial measurement fails loudly here instead of being graded as the suite.
    """
    return load_run(sessions_dir())["scenarios"]


def _graded(measured: Mapping[str, dict], scenario_id: str) -> dict:
    """One scenario's projection, with the cardinality floor applied.

    Without it an empty projection would satisfy every ``all(...)`` below vacuously, so a
    scenario must have run on every in-process transport: not silently zero, not silently
    one. The transport names come from the plugin's own projection.
    """
    record = measured.get(scenario_id)
    assert record is not None, f"{scenario_id} was not measured by the real run at all"
    ran_on = {o["transport"] for o in record["observations"]}
    assert ran_on == _IN_PROCESS_NAMES, f"{scenario_id} ran in-process only on {sorted(ran_on)}"
    return record


def _identity_tags(scenarios: Sequence[storyboard_spec.TaggedScenario]) -> set[str]:
    return {s.identifier for s in scenarios}


def _ledgered_reasons() -> Mapping[str, str]:
    """The scenario-level xfail ledger, read LIVE from the routing contract that applies it.

    ``tests/bdd/conftest.py:_XFAIL_TAGS`` is the map the collection hook iterates
    to attach the marker, so reading the live module (the same way
    ``tests/unit/test_architecture_bdd_no_stale_xfail_citations.py`` and
    ``...stale_xfail_reason_text.py`` already do) makes a GRADUATION move this
    grader's expectation in the commit that removes the route. A frozen id list
    here did the opposite: five UC-006 storyboard scenarios graduated when
    production started emitting the per-creative ``status`` and the pin's
    ``PROVENANCE_*`` codes, and this file went on demanding their xfails.

    Scenario-level only, which is all this module grades: the row-level maps
    (``_SELECTIVE_XFAIL``) park individual Examples rows, and neither slice here
    has one. A scenario re-ledgered through the function-local
    ``_UC006_SPECGAP_XFAIL_TAGS`` map is invisible to this lookup — that direction
    fails LOUDLY (the record says ledgered where the expectation says live)
    rather than silently, which is the safe way round.
    """
    return dict(importlib.import_module("tests.bdd.conftest")._XFAIL_TAGS)


def _assert_ledgered_or_live(scenarios: Mapping[str, dict], graded_ids: set[str]) -> None:
    """Grade an EXACT partition of ``graded_ids`` into ledgered-xfail and live-pass.

    Exact, not a relaxed "either live or ledgered" predicate: the latter would
    satisfy the letter of the assertion while destroying its regression-detecting
    power. Which side each id falls on comes from ``_ledgered_reasons()``, and a
    ledgered record must carry that ledger's own reason VERBATIM — the artifact
    is meant to publish the curated gap text, not merely the fact of an xfail.
    """
    ledger = _ledgered_reasons()
    expected_ledgered = graded_ids & set(ledger)
    for scenario_id in sorted(graded_ids):
        record = _graded(scenarios, scenario_id)
        # Every graded scenario has real steps bound — the dormant/unbound axis is
        # graded separately, on the one member that genuinely is dormant.
        assert record["steps_bound"] is True, f"{scenario_id} unexpectedly reports steps_bound=False"
        assert record["unbound_steps"] == [], f"{scenario_id} unexpectedly reports unbound step text"
        if scenario_id in expected_ledgered:
            assert record["ledgered"] is True, f"{scenario_id} unexpectedly reports ledgered=False"
            assert all(o["outcome"] == "xfailed" for o in record["observations"])
            assert all(o["reason_category"] == "ledgered" for o in record["observations"])
            assert all(o["reason"] == ledger[scenario_id] for o in record["observations"]), (
                f"{scenario_id}'s recorded reason is not the ledger's own gap text"
            )
        else:
            assert record["ledgered"] is False, f"{scenario_id} unexpectedly reports ledgered=True"
            assert all(o["outcome"] == "passed" for o in record["observations"])
            assert all(o["reason_category"] == "live" for o in record["observations"])


def test_real_run_records_uc006_storyboard_scenarios_as_ledgered_or_live() -> None:
    """The UC-006 storyboard-routing slice is measured in full, and its
    ``@storyboard-v3.1``-tagged members have real, bound step definitions
    — none are dormant/steps-unbound — and each is recorded on the side of the
    ledgered/live partition the routing map puts it on. All of them are live
    today: the status gap closed when production began deriving the per-creative
    ``status`` from the row's review state, and the four provenance gaps closed
    when it began refusing a policy-violating creative with the pin's
    ``PROVENANCE_*`` codes. Proves the artifact tracks real state, not a frozen
    count."""
    scenarios = _measured_scenarios()

    # DERIVED, not frozen: every scenario the slice runs carries a `@T-*` identity
    # tag, and identity — not provenance — is what the artifact keys membership on.
    # The two `@schema-v3.1`-retagged members (provenance-claim-contradicted,
    # creative-reception-stateful-render) are part of this slice and must be
    # measured; a provenance retag must not be able to delete a scenario from the
    # measurement.
    slice_scenarios = _slice_scenarios("BR-UC-006-sync-creatives.feature", UC006_MARKER)
    # PRESENT in, not EQUAL to: the artifact covers the whole suite, so the slice's ids are
    # a subset of it. Every expected id must still be there.
    missing = _identity_tags(slice_scenarios) - set(scenarios)
    assert not missing, f"the real run measured none of: {sorted(missing)}"

    # The scenarios whose detailed ledgered/live behaviour this test pins — again
    # derived from the feature's own tags, not listed here.
    storyboard_tagged = {s.identifier for s in slice_scenarios if storyboard_spec.TAG in s.tags}
    # Anti-vacuity: the slice really does have @storyboard-v3.1 members to grade,
    # so a provenance retag of all of them fails here instead of passing over an
    # empty loop.
    assert storyboard_tagged, f"{UC006_MARKER} slice has no {storyboard_spec.TAG} member left to grade"
    _assert_ledgered_or_live(scenarios, storyboard_tagged)


def test_real_run_records_uc005_scenarios_as_ledgered_or_live() -> None:
    """A scenario that is NOT dormant reports steps_bound=True/harness_wired=True, and the
    artifact puts each member on the right side of the ledgered/live partition — the
    guard proves both directions, not only the failure case.

    This is where the ledgered-vs-live distinction is graded for scenarios whose
    steps are all bound: the two format-id-roundtrip scenarios pass for real on
    every in-process transport, and baseline-format-id-object-shape is ledgered
    against upstream adcp#7338. Both halves are asserted non-empty below, so the
    day one of them empties out this test says so instead of going quiet.
    """
    scenarios = _measured_scenarios()

    graded = _identity_tags(_slice_scenarios("BR-UC-005-discover-creative-formats.feature", "storyboard-v3.1"))
    missing = graded - set(scenarios)
    assert not missing, f"the real run measured none of: {sorted(missing)}"
    ledgered = graded & set(_ledgered_reasons())
    assert ledgered, (
        "the UC-005 storyboard slice no longer carries a ledgered member — "
        "the ledgered half of this partition would be graded by nothing"
    )
    assert graded - ledgered, "the UC-005 storyboard slice no longer carries a live member"

    _assert_ledgered_or_live(scenarios, graded)
    for scenario_id in sorted(graded):
        # Not dormant, and it RAN: the positive case the negative-only guard misses.
        assert scenarios[scenario_id]["harness_wired"] is True, f"{scenario_id} executed no step body"


def test_provenance_tag_is_a_recorded_field_not_a_collection_filter() -> None:
    """A ``@schema-v3.1`` retag must not delete a scenario from the measurement.

    ``scenario_liveness._TAG`` is used at ``pytest_bdd_before_scenario`` as an
    early-``return`` COLLECTION FILTER, so the two UC-006 scenarios retagged
    ``@schema-v3.1`` are invisible to the artifact — the instrument reports on a
    population that a tag edit can silently shrink. A later change converts that filter
    into a recorded field: membership is the scenario's ``@T-*`` identity tag
    (which a provenance retag cannot move), and the provenance tags are carried
    on the record as data.

    Graded on the two retagged members specifically, and on what the artifact
    then says about them. One is genuinely DORMANT today (pytest-bdd raises
    ``StepDefinitionNotFoundError`` on its first Given), so honest measurement
    must report it ``steps_bound=False`` with the unbound step named — the exact
    fact the old filter hid. The other is wired now (its Givens are the shared
    ones), and the record must say THAT: a retag can neither hide a scenario nor
    invent dormancy for one that runs.
    """
    scenarios = _measured_scenarios()

    slice_scenarios = _slice_scenarios("BR-UC-006-sync-creatives.feature", UC006_MARKER)
    retagged = [s for s in slice_scenarios if storyboard_spec.TAG not in s.tags]
    # The premise of this test: the slice really does contain members whose
    # provenance tag is not @storyboard-v3.1. If a retag ever removes them, this
    # fails loudly rather than passing vacuously over an empty list.
    assert {s.identifier for s in retagged} == {
        "T-UC-006-storyboard-provenance-claim-contradicted",
        "T-UC-006-storyboard-creative-reception-stateful-render",
    }

    unbound_given = {
        "T-UC-006-storyboard-provenance-claim-contradicted": (
            'the Buyer Agent submits a creative claiming digital_source_type "digital_capture"'
        ),
    }

    for scenario in retagged:
        record = _graded(scenarios, scenario.identifier)
        # The provenance tag is DATA on the record, not the membership predicate.
        assert set(record["tags"]) == {t.lstrip("@") for t in scenario.tags}
        if scenario.identifier in unbound_given:
            # Dormancy, measured — this is what the collection filter used to hide.
            assert record["steps_bound"] is False
            assert unbound_given[scenario.identifier] in record["unbound_steps"]
            assert all(o["outcome"] == "xfailed" for o in record["observations"])
            assert all(o["reason_category"] == "no_steps_bound" for o in record["observations"])
            # Dormancy is reported AS dormancy and never laundered into the
            # curated-gap bucket: a scenario nobody wired has graded nothing, and
            # "ledgered" would claim a production gap it never reached.
            assert record["ledgered"] is False
            # ``harness_wired`` answers "did a step body run", and the feature's
            # Background steps ARE bound, so they run before pytest-bdd reaches
            # this scenario's unbound Given — the same partly-dormant case
            # tests/unit/test_architecture_bdd_scenario_liveness.py::
            # test_harness_wired_follows_what_ran_not_the_category pins. The
            # dormancy is carried by steps_bound/unbound_steps above, which is
            # the field that cannot be confused by a Background.
            assert record["harness_wired"] is True
        else:
            # Wired and running: the record says so, whatever its provenance tag.
            assert record["steps_bound"] is True
            assert not record["unbound_steps"]
