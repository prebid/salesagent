"""Regression tests for tests/bdd/scenario_liveness.py.

The parent finding: a ``@storyboard-v3.1``-tagged scenario
whose steps have no bound step definitions is converted to ``xfail`` by
``tests/bdd/conftest.py``'s auto-xfail hookwrapper and counts as "covered" in
``storyboard_coverage_map`` forever — nothing measures whether the scenario
actually runs. ``scenario_liveness.py`` fixes the measurement by emitting a
per-run artifact from a real BDD invocation. This file pins the pure logic
(reason classification, identifier/transport extraction, the
``ScenarioLiveness`` record's aggregation rules) offline, without running
pytest-bdd. The real-run proof — that a genuinely unbound scenario is recorded
as such, and a genuinely bound one is not — lives in
``tests/integration/test_bdd_scenario_liveness_real_run.py``, which shells out
to a real narrow ``pytest tests/bdd`` slice against Postgres.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.bdd import scenario_liveness as sl


def test_classify_reason_none_is_live() -> None:
    assert sl._classify_reason(None) == "live"


def test_classify_reason_step_definition_not_found() -> None:
    reason = 'Step definition not found: Step definition is not found: Given "foo". Line 12 in scenario "x"'
    assert sl._classify_reason(reason) == "no_steps_bound"


def test_classify_reason_not_implemented() -> None:
    assert sl._classify_reason("Not implemented: something") == "no_steps_bound"


def test_classify_reason_harness_not_wired_generic() -> None:
    assert sl._classify_reason("No harness wired for UC-999") == "harness_not_wired"


def test_classify_reason_harness_not_wired_uc004_variant() -> None:
    assert sl._classify_reason("UC-004 harness not yet wired for type: bogus") == "harness_not_wired"


def test_classify_reason_residual_bucket_is_ledgered() -> None:
    """Anything that isn't the two structured categories is the curated/production-gap bucket."""
    assert sl._classify_reason("MCP wrapper does not accept disclosure_positions") == "ledgered"


def test_transport_name_extracts_bracket_prefix() -> None:
    class _Item:
        nodeid = "tests/bdd/test_x.py::test_foo[mcp]"

    assert sl._transport_name(_Item()) == "mcp"  # type: ignore[arg-type]


def test_transport_name_extracts_first_dash_segment_of_compound_param() -> None:
    class _Item:
        nodeid = "tests/bdd/test_x.py::test_foo[rest-some-param-value]"

    assert sl._transport_name(_Item()) == "rest"  # type: ignore[arg-type]


def test_transport_name_no_bracket_is_default() -> None:
    class _Item:
        nodeid = "tests/bdd/test_x.py::test_foo"

    assert sl._transport_name(_Item()) == "default"  # type: ignore[arg-type]


#: Parametrize values that CONTAIN a bracket, with the transport each one really has.
#:
#: THIS IS THE CASE WHOSE ABSENCE LET THE DEFECT SHIP. The three tests above use
#: bracket-free params, so a derivation splitting on the LAST bracket passes all of them --
#: and that is what shipped: 433 of 6095 observations in one full run carried a fragment of
#: a parametrize value as their transport, across 94 distinct phantom values, every one a
#: plausible string rather than an error (salesagent-8qela.159). The same shape is live in
#: ``suite_delta.nodeid_of`` in another repository (salesagent-8qela.72/.128), whose own
#: round-trip self-check likewise has no bracketed case.
#:
#: All payloads are REAL, taken from the artifact rather than invented.
#:
#: WHICH OF THESE ARE LOAD-BEARING, measured by reinstating the last-bracket split: THREE of
#: the five fire. The two IPv6 rows do, because their value contains a literal ``[``, and so
#: does ``test_transport_name_refuses_a_param_merely_containing_a_transport_name`` below.
#: The two JSON rows DO NOT -- ``{"radius": 10}`` and ``"US"]}`` carry braces, not brackets,
#: so a last-bracket split still answers correctly for the nodeids as spelled here; their
#: real nodeids must have carried an earlier ``[`` that these reconstructions omit. They are
#: kept because they document the shapes the artifact actually held, but a weakening of the
#: split would pass them. If you are changing the derivation, the IPv6 rows are the ones to
#: watch.
_BRACKETED_PARAMS = [
    # an IPv6 literal inside a URL -- the worst shape, and the one shared with .72/.128
    ("tests/bdd/test_x.py::test_foo[rest-https://[fc00::1]/hook]", "rest"),
    ("tests/bdd/test_x.py::test_foo[a2a-https://[ff02::1]/hook]", "a2a"),
    # a JSON payload
    ('tests/bdd/test_x.py::test_foo[mcp-{"radius": 10}]}]', "mcp"),
    # a JSON array tail
    ('tests/bdd/test_x.py::test_foo[rest-"US"]}]', "rest"),
    # the admin transport, which a hand-written vocabulary omitted while it had 44 real
    # observations -- so this row grades the VOCABULARY, not just the split
    ("tests/bdd/test_x.py::test_foo[admin_integration-case]", "admin_integration"),
]


@pytest.mark.parametrize(("nodeid", "expected"), _BRACKETED_PARAMS, ids=lambda v: str(v)[:40])
def test_transport_name_survives_a_bracket_inside_the_parametrize_value(nodeid: str, expected: str) -> None:
    """A ``[`` in the VALUE must not become the transport."""

    class _Item:
        pass

    _Item.nodeid = nodeid  # type: ignore[attr-defined]
    assert sl._transport_name(_Item()) == expected  # type: ignore[arg-type]


def test_transport_name_refuses_a_param_merely_containing_a_transport_name() -> None:
    """``restaurant`` is not ``rest``.

    The other direction, so the bracket discipline is graded both ways: a derivation loose
    enough to survive the cases above could also match a transport name embedded in an
    ordinary word, and would then invent a transport where there is none.
    """

    class _Item:
        nodeid = "tests/bdd/test_x.py::test_foo[restaurant-case]"

    assert sl._transport_name(_Item()) == "default"  # type: ignore[arg-type]


def test_scenario_liveness_record_unbound_flips_steps_bound_false() -> None:
    record = sl.ScenarioLiveness(scenario_id="T-X", feature="f.feature")
    assert record.steps_bound is True
    record.record_unbound(["a step nobody implemented"])
    assert record.steps_bound is False
    assert record.unbound_steps == ["a step nobody implemented"]


def test_scenario_liveness_record_unbound_dedupes_across_transports() -> None:
    """The same scenario is observed once per transport; an unbound step must not repeat."""
    record = sl.ScenarioLiveness(scenario_id="T-X", feature="f.feature")
    record.record_unbound(["step a"])
    record.record_unbound(["step a", "step b"])
    assert record.unbound_steps == ["step a", "step b"]


def test_scenario_liveness_record_unbound_with_no_steps_is_a_noop() -> None:
    record = sl.ScenarioLiveness(scenario_id="T-X", feature="f.feature")
    record.record_unbound([])
    assert record.steps_bound is True
    assert record.unbound_steps == []


def _obs(
    reason: str | None,
    category: str,
    nodeid: str = "tests/bdd/test_x.py::test_foo[mcp]",
    *,
    steps_executed: bool = True,
) -> sl.Observation:
    """One observation. ``steps_executed`` defaults to True — a run that got into its
    own steps — because that is the ordinary case; the tests about suppression pass
    False explicitly, which is the whole point of the field."""
    return sl.Observation(
        transport="mcp",
        nodeid=nodeid,
        outcome="xfailed",
        reason=reason,
        reason_category=category,
        steps_executed=steps_executed,
    )


def test_record_observation_ledgered_by_category() -> None:
    record = sl.ScenarioLiveness(scenario_id="T-X", feature="f.feature")
    record.record_observation(_obs("some curated gap", "ledgered"))
    assert record.ledgered is True


def test_record_observation_ledgered_by_e2e_rest_ledger_nodeid() -> None:
    """A nodeid present in the e2e_rest known-failures ledger is ledgered even if its
    reason text doesn't independently classify as such (the ledger is the real signal,
    the reason-text bucketing is a heuristic on top of it — see module docstring)."""
    ledgered_nodeid = next(iter(sl._LEDGERED_NODEIDS))
    record = sl.ScenarioLiveness(scenario_id="T-X", feature="f.feature")
    record.record_observation(_obs(None, "live", nodeid=ledgered_nodeid))
    assert record.ledgered is True


def test_record_observation_harness_not_wired_sets_false_and_sticks() -> None:
    """harness_wired=False is a real observed fact; a later 'live' observation for a
    different transport must not paper over it.

    The suppressed transport carries ``steps_executed=False`` because that reason is
    raised from fixture setup, where no step body can have run — the category no
    longer decides the verdict, the fact does.
    """
    record = sl.ScenarioLiveness(scenario_id="T-X", feature="f.feature")
    record.record_observation(_obs("No harness wired for UC-999", "harness_not_wired", steps_executed=False))
    assert record.harness_wired is False
    record.record_observation(_obs(None, "live"))
    assert record.harness_wired is False


def test_record_observation_live_sets_harness_wired_true() -> None:
    record = sl.ScenarioLiveness(scenario_id="T-X", feature="f.feature")
    record.record_observation(_obs(None, "live"))
    assert record.harness_wired is True


def test_harness_wired_follows_what_ran_not_the_category() -> None:
    """``no_steps_bound`` does not decide the verdict; whether a step ran does.

    This replaces an assertion that a ``no_steps_bound`` observation left
    ``harness_wired`` None. It no longer can: the verdict is derived from
    ``steps_executed``, a fact the run records, so there is nothing left to be
    unknown about. And the category genuinely does not answer it —
    ``StepDefinitionNotFoundError`` is raised at the FIRST unmatched step, so a
    scenario whose third step is missing has already executed two (measured: 33 such
    observations in one UC-010 run).
    """
    record = sl.ScenarioLiveness(scenario_id="T-X", feature="f.feature")
    record.record_observation(_obs("Step definition not found: ...", "no_steps_bound", steps_executed=False))
    assert record.harness_wired is False

    partway = sl.ScenarioLiveness(scenario_id="T-Y", feature="f.feature")
    partway.record_observation(_obs("Step definition not found: the third one", "no_steps_bound"))
    assert partway.harness_wired is True


def test_scenario_liveness_to_dict_shape() -> None:
    record = sl.ScenarioLiveness(scenario_id="T-X", feature="f.feature")
    record.record_unbound(["a step"])
    record.record_observation(_obs("Step definition not found: a step", "no_steps_bound", steps_executed=False))
    d = record.to_dict()
    assert d == {
        "scenario_id": "T-X",
        "feature": "f.feature",
        "steps_bound": False,
        "unbound_steps": ["a step"],
        # False, not None: the observation records that no step body ran, which is a
        # measured fact rather than an open question.
        "harness_wired": False,
        "ledgered": False,
        # The scenario's own tags, carried as DATA. The provenance tag used to be
        # a collection FILTER, so a retag could delete a scenario from the
        # measurement entirely.
        "tags": [],
        # The routing contract's marker set, persisted so the audit join can
        # resolve the SAME route the conftest did. Both are empty here
        # because this record was built directly, not through
        # pytest_bdd_before_scenario.
        "marker_names": [],
        "observations": [
            {
                "transport": "mcp",
                "nodeid": "tests/bdd/test_x.py::test_foo[mcp]",
                "outcome": "xfailed",
                "reason": "Step definition not found: a step",
                "reason_category": "no_steps_bound",
                "steps_executed": False,
            }
        ],
    }


def test_build_artifact_is_sorted_and_empty_by_default() -> None:
    """A run that collects zero storyboard-tagged scenarios still emits a valid,
    empty artifact — presence of the key with an empty list, not a missing file."""
    sl._RECORDS.clear()
    try:
        artifact = sl.build_artifact()
        assert artifact == {"scenarios": []}
    finally:
        sl._RECORDS.clear()


def test_build_artifact_sorts_by_scenario_id() -> None:
    sl._RECORDS.clear()
    try:
        sl._RECORDS["T-Z"] = sl.ScenarioLiveness(scenario_id="T-Z", feature="f.feature")
        sl._RECORDS["T-A"] = sl.ScenarioLiveness(scenario_id="T-A", feature="f.feature")
        artifact = sl.build_artifact()
        assert [s["scenario_id"] for s in artifact["scenarios"]] == ["T-A", "T-Z"]
    finally:
        sl._RECORDS.clear()


def test_artifact_path_defaults_to_test_results() -> None:
    assert sl.artifact_path() == Path("test-results") / "bdd_scenario_liveness.json"


def test_artifact_path_honors_env_override(monkeypatch) -> None:
    monkeypatch.setenv("BDD_LIVENESS_ARTIFACT", "/tmp/custom-liveness.json")
    assert sl.artifact_path() == Path("/tmp/custom-liveness.json")


def test_scenario_liveness_plugin_is_registered_in_conftest() -> None:
    """A guard that cannot fail is not a guard: pin the wiring that makes this module
    actually run — dropping the ``pytest_plugins`` entry would silently stop emitting
    the artifact with no other test catching it."""
    conftest_text = (Path(__file__).resolve().parents[2] / "tests" / "bdd" / "conftest.py").read_text(encoding="utf-8")
    assert '"tests.bdd.scenario_liveness"' in conftest_text


# ---------------------------------------------------------------------------
# The verdict is derived from what ran, not from how the reason is worded.
#
# ``_classify_reason``'s residual bucket is "ledgered", and "ledgered" used to set
# harness_wired=True. Two blanket routes in tests/bdd/conftest.py name reasons that
# land in it, so feeding either to the old logic produced a WIRED verdict for a
# scenario that executed nothing. Quoted verbatim below because the point is that
# THIS wording used to move a verdict.
# ---------------------------------------------------------------------------

UC010_NOT_WIRED = "UC-010 harness wiring not extended to this tag (dormant, never graded)"
UC006_UNCLASSIFIED = (
    "UC-006 UNCLASSIFIED: no row names this scenario, so nobody has decided what "
    "blocks it. Wire it, or give it a row naming its blocker -- do not leave it here"
)


def _verdict(reason: str, *, steps_executed: bool) -> sl.ScenarioLiveness:
    record = sl.ScenarioLiveness(scenario_id="T-UC-000-probe", feature="probe.feature")
    record.record_observation(_obs(reason, sl._classify_reason(reason), steps_executed=steps_executed))
    return record


@pytest.mark.parametrize(
    ("route", "reason"),
    [("uc010-not-wired", UC010_NOT_WIRED), ("uc006-unclassified", UC006_UNCLASSIFIED)],
)
def test_a_blanket_route_reason_is_never_wired(route: str, reason: str) -> None:
    """Both live offenders. Fails whenever the verdict is read off the text."""
    assert sl._classify_reason(reason) == "ledgered", (
        f"{route}: precondition — this reason falls into the residual bucket, which is what made it dangerous"
    )
    record = _verdict(reason, steps_executed=False)
    assert record.harness_wired is False, (
        f"{route}: a scenario aborted in fixture setup reported harness_wired={record.harness_wired!r}; "
        "no step body ran, so it graded nothing"
    )


@pytest.mark.parametrize("steps_executed", [True, False])
def test_rewording_cannot_move_the_verdict(steps_executed: bool) -> None:
    """Four spellings of one situation, including the "wired"/"wiring" near-miss."""
    wordings = [
        "harness not wired for this tag",
        "harness wiring not extended to this tag",
        "nobody has decided what blocks this scenario",
        "",
    ]
    verdicts = {w: _verdict(w, steps_executed=steps_executed).harness_wired for w in wordings}
    assert len(set(verdicts.values())) == 1, f"rewording moved the verdict: {verdicts}"


def test_the_verdict_tracks_the_fact_that_did_change() -> None:
    """The control: with the wording fixed, the structural fact decides. Without this,
    the test above would pass for a verdict that is constant because it ignores
    everything."""
    reason = "harness wiring not extended to this tag"
    assert _verdict(reason, steps_executed=True).harness_wired is True
    assert _verdict(reason, steps_executed=False).harness_wired is False
