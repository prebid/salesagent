"""Lock test for the e2e_rest known-failures ledger (#1418, Wave 3).

The ledger (``tests/bdd/e2e_rest_known_failures.txt``) is a shrinking work-list of
e2e_rest BDD scenarios that fail over real HTTP. Wave 3 graduated every scenario
that now passes in-network and moved every format-injection-only scenario to an
env-level ``E2EUnsupportedSetup`` declaration (surfaced as xfail by the conftest
report hook, NOT listed in the ledger). What remains are genuine production /
harness gaps, enumerated below.

This test pins that end state so the ledger cannot silently drift:

* a removed entry that creeps back (a graduation regression) fails here;
* a genuine-gap entry deleted without landing the underlying fix fails here;
* the conftest loader must still read the same file the BDD suite xfails against.

When a gap is genuinely fixed (its scenario now passes in-network) or moved to an
env declaration, remove it from BOTH the ledger file and ``EXPECTED_LEDGER`` below
in the same change.
"""

from __future__ import annotations

import re
from pathlib import Path

# The 12 e2e_rest nodeids remaining, ALL of them production code gaps (kept in sync
# with the "CURRENT SET" header in tests/bdd/e2e_rest_known_failures.txt — see
# test_ledger_header_count_matches_actual below):
#   6  uc004 invalid-input rows the live server still accepts
#   1  push-notification ack (inline-xfail in the step body)
#   2  daily-breakdown rows — GH #1776
# None is a harness artifact and none masks a regression this suite introduced. Where an
# entry was ADDED rather than graduated, it is because repairing a vacuous test UNMASKED a
# pre-existing production failure; the GH issue named on that entry owns closing it.
# Graduated on the way here: the 10 parallel-e2e_rest mock-injection artifacts (5887ac7d9,
# GH #1739), the attribution-default scenario (retired, GH #1726),
# the 2 date-range boundary rows (2026-07-09, first
# in-network CI run), the 2 date-range partition twins (origin/pr-1417 merge,
# d4af23095 — strict-xfail XPASS in-network), and the 2 uc004 account valid rows
# (#1417 merge, jr5b seeded-account Given, XPASS innet_140726_1516).
# (47 after Wave 3 triage; jdy1
# graduated M3 6 get_products tenant-duplicate, M1 6 uc004 REST-422 wire-shape,
# M4 4 uc004 webhook-observability entries [now tag-declared in conftest]; the
# uc004 attribution campaign-interval boundary graduated at the main merge after
# upstream re-pointed its expected cell at error "VALIDATION_ERROR"; 12 uc006
# account billing-state entries graduated at the #1417 merge — its account
# resolution wiring makes them pass, xpass confirmed innet_040726_0013; 3 uc002
# creative extension entries imported at the #1417 merge — newly wired there,
# confirmed still failing in-network post-merge, innet_040726_0013; the uc004
# roas/cpa entry retired at #1430 item 4 — its Then steps now exist and the
# scenario is tag-declared T-UC-004-aggregated-roas-and-cpa on ALL transports;
# #1430 items 1-3 graduated the 6 uc011 read-back entries [_db_scope_for repoint
# + agent auth_token fix] and 2 uc002 ext-o/ext-p entries [auto-approval seeding],
# all 8 xpassed in-network, innet_050726_2030; the uc002 ext-q upload entry
# graduated after the fail_on_upload mock-fidelity + catalog-format +
# run_async_in_sync_context format-resolution fixes, verified in-network).
# Grouped by gap in the ledger file's section comments; flat here for exact-set
# comparison.
EXPECTED_LEDGER: frozenset[str] = frozenset(
    {
        # All four date-range invalid rows graduated: boundary rows 2026-07-09
        # (#1270 tripwires fired on the first in-network CI run — live server
        # validates start>=end now), partition twins at the origin/pr-1417 merge
        # (d4af23095, strict-xfail XPASS in-network).
        # Account valid rows graduated at the #1417 merge (jr5b seeded-account
        # Given; XPASS in-network innet_140726_1516) — see ledger note.
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_include_package_daily_breakdown_boundary__boundary_point[e2e_rest-string 'true' (non-boolean type)-\"true\"-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_principal_ownership_boundary__boundary_point[e2e_rest-principal differs from owner-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_principal_ownership_partition__partition[e2e_rest-owner_mismatch-invalid]",
        # GRADUATED (salesagent-ulft): both geo_metro_missing_system rows (boundary +
        # partition) removed. Item C10's premise was wrong — the pinned spec's
        # reporting_dimensions.geo.required is ["geo_level"] only; system is genuinely
        # optional ("Omit to request the level without selecting a specific system").
        # Not a "no validator" gap: there was never anything to validate. Reconciled to
        # expect "valid" in the local .feature file (both nodeids changed accordingly
        # and no longer collect under their old -invalid/-error names).
        # "Unknown string not in enum" boundary row GRADUATED (salesagent-oyiv.15):
        # removing DeliveryPollEnv._BODY_FIELDS let e2e_rest reach the real endpoint,
        # which rejects it like every other transport — XPASS(strict) confirmed
        # in-network (innet_050826_0756). Replaced by the 6 "valid" named-method rows
        # below (same root cause, opposite direction: those newly FAIL).
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_sampling_method_partition__partition[e2e_rest-random-random-valid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_sampling_method_partition__partition[e2e_rest-stratified-stratified-valid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_sampling_method_partition__partition[e2e_rest-recent-recent-valid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_sampling_method_partition__partition[e2e_rest-failures_only-failures_only-valid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_sampling_method_boundary__boundary_point[e2e_rest-random (first enum value)-random-valid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_sampling_method_boundary__boundary_point[e2e_rest-failures_only (last enum value)-failures_only-valid]",
        # RETIRED (GH #1726, 2026-07-28): T-UC-004-attr-unsupported was reconciled away.
        # AdCP 3.1.1 says sellers that do NOT support configurable attribution windows ignore the
        # field; it does not require any seller to be non-supporting, and this seller always
        # honours the requested window, so INV-2 never applied to it. The nodeid no longer exists,
        # so this is a scenario retirement, not a graduation.
        "tests/bdd/test_uc011_manage_accounts.py::test_push_notification_for_async_status_changes__with_push_notification[e2e_rest]",
        # Added 2026-07-28 — THE GAP IS GH #1776: include_package_daily_breakdown=true is
        # accepted and never honoured (media_buy_delivery.py:549 hardcodes daily_breakdown=None).
        # NOT newly broken — these two rows were passing vacuously against a guard that could
        # never be entered (the oracle read pkg.daily / pkg.by_day, neither of which exists on
        # ByPackageItem). Repairing it made the pre-existing gap observable; that repair's defect
        # CLASS is GH #1751, which is not this gap, and GH #1319 item C5 is only the
        # strict-marker debt bucket these rows sit in. Retire with the in-process twins in
        # conftest _UC004_GENUINE_XFAIL_ROWS when #1776 is fixed.
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_include_package_daily_breakdown_partition__partition[e2e_rest-explicit_true-true-valid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_include_package_daily_breakdown_boundary__boundary_point[e2e_rest-true (explicit)-true-valid]",
        # (The 2026-07-09 E2E_PER_WORKER mock-injection block that used to sit here is
        # gone with its entries: all 10 graduated in 5887ac7d9, GH #1739. It described
        # those rows, NOT the daily-breakdown pair above — do not reintroduce it here,
        # where it would read as if the #1776 gap were a parallel-execution artifact.)
    }
)

_LEDGER_PATH = Path(__file__).parent.parent / "bdd" / "e2e_rest_known_failures.txt"


def _load_ledger_nodeids() -> frozenset[str]:
    """Parse the ledger the way the conftest loader does (drop comments/blanks)."""
    return frozenset(
        line.strip()
        for line in _LEDGER_PATH.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def test_ledger_matches_expected_genuine_gaps() -> None:
    """The ledger file contains exactly the pinned genuine-gap nodeids."""
    actual = _load_ledger_nodeids()
    crept_back = actual - EXPECTED_LEDGER
    disappeared = EXPECTED_LEDGER - actual
    assert actual == EXPECTED_LEDGER, (
        "e2e_rest ledger drifted from its pinned Wave-3 end state.\n"
        f"Entries that crept back in (un-graduate them or update EXPECTED_LEDGER): {sorted(crept_back)}\n"
        f"Entries removed without updating this test: {sorted(disappeared)}"
    )


def test_ledger_entries_are_e2e_rest_bdd_nodeids() -> None:
    """Every ledger entry is a tests/bdd e2e_rest scenario nodeid."""
    for nodeid in _load_ledger_nodeids():
        assert nodeid.startswith("tests/bdd/"), f"non-bdd ledger entry: {nodeid}"
        assert "::" in nodeid, f"ledger entry is not a nodeid: {nodeid}"
        assert "e2e_rest" in nodeid, f"ledger entry is not an e2e_rest variant: {nodeid}"


def test_conftest_loader_reads_this_ledger() -> None:
    """The BDD conftest loads the same ledger this test pins.

    Guards against the loader being deleted or pointed elsewhere while the file
    still exists — that would silently stop xfailing these known failures.
    """
    from tests.bdd.conftest import _E2E_REST_KNOWN_FAILURES

    assert _E2E_REST_KNOWN_FAILURES == EXPECTED_LEDGER


_HEADER_COUNT_RE = re.compile(r"# CURRENT SET: (\d+) entries =")


def test_ledger_header_count_matches_actual() -> None:
    """The ledger's own 'CURRENT SET: N entries' header must match the real count.

    GH #1782: the header is unchecked prose that can (and did) drift from the
    actual entry count without failing anything — salesagent-ulft found it stale
    by 1 (stated 8, actual 9) and this ticket's own changes moved the count again.
    This closes #1782 by making the header a checked claim, not free text.
    """
    header_text = _LEDGER_PATH.read_text()
    match = _HEADER_COUNT_RE.search(header_text)
    assert match, "e2e_rest_known_failures.txt header no longer has a '# CURRENT SET: N entries =' line"
    stated = int(match.group(1))
    actual = len(EXPECTED_LEDGER)
    assert stated == actual, (
        f"Ledger header claims {stated} entries but EXPECTED_LEDGER (and the file) has {actual}. "
        "Update the '# CURRENT SET: N entries =' line and its breakdown in "
        "tests/bdd/e2e_rest_known_failures.txt in the same change."
    )
