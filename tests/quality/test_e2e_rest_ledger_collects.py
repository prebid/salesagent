"""Every e2e_rest ledger entry names a node-id pytest actually collects.

A BDD outcome cell's node-id is spelled in THREE independent places — the feature's
``Examples`` cell, ``tests/bdd/e2e_rest_known_failures.txt``, and ``EXPECTED_LEDGER``
in ``tests/unit/test_e2e_rest_ledger_state.py`` — and nothing tied the first to the
other two. A lane renamed a cell from ``invalid`` to
``error "INVALID_REQUEST" with suggestion``, updated the feature and the ledger, and
the branch head went red in ``test_e2e_rest_ledger_state`` — a file the change had no
reason to open.

The existing unit tests check each entry's SHAPE and never that the id EXISTS:
``test_ledger_entries_are_e2e_rest_bdd_nodeids`` asserts ``startswith("tests/bdd/")``,
``"::" in nodeid`` and ``"e2e_rest" in nodeid``. A rename that leaves a shape-valid
orphan satisfies all three. Tonight it surfaced only because the same lane also
edited the ledger, which made the exact-set comparison disagree; without that
coincidence the orphan would have sat there, xfailing nothing.

NOT A DERIVATION, DELIBERATELY
------------------------------
``EXPECTED_LEDGER`` is not computed from the ledger file and must not become so. The
exact-set comparison is what catches an entry **added or removed** without a
deliberate edit — a scenario quietly un-graduated by re-adding it — and collection
structurally **cannot** see that, because a newly added entry that collects fine
passes this test. The two checks grade different failures and compose:

    feature Examples cell --(this test)--> ledger file --(EXPECTED_LEDGER)--> the test

Neither link derives from what it grades. Deriving either one would trade a loud
check for a different one rather than strengthening anything — and an oracle computed
from its own subject is the failure mode that makes a guard green while it measures
nothing.

WHY THIS LIVES IN THE quality ENV
---------------------------------
It collects the whole BDD suite in a subprocess: 11503 items, ~14.5s wall (1.1s of
collection, the rest interpreter start and conftest import). The unit suite runs under
xdist ``-n 16``, where a nested pytest from inside a worker is a second interpreter
importing the whole bdd conftest, up to sixteen at once. The loud half of the pairing
(``EXPECTED_LEDGER``) already runs in the unit suite and fires immediately on an
added or removed line; this catches the subtle half, for which per-full-run is enough.

``BDD_E2E_ENABLED`` MUST BE THE LITERAL STRING ``"true"``
---------------------------------------------------------
``tests/bdd/conftest.py``'s ``_parametrize_ctx`` gates on
``os.environ.get("BDD_E2E_ENABLED") == "true"``, so **without it the e2e_rest ids are
not parametrized at all** and a collection returns zero of them — this test would
then compare the ledger against a set that cannot contain it. Measured while building
this: ``BDD_E2E_ENABLED=1`` yields **0** e2e_rest ids and ``=true`` yields 2853. A
truthy-looking value that is not the literal is the failure this guard is one typo
away from, which is why ``test_the_probe_can_see_e2e_rest_ids_at_all`` below asserts
the collection is non-empty before anything is compared against it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers.ledger import load_ledger_nodeids

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: The ledger FILE is what this grades, read through the same loader
#: ``tests/bdd/conftest.py`` uses — not ``EXPECTED_LEDGER``. The file is the artifact
#: whose entries must name real scenarios; ``EXPECTED_LEDGER`` is a second copy of it,
#: and the unit suite already asserts the two are equal. Reading the file keeps the
#: chain's two links independent and keeps a quality-tier module from importing a
#: unit-tier test.
_LEDGER_PATH = _REPO_ROOT / "tests" / "bdd" / "e2e_rest_known_failures.txt"

#: The gate's exact value. Named once so the test and its docstring cannot disagree.
_E2E_GATE = {"BDD_E2E_ENABLED": "true"}


def _collect_bdd_nodeids(tmp_path: Path) -> frozenset[str]:
    """Every node-id ``pytest --collect-only`` builds for ``tests/bdd``, e2e_rest included.

    Read from the plugin's dump rather than from pytest's stdout: ``--collect-only -q``
    prints no node-id list on pytest 9.0.3 here, so a stdout parser would read an empty
    set and pass vacuously.
    """
    dump = tmp_path / "nodeids.txt"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/bdd",
            "--collect-only",
            "-q",
            "-p",
            "no:randomly",
            "-p",
            "tests.utils.collect_nodeids_plugin",
        ],
        cwd=_REPO_ROOT,
        env={**os.environ, **_E2E_GATE, "NODEID_DUMP": str(dump)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert dump.exists(), (
        "the collection subprocess wrote no node-id dump, so there is nothing to grade "
        f"(exit {result.returncode}).\nstdout tail:\n{result.stdout[-2000:]}\n"
        f"stderr tail:\n{result.stderr[-2000:]}"
    )
    return frozenset(dump.read_text(encoding="utf-8").splitlines())


@pytest.fixture(scope="module")
def collected_bdd_nodeids(tmp_path_factory: pytest.TempPathFactory) -> frozenset[str]:
    """One collection for the module — it costs ~14.5s and both tests read it."""
    return _collect_bdd_nodeids(tmp_path_factory.mktemp("collect"))


@pytest.mark.slow
def test_the_probe_can_see_e2e_rest_ids_at_all(collected_bdd_nodeids: frozenset[str]) -> None:
    """The instrument before the measurement: a collection with no e2e_rest ids grades nothing.

    Without ``BDD_E2E_ENABLED="true"`` the transport is not parametrized and every
    assertion below holds trivially over an empty intersection. This is the check that
    makes the next test's pass mean something.
    """
    e2e_rest = {nodeid for nodeid in collected_bdd_nodeids if "[e2e_rest" in nodeid or "-e2e_rest" in nodeid}
    assert e2e_rest, (
        "collected no e2e_rest node-ids, so this module cannot grade the ledger. The gate "
        f"is the literal string: {_E2E_GATE!r} — a truthy value like '1' silently yields zero. "
        f"(collected {len(collected_bdd_nodeids)} node-ids in total)"
    )


@pytest.mark.slow
def test_every_ledger_entry_collects(collected_bdd_nodeids: frozenset[str]) -> None:
    """Every e2e_rest ledger entry names a scenario that still exists.

    An orphan means a feature's ``Examples`` cell was renamed without the ledger
    following: the entry xfails nothing, and the scenario it was meant to cover now runs
    ungated in-network.
    """
    orphans = load_ledger_nodeids(_LEDGER_PATH) - collected_bdd_nodeids
    assert not orphans, (
        "e2e_rest ledger entries name node-ids that pytest does not collect. An outcome "
        "cell was almost certainly renamed without updating the ledger — the entry now "
        "xfails nothing.\n"
        "Fix the ledger AND EXPECTED_LEDGER to the new spelling, or delete the entry if "
        "the scenario is gone:\n  " + "\n  ".join(sorted(orphans))
    )
