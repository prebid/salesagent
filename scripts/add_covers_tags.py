#!/usr/bin/env python3
"""Add Covers: tags to unit test docstrings based on obligation mapping.

Usage:
    python scripts/add_covers_tags.py --dry-run   # Preview changes
    python scripts/add_covers_tags.py              # Apply changes

This script modifies test files in-place to add Covers: tags that link
unit tests to obligation IDs from docs/test-obligations/.
"""

import argparse
import ast
from pathlib import Path

# Mapping: (file_path, "ClassName::func" or just "func") -> list of obligation IDs
# Use "ClassName::func" only when the function name is duplicated within the file.
MAPPINGS: dict[tuple[str, str], list[str]] = {
    # ========================================================================
    # tests/unit/test_media_buy.py
    # ========================================================================
    # Schema compliance
    ("tests/unit/test_media_buy.py", "test_create_request_requires_brand"): ["UC-002-MAIN-02"],
    ("tests/unit/test_media_buy.py", "test_create_request_requires_buyer_ref"): ["UC-002-MAIN-02"],
    ("tests/unit/test_media_buy.py", "test_create_request_accepts_valid_minimal"): ["UC-002-MAIN-02"],
    ("tests/unit/test_media_buy.py", "test_create_request_start_time_must_be_tz_aware"): ["UC-002-EXT-C-06"],
    ("tests/unit/test_media_buy.py", "test_create_request_accepts_asap_start_time"): [
        "UC-002-ALT-ASAP-START-TIMING-01"
    ],
    ("tests/unit/test_media_buy.py", "test_create_request_get_total_budget"): ["UC-002-MAIN-07"],
    ("tests/unit/test_media_buy.py", "test_create_request_get_product_ids_deduplicates"): ["UC-002-EXT-E-01"],
    # Response shapes
    ("tests/unit/test_media_buy.py", "test_success_response_has_media_buy_id"): ["UC-002-POST-04"],
    ("tests/unit/test_media_buy.py", "test_error_response_has_errors_not_media_buy_id"): [
        "UC-002-CC-ATOMIC-RESPONSE-SEMANTICS-02"
    ],
    ("tests/unit/test_media_buy.py", "test_success_response_excludes_internal_fields"): ["UC-002-MAIN-21"],
    ("tests/unit/test_media_buy.py", "test_result_wrapper_supports_tuple_unpacking"): ["UC-002-MAIN-21"],
    ("tests/unit/test_media_buy.py", "test_result_serializes_with_status_field"): ["UC-002-MAIN-21"],
    ("tests/unit/test_media_buy.py", "test_error_str_includes_error_count"): ["UC-002-POST-02"],
    ("tests/unit/test_media_buy.py", "test_success_str_includes_media_buy_id"): ["UC-002-POST-04"],
    # Product validation
    ("tests/unit/test_media_buy.py", "test_product_not_found_returns_error"): ["UC-002-EXT-B-01"],
    ("tests/unit/test_media_buy.py", "test_max_daily_spend_exceeded"): ["UC-002-EXT-K-01"],
    ("tests/unit/test_media_buy.py", "test_pricing_option_xor_both_rejected"): ["UC-002-EXT-N-06"],
    ("tests/unit/test_media_buy.py", "test_pricing_option_xor_neither_rejected"): ["UC-002-EXT-N-07"],
    # Upgrade fields
    ("tests/unit/test_media_buy.py", "test_buyer_campaign_ref_roundtrip"): ["UC-002-UPG-03"],
    ("tests/unit/test_media_buy.py", "test_ext_fields_roundtrip"): ["UC-002-UPG-05"],
    ("tests/unit/test_media_buy.py", "test_account_id_accepted_at_boundary"): ["UC-002-UPG-06"],
    # Budget validation
    ("tests/unit/test_media_buy.py", "test_zero_budget_rejected"): ["UC-002-EXT-A-01"],
    ("tests/unit/test_media_buy.py", "test_duplicate_buyer_ref_rejected"): ["UC-002-EXT-E-01"],
    ("tests/unit/test_media_buy.py", "test_missing_start_time_rejected"): ["UC-002-EXT-C-04"],
    ("tests/unit/test_media_buy.py", "test_end_before_start_rejected"): ["UC-002-EXT-C-02"],
    ("tests/unit/test_media_buy.py", "test_pricing_model_not_offered_rejected"): ["UC-002-EXT-N-01"],
    ("tests/unit/test_media_buy.py", "test_bid_price_below_floor_rejected"): ["UC-002-EXT-N-04"],
    ("tests/unit/test_media_buy.py", "test_budget_below_minimum_spend_rejected"): ["UC-002-CC-MINIMUM-SPEND-PER-02"],
    # Creative validation
    ("tests/unit/test_media_buy.py", "test_creative_missing_url_rejected"): ["UC-002-EXT-G-01"],
    ("tests/unit/test_media_buy.py", "TestCreateMediaBuyCreativeValidation::test_creative_error_state_rejected"): [
        "UC-002-CC-CREATIVE-ASSIGNMENT-VALIDATION-01"
    ],
    ("tests/unit/test_media_buy.py", "test_creative_rejected_state_rejected"): [
        "UC-002-CC-CREATIVE-ASSIGNMENT-VALIDATION-02"
    ],
    ("tests/unit/test_media_buy.py", "TestCreateMediaBuyCreativeValidation::test_creative_format_mismatch_rejected"): [
        "UC-002-EXT-P-01"
    ],
    ("tests/unit/test_media_buy.py", "test_generative_creatives_skip_validation"): [
        "UC-002-ALT-WITH-INLINE-CREATIVES-03"
    ],
    ("tests/unit/test_media_buy.py", "test_multiple_creative_errors_accumulated"): ["UC-002-EXT-G-04"],
    # Status determination
    ("tests/unit/test_media_buy.py", "TestCreateMediaBuyStatusDetermination::test_completed_when_past_end"): [
        "UC-002-MAIN-21"
    ],
    ("tests/unit/test_media_buy.py", "test_active_when_in_flight_with_creatives"): ["UC-002-MAIN-21"],
    ("tests/unit/test_media_buy.py", "test_pending_when_manual_approval_required"): [
        "UC-002-ALT-MANUAL-APPROVAL-REQUIRED-03"
    ],
    ("tests/unit/test_media_buy.py", "test_pending_when_missing_creatives"): ["UC-002-MAIN-21"],
    ("tests/unit/test_media_buy.py", "test_pending_when_before_start"): ["UC-002-MAIN-21"],
    # Auth
    ("tests/unit/test_media_buy.py", "test_missing_identity_raises_validation_error"): ["UC-002-EXT-I-01"],
    ("tests/unit/test_media_buy.py", "test_missing_principal_returns_error_response"): ["UC-002-EXT-I-02"],
    ("tests/unit/test_media_buy.py", "test_missing_tenant_raises_auth_error"): ["UC-002-PRECOND-03"],
    ("tests/unit/test_media_buy.py", "test_setup_incomplete_raises_error"): ["UC-002-PRECOND-03"],
    # Adapter
    ("tests/unit/test_media_buy.py", "test_adapter_error_logged"): ["UC-002-EXT-J-01"],
    ("tests/unit/test_media_buy.py", "test_adapter_exception_propagates"): ["UC-002-EXT-J-03"],
    ("tests/unit/test_media_buy.py", "test_dry_run_skips_adapter"): ["UC-002-MAIN-19"],
    # Update request
    ("tests/unit/test_media_buy.py", "test_update_request_accepts_media_buy_id"): ["UC-003-MAIN-01"],
    ("tests/unit/test_media_buy.py", "test_update_request_parses_iso_datetime_strings"): [
        "UC-003-ALT-UPDATE-TIMING-01"
    ],
    ("tests/unit/test_media_buy.py", "test_update_request_accepts_asap_start_time"): ["UC-003-ALT-UPDATE-TIMING-02"],
    # Update response
    ("tests/unit/test_media_buy.py", "test_update_buyer_campaign_ref_roundtrip"): ["UC-003-MAIN-11"],
    ("tests/unit/test_media_buy.py", "test_update_ext_fields_roundtrip"): ["UC-003-MAIN-12"],
    ("tests/unit/test_media_buy.py", "test_success_response_includes_affected_packages"): ["UC-003-MAIN-09"],
    ("tests/unit/test_media_buy.py", "test_error_response_atomic"): ["UC-003-EXT-O-05"],
    ("tests/unit/test_media_buy.py", "test_affected_packages_excludes_internal_fields"): ["UC-003-MAIN-09"],
    # Update budget
    ("tests/unit/test_media_buy.py", "test_package_budget_update_via_media_buy_id"): ["UC-003-MAIN-01"],
    ("tests/unit/test_media_buy.py", "test_package_budget_update_via_buyer_ref"): ["UC-003-MAIN-02"],
    ("tests/unit/test_media_buy.py", "test_partial_update_omitted_fields_unchanged"): ["UC-003-MAIN-03"],
    ("tests/unit/test_media_buy.py", "test_empty_update_rejected"): ["UC-003-MAIN-04"],
    # Pause/Resume
    ("tests/unit/test_media_buy.py", "test_pause_active_media_buy"): ["UC-003-ALT-PAUSE-RESUME-CAMPAIGN-01"],
    ("tests/unit/test_media_buy.py", "test_resume_paused_media_buy"): ["UC-003-ALT-PAUSE-RESUME-CAMPAIGN-02"],
    ("tests/unit/test_media_buy.py", "test_pause_skips_budget_validation"): ["UC-003-ALT-PAUSE-RESUME-CAMPAIGN-03"],
    # Timing
    ("tests/unit/test_media_buy.py", "test_valid_date_range_accepted"): ["UC-003-ALT-UPDATE-TIMING-01"],
    ("tests/unit/test_media_buy.py", "TestUpdateMediaBuyTiming::test_end_before_start_returns_error"): [
        "UC-003-EXT-E-02"
    ],
    ("tests/unit/test_media_buy.py", "test_shortened_flight_recalculates_daily_spend"): ["UC-003-ALT-UPDATE-TIMING-04"],
    # Campaign budget
    ("tests/unit/test_media_buy.py", "test_positive_campaign_budget_accepted"): ["UC-003-ALT-CAMPAIGN-LEVEL-BUDGET-01"],
    ("tests/unit/test_media_buy.py", "test_zero_campaign_budget_rejected"): ["UC-003-EXT-D-01"],
    ("tests/unit/test_media_buy.py", "test_negative_campaign_budget_rejected"): ["UC-003-EXT-D-02"],
    # Creative IDs update
    ("tests/unit/test_media_buy.py", "test_creative_ids_replaces_all"): ["UC-003-ALT-UPDATE-CREATIVE-IDS-01"],
    ("tests/unit/test_media_buy.py", "test_creative_ids_not_found"): ["UC-003-EXT-I-01"],
    ("tests/unit/test_media_buy.py", "TestUpdateMediaBuyCreativeIds::test_creative_error_state_rejected"): [
        "UC-003-EXT-J-01"
    ],
    ("tests/unit/test_media_buy.py", "TestUpdateMediaBuyCreativeIds::test_creative_format_mismatch_rejected"): [
        "UC-003-EXT-J-03"
    ],
    ("tests/unit/test_media_buy.py", "test_change_set_computation"): ["UC-003-ALT-UPDATE-CREATIVE-IDS-06"],
    # Identification
    ("tests/unit/test_media_buy.py", "test_both_ids_rejected"): ["UC-003-EXT-B-03"],
    ("tests/unit/test_media_buy.py", "test_neither_id_rejected"): ["UC-003-EXT-B-04"],
    ("tests/unit/test_media_buy.py", "test_media_buy_id_not_found"): ["UC-003-EXT-B-01"],
    ("tests/unit/test_media_buy.py", "test_buyer_ref_not_found"): ["UC-003-EXT-B-02"],
    ("tests/unit/test_media_buy.py", "test_ownership_mismatch_rejected"): ["UC-003-EXT-C-01"],
    # Manual approval update
    ("tests/unit/test_media_buy.py", "test_manual_approval_pending_state"): ["UC-003-ALT-MANUAL-APPROVAL-REQUIRED-01"],
    ("tests/unit/test_media_buy.py", "test_implementation_date_null_when_pending"): [
        "UC-003-ALT-MANUAL-APPROVAL-REQUIRED-02"
    ],
    # Adapter atomicity update
    ("tests/unit/test_media_buy.py", "test_adapter_network_error"): ["UC-003-EXT-O-01"],
    ("tests/unit/test_media_buy.py", "test_no_db_changes_on_adapter_failure"): ["UC-003-EXT-O-04"],
    # Pricing option
    ("tests/unit/test_media_buy.py", "test_pricing_option_lookup_uses_string_field"): ["UC-002-EXT-N-08"],
    # Atomic response
    ("tests/unit/test_media_buy.py", "test_create_success_has_no_errors"): ["UC-002-CC-ATOMIC-RESPONSE-SEMANTICS-01"],
    ("tests/unit/test_media_buy.py", "test_create_error_has_no_media_buy_id"): [
        "UC-002-CC-ATOMIC-RESPONSE-SEMANTICS-02"
    ],
    ("tests/unit/test_media_buy.py", "test_update_success_has_no_errors"): ["UC-003-EXT-O-05"],
    ("tests/unit/test_media_buy.py", "test_update_error_has_no_affected_packages"): ["UC-003-EXT-O-05"],
    # Context echo
    ("tests/unit/test_media_buy.py", "test_create_echoes_context"): ["BR-RULE-043-01"],
    ("tests/unit/test_media_buy.py", "test_delivery_echoes_context"): ["BR-RULE-043-01"],
    ("tests/unit/test_media_buy.py", "test_get_media_buys_echoes_context"): ["BR-RULE-043-01"],
    # ========================================================================
    # tests/unit/test_creative.py
    # ========================================================================
    # Schema compliance
    ("tests/unit/test_creative.py", "test_creative_extends_library_creative"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-01"],
    ("tests/unit/test_creative.py", "test_creative_model_dump_excludes_internal_fields"): [
        "UC-006-CREATIVE-SCHEMA-COMPLIANCE-07"
    ],
    ("tests/unit/test_creative.py", "test_creative_model_dump_internal_includes_all"): [
        "UC-006-CREATIVE-SCHEMA-COMPLIANCE-07"
    ],
    ("tests/unit/test_creative.py", "test_creative_format_id_auto_upgrade_from_dict"): [
        "UC-006-CREATIVE-FORMAT-VALIDATION-01"
    ],
    ("tests/unit/test_creative.py", "test_creative_format_property_aliases"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-07"],
    ("tests/unit/test_creative.py", "test_all_creative_status_enum_values_serialize"): [
        "UC-006-CREATIVE-SCHEMA-COMPLIANCE-09"
    ],
    # SyncCreativeResult
    ("tests/unit/test_creative.py", "test_excludes_internal_fields"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-08"],
    ("tests/unit/test_creative.py", "test_empty_lists_excluded"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-08"],
    ("tests/unit/test_creative.py", "test_populated_lists_included"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-08"],
    ("tests/unit/test_creative.py", "test_assignment_fields_present"): ["UC-006-ASSIGNMENTS-RESPONSE-COMPLETENESS-01"],
    ("tests/unit/test_creative.py", "test_creative_action_enum_values"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-09"],
    # SyncCreativesResponse
    ("tests/unit/test_creative.py", "test_success_variant_construction"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-08"],
    ("tests/unit/test_creative.py", "test_str_method_summary"): ["UC-006-MAIN-MCP-10"],
    ("tests/unit/test_creative.py", "test_str_method_dry_run"): ["UC-006-DRY-RUN-01"],
    # ListCreativesResponse
    ("tests/unit/test_creative.py", "TestListCreativesResponseSchema::test_construction"): [
        "UC-006-CREATIVE-SCHEMA-COMPLIANCE-07"
    ],
    ("tests/unit/test_creative.py", "test_str_all_on_one_page"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-07"],
    ("tests/unit/test_creative.py", "test_str_paginated"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-07"],
    ("tests/unit/test_creative.py", "test_nested_creative_excludes_internal_fields"): [
        "UC-006-CREATIVE-SCHEMA-COMPLIANCE-06"
    ],
    # SyncCreativesRequest
    ("tests/unit/test_creative.py", "test_accepts_creative_ids_filter"): ["UC-006-CREATIVE-IDS-SCOPE-01"],
    ("tests/unit/test_creative.py", "test_accepts_assignments_dict"): ["UC-006-ASSIGNMENT-PACKAGE-VALIDATION-01"],
    # CreativeAssignment
    ("tests/unit/test_creative.py", "test_does_not_extend_library_type"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-07"],
    ("tests/unit/test_creative.py", "test_full_construction"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-07"],
    # ListCreativeFormatsResponse
    ("tests/unit/test_creative.py", "test_str_empty"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-10"],
    ("tests/unit/test_creative.py", "test_str_single"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-10"],
    ("tests/unit/test_creative.py", "test_str_multiple"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-10"],
    # Auth (sync)
    ("tests/unit/test_creative.py", "TestSyncCreativesAuth::test_no_identity_raises_auth_error"): ["UC-006-EXT-A-01"],
    ("tests/unit/test_creative.py", "test_identity_without_principal_raises"): ["UC-006-EXT-A-01"],
    ("tests/unit/test_creative.py", "test_identity_without_tenant_raises"): ["UC-006-EXT-B-01"],
    # Cross-principal
    ("tests/unit/test_creative.py", "test_creative_lookup_filters_by_principal"): [
        "UC-006-CROSS-PRINCIPAL-CREATIVE-01"
    ],
    ("tests/unit/test_creative.py", "test_same_creative_id_different_principal_creates_new"): [
        "UC-006-CROSS-PRINCIPAL-CREATIVE-02"
    ],
    ("tests/unit/test_creative.py", "test_new_creative_stamped_with_principal_id"): [
        "UC-006-CROSS-PRINCIPAL-CREATIVE-03"
    ],
    # Input validation
    ("tests/unit/test_creative.py", "test_empty_name_rejected"): ["UC-006-EXT-D-01"],
    ("tests/unit/test_creative.py", "test_whitespace_only_name_rejected"): ["UC-006-EXT-D-01"],
    ("tests/unit/test_creative.py", "test_missing_format_id_rejected_at_schema_level"): ["UC-006-EXT-E-01"],
    # Format validation
    ("tests/unit/test_creative.py", "test_adapter_format_skips_external_validation"): [
        "UC-006-CREATIVE-FORMAT-VALIDATION-02"
    ],
    ("tests/unit/test_creative.py", "test_unreachable_agent_raises_with_retry"): [
        "UC-006-CREATIVE-FORMAT-VALIDATION-03"
    ],
    ("tests/unit/test_creative.py", "test_unknown_format_raises_with_discovery_hint"): [
        "UC-006-CREATIVE-FORMAT-VALIDATION-04"
    ],
    # URL extraction
    ("tests/unit/test_creative.py", "test_direct_url_attribute_takes_priority"): ["UC-006-EXT-H-02"],
    ("tests/unit/test_creative.py", "test_no_assets_returns_none"): ["UC-006-EXT-H-01"],
    # Build creative data
    ("tests/unit/test_creative.py", "test_standard_fields_always_present"): ["UC-006-MAIN-MCP-01"],
    ("tests/unit/test_creative.py", "test_context_stored_when_provided"): ["UC-006-MAIN-MCP-01"],
    ("tests/unit/test_creative.py", "test_assets_stored_when_present"): ["UC-006-MAIN-MCP-01"],
    # Approval workflow
    ("tests/unit/test_creative.py", "test_auto_approve_sets_approved_status"): ["UC-006-CREATIVE-APPROVAL-WORKFLOW-01"],
    ("tests/unit/test_creative.py", "test_require_human_sets_pending_review"): ["UC-006-CREATIVE-APPROVAL-WORKFLOW-02"],
    ("tests/unit/test_creative.py", "test_default_approval_mode_is_require_human"): [
        "UC-006-CREATIVE-APPROVAL-WORKFLOW-04"
    ],
    ("tests/unit/test_creative.py", "test_ai_powered_defers_slack_notification"): [
        "UC-006-CREATIVE-APPROVAL-WORKFLOW-03"
    ],
    # Notifications
    ("tests/unit/test_creative.py", "test_no_notification_without_webhook"): ["UC-006-CREATIVE-APPROVAL-WORKFLOW-05"],
    ("tests/unit/test_creative.py", "test_no_notification_for_auto_approve"): ["UC-006-CREATIVE-APPROVAL-WORKFLOW-01"],
    ("tests/unit/test_creative.py", "test_no_notification_for_ai_powered"): ["UC-006-CREATIVE-APPROVAL-WORKFLOW-03"],
    # Assignment
    ("tests/unit/test_creative.py", "test_none_assignments_returns_empty"): ["UC-006-ASSIGNMENT-PACKAGE-VALIDATION-01"],
    ("tests/unit/test_creative.py", "test_empty_dict_assignments_returns_empty"): [
        "UC-006-ASSIGNMENT-PACKAGE-VALIDATION-01"
    ],
    ("tests/unit/test_creative.py", "test_strict_mode_package_not_found_raises"): [
        "UC-006-ASSIGNMENT-PACKAGE-VALIDATION-02"
    ],
    ("tests/unit/test_creative.py", "test_lenient_mode_package_not_found_continues"): [
        "UC-006-ASSIGNMENT-PACKAGE-VALIDATION-03"
    ],
    # List creatives auth
    ("tests/unit/test_creative.py", "TestListCreativesAuth::test_no_identity_raises_auth_error"): ["UC-006-EXT-A-01"],
    ("tests/unit/test_creative.py", "test_no_principal_raises_auth_error"): ["UC-006-EXT-A-01"],
    ("tests/unit/test_creative.py", "TestListCreativesAuth::test_no_tenant_raises_auth_error"): ["UC-006-EXT-B-01"],
    # List creatives validation
    ("tests/unit/test_creative.py", "test_invalid_created_after_date_raises"): ["UC-006-EXT-C-01"],
    ("tests/unit/test_creative.py", "test_invalid_created_before_date_raises"): ["UC-006-EXT-C-01"],
    # List creatives raw boundary
    ("tests/unit/test_creative.py", "test_raw_forwards_filters_to_impl"): ["UC-006-MAIN-REST-01"],
    ("tests/unit/test_creative.py", "test_raw_forwards_include_performance"): ["UC-006-MAIN-REST-01"],
    ("tests/unit/test_creative.py", "test_raw_forwards_include_assignments"): ["UC-006-MAIN-REST-01"],
    # Creative formats
    ("tests/unit/test_creative.py", "TestListCreativeFormatsAuth::test_no_tenant_raises"): ["UC-006-EXT-B-01"],
    ("tests/unit/test_creative.py", "test_no_filters_returns_all"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-10"],
    ("tests/unit/test_creative.py", "test_type_filter"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-10"],
    ("tests/unit/test_creative.py", "test_name_search_case_insensitive"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-10"],
    ("tests/unit/test_creative.py", "test_default_request_when_none"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-10"],
    # Generative build
    ("tests/unit/test_creative.py", "test_prompt_extracted_from_message_role"): ["UC-006-GENERATIVE-CREATIVE-BUILD-02"],
    ("tests/unit/test_creative.py", "test_prompt_extracted_from_brief_role"): ["UC-006-GENERATIVE-CREATIVE-BUILD-03"],
    ("tests/unit/test_creative.py", "test_prompt_from_inputs_context_description"): [
        "UC-006-GENERATIVE-CREATIVE-BUILD-05"
    ],
    ("tests/unit/test_creative.py", "test_creative_name_fallback_prompt"): ["UC-006-GENERATIVE-CREATIVE-BUILD-06"],
    ("tests/unit/test_creative.py", "test_update_without_prompt_preserves_data"): [
        "UC-006-GENERATIVE-CREATIVE-BUILD-07"
    ],
    ("tests/unit/test_creative.py", "test_user_assets_priority_over_generative"): [
        "UC-006-GENERATIVE-CREATIVE-BUILD-08"
    ],
    ("tests/unit/test_creative.py", "test_missing_gemini_key_fails_generative"): ["UC-006-EXT-I-01"],
    # Workflow step
    ("tests/unit/test_creative.py", "test_creates_workflow_step_for_pending_creative"): [
        "UC-006-CREATIVE-APPROVAL-WORKFLOW-02"
    ],
    # Audit logging
    ("tests/unit/test_creative.py", "test_audit_log_sync_succeeds_without_principal_in_db"): ["UC-006-MAIN-MCP-10"],
    # Creative IDs scope
    ("tests/unit/test_creative.py", "test_filter_narrows_to_matching_creatives"): ["UC-006-CREATIVE-IDS-SCOPE-01"],
    ("tests/unit/test_creative.py", "test_empty_creative_ids_filters_all"): ["UC-006-CREATIVE-IDS-SCOPE-01"],
    # Delete missing
    ("tests/unit/test_creative.py", "test_delete_missing_archives_unlisted_creatives"): ["UC-006-DELETE-MISSING-01"],
    # Dry run
    ("tests/unit/test_creative.py", "test_dry_run_does_not_persist"): ["UC-006-DRY-RUN-01"],
    # Webhook
    ("tests/unit/test_creative.py", "test_webhook_delivered_on_approval"): ["UC-006-CREATIVE-APPROVAL-WORKFLOW-02"],
    # Media URL fallback
    ("tests/unit/test_creative.py", "test_no_previews_no_media_url_fails"): ["UC-006-EXT-H-01"],
    # Approval status schema
    ("tests/unit/test_creative.py", "TestCreativeApprovalStatusSchema::test_construction"): [
        "UC-006-CREATIVE-APPROVAL-WORKFLOW-01"
    ],
    ("tests/unit/test_creative.py", "test_with_suggested_adaptations"): ["UC-006-CREATIVE-APPROVAL-WORKFLOW-01"],
    # FormatId
    ("tests/unit/test_creative.py", "test_str_returns_id"): ["UC-006-CREATIVE-FORMAT-VALIDATION-01"],
    ("tests/unit/test_creative.py", "test_get_dimensions"): ["UC-006-CREATIVE-FORMAT-VALIDATION-01"],
    ("tests/unit/test_creative.py", "test_get_dimensions_none_when_missing"): ["UC-006-CREATIVE-FORMAT-VALIDATION-01"],
    ("tests/unit/test_creative.py", "test_get_duration_ms"): ["UC-006-CREATIVE-FORMAT-VALIDATION-01"],
    # Routes
    ("tests/unit/test_creative.py", "test_creative_formats_route_exists"): ["UC-006-MAIN-REST-01"],
    ("tests/unit/test_creative.py", "test_sync_creatives_route_exists"): ["UC-006-MAIN-REST-01"],
    ("tests/unit/test_creative.py", "test_list_creatives_route_exists"): ["UC-006-MAIN-REST-01"],
    # V3.6 contract
    ("tests/unit/test_creative.py", "test_creative_extends_listing_base_not_delivery"): [
        "UC-006-CREATIVE-SCHEMA-COMPLIANCE-01"
    ],
    ("tests/unit/test_creative.py", "test_list_creatives_response_includes_name"): [
        "UC-006-CREATIVE-SCHEMA-COMPLIANCE-02"
    ],
    ("tests/unit/test_creative.py", "test_list_creatives_response_includes_status"): [
        "UC-006-CREATIVE-SCHEMA-COMPLIANCE-03"
    ],
    ("tests/unit/test_creative.py", "test_list_creatives_response_includes_created_date"): [
        "UC-006-CREATIVE-SCHEMA-COMPLIANCE-04"
    ],
    ("tests/unit/test_creative.py", "test_list_creatives_response_includes_updated_date"): [
        "UC-006-CREATIVE-SCHEMA-COMPLIANCE-05"
    ],
    ("tests/unit/test_creative.py", "test_list_creatives_response_excludes_delivery_fields"): [
        "UC-006-CREATIVE-SCHEMA-COMPLIANCE-06"
    ],
    ("tests/unit/test_creative.py", "test_model_dump_validates_against_listing_schema"): [
        "UC-006-CREATIVE-SCHEMA-COMPLIANCE-07"
    ],
    ("tests/unit/test_creative.py", "test_all_11_asset_types_accepted"): ["UC-006-CREATIVE-SCHEMA-COMPLIANCE-10"],
    # Savepoint isolation
    ("tests/unit/test_creative.py", "test_lenient_per_creative_savepoint_isolation"): ["UC-006-MAIN-MCP-05"],
    # Validation mode
    ("tests/unit/test_creative.py", "test_strict_mode_aborts_remaining_assignments"): ["UC-006-MAIN-MCP-06"],
    ("tests/unit/test_creative.py", "test_lenient_mode_continues_on_assignment_error"): ["UC-006-MAIN-MCP-07"],
    ("tests/unit/test_creative.py", "test_default_validation_mode_is_strict"): ["UC-006-MAIN-MCP-08"],
    # Assignment idempotency
    ("tests/unit/test_creative.py", "test_idempotent_upsert_duplicate_assignment"): [
        "UC-006-ASSIGNMENT-PACKAGE-VALIDATION-04"
    ],
    # Cross-tenant isolation
    ("tests/unit/test_creative.py", "test_cross_tenant_package_isolation"): ["UC-006-ASSIGNMENT-PACKAGE-VALIDATION-05"],
    # Format compatibility
    ("tests/unit/test_creative.py", "test_format_match_after_url_normalization"): [
        "UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-01"
    ],
    ("tests/unit/test_creative.py", "test_format_mismatch_strict_raises"): [
        "UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-02"
    ],
    ("tests/unit/test_creative.py", "test_format_mismatch_lenient_logs_error"): [
        "UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-03"
    ],
    ("tests/unit/test_creative.py", "test_empty_product_format_ids_allows_all"): [
        "UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-04"
    ],
    ("tests/unit/test_creative.py", "test_product_format_ids_dual_key_support"): [
        "UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-05"
    ],
    ("tests/unit/test_creative.py", "test_package_without_product_skips_format_check"): [
        "UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-06"
    ],
    # Media buy status transition
    ("tests/unit/test_creative.py", "test_draft_with_approved_at_transitions"): ["UC-006-MEDIA-BUY-STATUS-01"],
    ("tests/unit/test_creative.py", "test_draft_without_approved_at_stays_draft"): ["UC-006-MEDIA-BUY-STATUS-02"],
    ("tests/unit/test_creative.py", "test_non_draft_status_unchanged"): ["UC-006-MEDIA-BUY-STATUS-03"],
    ("tests/unit/test_creative.py", "test_transition_fires_on_upsert"): ["UC-006-MEDIA-BUY-STATUS-04"],
    # Core sync
    ("tests/unit/test_creative.py", "test_batch_sync_multiple_creatives"): ["UC-006-MAIN-MCP-02"],
    ("tests/unit/test_creative.py", "test_upsert_by_triple_key"): ["UC-006-MAIN-MCP-03"],
    ("tests/unit/test_creative.py", "test_unchanged_creative_detection"): ["UC-006-MAIN-MCP-04"],
    ("tests/unit/test_creative.py", "test_format_registry_cached_per_sync"): ["UC-006-MAIN-MCP-09"],
    ("tests/unit/test_creative.py", "test_mcp_response_valid_sync_creatives_response"): ["UC-006-MAIN-MCP-10"],
    # Extension tests
    ("tests/unit/test_creative.py", "test_ext_b_tenant_not_found"): ["UC-006-EXT-B-01"],
    ("tests/unit/test_creative.py", "test_ext_c_validation_failure_strict_others_processed"): ["UC-006-EXT-C-02"],
    ("tests/unit/test_creative.py", "test_ext_c_validation_failure_lenient"): ["UC-006-EXT-C-03"],
    ("tests/unit/test_creative.py", "test_ext_d_missing_name_field"): ["UC-006-EXT-D-02"],
    ("tests/unit/test_creative.py", "test_ext_h_media_url_fallback"): ["UC-006-EXT-H-02"],
    ("tests/unit/test_creative.py", "test_ext_f_unknown_format_with_hint"): ["UC-006-EXT-F-01"],
    ("tests/unit/test_creative.py", "test_ext_g_unreachable_agent_retry"): ["UC-006-EXT-G-01"],
    ("tests/unit/test_creative.py", "test_ext_j_package_not_found_lenient"): ["UC-006-EXT-J-02"],
    ("tests/unit/test_creative.py", "test_ext_k_format_mismatch_strict"): ["UC-006-EXT-K-01"],
    ("tests/unit/test_creative.py", "test_ext_k_format_mismatch_lenient"): ["UC-006-EXT-K-02"],
    # A2A integration
    ("tests/unit/test_creative.py", "test_sync_creatives_via_a2a"): ["UC-006-MAIN-REST-01"],
    ("tests/unit/test_creative.py", "test_a2a_slack_notification_require_human"): ["UC-006-MAIN-REST-02"],
    ("tests/unit/test_creative.py", "test_a2a_ai_review_submission"): ["UC-006-MAIN-REST-03"],
    ("tests/unit/test_creative.py", "test_list_creatives_raw_boundary"): ["UC-006-MAIN-REST-01"],
    ("tests/unit/test_creative.py", "test_list_creative_formats_raw_boundary"): ["UC-006-MAIN-REST-01"],
    # ========================================================================
    # tests/unit/test_delivery.py
    # ========================================================================
    # Happy path
    ("tests/unit/test_delivery.py", "test_single_buy_returns_complete_response"): ["UC-004-MAIN-01"],
    ("tests/unit/test_delivery.py", "test_two_buys_aggregate_correctly"): ["UC-004-MAIN-03"],
    ("tests/unit/test_delivery.py", "test_media_buy_ids_only"): ["UC-004-MAIN-01"],
    ("tests/unit/test_delivery.py", "test_buyer_refs_only"): ["UC-004-MAIN-02"],
    ("tests/unit/test_delivery.py", "test_both_provided_media_buy_ids_wins"): ["UC-004-MAIN-05"],
    ("tests/unit/test_delivery.py", "test_neither_provided_fetches_all"): ["UC-004-MAIN-04"],
    ("tests/unit/test_delivery.py", "test_partial_ids_returns_found_and_errors_for_missing"): ["UC-004-MAIN-17"],
    ("tests/unit/test_delivery.py", "test_all_ids_invalid_returns_empty_with_errors"): ["UC-004-MAIN-18"],
    # Status filter
    ("tests/unit/test_delivery.py", "test_status_filter_all_returns_all_statuses"): [
        "UC-004-ALT-STATUS-FILTERED-DELIVERY-06"
    ],
    ("tests/unit/test_delivery.py", "test_status_filter_default_is_active"): ["UC-004-ALT-STATUS-FILTERED-DELIVERY-05"],
    ("tests/unit/test_delivery.py", "test_default_filter_only_returns_active_buys"): [
        "UC-004-ALT-STATUS-FILTERED-DELIVERY-01"
    ],
    ("tests/unit/test_delivery.py", "test_status_filter_completed"): ["UC-004-ALT-STATUS-FILTERED-DELIVERY-02"],
    ("tests/unit/test_delivery.py", "test_status_filter_paused"): ["UC-004-ALT-STATUS-FILTERED-DELIVERY-03"],
    ("tests/unit/test_delivery.py", "test_status_filter_no_match_returns_empty"): [
        "UC-004-ALT-STATUS-FILTERED-DELIVERY-04"
    ],
    ("tests/unit/test_delivery.py", "test_valid_status_enum_values_accepted"): [
        "UC-004-ALT-STATUS-FILTERED-DELIVERY-07"
    ],
    # Date range
    ("tests/unit/test_delivery.py", "test_custom_date_range_reflected_in_reporting_period"): [
        "UC-004-ALT-CUSTOM-DATE-RANGE-01"
    ],
    ("tests/unit/test_delivery.py", "test_no_date_range_defaults_to_last_30_days"): ["UC-004-MAIN-06"],
    ("tests/unit/test_delivery.py", "test_only_start_date_end_defaults_to_now"): ["UC-004-ALT-CUSTOM-DATE-RANGE-02"],
    ("tests/unit/test_delivery.py", "test_only_end_date_start_defaults_to_30_days"): [
        "UC-004-ALT-CUSTOM-DATE-RANGE-03"
    ],
    ("tests/unit/test_delivery.py", "test_custom_range_overrides_default"): ["UC-004-ALT-CUSTOM-DATE-RANGE-04"],
    # Pricing
    ("tests/unit/test_delivery.py", "test_pricing_option_lookup_uses_string_field_not_integer_pk"): [
        "UC-004-PRICINGOPTION-TYPE-CONSISTENCY-01"
    ],
    ("tests/unit/test_delivery.py", "test_delivery_spend_correct_with_cpm_pricing"): [
        "UC-004-PRICINGOPTION-TYPE-CONSISTENCY-03"
    ],
    ("tests/unit/test_delivery.py", "test_delivery_spend_correct_with_cpc_pricing"): [
        "UC-004-PRICINGOPTION-TYPE-CONSISTENCY-04"
    ],
    ("tests/unit/test_delivery.py", "test_delivery_spend_correct_with_flat_rate_pricing"): [
        "UC-004-PRICINGOPTION-TYPE-CONSISTENCY-05"
    ],
    ("tests/unit/test_delivery.py", "test_buyer_ref_present_in_delivery_entries"): ["UC-004-MAIN-16"],
    # Serialization
    ("tests/unit/test_delivery.py", "test_nested_serialization_model_dump"): [
        "UC-004-RESPONSE-SERIALIZATION-SALESAGENT-01"
    ],
    ("tests/unit/test_delivery.py", "test_ext_fields_preserved"): ["UC-004-RESPONSE-SERIALIZATION-SALESAGENT-02"],
    # Error paths
    ("tests/unit/test_delivery.py", "test_missing_principal_id_returns_error"): ["UC-004-EXT-A-01"],
    ("tests/unit/test_delivery.py", "test_principal_not_found_returns_error"): ["UC-004-EXT-B-01"],
    ("tests/unit/test_delivery.py", "test_auth_failure_no_state_change"): ["UC-004-EXT-A-02"],
    ("tests/unit/test_delivery.py", "test_media_buy_not_found_returns_error"): ["UC-004-EXT-C-01"],
    ("tests/unit/test_delivery.py", "test_partial_ids_returns_found_and_errors"): ["UC-004-EXT-C-02"],
    ("tests/unit/test_delivery.py", "test_buyer_ref_not_found_returns_error"): ["UC-004-EXT-C-03"],
    ("tests/unit/test_delivery.py", "test_ownership_mismatch_returns_not_found"): ["UC-004-EXT-D-01"],
    ("tests/unit/test_delivery.py", "test_no_info_leakage_on_ownership_error"): ["UC-004-EXT-D-02"],
    ("tests/unit/test_delivery.py", "test_mixed_ownership_behavior"): ["UC-004-EXT-D-03"],
    ("tests/unit/test_delivery.py", "test_start_date_equals_end_date_returns_error"): ["UC-004-EXT-E-01"],
    ("tests/unit/test_delivery.py", "test_start_date_after_end_date_returns_error"): ["UC-004-EXT-E-02"],
    ("tests/unit/test_delivery.py", "test_date_range_error_no_state_change"): ["UC-004-EXT-E-03"],
    ("tests/unit/test_delivery.py", "test_adapter_exception_returns_adapter_error"): ["UC-004-EXT-F-01"],
    ("tests/unit/test_delivery.py", "test_adapter_error_preserves_reporting_period"): ["UC-004-EXT-F-02"],
    ("tests/unit/test_delivery.py", "test_adapter_failure_audit_logged"): ["UC-004-EXT-F-03"],
    ("tests/unit/test_delivery.py", "test_adapter_error_no_state_change"): ["UC-004-EXT-F-04"],
    # Webhook
    ("tests/unit/test_delivery.py", "test_next_expected_at_computed"): ["UC-004-ALT-WEBHOOK-PUSH-REPORTING-06"],
    ("tests/unit/test_delivery.py", "test_hmac_sha256_signature_headers"): ["UC-004-ALT-WEBHOOK-PUSH-REPORTING-07"],
    ("tests/unit/test_delivery.py", "test_webhook_excludes_aggregated_totals"): [
        "UC-004-ALT-WEBHOOK-PUSH-REPORTING-09"
    ],
    ("tests/unit/test_delivery.py", "test_webhook_filters_requested_metrics"): ["UC-004-ALT-WEBHOOK-PUSH-REPORTING-10"],
    ("tests/unit/test_delivery.py", "test_only_active_trigger_webhook"): ["UC-004-ALT-WEBHOOK-PUSH-REPORTING-11"],
    # Circuit breaker
    ("tests/unit/test_delivery.py", "test_five_failures_opens_circuit_breaker"): ["UC-004-EXT-G-03"],
    ("tests/unit/test_delivery.py", "test_open_circuit_rejects_requests"): ["UC-004-EXT-G-03"],
    ("tests/unit/test_delivery.py", "test_open_transitions_to_half_open_after_timeout"): ["UC-004-EXT-G-04"],
    ("tests/unit/test_delivery.py", "test_half_open_recovers_after_success_threshold"): ["UC-004-EXT-G-04"],
    ("tests/unit/test_delivery.py", "test_half_open_failure_returns_to_open"): ["UC-004-EXT-G-04"],
    ("tests/unit/test_delivery.py", "test_auth_rejection_marks_webhook_failed"): ["UC-004-EXT-G-06"],
    ("tests/unit/test_delivery.py", "test_webhook_failures_no_synchronous_error"): ["UC-004-EXT-G-08"],
    # Protocol
    ("tests/unit/test_delivery.py", "test_protocol_envelope_status_completed"): ["UC-004-MAIN-12"],
    ("tests/unit/test_delivery.py", "test_mcp_toolresult_content_and_structured"): ["UC-004-MAIN-13"],
    # Metrics
    ("tests/unit/test_delivery.py", "test_delivery_metrics_all_standard_fields"): ["UC-004-MAIN-19"],
    ("tests/unit/test_delivery.py", "test_unpopulated_fields_handled_gracefully"): ["UC-004-MAIN-20"],
}


def _build_func_locations(filepath: str) -> dict[tuple[str | None, str], int]:
    """Parse a file with AST to find all (class_name, func_name) -> line pairs."""
    source = Path(filepath).read_text()
    tree = ast.parse(source)
    locations: dict[tuple[str | None, str], int] = {}

    # Only iterate top-level statements (not ast.walk which flattens the tree)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name.startswith("test_"):
                        locations[(node.name, item.name)] = item.lineno - 1  # 0-indexed
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                locations[(None, node.name)] = node.lineno - 1

    return locations


def _find_func_line(locations: dict[tuple[str | None, str], int], key: str) -> int | None:
    """Find function line from key. Key is either 'func_name' or 'ClassName::func_name'."""
    if "::" in key:
        class_name, func_name = key.split("::", 1)
        return locations.get((class_name, func_name))
    else:
        # Search all classes for this function name
        matches = [(k, v) for k, v in locations.items() if k[1] == key]
        if len(matches) == 1:
            return matches[0][1]
        elif len(matches) > 1:
            # Multiple matches — return None (caller should use ClassName:: prefix)
            print(f"  AMBIGUOUS: {key} found in {[m[0][0] for m in matches]}")
            return None
        return None


def _find_docstring_info(lines: list[str], func_line: int) -> tuple[int, int, bool] | None:
    """Find docstring start line, end line, and whether it's single-line.

    Returns (start_line, end_line, is_single_line) or None.
    """
    for i in range(func_line + 1, min(func_line + 5, len(lines))):
        stripped = lines[i].strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            quote = stripped[:3]
            # Single-line docstring: """text"""
            if stripped.count(quote) >= 2 and stripped.endswith(quote) and len(stripped) > 6:
                return (i, i, True)
            # Multi-line docstring: find closing quote
            for j in range(i + 1, len(lines)):
                if quote in lines[j]:
                    return (i, j, False)
            break
    return None


def add_covers_tags(file_path: str, mappings: dict[str, list[str]], dry_run: bool = False) -> int:
    """Add Covers: tags to test functions. Returns count of tags added."""
    path = Path(file_path)
    if not path.exists():
        print(f"  SKIP: {file_path} not found")
        return 0

    locations = _build_func_locations(file_path)
    lines = path.read_text().splitlines()

    # Collect edits: (line_index, obligation_ids, is_single_line)
    edits: list[tuple[int, int, list[str], bool]] = []
    added = 0

    for key, obligation_ids in mappings.items():
        func_line = _find_func_line(locations, key)
        if func_line is None:
            print(f"  WARN: {file_path}: function {key} not found")
            continue

        info = _find_docstring_info(lines, func_line)
        if info is None:
            print(f"  WARN: {file_path}: no docstring for {key}")
            continue

        start, end, is_single = info

        # Skip if Covers: tag already exists
        has_covers = any("Covers:" in lines[j] for j in range(start, end + 1))
        if has_covers:
            continue

        edits.append((start, end, obligation_ids, is_single))
        added += len(obligation_ids)

    if not edits:
        return 0

    # Apply from bottom to top to preserve line numbers
    edits.sort(key=lambda x: x[0], reverse=True)
    for start, end, obligation_ids, is_single in edits:
        indent = len(lines[start]) - len(lines[start].lstrip())
        indent_str = " " * indent
        covers_lines = [f"{indent_str}Covers: {oid}" for oid in obligation_ids]

        if is_single:
            # Expand single-line docstring: """text""" -> """text\n\nCovers: ...\n"""
            original = lines[start]
            quote = original.strip()[:3]
            # Extract content between quotes
            content = original.strip()[3:-3]
            lines[start] = f"{indent_str}{quote}{content}"
            # Insert Covers: lines and closing quote after
            insert_pos = start + 1
            lines.insert(insert_pos, "")  # blank line separator
            insert_pos += 1
            for cl in covers_lines:
                lines.insert(insert_pos, cl)
                insert_pos += 1
            lines.insert(insert_pos, f"{indent_str}{quote}")
        else:
            # Multi-line: insert Covers: lines before closing """
            for cl in reversed(covers_lines):
                lines.insert(end, cl)

    if not dry_run:
        path.write_text("\n".join(lines) + "\n")
        print(f"  WROTE: {file_path} ({added} Covers: tags)")
    else:
        print(f"  DRY RUN: {file_path} ({added} Covers: tags)")

    return added


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    by_file: dict[str, dict[str, list[str]]] = {}
    for (file_path, func_key), obligation_ids in MAPPINGS.items():
        by_file.setdefault(file_path, {})[func_key] = obligation_ids

    total = 0
    for file_path, file_mappings in sorted(by_file.items()):
        print(f"\n{file_path}:")
        total += add_covers_tags(file_path, file_mappings, dry_run=args.dry_run)

    print(f"\nTotal: {total} Covers: tags {'would be ' if args.dry_run else ''}added")


if __name__ == "__main__":
    main()
