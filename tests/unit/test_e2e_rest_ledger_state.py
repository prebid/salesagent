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

# The 17 e2e_rest nodeids remaining: 7 genuine gaps + 10 parallel-e2e_rest
# mock-injection artifacts (owner-approved, added on the adcp-6.6 /
# perf/parallelize-test-suite work — see the block comment inside the set).
# Graduated on the way here: the 2 date-range boundary rows (2026-07-09, first
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
        'tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_reporting_dimensions_boundary__boundary_point[e2e_rest-geo with geo_level=metro but no system (behavioral gap)-{"geo": {"geo_level": "metro"}}-invalid]',
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_sampling_method_boundary__boundary_point[e2e_rest-Unknown string not in enum-systematic-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_seller_ignores_attribution_request__returns_platform_default[e2e_rest]",
        "tests/bdd/test_uc011_manage_accounts.py::test_push_notification_for_async_status_changes__with_push_notification[e2e_rest]",
        # Added 2026-07-09 on the adcp-6.6 branch (owner-approved) when
        # perf/parallelize-test-suite enabled parallel e2e_rest (E2E_PER_WORKER):
        # mock-injection-incompatible artifacts, not regressions — UC-004
        # set_adapter_response (delivery), UC-005 set_registry_formats, UC-018
        # injected cross-principal creatives are invisible to the separate HTTP
        # server. Preserved through the main merge.
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_breakdown_complete_not_truncated__truncation_flag_set_false[e2e_rest]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_breakdown_truncated_by_limit__truncation_flag_set_true[e2e_rest]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_buyer_requests_supported_dimension__seller_returns_breakdown[e2e_rest]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_multiple_dimensions_requested_simultaneously[e2e_rest]",
        "tests/bdd/test_uc005_discover_creative_formats.py::test_baseline_list_creative_formats_response_carries_format_id_objects_with_agent_url_and_id[e2e_rest]",
        "tests/bdd/test_uc005_discover_creative_formats.py::test_format_id_roundtrip__list_creative_formats_returns_the_same_format_object_that_get_products_advertised[e2e_rest]",
        "tests/bdd/test_uc005_discover_creative_formats.py::test_format_id_with_agent_url_pointing_at_a_thirdparty_creative_agent_is_reported_as_observation_not_failure[e2e_rest]",
        "tests/bdd/test_uc018_list_creatives.py::test_brrule034_inv1_counter__crossprincipal_creatives_never_visible[e2e_rest]",
        "tests/bdd/test_uc018_list_creatives.py::test_brrule034_inv1_holds__query_always_scoped_by_principal[e2e_rest]",
        "tests/bdd/test_uc018_list_creatives.py::test_list_creatives_filtered_by_concept_ids_returns_only_creatives_in_that_concept_carrying_concept_id_and_concept_name[e2e_rest]",
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
