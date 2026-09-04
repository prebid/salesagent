"""Real-run proof for tests/bdd/scenario_liveness.py.

The pure-logic tests live in ``tests/unit/test_architecture_bdd_scenario_liveness.py``.
This file is the grounding the parent finding demands: the liveness artifact must be
emitted from an ACTUAL BDD run, and a scenario with genuinely unbound steps must be
recorded as such — not asserted against hand-constructed dataclasses.

Shells out to two narrow, fast, real ``pytest tests/bdd`` slices (selected by the
``@storyboard-v3.1`` marker so the count of scenarios discovered matches what
``scripts/audit/storyboard_coverage_map.covered_storyboards`` claims as covered):

* UC-006's ``uc006-storyboard-routing`` scenarios landed real
  step definitions for all six (none are dormant/steps-unbound any more). Five
  genuinely xfail with a ``ledgered`` reason citing a real production gap
  (provenance validation, multi-format sync status); the sixth,
  format-id-roundtrip-on-sync, genuinely passes. Proves the artifact distinguishes
  ledgered-xfail from live-pass for scenarios that both have their steps bound —
  not just the steps-bound/unbound axis.
* UC-005's format-id-roundtrip scenarios, which pass for real on all three
  in-process transports. Proves ``steps_bound=True`` and ``harness_wired=True`` for a
  scenario that isn't dormant — a guard that only ever proves the negative case isn't
  a guard.

Needs a real Postgres reachable via ``DATABASE_URL`` (the harness these scenarios
exercise creates tenants/principals/products for real) — skipped otherwise, same as
every other ``requires_db`` test in this suite.

Two further obligations are graded here (steps 1-2),
because neither is observable from the pure-logic unit tests:

* **The artifact must survive xdist.** ``tox.ini`` runs the bdd env under
  ``-n auto``; ``_RECORDS`` is a per-PROCESS global, so a sharded run's
  controller writes the artifact from an empty dict while every measurement
  sits in the workers. ``test_liveness_artifact_is_identical_under_xdist_and_
  serial`` compares a sharded run against a serial one by SET EQUALITY of
  scenario ids plus per-scenario observation counts — never by non-emptiness,
  which a "let the last worker write" non-fix would satisfy with one shard.
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

from collections.abc import Sequence
from pathlib import Path

import pytest

from scripts.audit import storyboard_spec
from tests.bdd.scenario_liveness import load_run, sessions_dir

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

REPO_ROOT = Path(__file__).resolve().parents[2]
FEATURES_DIR = REPO_ROOT / "tests" / "bdd" / "features"

# The two in-process slices this module shells out to.
UC006_FILE = "tests/bdd/test_uc006_sync_creatives.py"
UC006_MARKER = "uc006-storyboard-routing"
UC005_FILE = "tests/bdd/test_uc005_discover_creative_formats.py"

# Every in-process transport the bdd suite parametrizes over with
# ``BDD_E2E_ENABLED`` popped (see _run_bdd_slice's docstring).


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

    Replaces five miniature `pytest` sessions. `load_run` refuses an artifact
    that does not account for every BDD module, so a partial measurement fails
    loudly here instead of being graded as the suite.
    """
    return load_run(sessions_dir())["scenarios"]


def _graded(measured: dict[str, dict], scenario_id: str) -> dict:
    """One scenario's projection, with the cardinality floor applied.

    The floor is the honest half of the transport-set equality this migration
    drops: "not silently zero" is a real obligation, "exactly three" was a
    property of the old slice's BDD_ALL_TRANSPORTS override rather than of the
    scenario. Without it an empty projection would satisfy every `all(...)`
    assertion below vacuously.
    """
    record = measured.get(scenario_id)
    assert record is not None, f"{scenario_id} was not measured by the real run at all"
    assert record["observations"], (
        f"{scenario_id} has no in-process observation in the real run's artifact — "
        "every assertion about it below would pass over an empty list"
    )
    return record


def _identity_tags(scenarios: Sequence[storyboard_spec.TaggedScenario]) -> set[str]:
    return {s.identifier for s in scenarios}


def test_real_run_records_uc006_storyboard_scenarios_as_ledgered_or_live() -> None:
    """The UC-006 storyboard-routing slice is measured in full, and its
    ``@storyboard-v3.1``-tagged members have real, bound step definitions
    — none are dormant/steps-unbound. Five genuinely xfail
    with a real, ledgered production-gap reason (not StepDefinitionNotFoundError);
    the sixth, format-id-roundtrip-on-sync, genuinely passes. Proves the artifact
    tracks real state, not a frozen count, and distinguishes ledgered-xfail from
    live-pass even though both have steps_bound=True."""
    scenarios = _measured_scenarios()

    # DERIVED, not frozen: every scenario the slice runs carries a `@T-*` identity
    # tag, and identity — not provenance — is what the artifact keys membership on.
    # The two `@schema-v3.1`-retagged members (provenance-claim-contradicted,
    # creative-reception-stateful-render) are part of this slice and must be
    # measured; a provenance retag must not be able to delete a scenario from the
    # measurement.
    slice_scenarios = _slice_scenarios("BR-UC-006-sync-creatives.feature", UC006_MARKER)
    # PRESENT in, not EQUAL to: the artifact now covers the whole suite, so the
    # slice's ids are a subset of it. Every expected id must still be there --
    # that is the "expected list versus what actually ran" this test exists for.
    missing = _identity_tags(slice_scenarios) - set(scenarios)
    assert not missing, f"the real run measured none of: {sorted(missing)}"

    # The scenarios whose detailed ledgered/live behaviour this test pins — again
    # derived from the feature's own tags, not listed here.
    storyboard_tagged = {s.identifier for s in slice_scenarios if storyboard_spec.TAG in s.tags}
    # An EXACT partition, not a relaxed "either live or ledgered" predicate: the
    # latter would satisfy the letter of the assertion while destroying its
    # regression-detecting power.
    #
    # TWO live members since the split of @T-UC-006-storyboard-multi-format-sync.
    # Its ACTION obligations were dead code — the status gap's xfail aborted the
    # scenario before they ran — so the split left them live here and moved the
    # status obligations to their own ledgered scenario. A single live id was
    # correct only while that scenario was wholly ledgered.
    live_scenario_ids = {
        "T-UC-006-storyboard-format-id-roundtrip-on-sync",
        "T-UC-006-storyboard-multi-format-sync",
    }
    assert live_scenario_ids <= storyboard_tagged, (
        f"expected live members missing from the slice: {sorted(live_scenario_ids - storyboard_tagged)}"
    )

    for scenario_id in sorted(storyboard_tagged):
        record = _graded(scenarios, scenario_id)
        # Every scenario has real steps bound now — the dormant/unbound axis is
        # fully retired for this feature.
        assert record["steps_bound"] is True, f"{scenario_id} unexpectedly reports steps_bound=False"
        assert record["unbound_steps"] == [], f"{scenario_id} unexpectedly reports unbound step text"
        if scenario_id in live_scenario_ids:
            assert record["ledgered"] is False
            assert all(o["outcome"] == "passed" for o in record["observations"])
            assert all(o["reason_category"] == "live" for o in record["observations"])
        else:
            assert record["ledgered"] is True, f"{scenario_id} unexpectedly reports ledgered=False"
            assert all(o["outcome"] == "xfailed" for o in record["observations"])
            assert all(o["reason_category"] == "ledgered" for o in record["observations"])
            assert all("SPEC-PRODUCTION GAP" in o["reason"] for o in record["observations"]), (
                f"{scenario_id}'s xfail reason doesn't cite a real production gap"
            )
        # The cardinality floor is applied by `_graded` above. The old
        # `== {"mcp","a2a","rest"}` is deliberately gone: measured on a real run
        # it is 3 transports for 32 ledgered scenarios and a2a alone for 21,
        # split by whether the xfail reason carries `scope=per-transport` --
        # a property of the deselection optimization, which
        # test_architecture_bdd_xfail_reason_tokens.py already grades.


def test_real_run_records_uc005_format_id_roundtrip_scenarios_as_live() -> None:
    """A scenario that is NOT dormant reports steps_bound=True/harness_wired=True — the
    guard proves both directions, not only the failure case."""
    scenarios = _measured_scenarios()

    expected = _identity_tags(_slice_scenarios("BR-UC-005-discover-creative-formats.feature", "storyboard-v3.1"))
    # Both siblings pin their premise; this one did not. `expected` is keyed off
    # a feature FILENAME, so a rename would empty it and every assertion below
    # would loop zero times and pass.
    assert expected, "the UC-005 storyboard-v3.1 slice derived no scenarios — renamed feature file?"
    missing = expected - set(scenarios)
    assert not missing, f"the real run measured none of: {sorted(missing)}"
    for scenario_id in sorted(expected):
        record = _graded(scenarios, scenario_id)
        assert record["steps_bound"] is True, f"{scenario_id} unexpectedly reports steps_bound=False"
        assert record["unbound_steps"] == []
        assert record["harness_wired"] is True
        assert record["ledgered"] is False
        assert all(o["outcome"] == "passed" for o in record["observations"])


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
    then says about them: both are genuinely DORMANT today (pytest-bdd raises
    ``StepDefinitionNotFoundError`` on their first Given), so honest measurement
    must report them ``steps_bound=False`` with the unbound step named — the
    exact fact the current filter hides.
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
        "T-UC-006-storyboard-creative-reception-stateful-render": (
            "the Buyer Agent pushes creative assets to a stateful sales agent"
        ),
    }

    for scenario in retagged:
        record = _graded(scenarios, scenario.identifier)
        # The provenance tag is DATA on the record, not the membership predicate.
        assert set(record["tags"]) == {t.lstrip("@") for t in scenario.tags}
        # Dormancy, measured — this is what the collection filter currently hides.
        assert record["steps_bound"] is False
        assert unbound_given[scenario.identifier] in record["unbound_steps"]
        assert record["harness_wired"] is None
        assert all(o["outcome"] == "xfailed" for o in record["observations"])
        assert all(o["reason_category"] == "no_steps_bound" for o in record["observations"])
