"""Lock test for the integration known-failures ledger.

``tests/integration/known_failures.txt`` is a SHRINKING work-list: 48 integration tests
xfailed pending rewrite under epic salesagent-e30o7, one child issue per cluster. This
test pins that state so the ledger cannot drift silently:

* an entry added without a decision fails here;
* an entry removed without landing the rewrite fails here;
* the conftest loader must still read the same file the suite xfails against.

When a cluster is rewritten, remove its nodeids from BOTH the ledger file and
``EXPECTED_LEDGER`` below in the same change, and close the child issue.

**The ledger only shrinks.** ``test_the_ledger_never_grows`` states that as a bound on the
count, not just as a convention in a comment: a new failing integration test is a defect to
fix, not a line to add here.

This is the sibling of ``tests/unit/test_e2e_rest_ledger_state.py``, which does the same
job for the e2e_rest BDD ledger.
"""

from __future__ import annotations

from pathlib import Path

from tests.helpers.ledger import load_ledger_nodeids

LEDGER = Path(__file__).parent.parent / "integration" / "known_failures.txt"

#: The count the ledger was created with. A bound, never a target — see
#: ``test_the_ledger_never_grows``.
#:
#: 53, where a full box run measured 48. Both additions were mine to see and did not:
#: cluster I is GRADUATED: it passed on the box because it had ``ADCP_AUTH_TEST_MODE`` on and failed
#: wherever it is off, so a baseline measured in one environment does not transfer; and
#: cluster J (3) I excluded by judgement as another PR's to fix, which conflated fixing
#: with recording and left CI red for a reason no reader could find. A ledger created from
#: a single run is a floor on its contents, and the number only falls from here.
#:
#: 51 after the first two came off cluster B — deleted, not repaired, because
#: test-results/bdd_scenario_liveness.json records @T-UC-002-ext-b and @T-UC-002-ext-o as
#: bound, wired, unledgered and PASSING on all three in-process transports, grading the
#: same two conditions and more of each. That is the shape the ledger's header asks for:
#: a scenario replaces such a test, it does not port it.
CEILING = 51

EXPECTED_LEDGER: frozenset[str] = frozenset(
    {
        # Cluster A (salesagent-e30o7.1) — half-seeded tenant trips validate_setup_complete
        "tests/integration/test_create_media_buy_behavioral.py::TestExtensionObligations::test_currency_not_supported_by_gam",
        "tests/integration/test_create_media_buy_behavioral.py::TestPostconditionObligations::test_error_response_contains_recovery_guidance",
        "tests/integration/test_create_media_buy_behavioral.py::TestProposalBasedObligations::test_proposal_based_product_validation",
        # Cluster B (salesagent-e30o7.2) — production refuses correctly, the test expects otherwise
        "tests/integration/test_create_media_buy_behavioral.py::TestExtensionObligations::test_product_with_no_pricing_options",
        "tests/integration/test_create_media_buy_behavioral.py::TestExtensionObligations::test_proposal_budget_amount_zero_rejected",
        "tests/integration/test_create_media_buy_behavioral.py::TestMaxDailySpendExceeded::test_max_daily_spend_exceeded",
        "tests/integration/test_create_media_buy_behavioral.py::TestMaxDailySpendExceeded::test_max_daily_spend_same_day_flight_uses_min_one_day",
        "tests/integration/test_create_media_buy_behavioral.py::TestPostconditionObligations::test_system_state_unchanged_on_failure",
        "tests/integration/test_idempotency_race.py::TestRaceLoserPayloadRules::test_different_payload_under_a_stored_key_conflicts",
        # Cluster C (salesagent-e30o7.3) — the test holds a stale API
        "tests/integration/test_account_mcp_context_bypass.py::TestBDDTransportBypass::test_list_accounts_context_through_dispatch",
        "tests/integration/test_account_mcp_context_bypass.py::TestMCPContextThroughRealPipeline::test_mcp_pipeline_forwards_context_to_the_response",
        "tests/integration/test_creative_formats_validation_b.py::TestMultiFieldValidationErrors::test_multi_field_errors_formatted_with_per_field_messages",
        "tests/integration/test_creative_formats_validation_b.py::TestNonIntegerDimensionValues::test_formatted_error_identifies_dimension_field",
        "tests/integration/test_creative_sync_transport.py::TestGeminiKeyMissing::test_generative_no_gemini_key_fails[a2a]",
        "tests/integration/test_creative_sync_transport.py::TestGeminiKeyMissing::test_generative_no_gemini_key_fails[mcp]",
        "tests/integration/test_creative_sync_transport.py::TestGeminiKeyMissing::test_generative_no_gemini_key_fails[rest]",
        "tests/integration/test_tenant_utils.py::test_account_approval_mode_round_trips_through_tenant_context",
        # Cluster D (salesagent-e30o7.4) — the test injects an identity through the wire
        "tests/integration/test_creative_formats_discovery.py::TestAuthOptionalForDiscovery::test_a2a_returns_formats_with_no_auth_token",
        "tests/integration/test_creative_formats_discovery.py::TestAuthOptionalForDiscovery::test_a2a_with_no_auth_token_via_call_via",
        "tests/integration/test_creative_formats_protocol.py::TestTenantContextFromA2AHeaders::test_a2a_different_tenants_get_different_catalogs",
        "tests/integration/test_creative_formats_protocol.py::TestTenantContextFromA2AHeaders::test_a2a_uses_tenant_from_identity",
        # Cluster E (salesagent-e30o7.5) — fixture collisions hit real DB constraints
        "tests/integration/test_idempotency_race.py::TestDegradedFallbackScopeRules::test_degraded_path_conflicts_on_mutated_payload",
        "tests/integration/test_idempotency_race.py::TestDegradedFallbackScopeRules::test_post_ttl_retry_rejects_idempotency_expired",
        "tests/integration/test_idempotency_race.py::TestRaceSeamThroughEntrypoint::test_retry_after_lost_cache_row_fails_closed_via_backstop",
        "tests/integration/test_media_buy_v3.py::TestCreateMediaBuyPrincipalResolution::test_principal_not_found_returns_error",
        # Cluster F (salesagent-e30o7.6) — the test asserts on a mock of our own code
        "tests/integration/test_create_media_buy_behavioral.py::TestCreativeUploadFailure::test_creative_upload_failure_raises_tool_error",
        "tests/integration/test_create_media_buy_behavioral.py::TestInlineCreativeObligations::test_inline_creatives_uploaded_and_assigned",
        "tests/integration/test_create_media_buy_behavioral.py::TestMainFlowObligations::test_auto_approval_determination",
        # Cluster G (salesagent-e30o7.7) — vendor-client refusal for a dry-run adapter
        "tests/integration/test_vendor_egress.py::test_a_dry_run_adapter_holds_no_vendor_client[kevel]",
        "tests/integration/test_vendor_egress.py::test_a_dry_run_adapter_holds_no_vendor_client[triton]",
        "tests/integration/test_vendor_egress.py::test_require_vendor_refuses_an_unconfigured_client[Kevel]",
        "tests/integration/test_vendor_egress.py::test_require_vendor_refuses_an_unconfigured_client[Triton Digital]",
        "tests/integration/test_vendor_egress.py::test_require_vendor_refuses_an_unconfigured_client[Xandr]",
        "tests/integration/test_webhook_refusal_reaches_both_seats.py::TestSeatTwoTheStashPathRefusesWithoutAnOutcome::test_a_stored_legacy_row_stops_delivering_and_says_so[sub-32 credential-schemes1-sssssssssssssssssssssssssssssss-Bearer]",
        # Cluster H (salesagent-e30o7.8) — wire and status expectations that drifted
        "tests/integration/test_admin_media_buy_reject_webhook.py::TestAdminMediaBuyRejectWebhook::test_approve_webhook_echoes_buyer_request_context",
        "tests/integration/test_bdd_dispatch_seam.py::TestCallViaForwardsReqWholeAndTheEnvUnpacksIt::test_a_format_ids_filter_still_reaches_the_tool[mcp]",
        "tests/integration/test_bdd_dispatch_seam.py::TestCallViaForwardsReqWholeAndTheEnvUnpacksIt::test_a_format_ids_filter_still_reaches_the_tool[rest]",
        "tests/integration/test_create_media_buy_pending_approval_wire.py::test_config_approval_create_emits_submitted_not_confirmed",
        "tests/integration/test_create_media_buy_pending_approval_wire.py::test_submitted_create_replays_verbatim_without_second_workflow_step",
        "tests/integration/test_delivery_poll_behavioral.py::TestPrincipalNotFoundReturnsError::test_principal_not_found_returns_error_in_response",
        "tests/integration/test_delivery_poll_behavioral.py::TestSimulationReachesFinalThroughRealHook::test_mock_time_in_flight_reports_active_and_scheduled",
        "tests/integration/test_delivery_poll_behavioral.py::TestSimulationReachesFinalThroughRealHook::test_mock_time_past_flight_reaches_completed_and_final",
        "tests/integration/test_list_accounts_auth_missing_wire.py::TestListAccountsNoTokenEmitsAuthMissing::test_no_token_rest_wire_emits_auth_missing",
        "tests/integration/test_mcp_client_util.py::TestExhaustedFailureReachesTheRegistryClassified::test_seam_failure_surfaces_as_the_mapped_envelope[connection-level]",
        "tests/integration/test_mcp_client_util.py::TestExhaustedFailureReachesTheRegistryClassified::test_seam_failure_surfaces_as_the_mapped_envelope[tool-level]",
        # Cluster I (salesagent-091d8) — production composition depends on a test flag
        # Cluster J (GH #2189) — these re-run a BDD slice and grade their own subrun
        "tests/integration/test_bdd_scenario_liveness_real_run.py::test_real_run_records_uc006_storyboard_scenarios_as_ledgered_or_live",
        "tests/integration/test_bdd_scenario_liveness_real_run.py::test_real_run_records_uc005_format_id_roundtrip_scenarios_as_live",
        "tests/integration/test_bdd_scenario_liveness_real_run.py::test_provenance_tag_is_a_recorded_field_not_a_collection_filter",
    }
)


def test_the_ledger_matches_this_pin() -> None:
    """The file and this test agree, entry for entry."""
    actual = load_ledger_nodeids(LEDGER)
    added = sorted(actual - EXPECTED_LEDGER)
    removed = sorted(EXPECTED_LEDGER - actual)
    assert not added, (
        "these nodeids were added to the ledger without a decision here — a new failing "
        "integration test is a defect to fix, not a line to add:\n  " + "\n  ".join(added)
    )
    assert not removed, (
        "these nodeids left the ledger; if the rewrite landed, delete them from "
        "EXPECTED_LEDGER in the SAME change and close the child issue:\n  " + "\n  ".join(removed)
    )


def test_the_ledger_never_grows() -> None:
    """A bound on the count, so shrink-only is checked and not merely documented."""
    actual = load_ledger_nodeids(LEDGER)
    assert len(actual) <= CEILING, (
        f"the ledger holds {len(actual)} entries, above its ceiling of {CEILING}. It only "
        "shrinks: fix the new failure instead of ledgering it. Lowering CEILING as entries "
        "graduate is the intended direction."
    )


def test_every_entry_names_a_collectable_file() -> None:
    """Each entry points at a file that exists, so a rename cannot leave a dead entry.

    The nodeid's TEST half is checked by the conftest hook matching it at collection; what
    cannot be checked there is an entry whose file is gone, because an unselected test and
    a deleted one look identical to a collection hook.
    """
    repo_root = Path(__file__).parent.parent.parent
    missing = sorted(
        {e.split("::", 1)[0] for e in load_ledger_nodeids(LEDGER)}
        - {str(p.relative_to(repo_root)) for p in (repo_root / "tests" / "integration").glob("test_*.py")}
    )
    assert not missing, "ledger entries name files that no longer exist:\n  " + "\n  ".join(missing)
