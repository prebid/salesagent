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

# The 47 genuine-gap e2e_rest nodeids remaining after Wave 3 triage. Grouped by
# gap in the ledger file's section comments; flat here for exact-set comparison.
EXPECTED_LEDGER: frozenset[str] = frozenset(
    {
        "tests/bdd/test_get_products_inventory_profile.py::test_profile_with_extra_metadata_fields_preserves_them[e2e_rest]",
        "tests/bdd/test_get_products_inventory_profile.py::test_profile_with_invalid_property_ids_falls_back_to_selection_type_all[e2e_rest]",
        "tests/bdd/test_get_products_inventory_profile.py::test_profile_with_only_publisher_domain_infers_selection_type_all[e2e_rest]",
        "tests/bdd/test_get_products_inventory_profile.py::test_profile_with_property_ids_infers_selection_type_by_id[e2e_rest]",
        "tests/bdd/test_get_products_inventory_profile.py::test_profile_with_property_tags_infers_selection_type_by_tag[e2e_rest]",
        "tests/bdd/test_get_products_inventory_profile.py::test_profile_with_selection_type_already_present_passes_through[e2e_rest]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_aggregated_totals_scalar_fields_include_roas_and_cost_per_acquisition[e2e_rest]",
        'tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_attribution_window_boundary__boundary_point[e2e_rest-interval=0 (below minimum)-{"post_click": {"interval": 0, "unit": "days"}}-invalid]',
        'tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_attribution_window_boundary__boundary_point[e2e_rest-model=last_click (not in enum)-{"model": "last_click"}-invalid]',
        'tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_attribution_window_boundary__boundary_point[e2e_rest-unit=campaign with interval=2 (desc says must be 1)-{"post_click": {"interval": 2, "unit": "campaign"}}-invalid]',
        'tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_attribution_window_boundary__boundary_point[e2e_rest-unit=weeks (not in enum)-{"post_click": {"interval": 1, "unit": "weeks"}}-invalid]',
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_delivery_date_range_boundary__boundary_point[e2e_rest-start_date after end_date-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_delivery_date_range_boundary__boundary_point[e2e_rest-start_date equals end_date-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_delivery_date_range_partition__partition[e2e_rest-start_after_end-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_delivery_date_range_partition__partition[e2e_rest-start_equals_end-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_include_package_daily_breakdown_boundary__boundary_point[e2e_rest-string 'true' (non-boolean type)-\"true\"-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_principal_ownership_boundary__boundary_point[e2e_rest-principal differs from owner-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_principal_ownership_partition__partition[e2e_rest-owner_mismatch-invalid]",
        'tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_reporting_dimensions_boundary__boundary_point[e2e_rest-geo with geo_level=metro but no system (behavioral gap)-{"geo": {"geo_level": "metro"}}-invalid]',
        'tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_reporting_dimensions_boundary__boundary_point[e2e_rest-geo without geo_level (required field missing)-{"geo": {"limit": 10}}-invalid]',
        'tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_reporting_dimensions_boundary__boundary_point[e2e_rest-limit negative-{"device_type": {"limit": -1}}-invalid]',
        'tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_reporting_dimensions_boundary__boundary_point[e2e_rest-limit=0 (below minimum)-{"geo": {"geo_level": "country", "limit": 0}}-invalid]',
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_sampling_method_boundary__boundary_point[e2e_rest-Unknown string not in enum-systematic-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_seller_ignores_attribution_request__returns_platform_default[e2e_rest]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_webhook_delivery_does_not_retry_on_4xx_response[e2e_rest]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_webhook_delivery_retries_on_5xx_response[e2e_rest]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_webhook_delivery_retries_on_network_error[e2e_rest]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_webhook_sequence_numbers_are_monotonically_increasing[e2e_rest]",
        'tests/bdd/test_uc006_sync_creatives.py::test_account_resolution__partition[e2e_rest-account_payment_required-{"account_id": "acc_overdue"}-the error should be ACCOUNT_PAYMENT_REQUIRED with suggestion]',
        'tests/bdd/test_uc006_sync_creatives.py::test_account_resolution__partition[e2e_rest-account_setup_required-{"account_id": "acc_new_unconfigured"}-the error should be ACCOUNT_SETUP_REQUIRED with suggestion]',
        'tests/bdd/test_uc006_sync_creatives.py::test_account_resolution__partition[e2e_rest-account_suspended-{"account_id": "acc_suspended"}-the error should be ACCOUNT_SUSPENDED with suggestion]',
        'tests/bdd/test_uc006_sync_creatives.py::test_account_resolution__partition[e2e_rest-explicit_not_found-{"account_id": "acc_nonexistent"}-the error should be ACCOUNT_NOT_FOUND with suggestion]',
        'tests/bdd/test_uc006_sync_creatives.py::test_account_resolution__partition[e2e_rest-natural_key_ambiguous-{"brand": {"domain": "multi.com"}, "operator": "agency.com"}-the error should be ACCOUNT_AMBIGUOUS with suggestion]',
        'tests/bdd/test_uc006_sync_creatives.py::test_account_resolution__partition[e2e_rest-natural_key_not_found-{"brand": {"domain": "unknown.com"}, "operator": "unknown.com"}-the error should be ACCOUNT_NOT_FOUND with suggestion]',
        'tests/bdd/test_uc006_sync_creatives.py::test_account_resolution_boundary__boundary_point[e2e_rest-account resolved + payment due-{"account_id": "acc_overdue"}-the error should be ACCOUNT_PAYMENT_REQUIRED with suggestion]',
        'tests/bdd/test_uc006_sync_creatives.py::test_account_resolution_boundary__boundary_point[e2e_rest-account resolved + setup incomplete-{"account_id": "acc_new_unconfigured"}-the error should be ACCOUNT_SETUP_REQUIRED with suggestion]',
        'tests/bdd/test_uc006_sync_creatives.py::test_account_resolution_boundary__boundary_point[e2e_rest-account resolved + suspended-{"account_id": "acc_suspended"}-the error should be ACCOUNT_SUSPENDED with suggestion]',
        'tests/bdd/test_uc006_sync_creatives.py::test_account_resolution_boundary__boundary_point[e2e_rest-account_id present + not found-{"account_id": "acc_nonexistent"}-the error should be ACCOUNT_NOT_FOUND with suggestion]',
        'tests/bdd/test_uc006_sync_creatives.py::test_account_resolution_boundary__boundary_point[e2e_rest-brand + operator present + multiple matches-{"brand": {"domain": "multi.com"}, "operator": "agency.com"}-the error should be ACCOUNT_AMBIGUOUS with suggestion]',
        'tests/bdd/test_uc006_sync_creatives.py::test_account_resolution_boundary__boundary_point[e2e_rest-brand + operator present + no match-{"brand": {"domain": "unknown.com"}, "operator": "unknown.com"}-the error should be ACCOUNT_NOT_FOUND with suggestion]',
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
