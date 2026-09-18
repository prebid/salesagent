"""Fitness function for the storyboard-conformance ledger.

The mechanism graded here is DE-COLLECTION: a ledger entry that resolves to no collected
check must fail the session. Nothing else catches it. The ledger xfails NON-STRICTLY by
nodeid, and ``test_storyboard_conformance._collect_checks`` enumerates only failures and
skips, so a check that GRADUATES stops being parametrized at all — it produces no test
item, no xpass and no signal, and the entry becomes dead weight. An entry renamed
upstream goes the same way. ``tests/bdd/e2e_rest_known_failures.txt`` has its own
fitness function for the same reason (``tests/unit/test_e2e_rest_ledger_fitness.py::
test_every_ledger_entry_resolves_to_a_collected_item``).

Every case below drives a SYNTHETIC ledger, injected through
``STORYBOARD_LEDGER_PATH``, because ``tests/storyboard/known_failures.txt`` is empty:
the storyboard grades all of its checks, so no entry is routed to xfail. A mechanism
whose only subject is an empty file grades nothing, and this one has to keep working for
whenever an entry is seeded again.

**Why the e2e_rest test cannot be ported verbatim.** That one re-collects the
suite in a subprocess (``pytest tests/bdd --collect-only``) and joins the ledger
against the collected nodeids. The storyboard suite's parametrization is not
static: ``pytest_generate_tests`` SHELLS OUT TO A LIVE AGENT, and when the pinned
compliance/schema bundle cannot be RESOLVED it short-circuits to a single gate id
— ``bundle-not-present`` when the pin resolved and the bundle did not,
``environment-not-configured`` when even the pin could not be read.
(Resolvability, not env-var set-ness: the two
STORYBOARD_* vars are overrides, and the paths are derived when they are absent.) A verbatim port would therefore
declare every entry stale on every developer machine. The join must be computed
**in-session**, from the ids
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
# (the collection-gate module, deleted -- see below) drove
# the same sessions, and two copies of it would be two things to keep true.
_SYNTHETIC_REASON = "synthetic failure injected by the ledger-fitness grader"

# Bundle materialization's last resort is an HTTPS download of the pinned release
# asset, so every nested session below stubs it: a real fetch is slow, flaky,
# behaves differently offline, and writes the bundle into the repo tree as a side
# effect. The configured sessions never read this reason (their paths resolve);
# the unconfigured one has it folded into the failure it grades.
_MATERIALIZE_FAILURE_MESSAGE = "synthetic fetch failure injected by the ledger-fitness grader"

# The id the gate parametrizes when the pin resolves but the bundle does not.
# Spelled out rather than imported: this grader asserts what a reader of a red
# session sees, so borrowing production's own constant would let a wrong value
# agree with itself.
_BUNDLE_ABSENT_ID = "bundle-not-present"

# The in-session ledger join's own id. Its ABSENCE is what the unconfigured case
# grades — the join firing there would report every ledger entry as stale.
_LEDGER_FITNESS_ID = "ledger::fitness::ledger_fitness::stale_entries"


# The ledger every case below drives. SYNTHETIC, and deliberately not the production
# ledger at tests/storyboard/known_failures.txt: that file is empty, and a mechanism
# whose only subject is an empty file grades nothing. tests/storyboard/conftest.py
# redirects its loader to STORYBOARD_LEDGER_PATH so these entries become the session's
# ledger.
#
# Both protocols are present because the join keys on protocol as the first id segment,
# and one storyboard carries a hyphenated step id because parse() must store the RAW
# segment -- a normalizing parse() would break round-tripping for exactly that shape.
_SYNTHETIC_LEDGER: tuple[ledger.LedgerCheckId, ...] = (
    ledger.LedgerCheckId("mcp", "core", "read_tool_idempotency", "assert_omitted_key_grace_handled"),
    ledger.LedgerCheckId("mcp", "security_transport", "signed_requests", "negative-001-no-signature-header"),
    ledger.LedgerCheckId("a2a", "core", "capability_discovery", "get_capabilities"),
    ledger.LedgerCheckId("a2a", "error_handling", "error_compliance", "nonexistent_product"),
)

_NODEID = "tests/storyboard/test_storyboard_conformance.py::test_storyboard_check[{}]"


def _ledger_entries() -> list[ledger.LedgerCheckId]:
    """The synthetic ledger, re-parsed from the lines it will be written as.

    Round-tripping through the file grammar is what makes these entries the same kind of
    object production loads. A literal list that never passes through ``parse()`` could
    describe a shape the parser rejects, and every case here would then grade a ledger
    the real loader would have read as empty.
    """
    lines = [_NODEID.format(e.format()) for e in _SYNTHETIC_LEDGER]
    entries = [ledger.LedgerCheckId.parse(line) for line in lines]
    assert all(e is not None for e in entries), f"synthetic ledger does not parse: {lines}"
    assert [e.format() for e in entries] == [e.format() for e in _SYNTHETIC_LEDGER]
    assert {e.protocol for e in entries} == {"mcp", "a2a"}
    return entries  # type: ignore[return-value]


def _write_ledger(tmp_path: Path, entries: list[ledger.LedgerCheckId]) -> dict[str, str]:
    """Write ``entries`` as a ledger file and return the env that points the loader at it."""
    path = tmp_path / "synthetic_known_failures.txt"
    path.write_text("\n".join(_NODEID.format(e.format()) for e in entries) + "\n")
    return {"STORYBOARD_LEDGER_PATH": str(path)}


def test_production_ledger_matches_its_pin() -> None:
    """The production ledger still agrees with ``EXPECTED_LEDGER``.

    The cases below no longer read that file, so nothing else in this module would
    notice it drifting from its pin.
    """
    assert load_ledger_nodeids(LEDGER) == ledger_state.EXPECTED_LEDGER


def _run_storyboard_session(
    tmp_path: Path,
    reported: list[ledger.LedgerCheckId] | None,
    *,
    ledgered: list[ledger.LedgerCheckId],
) -> subprocess.CompletedProcess[str]:
    """Run ``pytest`` on the conformance module for real; ``reported=None`` leaves it unconfigured.

    ``ledgered`` is what the session's ledger CONTAINS; ``reported`` is what the stubbed
    runner GRADED. The join under test is the difference between them, so both are
    supplied per case rather than one of them being ambient.

    Unconfigured means the pinned bundle does not RESOLVE, so the two overrides point
    at a path that does not exist. Removing them instead would no longer work: the
    conformance gate derives the paths when they are unset, and the derivation finds
    the in-repo bundle that CI extracts -- the nested session would flip to configured
    and shell out toward an agent that is not there.

    Both branches stub bundle MATERIALIZATION, whose last resort is an HTTPS fetch of
    the pinned release asset. Unstubbed, the unconfigured case would reach the network
    to decide its outcome, and the configured cases would reach it for nothing.
    """
    fetch_name, fetch_env = rig.stub_unmaterializable_bundle(tmp_path, _MATERIALIZE_FAILURE_MESSAGE)
    ledger_env = _write_ledger(tmp_path, ledgered)
    if reported is None:
        absent = tmp_path / "no-bundle-here"
        return rig.run_conformance_session(
            tmp_path,
            env={
                "STORYBOARD_COMPLIANCE_DIR": str(absent / "compliance"),
                "STORYBOARD_SCHEMA_ROOT": str(absent / "schemas"),
                **fetch_env,
                **ledger_env,
            },
            plugins=(fetch_name,),
        )

    stub_name, stub_env = rig.stub_runner(tmp_path, rig.synthetic_summaries(reported, _SYNTHETIC_REASON))
    return rig.run_conformance_session(
        tmp_path,
        env={
            **stub_env,
            **fetch_env,
            **ledger_env,
            # These two are the overrides that make the bundle resolve, so the
            # session counts as configured; the stub means the paths themselves are
            # never dereferenced. tmp_path exists, which is all the gate checks.
            "STORYBOARD_COMPLIANCE_DIR": str(tmp_path),
            "STORYBOARD_SCHEMA_ROOT": str(tmp_path),
        },
        plugins=(stub_name, fetch_name),
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
    proc = _run_storyboard_session(tmp_path, [e for e in entries if e is not graduated], ledgered=entries)

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
    proc = _run_storyboard_session(tmp_path, entries, ledgered=entries)

    output = proc.stdout + proc.stderr
    counts = _outcome_counts(proc)
    assert counts.get("failed") is None, f"healthy ledger reported failures {counts}:\n{output[-4000:]}"
    assert counts.get("xfailed") == len(entries)
    assert proc.returncode == 0, output[-4000:]


def test_unconfigured_session_does_not_report_stale_entries(tmp_path: Path) -> None:
    """Off the in-network job the join has no ids to join against — it must stay silent.

    With no resolvable pinned bundle, ``pytest_generate_tests`` emits a single
    ``bundle-not-present`` check and never collects a conformance check at all.
    That absence is itself graded as a failure — a session that grades zero
    checks and exits 0 is indistinguishable from a clean run — so this session is
    EXPECTED to be red, and the one failure it reports is that one.

    What must not happen is the join firing against that lone id. Treating it as
    "every ledger entry but this one graduated" would bury the real reason under one
    phantom stale entry per ledgered check; it is the failure mode that makes a verbatim port
    of the e2e_rest fitness test unusable, and it would misdiagnose every offline
    run of this suite.
    """
    proc = _run_storyboard_session(tmp_path, None, ledgered=_ledger_entries())

    output = proc.stdout + proc.stderr
    counts = _outcome_counts(proc)
    assert counts == {"failed": 1}, (
        f"expected only the absent-bundle failure, got {counts} — anything more means the "
        f"ledger join ran against a session that collected no checks:\n{output[-4000:]}"
    )
    assert _BUNDLE_ABSENT_ID in output, (
        f"the one failure must be the absent bundle, not something else:\n{output[-4000:]}"
    )
    assert _LEDGER_FITNESS_ID not in output, (
        f"the ledger join reported stale entries for a session that had no ids to join against:\n{output[-4000:]}"
    )
    assert proc.returncode != 0, output[-4000:]
