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

from pathlib import Path

# The 20 genuine-gap e2e_rest nodeids remaining (47 after Wave 3 triage; jdy1
# graduated M3 6 get_products tenant-duplicate, M1 6 uc004 REST-422 wire-shape,
# M4 4 uc004 webhook-observability entries [now tag-declared in conftest]; the
# uc004 attribution campaign-interval boundary graduated at the main merge after
# upstream re-pointed its expected cell at error "VALIDATION_ERROR"; 12 uc006
# account billing-state entries graduated at the #1417 merge — its account
# resolution wiring makes them pass, xpass confirmed innet_040726_0013; 3 uc002
# creative extension entries imported at the #1417 merge — newly wired there,
# confirmed still failing in-network post-merge, innet_040726_0013; the uc004
# roas/cpa entry retired at #1430 item 4 — its Then steps now exist and the
# scenario is tag-declared T-UC-004-aggregated-roas-and-cpa on ALL transports).
# Grouped by gap in the ledger file's section comments; flat here for exact-set
# comparison.
EXPECTED_LEDGER: frozenset[str] = frozenset(
    {
        "tests/bdd/test_uc002_create_media_buy.py::test_creative_ids_not_found_in_library[e2e_rest]",
        "tests/bdd/test_uc002_create_media_buy.py::test_creative_format_does_not_match_product_supported_formats[e2e_rest]",
        "tests/bdd/test_uc002_create_media_buy.py::test_creative_upload_to_ad_server_fails[e2e_rest]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_delivery_date_range_boundary__boundary_point[e2e_rest-start_date after end_date-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_delivery_date_range_boundary__boundary_point[e2e_rest-start_date equals end_date-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_delivery_date_range_partition__partition[e2e_rest-start_after_end-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_delivery_date_range_partition__partition[e2e_rest-start_equals_end-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_include_package_daily_breakdown_boundary__boundary_point[e2e_rest-string 'true' (non-boolean type)-\"true\"-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_principal_ownership_boundary__boundary_point[e2e_rest-principal differs from owner-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_principal_ownership_partition__partition[e2e_rest-owner_mismatch-invalid]",
        'tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_reporting_dimensions_boundary__boundary_point[e2e_rest-geo with geo_level=metro but no system (behavioral gap)-{"geo": {"geo_level": "metro"}}-invalid]',
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_sampling_method_boundary__boundary_point[e2e_rest-Unknown string not in enum-systematic-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_seller_ignores_attribution_request__returns_platform_default[e2e_rest]",
        "tests/bdd/test_uc011_manage_accounts.py::test_delete_missing_false_preserves_absent_accounts_delete_missing__false_with_absent_accounts[e2e_rest]",
        "tests/bdd/test_uc011_manage_accounts.py::test_delete_missing_omitted__default_preserves_accounts_delete_missing_omitted[e2e_rest]",
        "tests/bdd/test_uc011_manage_accounts.py::test_delete_missing_scoped_to_authenticated_agent_only[e2e_rest]",
        "tests/bdd/test_uc011_manage_accounts.py::test_dry_run_false__normal_sync_applies_changes_dry_run__false[e2e_rest]",
        "tests/bdd/test_uc011_manage_accounts.py::test_dry_run_omitted__default_behavior_applies_changes_dry_run_omitted[e2e_rest]",
        "tests/bdd/test_uc011_manage_accounts.py::test_push_notification_for_async_status_changes__with_push_notification[e2e_rest]",
        "tests/bdd/test_uc011_manage_accounts.py::test_sandbox_account_provisioned_via_sync_accounts_with_sandbox_flag[e2e_rest]",
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
