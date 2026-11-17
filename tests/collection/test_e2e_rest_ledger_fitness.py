"""Fitness function for the e2e_rest known-failures ledger.

PR #1420 review finding: unlike the duplication baseline and
the structural-guard allowlists, the e2e_rest ledger
(``tests/bdd/e2e_rest_known_failures.txt``) had no ratchet and no stale-entry
test, so it could silently grow or accumulate dead nodeids after a feature/param
rename. Two invariants, enforced in two places:

1. **No silent growth or shrinkage** — enforced by the exact-set lock in
   ``test_e2e_rest_ledger_state.py`` (``EXPECTED_LEDGER``): any added, removed,
   or re-added entry fails there and must be justified in the same change. A
   separate count ceiling derived from that same pin could never fail
   independently, so this module no longer carries one (#1430 review: the old
   ``count <= len(EXPECTED_LEDGER)`` ratchet was tautological). PR #1417's
   branch carried the same shrink-only invariant as a monotonic
   ``_LEDGER_CEILING`` ratchet (last at 305 against its pre-#1430-retirement
   ledger); at the merge that ceiling is subsumed by the exact-set pin, and
   its final graduations (the two uc004 date-range partition rows) are
   reflected in ``EXPECTED_LEDGER``.
2. **No stale entries** — every ledger nodeid must resolve to a currently
   collected test item. A param/feature rename that orphans a nodeid is caught
   here rather than silently masking a never-run scenario.
"""

from pathlib import Path

from tests._collection_manifest import BDD_TREE, load, manifest_dir
from tests.helpers.ledger import load_ledger_nodeids

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = _REPO_ROOT / "tests" / "bdd" / "e2e_rest_known_failures.txt"


def test_every_ledger_entry_resolves_to_a_collected_item():
    entries = load_ledger_nodeids(_LEDGER)

    # The ledger's nodeids only EXIST in a collection with the e2e_rest
    # transport on, so demand that stamp rather than infer it. Asking for
    # e2e_enabled=True makes a host run -- where docker-compose does not set
    # BDD_E2E_ENABLED, so the record has no e2e_rest rows at all -- raise with
    # a cause, instead of quietly reporting every ledger entry as stale.
    collected_rows = load(manifest_dir(), target=BDD_TREE, e2e_enabled=True)

    collected = {row["nodeid"] for row in collected_rows}
    stale = sorted(e for e in entries if e not in collected)
    assert not stale, (
        f"{len(stale)} stale e2e_rest ledger nodeid(s) resolve to no collected test "
        "(feature/param rename?). Remove them from the ledger:\n  " + "\n  ".join(stale[:20])
    )
