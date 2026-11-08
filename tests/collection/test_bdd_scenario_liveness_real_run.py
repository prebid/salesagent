"""Real-run proof for tests/bdd/scenario_liveness.py.

The pure-logic tests live in ``tests/unit/test_architecture_bdd_scenario_liveness.py``.
This file is the grounding the parent finding demands: the liveness artifact must be
emitted from an ACTUAL BDD run, and a scenario with genuinely unbound steps must be
recorded as such — not asserted against hand-constructed dataclasses.

Reads the artifact the suite's own BDD sessions published (``load_run``), and grades two
slices of it. Each slice is selected by something a PROVENANCE RETAG CANNOT MOVE — a
routing marker for UC-006, the scenarios' own identity-tag prefix for UC-005:

* UC-006's ``uc006-storyboard-routing`` scenarios. ``@uc006-storyboard-routing``
  names the harness route, not a storyboard claim, so no pin move withdraws it.
  Its ``@storyboard-v3.1`` members all landed real step definitions and now all pass
  for real — the multi-format-sync-status and the four provenance gaps that used to be
  ledgered graduated once production emitted the per-creative ``status`` and the pin's
  ``PROVENANCE_*`` codes. Proves ``steps_bound=True``/live-pass for a whole slice,
  alongside the one member of it that IS still dormant (third test below).
* UC-005's ``T-UC-005-storyboard-*`` scenarios, selected by that identity-tag PREFIX
  because UC-005 carries no routing marker of its own. Proves
  ``steps_bound=True``/``harness_wired=True`` for scenarios that aren't dormant,
  because a guard that only ever proves the negative case isn't a guard.

  This slice used to be selected by ``@storyboard-v3.1`` itself, and that is the
  defect the identity prefix fixes: the pin WITHDREW that tag from members whose cited
  ``list_formats``/``list_formats_integrity`` steps do not exist at the pin, so the slice
  shrank to the one remaining member — which GRADUATED out of the xfail ledger in the
  same pin move. A selector that moves takes the expectation derived from it along, and
  this module ended up demanding a ledgered member of a slice the pin had emptied of
  ledgered members. Selecting by identity keeps every member measured whatever its
  provenance tag says.

Which members are ledgered is never frozen into this file: both graders read the
live ``_XFAIL_TAGS`` routing map (``_ledgered_reasons`` below), so a graduation
moves the expectation in the same commit that removes the route. Nor does either
grader REQUIRE a ledgered member to exist — an assertion of that shape fails exactly
when a gap closes, which is the one time a guard must not. The ledgered axis is
graded instead by SET EQUALITY against the live ledger (see
``_assert_ledgered_set_matches_ledger``), which grades an empty ledger as strictly as
a populated one: an artifact that invents a ledgered record fails it either way.

One further obligation is graded here, because it is not observable from the pure-logic
unit tests:

* **Membership is keyed on the scenario's own ``@T-*`` IDENTITY tag**, with the
  ``@storyboard-v3.1``/``@schema-v3.1`` provenance tags RECORDED as a field
  rather than used as a collection filter. A provenance retag currently
  removes a scenario from the measurement silently; an identity tag cannot
  move under a retag. Every expected id here is DERIVED in-test from the
  feature file's own tags (via the single owner, ``storyboard_spec.
  tagged_scenarios``) — a re-frozen literal id list would be exactly the
  frozen-literal artifact this lane's Core Invariant forbids. That applies to
  the SELECTOR too, which is what this module got wrong while asserting it:
  the UC-005 slice was collected by the very provenance tag the docstring
  forbids as a filter, and the UC-006 non-storyboard population was a frozen
  pair of ids that the 3.2.1 retag grew to five.

Every population derived here carries a non-emptiness assertion, because a
derivation that matches nothing is GREEN — which is how a guard silently stops
grading rather than announcing that its subject moved.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence
from pathlib import Path

from scripts.audit import storyboard_spec
from tests.bdd.scenario_liveness import _IN_PROCESS_NAMES, load_run, sessions_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
FEATURES_DIR = REPO_ROOT / "tests" / "bdd" / "features"

# The two slices this module grades, each named by a selector no pin move can withdraw:
# UC-006 by its harness ROUTING marker, UC-005 — which has no routing marker — by the
# IDENTITY-tag prefix its storyboard scenarios share. Neither is a provenance claim,
# which is the whole point (see the module docstring).
UC006_FEATURE = "BR-UC-006-sync-creatives.feature"
UC006_MARKER = "uc006-storyboard-routing"
UC005_FEATURE = "BR-UC-005-discover-creative-formats.feature"
UC005_SELECTOR = "T-UC-005-storyboard-"


def _slice_scenarios(feature: str, selector: str) -> list[storyboard_spec.TaggedScenario]:
    """The scenarios of ``feature`` whose tag line carries ``selector``, from its own tags.

    *selector* is spelled without the leading ``@`` and matched as a SUBSTRING of
    the tag line, so it is either a whole tag (``uc006-storyboard-routing``) or an
    identity-tag PREFIX (``T-UC-005-storyboard-``) naming a family of scenarios.
    Both are facts about where a scenario lives, not claims about what grades it,
    which is what makes them safe to select by.

    Read through ``storyboard_spec.tagged_scenarios`` — the single owner of the
    tag-line/identity-tag grammar (``_IDENT_TAG_RE``) — so this grader never
    grows a second copy of the regex, and so the expected set is regenerated
    from the tree on every run instead of frozen into this file.
    """
    return [s for s in storyboard_spec.tagged_scenarios(FEATURES_DIR, tag=f"@{selector}") if s.feature == feature]


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


def _assert_measured(scenarios: Mapping[str, dict], expected: set[str]) -> None:
    """Every id of a derived slice is present in the real run's artifact.

    PRESENT in, not EQUAL to: the artifact covers the whole suite, so a slice's ids are
    a subset of it.
    """
    missing = expected - set(scenarios)
    assert not missing, f"the real run measured none of: {sorted(missing)}"


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
            assert all(o["outcome"] == "passed" for o in record["observations"]), (
                f"{scenario_id} did not pass on every transport: {record['observations']}"
            )
            assert all(o["reason_category"] == "live" for o in record["observations"])


def _assert_ledgered_set_matches_ledger(scenarios: Mapping[str, dict], slice_ids: set[str]) -> None:
    """The artifact's ledgered set over ``slice_ids`` is EXACTLY what the live ledger routes.

    This is the ledgered axis's anti-vacuity guard, and it deliberately does NOT
    take the form "the slice must contain a ledgered member". That form fails
    precisely when a gap CLOSES — the AdCP 3.2.1 pin graduated UC-005's one
    ledgered storyboard scenario and this module went red for the success — so it
    punishes progress and teaches nothing. Set equality has no such failure mode
    and is strictly harder to satisfy: it grades BOTH directions on EVERY member of
    the slice, including the empty case. A record that reports ``ledgered`` for
    something the ledger does not route fails it, a record that stops reporting a
    routed entry fails it, and no retag or graduation can make it vacuous, because
    its subject is every member of the slice rather than a subset the ledger happens
    to populate.

    Scoped to the slice because the artifact covers the whole suite, and this module
    grades the scenario-level ledger only (see ``_ledgered_reasons``). The
    function-local maps (``_UC006_SPECGAP_XFAIL_TAGS``) are invisible to
    ``_ledgered_reasons``, so a scenario re-ledgered through one fails HERE, which
    is the loud direction and the safe way round.
    """
    ledger = _ledgered_reasons()
    assert {sid for sid in slice_ids if scenarios[sid]["ledgered"]} == slice_ids & set(ledger), (
        "the artifact's ledgered set is not what tests/bdd/conftest.py:_XFAIL_TAGS routes for "
        "these scenarios — either a record claims a curated gap the ledger does not carry, or a "
        "ledger entry stopped being reported"
    )


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
    # Members whose provenance tag is not @storyboard-v3.1 are part of this slice and
    # must be measured; a provenance retag must not be able to delete a scenario from
    # the measurement. The count is deliberately not stated: the 3.2.1 pin moved it
    # from two to five, and whichever number is written here will go stale next.
    slice_scenarios = _slice_scenarios(UC006_FEATURE, UC006_MARKER)
    slice_ids = _identity_tags(slice_scenarios)
    assert slice_ids, f"no {UC006_FEATURE} scenario carries @{UC006_MARKER}"
    _assert_measured(scenarios, slice_ids)

    # The scenarios whose detailed ledgered/live behaviour this test pins — again
    # derived from the feature's own tags, not listed here.
    storyboard_tagged = {s.identifier for s in slice_scenarios if storyboard_spec.TAG in s.tags}
    # Anti-vacuity: the slice really does have @storyboard-v3.1 members to grade,
    # so a provenance retag of all of them fails here instead of passing over an
    # empty loop.
    assert storyboard_tagged, f"{UC006_MARKER} slice has no {storyboard_spec.TAG} member left to grade"
    _assert_ledgered_or_live(scenarios, storyboard_tagged)
    # Graded across the WHOLE slice, not just its storyboard-tagged subset: a record
    # that invents a curated gap is wrong whatever provenance tag the scenario carries.
    _assert_ledgered_set_matches_ledger(scenarios, slice_ids)


def test_real_run_records_uc005_scenarios_as_ledgered_or_live() -> None:
    """A scenario that is NOT dormant reports steps_bound=True/harness_wired=True, and the
    artifact puts each member on the right side of the ledgered/live partition — the
    guard proves both directions, not only the failure case.

    The slice is the ``T-UC-005-storyboard-*`` family, selected by that IDENTITY
    prefix. Every member passes for real on every in-process transport today and none
    is ledgered. So the live half is asserted
    non-empty and the ledgered half is graded by set equality against the live ledger
    (:func:`_assert_ledgered_set_matches_ledger`) rather than by demanding a member
    the pin has legitimately removed.
    """
    graded = _identity_tags(_slice_scenarios(UC005_FEATURE, UC005_SELECTOR))
    # Anti-vacuity on the DERIVED population: an identity prefix that matches nothing
    # would otherwise grade an empty slice green.
    assert graded, f"no {UC005_FEATURE} scenario carries an @{UC005_SELECTOR}* identity tag"

    scenarios = _measured_scenarios()
    _assert_measured(scenarios, graded)
    ledgered = graded & set(_ledgered_reasons())
    assert graded - ledgered, "the UC-005 storyboard slice no longer carries a live member"

    _assert_ledgered_or_live(scenarios, graded)
    _assert_ledgered_set_matches_ledger(scenarios, graded)
    for scenario_id in sorted(graded):
        # Not dormant, and it RAN: the positive case the negative-only guard misses.
        assert scenarios[scenario_id]["harness_wired"] is True, f"{scenario_id} executed no step body"


def test_provenance_tag_is_a_recorded_field_not_a_collection_filter() -> None:
    """A ``@schema-v3.1`` retag must not delete a scenario from the measurement.

    ``scenario_liveness._TAG`` is used at ``pytest_bdd_before_scenario`` as an
    early-``return`` COLLECTION FILTER, so every UC-006 scenario carrying
    ``@schema-v3.1`` instead is invisible to the artifact — the instrument reports on a
    population that a tag edit can silently shrink. A later change converts that filter
    into a recorded field: membership is the scenario's ``@T-*`` identity tag
    (which a provenance retag cannot move), and the provenance tags are carried
    on the record as data.

    Graded on the WHOLE slice and on what the artifact says about each member,
    with the non-storyboard subset asserted non-empty as the premise. One member is
    genuinely DORMANT today (pytest-bdd raises ``StepDefinitionNotFoundError`` on
    its first Given), so honest measurement must report it ``steps_bound=False``
    with the unbound step named — the exact fact the old filter hid. The rest are
    wired, and the record must say THAT: a retag can neither hide a scenario nor
    invent dormancy for one that runs.

    The non-storyboard subset used to be a frozen PAIR of ids here, and the AdCP
    3.2.1 pin grew it to five by withdrawing ``@storyboard-v3.1`` from
    multi-format-sync, multi-format-sync-status and format-id-roundtrip-on-sync.
    Re-freezing it with today's five would be the same defect with newer numbers, so
    the subset is derived and only its non-emptiness is asserted.
    """
    # Both premises are checked against the feature file BEFORE the artifact is read: a
    # grader whose subject has moved should say so as that, not as a missing record.
    slice_scenarios = _slice_scenarios(UC006_FEATURE, UC006_MARKER)
    # The slice really does contain members whose provenance tag is not
    # @storyboard-v3.1. If a retag ever removes them, this fails loudly rather than
    # grading the retag question over a slice that has nothing retagged in it.
    assert [s for s in slice_scenarios if storyboard_spec.TAG not in s.tags], (
        f"every {UC006_MARKER} member now carries {storyboard_spec.TAG}, so this slice no "
        "longer exercises the question this test asks: whether a non-storyboard provenance "
        "tag can delete a scenario from the measurement"
    )

    unbound_given = {
        "T-UC-006-storyboard-provenance-claim-contradicted": (
            'the Buyer Agent submits a creative claiming digital_source_type "digital_capture"'
        ),
    }
    # The dormancy half's subject is named by identity (a provenance retag cannot move
    # it), but a rename or a wiring-up can: assert the named scenario is still in the
    # slice, so that half going ungraded is a failure rather than a quietly skipped
    # branch of the loop below.
    assert set(unbound_given) <= _identity_tags(slice_scenarios), (
        f"{sorted(set(unbound_given) - _identity_tags(slice_scenarios))} is no longer a "
        f"{UC006_MARKER} member — the dormancy half of this grader has no subject"
    )

    scenarios = _measured_scenarios()

    for scenario in slice_scenarios:
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
