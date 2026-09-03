"""Fitness function for the storyboard-conformance ledger.

``tests/bdd/e2e_rest_known_failures.txt`` has one
(``tests/unit/test_e2e_rest_ledger_fitness.py::
test_every_ledger_entry_resolves_to_a_collected_item``);
``tests/storyboard/known_failures.txt`` — whose exact contents are pinned by
``EXPECTED_LEDGER`` — has none. The consequence is written into the ledger's own header, which
claims "a graduation (entry no longer failing) or a regression (new un-ledgered
failure) both fail CI". Only the second half is true today: the ledger xfails
NON-STRICTLY by nodeid, and ``test_storyboard_conformance._collect_checks``
enumerates only failures and skips, so a check that GRADUATES stops being
parametrized at all. It produces no test item, no xpass, and no signal — the
ledger entry simply becomes dead weight, and the same happens to an entry whose
check is renamed upstream.

**Why the e2e_rest test cannot be ported verbatim.** That one re-collects the
suite in a subprocess (``pytest tests/bdd --collect-only``) and joins the ledger
against the collected nodeids. The storyboard suite's parametrization is not
static: ``pytest_generate_tests`` SHELLS OUT TO A LIVE AGENT, and when the pinned
compliance/schema bundle cannot be RESOLVED it short-circuits to a single
``environment-not-configured`` id. (Resolvability, not env-var set-ness: the two
STORYBOARD_* vars are overrides, and the paths are derived when they are absent.) A verbatim port would therefore either
declare every entry stale on every developer machine, or skip and grade
nothing. The join must be computed **in-session**, from the ids
``pytest_generate_tests`` itself produced — which means it only BITES in the
in-network job, where the runner really grades the agent. Everywhere else the
session is unconfigured and the fitness check must stay silent (graded by
``test_unconfigured_session_does_not_report_stale_entries`` below).

**RED direction.** The lane plan's original phrasing — "revert one ledger
entry's underlying fix and the test must fail" — is inverted: reverting a fix
makes that entry FAIL again, i.e. RESOLVE against the ledger, so the fitness
test passes. The failing case is the opposite one: a ledger entry whose check no
longer collects (graduated, or renamed) must FAIL.

This module drives real ``pytest tests/storyboard/test_storyboard_conformance.py``
sessions in a subprocess with the runner subprocess stubbed by an injected ``-p``
plugin, so the parametrized ids are produced by production's own
``pytest_generate_tests``/``LedgerCheckId`` code path against a summary this test
controls. No live agent, no npm deps.

**Why the nested session names the module and not the directory.** The three
cases below grade EXACT outcome counts ("exactly 1 failed item"), so anything
else the nested session happens to collect is counted as if it were the join's
verdict. ``tests/storyboard/`` also holds ``test_runner_sdk_pin.py``, which
asserts on the runner's INSTALLED ``@adcp/sdk`` and deliberately ``pytest.fail``s
when ``npm ci`` has not been run in ``tests/storyboard/runner/`` — a real,
correct failure everywhere except the one job that installs those deps. Pointing
the nested session at the whole directory therefore added exactly one failing
item to all three cases on every machine without the npm install (CI run
32152198573's "Integration (other)": ``{'failed': 2, 'xfailed': 74}``,
``{'failed': 1, 'xfailed': N}``, ``{'failed': 1, 'skipped': 1}`` — each one more
than expected), and passed only where a developer had happened to install them.
The subject under measurement here is the conformance module's parametrization
and ledger join, so that is what the session collects; the sdk pin guard is
unweakened and still graded by the storyboard-conformance job that owns its
precondition.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.audit import ledger
from tests.helpers import storyboard_session as rig
from tests.helpers.ledger import load_ledger_nodeids
from tests.unit import test_storyboard_ledger_state as ledger_state

pytestmark = [pytest.mark.integration]

REPO_ROOT = rig.REPO_ROOT
LEDGER = REPO_ROOT / "tests" / "storyboard" / "known_failures.txt"

# The nested-session rig (which module is collected, the runner stub, the
# outcome parser) lives in ``tests/helpers/storyboard_session.py``: the
# collection-gate grader
# (``tests/integration/test_storyboard_collection_gate_real_session.py``) drives
# the same sessions, and two copies of it would be two things to keep true.
_SYNTHETIC_REASON = "synthetic failure injected by the ledger-fitness grader"


def _ledger_entries() -> list[ledger.LedgerCheckId]:
    entries = ledger.load(LEDGER)
    # The premise of every case below: a ledger that has been emptied, or whose
    # grammar drifted, would make all three vacuous.
    #
    # Pinned against EXPECTED_LEDGER — the ONE place the ledger's exact contents
    # are fixed — rather than against a literal count. A bare integer here has
    # now rotted on every re-seed (44 -> 75 -> 101 -> 81) and each time it went
    # stale in a DIFFERENT file from the one being re-seeded, so the re-seeder
    # never saw it. Comparing sets, not lengths, also makes the failure name the
    # entries that moved instead of just the arithmetic.
    # Both sides as NODEIDs: LedgerCheckId.format() emits the bracket CONTENT
    # (`mcp::core::…`), while EXPECTED_LEDGER holds full pytest nodeids.
    expected = ledger_state.EXPECTED_LEDGER
    actual = load_ledger_nodeids(LEDGER)
    if actual != expected:
        only_ledger = sorted(actual - expected)
        only_expected = sorted(expected - actual)
        raise AssertionError(
            "the storyboard ledger disagrees with EXPECTED_LEDGER in "
            "tests/unit/test_storyboard_ledger_state.py — re-seed both together. "
            f"ledger={len(actual)} EXPECTED_LEDGER={len(expected)}\n"
            f"  only in the ledger ({len(only_ledger)}): {only_ledger[:5]}\n"
            f"  only in EXPECTED_LEDGER ({len(only_expected)}): {only_expected[:5]}"
        )
    assert {e.protocol for e in entries} == {"mcp", "a2a"}
    return entries


def _run_storyboard_session(
    tmp_path: Path, entries: list[ledger.LedgerCheckId] | None
) -> subprocess.CompletedProcess[str]:
    """Run ``pytest`` on the conformance module for real; ``entries=None`` leaves it unconfigured.

    Unconfigured means the pinned bundle does not RESOLVE, so the two overrides point
    at a path that does not exist. Removing them instead would no longer work: the
    conformance gate derives the paths when they are unset, and the derivation finds
    the in-repo bundle that CI extracts -- the nested session would flip to configured
    and shell out toward an agent that is not there.
    """
    if entries is None:
        absent = tmp_path / "no-bundle-here"
        return rig.run_conformance_session(
            tmp_path,
            env={
                "STORYBOARD_COMPLIANCE_DIR": str(absent / "compliance"),
                "STORYBOARD_SCHEMA_ROOT": str(absent / "schemas"),
            },
        )

    stub_name, stub_env = rig.stub_runner(tmp_path, rig.synthetic_summaries(entries, _SYNTHETIC_REASON))
    return rig.run_conformance_session(
        tmp_path,
        env={
            **stub_env,
            # These two are the overrides that make the bundle resolve, so the
            # session counts as configured; the stub means the paths themselves are
            # never dereferenced. tmp_path exists, which is all the gate checks.
            "STORYBOARD_COMPLIANCE_DIR": str(tmp_path),
            "STORYBOARD_SCHEMA_ROOT": str(tmp_path),
        },
        plugins=(stub_name,),
    )


def _outcome_counts(proc: subprocess.CompletedProcess[str]) -> dict[str, int]:
    return rig.outcome_counts(proc)


def test_a_ledger_entry_whose_check_no_longer_collects_fails_the_session(tmp_path: Path) -> None:
    """THE RED CASE: a graduated/renamed check leaves a ledger entry resolving to nothing.

    One entry is withheld from the runner's output — exactly what a graduation
    looks like from the ledger's side — and the session must fail, naming it.
    Non-strict xfail cannot catch this: with no parametrized id there is no item
    to xpass.
    """
    entries = _ledger_entries()
    graduated = entries[0]
    proc = _run_storyboard_session(tmp_path, [e for e in entries if e is not graduated])

    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"session passed despite a stale ledger entry:\n{output[-4000:]}"
    assert graduated.format() in output, (
        f"session failed but never named the stale entry {graduated.format()!r}:\n{output[-4000:]}"
    )
    counts = _outcome_counts(proc)
    # Exactly one failing ITEM — the fitness check. A collection error or a
    # blanket failure would not be the graded, ledgerable signal this needs.
    assert counts.get("failed") == 1, f"expected exactly 1 failed item, got {counts}:\n{output[-4000:]}"
    assert counts.get("xfailed") == len(entries) - 1


def test_a_fully_resolving_ledger_passes_the_session(tmp_path: Path) -> None:
    """The other direction: when every entry resolves, the fitness check must not fire.

    A guard that only ever proves the negative case is not a guard — and one
    that fires on a healthy ledger would make the in-network job permanently red.
    """
    entries = _ledger_entries()
    proc = _run_storyboard_session(tmp_path, entries)

    output = proc.stdout + proc.stderr
    counts = _outcome_counts(proc)
    assert counts.get("failed") is None, f"healthy ledger reported failures {counts}:\n{output[-4000:]}"
    assert counts.get("xfailed") == len(entries)
    assert proc.returncode == 0, output[-4000:]


def test_unconfigured_session_does_not_report_stale_entries(tmp_path: Path) -> None:
    """Off the in-network job the join has no ids to join against — it must stay silent.

    With no resolvable pinned bundle, ``pytest_generate_tests`` emits the single
    ``environment-not-configured`` id. Treating that as "every entry but one is stale" is the failure mode that
    makes a verbatim port of the e2e_rest fitness test unusable, and it would
    red every offline run of this suite.
    """
    proc = _run_storyboard_session(tmp_path, None)

    output = proc.stdout + proc.stderr
    counts = _outcome_counts(proc)
    assert counts.get("failed") is None, f"unconfigured session reported failures {counts}:\n{output[-4000:]}"
    assert counts.get("skipped") == 1
    assert proc.returncode == 0, output[-4000:]
