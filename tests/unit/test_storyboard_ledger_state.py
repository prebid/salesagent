"""Lock test for the storyboard-conformance known-failures ledger (the storyboard-conformance job).

Mirrors ``tests/unit/test_e2e_rest_ledger_state.py`` verbatim in shape: the storyboard
CI job (the storyboard-conformance job) grades a MEASURED run of the real ``@adcp/sdk`` storyboard runner through
pytest as ordinary parametrized tests -- one per ``(protocol, track, storyboard_id,
step_id)``, the runner being executed once per protocol (mcp, a2a) against the same
agent -- reusing the exact ledger/xfail/lock-test discipline already established by
``tests/bdd/e2e_rest_known_failures.txt`` rather than inventing a second comparator
system (Core Invariant). That means a sibling ledger file
(``tests/storyboard/known_failures.txt``), a conftest loader
(``tests/storyboard/conftest.py``) that reads it to xfail(strict=False) exactly those
known-failing storyboard test ids, and this lock test pinning the ledger's exact
contents so it cannot silently drift -- the same triad as the e2e_rest precedent.

Per the Core Invariant, an entry must be seeded from a MEASURED in-network CI run,
never re-derived/inferred (the architect review's HIGH finding: the runner's host-side
numbers do not carry over to the in-network receiver topology).

The ledger holds no entries, so the storyboard grades every check it collects.
``EXPECTED_LEDGER`` is empty to match, and that emptiness is the pin: adding an entry to
one file without the other fails this module.

RE-SEEDING is a standing rule, not a one-off: whenever a run seeds or retires
entries, update the ledger file AND ``EXPECTED_LEDGER`` (in
``tests/helpers/storyboard_ledger_pin.py``, which also carries the seed
provenance) in the same change. Same discipline as the e2e_rest docstring — a
removed entry that creeps back is a graduation regression; a genuine-gap entry
deleted without landing the underlying fix is a silent gap-hiding regression.

The pin itself lives in ``tests/helpers/`` rather than here because the
storyboard fitness function
(``tests/integration/test_storyboard_ledger_fitness_real_session.py``) grades
against the same pin, and a module whose job is to BE a test must not double as
a helper library — see ``test_architecture_no_cross_test_module_imports.py``.
"""

from __future__ import annotations

from scripts.audit import ledger
from tests.helpers.ledger import load_ledger_nodeids
from tests.helpers.storyboard_ledger_pin import EXPECTED_LEDGER, LEDGER_PATH


def _load_ledger_nodeids() -> frozenset[str]:
    """Parse the ledger the way the storyboard conftest loader must.

    Same format as ``tests/bdd/e2e_rest_known_failures.txt``: one test-id-equivalent
    identifier per line, ``#``-prefixed comment lines and blank lines dropped.
    """
    return load_ledger_nodeids(LEDGER_PATH)


def test_ledger_matches_expected_genuine_gaps() -> None:
    """The storyboard ledger file contains exactly the pinned genuine-gap entries."""
    actual = _load_ledger_nodeids()
    crept_back = actual - EXPECTED_LEDGER
    disappeared = EXPECTED_LEDGER - actual
    assert actual == EXPECTED_LEDGER, (
        "storyboard-conformance ledger drifted from its pinned state.\n"
        f"Entries that crept back in (un-graduate them or update EXPECTED_LEDGER): {sorted(crept_back)}\n"
        f"Entries removed without updating this test: {sorted(disappeared)}"
    )


def test_ledger_entries_are_storyboard_conformance_test_ids() -> None:
    """Every ledger entry identifies a tests/storyboard parametrized check.

    Mirrors the e2e_rest ledger's nodeid-shape guard (test_ledger_entries_are_e2e_rest_bdd_nodeids):
    entries key on (protocol, track, storyboard_id, step_id) per the Core Invariant, carried as a
    pytest parametrize id on the storyboard-conformance test module -- not a free-text
    reason (reason/reason_kind are non-key annotations reported on failure, per plan
    step 2, never part of the ledger identity). Parsed through the shared grammar
    (scripts.audit.ledger.LedgerCheckId) rather than a hand-rolled partition split --
    a malformed entry now fails loudly (parse() returns None) instead of silently
    mis-parsing a prefix.
    """
    for entry in _load_ledger_nodeids():
        assert entry.startswith("tests/storyboard/"), f"non-storyboard ledger entry: {entry}"
        assert "::" in entry, f"ledger entry is not a test id: {entry}"
        parsed = ledger.LedgerCheckId.parse(entry)
        assert parsed is not None, f"ledger entry does not match the check-id grammar: {entry}"
        assert parsed.protocol in {"mcp", "a2a"}, f"ledger entry has no known protocol prefix: {entry}"


def test_conftest_loader_reads_this_ledger() -> None:
    """The storyboard-conformance conftest loads the same ledger this test pins.

    Asserts the loader's PATH, not only the set it produced. Two empty sets compare
    equal however the loader was wired, so a path assertion is the only form of this
    check that still bites while the ledger holds no entries -- and pointing the loader
    elsewhere is the silent breakage the ledger/lock-test triad exists to prevent.
    """
    from tests.storyboard import conftest

    assert conftest._LEDGER_PATH.resolve() == LEDGER_PATH.resolve()
    assert LEDGER_PATH.is_file(), f"the pinned ledger file is missing: {LEDGER_PATH}"
    assert conftest._STORYBOARD_KNOWN_FAILURES == EXPECTED_LEDGER
