"""Real-run proof for tests/bdd/scenario_liveness.py.

The pure-logic tests live in ``tests/unit/test_architecture_bdd_scenario_liveness.py``.
This file is the grounding the parent finding demands: the liveness artifact must be
emitted from an ACTUAL BDD run, and a scenario with genuinely unbound steps must be
recorded as such — not asserted against hand-constructed dataclasses.

Shells out to two narrow, fast, real ``pytest tests/bdd`` slices (selected by the
``@storyboard-v3.1`` marker so the count of scenarios discovered matches what
``scripts/audit/storyboard_coverage_map.covered_storyboards`` claims as covered):

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

import importlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from scripts.audit import storyboard_spec

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

REPO_ROOT = Path(__file__).resolve().parents[2]
FEATURES_DIR = REPO_ROOT / "tests" / "bdd" / "features"

# The two in-process slices this module shells out to.
UC006_FILE = "tests/bdd/test_uc006_sync_creatives.py"
UC006_MARKER = "uc006-storyboard-routing"
UC005_FILE = "tests/bdd/test_uc005_discover_creative_formats.py"

# Every in-process transport the bdd suite parametrizes over with
# ``BDD_E2E_ENABLED`` popped (see _run_bdd_slice's docstring).
IN_PROCESS_TRANSPORTS = {"mcp", "a2a", "rest"}


def _slice_scenarios(feature: str, marker: str) -> list[storyboard_spec.TaggedScenario]:
    """The scenarios a ``-m <marker>`` slice of ``feature`` runs, from the feature's own tags.

    Read through ``storyboard_spec.tagged_scenarios`` — the single owner of the
    tag-line/identity-tag grammar (``_IDENT_TAG_RE``) — so this grader never
    grows a second copy of the regex, and so the expected set is regenerated
    from the tree on every run instead of frozen into this file.
    """
    return [s for s in storyboard_spec.tagged_scenarios(FEATURES_DIR, tag=f"@{marker}") if s.feature == feature]


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
        record = scenarios[scenario_id]
        # Every graded scenario has real steps bound — the dormant/unbound axis is
        # graded separately, on the one member that genuinely is dormant.
        assert record["steps_bound"] is True, f"{scenario_id} unexpectedly reports steps_bound=False"
        assert record["unbound_steps"] == [], f"{scenario_id} unexpectedly reports unbound step text"
        # Real transports actually ran (not silently zero, not silently one).
        assert {o["transport"] for o in record["observations"]} == IN_PROCESS_TRANSPORTS
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


def _run_bdd_slice(tmp_path: Path, test_file: str, marker_expr: str, *, extra_args: Sequence[str] = ()) -> dict:
    """Shell out to a real, narrow pytest slice with a deliberately isolated env.

    This test's assertions pin an exact 3-way in-process transport set
    (mcp/a2a/rest). The nested subprocess must not silently inherit
    ``BDD_E2E_ENABLED`` from whatever ambient environment this test itself
    runs under (e.g. run_all_tests.sh, which sets it once the Docker e2e
    stack is up for the outer suite) — that would make the nested slice
    additionally parametrize over e2e_rest, and this test's fixed-3-transport
    assertions would fail for reasons unrelated to what they're testing.
    """
    artifact = tmp_path / "liveness.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop("BDD_E2E_ENABLED", None)
    env["BDD_LIVENESS_ARTIFACT"] = str(artifact)
    env.setdefault("ADCP_TESTING", "true")
    # Measure EVERY transport. The BDD conftest's single-transport optimization
    # deselects mcp/rest for any scenario carrying a strict xfail, which is
    # exactly what a ledgered scenario carries — so without this the artifact
    # would record one observation for every ledgered scenario and the
    # "not silently zero, not silently one" assertion below would be measuring
    # the optimization rather than the scenario. A later split moved these gaps from
    # inline pytest.xfail() (invisible to that optimization) to ledger tags,
    # which is what surfaced it.
    env["BDD_ALL_TRANSPORTS"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            test_file,
            "-m",
            marker_expr,
            "-p",
            "no:cacheprovider",
            "-q",
            *extra_args,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert artifact.is_file(), (
        f"pytest subprocess did not write the liveness artifact to {artifact}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(artifact.read_text(encoding="utf-8"))


# Measured 65.17s on an idle 40-core box against CI's --timeout=60 (ci.yml
# "Integration (other)"). Same class as test_liveness_artifact_is_identical_
# under_xdist_and_serial below: a real nested-pytest-subprocess cost, not a
# hang. Scoped rather than raising the job's CLI timeout, same reasoning.
@pytest.mark.timeout(300)
def test_real_run_records_uc006_storyboard_scenarios_as_ledgered_or_live(tmp_path: Path) -> None:
    """The UC-006 storyboard-routing slice is measured in full, and its
    ``@storyboard-v3.1``-tagged members have real, bound step definitions
    — none are dormant/steps-unbound — and each is recorded on the side of the
    ledgered/live partition the routing map puts it on. All of them are live
    today: the status gap closed when production began deriving the per-creative
    ``status`` from the row's review state, and the four provenance gaps closed
    when it began refusing a policy-violating creative with the pin's
    ``PROVENANCE_*`` codes. Proves the artifact tracks real state, not a frozen
    count."""
    data = _run_bdd_slice(tmp_path, UC006_FILE, UC006_MARKER)
    scenarios = {s["scenario_id"]: s for s in data["scenarios"]}

    # DERIVED, not frozen: every scenario the slice runs carries a `@T-*` identity
    # tag, and identity — not provenance — is what the artifact keys membership on.
    # The two `@schema-v3.1`-retagged members (provenance-claim-contradicted,
    # creative-reception-stateful-render) are part of this slice and must be
    # measured; a provenance retag must not be able to delete a scenario from the
    # measurement.
    slice_scenarios = _slice_scenarios("BR-UC-006-sync-creatives.feature", UC006_MARKER)
    assert set(scenarios) == _identity_tags(slice_scenarios)

    # The scenarios whose detailed ledgered/live behaviour this test pins — again
    # derived from the feature's own tags, not listed here.
    storyboard_tagged = {s.identifier for s in slice_scenarios if storyboard_spec.TAG in s.tags}
    # Anti-vacuity: the slice really does have @storyboard-v3.1 members to grade,
    # so a provenance retag of all of them fails here instead of passing over an
    # empty loop.
    assert storyboard_tagged, f"{UC006_MARKER} slice has no {storyboard_spec.TAG} member left to grade"
    _assert_ledgered_or_live(scenarios, storyboard_tagged)


def test_real_run_records_uc005_scenarios_as_ledgered_or_live(tmp_path: Path) -> None:
    """A scenario that is NOT dormant reports steps_bound=True/harness_wired=True, and the
    artifact puts each member on the right side of the ledgered/live partition — the
    guard proves both directions, not only the failure case.

    This is where the ledgered-vs-live distinction is graded for scenarios whose
    steps are all bound: the two format-id-roundtrip scenarios pass for real on
    every in-process transport, and baseline-format-id-object-shape is ledgered
    against upstream adcp#7338. Both halves are asserted non-empty below, so the
    day one of them empties out this test says so instead of going quiet.
    """
    data = _run_bdd_slice(tmp_path, UC005_FILE, "storyboard-v3.1")
    scenarios = {s["scenario_id"]: s for s in data["scenarios"]}

    graded = _identity_tags(_slice_scenarios("BR-UC-005-discover-creative-formats.feature", "storyboard-v3.1"))
    assert set(scenarios) == graded
    ledgered = graded & set(_ledgered_reasons())
    assert ledgered, (
        "the UC-005 storyboard slice no longer carries a ledgered member — "
        "the ledgered half of this partition would be graded by nothing"
    )
    assert graded - ledgered, "the UC-005 storyboard slice no longer carries a live member"

    _assert_ledgered_or_live(scenarios, graded)
    for scenario_id, record in scenarios.items():
        # Not dormant, and it RAN: the positive case the negative-only guard misses.
        assert record["harness_wired"] is True, f"{scenario_id} executed no step body"


# Measured 62.52s on an idle 40-core box against CI's --timeout=60. Same class
# as the two siblings above/below: a real nested-pytest-subprocess cost.
@pytest.mark.timeout(300)
def test_provenance_tag_is_a_recorded_field_not_a_collection_filter(tmp_path: Path) -> None:
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
    data = _run_bdd_slice(tmp_path, UC006_FILE, UC006_MARKER)
    scenarios = {s["scenario_id"]: s for s in data["scenarios"]}

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
        record = scenarios[scenario.identifier]
        # The provenance tag is DATA on the record, not the membership predicate.
        assert set(record["tags"]) == {t.lstrip("@") for t in scenario.tags}
        assert {o["transport"] for o in record["observations"]} == IN_PROCESS_TRANSPORTS
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


# Scoped budget, NOT a global relaxation. Every other test here shells out to ONE
# nested pytest session; this one shells out to TWO (serial, then ``-n 2``) plus
# xdist's worker bootstrap, and that is irreducible — the comparison IS the
# grader. Measured: 37.2s (M-series laptop) / 49.1s (x86 Linux CI box) here,
# against 20.7s/19.3s/10.0s and 25.0s/24.0s/14.3s for its single-run siblings. It
# is the only test in this module that crosses the 60s budget CI's integration job
# passes (``.github/workflows/ci.yml`` ``--timeout=60``) once a GitHub-hosted
# runner's slower cores are applied — exactly what fork run 32152198573 measured
# ("Failed: Timeout (>60.0s)") while the siblings passed.
#
# The alternative the design considered — shrinking the measured slice — was
# rejected: the slice's 8 scenarios x 3 in-process transports = 24 items under
# ``--dist load`` are what force a genuine shard-and-merge, and set equality of
# scenario ids PLUS per-scenario observation counts is the whole assertion.
# Trading that for speed would buy a green mark with a weaker grader.
#
# 300s is ~8x the measured cost, leaves the job's 25-minute budget untouched, and
# the marker overrides the CLI ``--timeout`` for this item only (pytest-timeout
# precedence: marker > CLI > ini), so no other test's budget moves.
@pytest.mark.timeout(300)
def test_liveness_artifact_is_identical_under_xdist_and_serial(tmp_path: Path) -> None:
    """A sharded run must publish the same measurement a serial run does.

    ``tox.ini:138`` runs the bdd env under ``-n {env:BDD_XDIST_N:auto}``, but
    ``scenario_liveness._RECORDS`` is a per-PROCESS module global: every worker
    accumulates its own records and the controller — which has none — writes the
    artifact last. The published file is therefore EMPTY in exactly the
    configuration CI uses.

    ``--dist load`` is PINNED here deliberately. ``tox.ini`` pairs ``-n`` with
    ``--dist loadfile``, and mirroring that "for fidelity" would put this
    single-file slice entirely on ONE worker — a last-writer-wins non-fix would
    then pass this grader vacuously. Under ``--dist load`` the slice's 8
    scenarios x 3 in-process transports = 24 items genuinely split across the
    two workers, so only a real shard-and-merge satisfies the comparison.

    The comparison itself is SET EQUALITY of scenario ids plus per-scenario
    observation counts, never non-emptiness: an artifact carrying one worker's
    shard is non-empty and still wrong.
    """
    serial = _run_bdd_slice(tmp_path / "serial", UC006_FILE, UC006_MARKER, extra_args=("-p", "no:randomly"))
    sharded = _run_bdd_slice(
        tmp_path / "sharded", UC006_FILE, UC006_MARKER, extra_args=("-p", "no:randomly", "-n", "2", "--dist", "load")
    )

    serial_records = {s["scenario_id"]: s for s in serial["scenarios"]}
    sharded_records = {s["scenario_id"]: s for s in sharded["scenarios"]}

    assert set(sharded_records) == set(serial_records), (
        f"-n 2 --dist load published {len(sharded_records)} scenario(s); serial published "
        f"{len(serial_records)}. The sharded run's measurements never reached the process "
        "that wrote the artifact."
    )

    # Anti-vacuity: the serial baseline must itself be the full slice, otherwise
    # "sharded == serial" could be satisfied by two equally-broken runs.
    assert set(serial_records) == _identity_tags(_slice_scenarios("BR-UC-006-sync-creatives.feature", UC006_MARKER))

    assert {sid: len(r["observations"]) for sid, r in sharded_records.items()} == {
        sid: len(r["observations"]) for sid, r in serial_records.items()
    }

    for scenario_id, serial_record in serial_records.items():
        sharded_record = sharded_records[scenario_id]
        assert {o["transport"] for o in sharded_record["observations"]} == {
            o["transport"] for o in serial_record["observations"]
        }
        # The merge must not lose or invert the joined verdicts either.
        assert sharded_record["steps_bound"] == serial_record["steps_bound"]
        assert sharded_record["unbound_steps"] == serial_record["unbound_steps"]
        assert sharded_record["ledgered"] == serial_record["ledgered"]
        assert sharded_record["harness_wired"] == serial_record["harness_wired"]
