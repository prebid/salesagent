"""Fitness function for the storyboard-conformance ledger.

``tests/bdd/e2e_rest_known_failures.txt`` has one
(``tests/collection/test_e2e_rest_ledger_fitness.py::
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
static: ``pytest_generate_tests`` SHELLS OUT TO A LIVE AGENT, and without
``STORYBOARD_COMPLIANCE_DIR``/``STORYBOARD_SCHEMA_ROOT`` it short-circuits to a
single ``environment-not-configured`` id. A verbatim port would therefore either
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

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.audit import ledger
from tests.helpers.ledger import load_ledger_nodeids
from tests.unit import test_storyboard_ledger_state as ledger_state

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER = REPO_ROOT / "tests" / "storyboard" / "known_failures.txt"

# The one module under measurement (see this module's docstring). Named
# explicitly rather than by directory so the graded outcome counts stay a
# function of the ledger join alone, not of which sibling guards the ambient
# environment happens to satisfy. ``tests/storyboard/conftest.py`` — the ledger
# xfail router this module is grading — still loads, it is the directory's
# conftest.
CONFORMANCE_MODULE = "tests/storyboard/test_storyboard_conformance.py"

# Injected via ``-p`` into the nested session. Replaces the one function that
# needs a live agent + npm deps; every other production code path
# (``_collect_checks``, ``pytest_generate_tests``, ``LedgerCheckId.format``,
# ``conftest.pytest_collection_modifyitems``) runs for real.
_STUB_PLUGIN = """
import json
import os
from pathlib import Path


def pytest_configure(config):
    from tests.storyboard import test_storyboard_conformance as mod

    summaries = json.loads(Path(os.environ["STUB_STORYBOARD_SUMMARIES"]).read_text(encoding="utf-8"))
    mod._run_storyboard_runner = lambda protocol: summaries[protocol]
"""


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


def _summaries(entries: list[ledger.LedgerCheckId]) -> dict[str, dict[str, Any]]:
    """One synthetic runner summary per protocol, failing exactly ``entries``."""
    summaries: dict[str, dict[str, Any]] = {}
    for protocol in ("mcp", "a2a"):
        failures = [
            {
                "track": e.track,
                "storyboard_id": e.storyboard_id,
                "step_id": e.step_id,
                "reason": "synthetic failure injected by the ledger-fitness grader",
                "reason_kind": "synthetic",
            }
            for e in entries
            if e.protocol == protocol
        ]
        summaries[protocol] = {
            "agent_url": f"stub://{protocol}",
            "overall_status": "fail",
            # Non-zero graded total, so `_no_graded_checks` does not inject its
            # own synthetic reachability check and change the id set.
            "passed": 1,
            "failed": len(failures),
            "skipped": 0,
            "failures": failures,
            "skip_causes": [],
        }
    return summaries


def _run_storyboard_session(
    tmp_path: Path, entries: list[ledger.LedgerCheckId] | None
) -> subprocess.CompletedProcess[str]:
    """Run ``pytest`` on the conformance module for real; ``entries=None`` leaves the env unconfigured."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), str(REPO_ROOT), env.get("PYTHONPATH", "")])
    args = [sys.executable, "-m", "pytest", CONFORMANCE_MODULE, "-q", "-o", "addopts=", "-p", "no:randomly"]

    if entries is not None:
        (tmp_path / "_stub_storyboard_runner.py").write_text(_STUB_PLUGIN, encoding="utf-8")
        summaries_path = tmp_path / "summaries.json"
        summaries_path.write_text(json.dumps(_summaries(entries)), encoding="utf-8")
        env["STUB_STORYBOARD_SUMMARIES"] = str(summaries_path)
        # Only these two decide `_missing_env()`; the stub means their values are
        # never dereferenced.
        env["STORYBOARD_COMPLIANCE_DIR"] = str(tmp_path)
        env["STORYBOARD_SCHEMA_ROOT"] = str(tmp_path)
        args += ["-p", "_stub_storyboard_runner"]
    else:
        env.pop("STORYBOARD_COMPLIANCE_DIR", None)
        env.pop("STORYBOARD_SCHEMA_ROOT", None)

    return subprocess.run(args, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=600)


def _outcome_counts(proc: subprocess.CompletedProcess[str]) -> dict[str, int]:
    """Parse pytest's ``-q`` summary line into ``{outcome: count}``."""
    output = proc.stdout + proc.stderr
    matches = re.findall(r"(\d+) (passed|failed|xfailed|xpassed|skipped|error|errors)", output)
    assert matches, f"could not read a pytest summary line from:\n{output[-4000:]}"
    return {outcome.rstrip("s") if outcome == "errors" else outcome: int(count) for count, outcome in matches}


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

    Without ``STORYBOARD_COMPLIANCE_DIR``/``STORYBOARD_SCHEMA_ROOT``,
    ``pytest_generate_tests`` emits the single ``environment-not-configured``
    id. Treating that as "every entry but one is stale" is the failure mode that
    makes a verbatim port of the e2e_rest fitness test unusable, and it would
    red every offline run of this suite.
    """
    proc = _run_storyboard_session(tmp_path, None)

    output = proc.stdout + proc.stderr
    counts = _outcome_counts(proc)
    assert counts.get("failed") is None, f"unconfigured session reported failures {counts}:\n{output[-4000:]}"
    assert counts.get("skipped") == 1
    assert proc.returncode == 0, output[-4000:]
