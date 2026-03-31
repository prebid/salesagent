"""Guard: Business logic uses repositories, not inline DB access.

Two invariants:
1. _impl functions must not call get_db_session() — data access belongs in repositories
2. Integration test bodies must not call session.add() — use factories or fixtures

Scanning approach: AST — parse source files for function calls matching prohibited
patterns. All pre-existing violations are allowlisted; new code fails immediately.

beads: salesagent-qo8a (repository pattern enforcement)
"""

import ast
import glob
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Invariant 1: No get_db_session() in _impl functions
# ---------------------------------------------------------------------------

# Production files that contain _impl functions to scan
IMPL_FILES = [
    "src/core/tools/media_buy_create.py",
    "src/core/tools/media_buy_update.py",
    "src/core/tools/media_buy_delivery.py",
    "src/core/tools/media_buy_list.py",
    "src/core/tools/products.py",
    "src/core/tools/capabilities.py",
    "src/core/tools/creative_formats.py",
    "src/core/tools/properties.py",
    "src/core/tools/creatives/listing.py",
    "src/core/tools/creatives/_sync.py",
    "src/core/tools/creatives/_assignments.py",
    "src/core/tools/creatives/_workflow.py",
    "src/core/tools/performance.py",
    "src/core/tools/signals.py",
    "src/core/tools/task_management.py",
    "src/core/context_manager.py",
    "src/admin/blueprints/creatives.py",
]

# Pre-existing violations: (file_path, function_name)
# These existed before the guard was created. Allowlist shrinks as repositories are introduced.
# FIXME(salesagent-qo8a): all _impl functions should use repositories instead of get_db_session()
IMPL_SESSION_ALLOWLIST: set[tuple[str, str]] = set()

# ---------------------------------------------------------------------------
# Invariant 2: No session.add() in integration test bodies
# ---------------------------------------------------------------------------


def _discover_integration_test_files() -> list[str]:
    """Dynamically discover all integration test files via glob.

    Scans tests/integration*/ directories for test_*.py files and conftest.py files.
    This ensures new test files are automatically covered by the guard.
    """
    test_files = sorted(glob.glob("tests/integration*/**/test_*.py", recursive=True))
    conftest_files = sorted(glob.glob("tests/integration*/conftest.py", recursive=True))
    return sorted(set(test_files + conftest_files))


INTEGRATION_TEST_FILES = _discover_integration_test_files()

# Pre-existing violations: (file_path, function_or_fixture_name)
# FIXME(salesagent-qo8a): integration tests should use polyfactory fixtures
INTEGRATION_SESSION_ADD_ALLOWLIST = {
    # tests/integration/conftest.py
    ("tests/integration/conftest.py", "authenticated_admin_session"),
    ("tests/integration/conftest.py", "test_tenant_with_data"),
    ("tests/integration/conftest.py", "sample_tenant"),
    ("tests/integration/conftest.py", "sample_principal"),
    ("tests/integration/conftest.py", "sample_products"),
    ("tests/integration/conftest.py", "test_media_buy_workflow"),
    # tests/integration/test_adapter_factory.py
    ("tests/integration/test_adapter_factory.py", "setup_adapters"),
    # tests/integration/test_gam_adapter_auth.py — no AdapterConfigFactory exists yet
    # FIXME(salesagent-zj9): migrate to factory when AdapterConfigFactory is created
    ("tests/integration/test_gam_adapter_auth.py", "oauth_tenant"),
    ("tests/integration/test_gam_adapter_auth.py", "sa_tenant"),
    # tests/integration/test_adapter_config_repository.py — same: no AdapterConfigFactory
    # FIXME(salesagent-zj9): migrate to factory when AdapterConfigFactory is created
    ("tests/integration/test_adapter_config_repository.py", "_tenants"),
    # tests/integration/test_admin_ui_pages.py
    ("tests/integration/test_admin_ui_pages.py", "test_cannot_access_other_tenant_data"),
    # tests/integration/test_audit_decorator.py
    ("tests/integration/test_audit_decorator.py", "test_decorator_logs_successful_action"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_filters_password_fields"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_filters_sensitive_json_fields"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_logs_failed_actions"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_truncates_long_values"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_extracts_custom_details"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_handles_missing_session"),
    # tests/integration/test_context_persistence.py
    ("tests/integration/test_context_persistence.py", "test_simplified_context"),
    # tests/integration/test_creative_assignment_principal_id.py
    ("tests/integration/test_creative_assignment_principal_id.py", "ca_creatives"),
    # tests/integration/test_product_repository.py — repository test legitimately uses session.add()
    ("tests/integration/test_product_repository.py", "_create_test_tenant"),
    ("tests/integration/test_product_repository.py", "_create_test_product"),
    # tests/integration/test_creative_review_model.py
    ("tests/integration/test_creative_review_model.py", "_create_test_tenant_with_creative"),
    ("tests/integration/test_creative_review_model.py", "test_get_creative_reviews_query"),
    ("tests/integration/test_creative_review_model.py", "test_get_creative_reviews_filters_by_review_type"),
    ("tests/integration/test_creative_review_model.py", "test_get_creative_reviews_tenant_isolation"),
    ("tests/integration/test_creative_review_model.py", "test_get_creative_with_latest_review_tenant_isolation"),
    # tests/integration/test_creative_v3.py (multiple classes share setup_tenant name)
    ("tests/integration/test_creative_v3.py", "setup_tenant"),
    # tests/integration/test_cross_principal_security.py
    ("tests/integration/test_cross_principal_security.py", "setup_test_data"),
    ("tests/integration/test_cross_principal_security.py", "test_cross_tenant_isolation_also_enforced"),
    # tests/integration/test_database_health_integration.py
    ("tests/integration/test_database_health_integration.py", "test_health_check_performance_with_real_database"),
    # tests/integration/test_database_integration.py
    ("tests/integration/test_database_integration.py", "test_settings_queries"),
    # tests/integration/test_delivery_simulator_restart.py
    ("tests/integration/test_delivery_simulator_restart.py", "test_tenant"),
    ("tests/integration/test_delivery_simulator_restart.py", "test_principal"),
    ("tests/integration/test_delivery_simulator_restart.py", "test_product"),
    ("tests/integration/test_delivery_simulator_restart.py", "test_webhook_config"),
    ("tests/integration/test_delivery_simulator_restart.py", "test_restart_finds_media_buys_with_principal_webhook"),
    ("tests/integration/test_delivery_simulator_restart.py", "test_restart_ignores_media_buys_without_webhook"),
    ("tests/integration/test_delivery_simulator_restart.py", "test_restart_join_cardinality"),
    # tests/integration/test_delivery_poll_behavioral.py
    ("tests/integration/test_delivery_poll_behavioral.py", "test_get_pricing_options_uses_string_id_not_integer_pk"),
    (
        "tests/integration/test_delivery_poll_behavioral.py",
        "test_non_numeric_pricing_option_id_is_not_silently_discarded",
    ),
    ("tests/integration/test_delivery_poll_behavioral.py", "test_pricing_options_keyed_by_string_id_not_integer_pk"),
    ("tests/integration/test_delivery_poll_behavioral.py", "test_integer_pk_lookup_returns_none"),
    # tests/integration/test_delivery_repository.py
    ("tests/integration/test_delivery_repository.py", "tenant_a"),
    ("tests/integration/test_delivery_repository.py", "tenant_b"),
    ("tests/integration/test_delivery_repository.py", "principal_a"),
    ("tests/integration/test_delivery_repository.py", "principal_b"),
    ("tests/integration/test_delivery_repository.py", "media_buy_a"),
    ("tests/integration/test_delivery_repository.py", "media_buy_b"),
    # tests/integration/test_delivery_v3.py
    ("tests/integration/test_delivery_v3.py", "_setup_base_state"),
    ("tests/integration/test_delivery_v3.py", "_create_media_buy"),
    ("tests/integration/test_delivery_v3.py", "test_ownership_isolation"),
    ("tests/integration/test_delivery_v3.py", "test_ownership_no_info_leakage"),
    ("tests/integration/test_delivery_v3.py", "test_mixed_ownership"),
    # tests/integration/test_delivery_webhooks_force.py
    (
        "tests/integration/test_delivery_webhooks_force.py",
        "test_force_trigger_delivery_webhook_bypasses_duplicate_check",
    ),
    ("tests/integration/test_delivery_webhooks_force.py", "test_trigger_report_fails_gracefully_no_webhook"),
    # tests/integration/test_delivery_webhooks_integration.py
    ("tests/integration/test_delivery_webhooks_integration.py", "_create_test_tenant_and_principal"),
    ("tests/integration/test_delivery_webhooks_integration.py", "_create_basic_media_buy_with_webhook"),
    # tests/integration/test_format_conversion_approval.py
    ("tests/integration/test_format_conversion_approval.py", "create_media_package"),
    ("tests/integration/test_format_conversion_approval.py", "test_tenant"),
    ("tests/integration/test_format_conversion_approval.py", "test_currency_limit"),
    ("tests/integration/test_format_conversion_approval.py", "test_property_tag"),
    ("tests/integration/test_format_conversion_approval.py", "test_principal"),
    ("tests/integration/test_format_conversion_approval.py", "test_valid_format_reference_dict_conversion"),
    ("tests/integration/test_format_conversion_approval.py", "test_invalid_format_missing_agent_url"),
    ("tests/integration/test_format_conversion_approval.py", "test_invalid_format_empty_agent_url"),
    ("tests/integration/test_format_conversion_approval.py", "test_invalid_agent_url_not_http"),
    ("tests/integration/test_format_conversion_approval.py", "test_invalid_format_missing_format_id"),
    ("tests/integration/test_format_conversion_approval.py", "test_valid_format_id_dict_conversion"),
    ("tests/integration/test_format_conversion_approval.py", "test_invalid_dict_missing_id"),
    ("tests/integration/test_format_conversion_approval.py", "test_empty_formats_list_fails"),
    ("tests/integration/test_format_conversion_approval.py", "test_mixed_valid_format_types"),
    ("tests/integration/test_format_conversion_approval.py", "test_invalid_format_unknown_type"),
    # tests/integration/test_gam_pricing_models_integration.py
    ("tests/integration/test_gam_pricing_models_integration.py", "setup_gam_tenant_with_all_pricing_models"),
    ("tests/integration/test_gam_pricing_models_integration.py", "test_gam_auction_cpc_creates_price_priority"),
    # tests/integration/test_gam_pricing_restriction.py
    ("tests/integration/test_gam_pricing_restriction.py", "setup_gam_tenant_with_non_cpm_product"),
    # tests/integration/test_inventory_profile_effective_properties.py
    ("tests/integration/test_inventory_profile_effective_properties.py", "test_tenant"),
    ("tests/integration/test_inventory_profile_effective_properties.py", "test_profile"),
    ("tests/integration/test_inventory_profile_effective_properties.py", "test_product_custom"),
    ("tests/integration/test_inventory_profile_effective_properties.py", "test_product_with_profile"),
    (
        "tests/integration/test_inventory_profile_effective_properties.py",
        "test_effective_properties_handle_none_profile_relationship",
    ),
    # tests/integration/test_inventory_profile_media_buy.py
    ("tests/integration/test_inventory_profile_media_buy.py", "test_create_media_buy_with_profile_based_product"),
    ("tests/integration/test_inventory_profile_media_buy.py", "test_create_media_buy_with_profile_formats"),
    ("tests/integration/test_inventory_profile_media_buy.py", "test_multiple_products_same_profile_in_media_buy"),
    ("tests/integration/test_inventory_profile_media_buy.py", "test_media_buy_reflects_profile_updates"),
    # tests/integration/test_inventory_profile_security.py
    ("tests/integration/test_inventory_profile_security.py", "tenant_a"),
    ("tests/integration/test_inventory_profile_security.py", "tenant_b"),
    ("tests/integration/test_inventory_profile_security.py", "profile_a"),
    ("tests/integration/test_inventory_profile_security.py", "profile_b"),
    (
        "tests/integration/test_inventory_profile_security.py",
        "test_product_cannot_reference_profile_from_different_tenant",
    ),
    # tests/integration/test_inventory_profile_transitions.py
    ("tests/integration/test_inventory_profile_transitions.py", "tenant"),
    ("tests/integration/test_inventory_profile_transitions.py", "profile_a"),
    ("tests/integration/test_inventory_profile_transitions.py", "profile_b"),
    ("tests/integration/test_inventory_profile_transitions.py", "create_product"),
    # tests/integration/test_inventory_profile_updates.py
    ("tests/integration/test_inventory_profile_updates.py", "test_updating_profile_formats_affects_all_products"),
    (
        "tests/integration/test_inventory_profile_updates.py",
        "test_updating_profile_inventory_affects_product_implementation_config",
    ),
    ("tests/integration/test_inventory_profile_updates.py", "test_updating_profile_properties_affects_all_products"),
    # tests/integration/test_list_authorized_properties_integration.py
    (
        "tests/integration/test_list_authorized_properties_integration.py",
        "test_list_authorized_properties_reads_from_publisher_partner",
    ),
    (
        "tests/integration/test_list_authorized_properties_integration.py",
        "test_list_authorized_properties_returns_all_registered_publishers",
    ),
    (
        "tests/integration/test_list_authorized_properties_integration.py",
        "test_list_authorized_properties_returns_empty_when_no_publishers",
    ),
    (
        "tests/integration/test_list_authorized_properties_integration.py",
        "test_list_authorized_properties_returns_sorted_domains",
    ),
    # tests/integration/test_media_buy_readiness.py
    ("tests/integration/test_media_buy_readiness.py", "test_tenant"),
    ("tests/integration/test_media_buy_readiness.py", "test_principal"),
    ("tests/integration/test_media_buy_readiness.py", "test_draft_state_no_packages"),
    ("tests/integration/test_media_buy_readiness.py", "test_needs_creatives_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_needs_approval_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_scheduled_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_live_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_completed_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_tenant_readiness_summary"),
    # tests/integration/test_media_buy_repository.py
    ("tests/integration/test_media_buy_repository.py", "tenant_a"),
    ("tests/integration/test_media_buy_repository.py", "tenant_b"),
    ("tests/integration/test_media_buy_repository.py", "principal_a"),
    ("tests/integration/test_media_buy_repository.py", "principal_b"),
    ("tests/integration/test_media_buy_repository.py", "seed_data"),
    # tests/integration/test_media_buy_repository_writes.py
    ("tests/integration/test_media_buy_repository_writes.py", "tenant_a"),
    ("tests/integration/test_media_buy_repository_writes.py", "tenant_b"),
    ("tests/integration/test_media_buy_repository_writes.py", "principal_a"),
    ("tests/integration/test_media_buy_repository_writes.py", "principal_b"),
    # tests/integration/test_media_buy_status_scheduler.py
    ("tests/integration/test_media_buy_status_scheduler.py", "_create_test_tenant"),
    ("tests/integration/test_media_buy_status_scheduler.py", "_create_test_principal"),
    ("tests/integration/test_media_buy_status_scheduler.py", "_create_media_buy"),
    ("tests/integration/test_media_buy_status_scheduler.py", "_create_creative"),
    ("tests/integration/test_media_buy_status_scheduler.py", "_create_creative_assignment"),
    # tests/integration/test_media_buy_v3.py
    ("tests/integration/test_media_buy_v3.py", "mb_creatives"),
    ("tests/integration/test_media_buy_v3.py", "test_unsupported_currency_rejected"),
    ("tests/integration/test_media_buy_v3.py", "test_ownership_mismatch_rejected"),
    # tests/integration/test_mock_adapter_publisher_sync.py
    ("tests/integration/test_mock_adapter_publisher_sync.py", "mock_tenant"),
    ("tests/integration/test_mock_adapter_publisher_sync.py", "publisher_partner"),
    # tests/integration/test_mock_ai_per_creative.py
    ("tests/integration/test_mock_ai_per_creative.py", "mock_adapter"),
    # tests/integration/test_pricing_models_integration.py
    ("tests/integration/test_pricing_models_integration.py", "setup_tenant_with_pricing_products"),
    # tests/integration/test_product_delete_with_pricing.py
    ("tests/integration/test_product_delete_with_pricing.py", "test_product_deletion_with_pricing_options"),
    (
        "tests/integration/test_product_delete_with_pricing.py",
        "test_pricing_option_direct_deletion_bypasses_trigger_due_to_cascade",
    ),
    # tests/integration/test_product_deletion_with_trigger.py
    ("tests/integration/test_product_deletion_with_trigger.py", "test_product_deletion_cascades_pricing_options"),
    (
        "tests/integration/test_product_deletion_with_trigger.py",
        "test_trigger_still_blocks_manual_deletion_of_last_pricing_option",
    ),
    ("tests/integration/test_product_deletion_with_trigger.py", "test_product_deletion_with_multiple_pricing_options"),
    # tests/integration/test_product_format_validation.py
    ("tests/integration/test_product_format_validation.py", "tenant_with_prereqs"),
    ("tests/integration/test_product_format_validation.py", "app_client"),
    # tests/integration/test_product_formats_update.py
    ("tests/integration/test_product_formats_update.py", "sample_product"),
    # tests/integration/test_product_multiple_format_ids.py
    ("tests/integration/test_product_multiple_format_ids.py", "test_tenant"),
    ("tests/integration/test_product_multiple_format_ids.py", "test_create_product_with_multiple_format_ids"),
    ("tests/integration/test_product_multiple_format_ids.py", "test_update_product_format_ids_preserves_all_formats"),
    ("tests/integration/test_product_multiple_format_ids.py", "test_product_format_ids_migration_compatibility"),
    # tests/integration/test_product_pricing_options_required.py
    ("tests/integration/test_product_pricing_options_required.py", "test_get_product_catalog_loads_pricing_options"),
    ("tests/integration/test_product_pricing_options_required.py", "test_product_query_with_eager_loading"),
    (
        "tests/integration/test_product_pricing_options_required.py",
        "test_product_without_eager_loading_fails_validation",
    ),
    ("tests/integration/test_product_pricing_options_required.py", "test_create_media_buy_loads_pricing_options"),
    # tests/integration/test_product_principal_access.py
    ("tests/integration/test_product_principal_access.py", "test_product_stores_and_retrieves_allowed_principal_ids"),
    ("tests/integration/test_product_principal_access.py", "test_product_with_null_allowed_principal_ids"),
    ("tests/integration/test_product_principal_access.py", "test_convert_product_includes_allowed_principal_ids"),
    ("tests/integration/test_product_principal_access.py", "test_allowed_principal_ids_excluded_from_serialization"),
    ("tests/integration/test_product_principal_access.py", "test_principal_model_exists_for_access_control"),
    # tests/integration/test_product_v3.py — migrated to factories
    # tests/integration/test_product_with_inventory_profile.py
    ("tests/integration/test_product_with_inventory_profile.py", "test_create_product_with_inventory_profile"),
    (
        "tests/integration/test_product_with_inventory_profile.py",
        "test_product_creation_validates_profile_belongs_to_tenant",
    ),
    # tests/integration/test_self_service_signup.py
    ("tests/integration/test_self_service_signup.py", "test_signup_completion_page_renders"),
    # tests/integration/test_setup_checklist_service.py
    ("tests/integration/test_setup_checklist_service.py", "setup_minimal_tenant"),
    ("tests/integration/test_setup_checklist_service.py", "setup_complete_tenant"),
    ("tests/integration/test_setup_checklist_service.py", "test_progress_calculation"),
    ("tests/integration/test_setup_checklist_service.py", "test_bulk_setup_status_for_multiple_tenants"),
    ("tests/integration/test_setup_checklist_service.py", "test_currency_count_in_details"),
    ("tests/integration/test_setup_checklist_service.py", "test_sso_is_optional_not_critical_in_multi_tenant_mode"),
    ("tests/integration/test_setup_checklist_service.py", "test_ready_for_orders_without_sso_in_multi_tenant_mode"),
    # tests/integration/test_sync_job_model.py
    ("tests/integration/test_sync_job_model.py", "test_sync_job_id_length"),
    # tests/integration/test_targeting_api.py
    ("tests/integration/test_targeting_api.py", "test_get_targeting_data_returns_audience_type"),
    # tests/integration/test_targeting_validation_chain.py
    ("tests/integration/test_targeting_validation_chain.py", "targeting_tenant"),
    # tests/integration/test_targeting_values_endpoint.py
    ("tests/integration/test_targeting_values_endpoint.py", "test_get_targeting_values_endpoint"),
    ("tests/integration/test_targeting_values_endpoint.py", "test_get_targeting_values_empty_result"),
    ("tests/integration/test_targeting_values_endpoint.py", "test_get_targeting_values_tenant_isolation"),
    ("tests/integration/test_targeting_values_endpoint.py", "test_get_targeting_values_requires_auth"),
    # tests/integration/test_tenant_dashboard.py
    ("tests/integration/test_tenant_dashboard.py", "test_dashboard_with_media_buys"),
    ("tests/integration/test_tenant_dashboard.py", "test_dashboard_metrics_calculation"),
    ("tests/integration/test_tenant_dashboard.py", "test_tenant_config_building"),
    ("tests/integration/test_tenant_dashboard.py", "test_dashboard_with_empty_tenant"),
    # tests/integration/test_tenant_isolation_breach_fix.py
    ("tests/integration/test_tenant_isolation_breach_fix.py", "test_cross_tenant_token_rejected"),
    # tests/integration/test_tenant_isolation_fix.py
    ("tests/integration/test_tenant_isolation_fix.py", "test_tenant_isolation_with_subdomain_and_cross_tenant_token"),
    ("tests/integration/test_tenant_isolation_fix.py", "test_global_token_lookup_sets_tenant_from_principal"),
    ("tests/integration/test_tenant_isolation_fix.py", "test_admin_token_with_subdomain_preserves_tenant_context"),
    # tests/integration/test_tenant_management_api_integration.py
    ("tests/integration/test_tenant_management_api_integration.py", "mock_api_key_auth"),
    ("tests/integration/test_tenant_management_api_integration.py", "test_tenant"),
    # tests/integration/test_tenant_settings_comprehensive.py
    ("tests/integration/test_tenant_settings_comprehensive.py", "test_database_queries"),
    # tests/integration/test_tenant_utils.py
    ("tests/integration/test_tenant_utils.py", "test_serialize_tenant_json_fields_are_deserialized"),
    ("tests/integration/test_tenant_utils.py", "test_serialize_tenant_nullable_fields_have_defaults"),
    # tests/integration/test_update_media_buy_creative_assignment.py
    (
        "tests/integration/test_update_media_buy_creative_assignment.py",
        "test_update_media_buy_assigns_creatives_to_package",
    ),
    ("tests/integration/test_update_media_buy_creative_assignment.py", "test_update_media_buy_replaces_creatives"),
    (
        "tests/integration/test_update_media_buy_creative_assignment.py",
        "test_update_media_buy_rejects_missing_creatives",
    ),
    ("tests/integration/test_update_media_buy_creative_assignment.py", "test_creative_assignments_with_weights"),
    ("tests/integration/test_update_media_buy_creative_assignment.py", "test_creative_assignments_replaces_all"),
    # tests/integration/test_update_media_buy_persistence.py
    ("tests/integration/test_update_media_buy_persistence.py", "test_tenant_setup"),
    ("tests/integration/test_update_media_buy_persistence.py", "test_update_media_buy_with_database_persisted_buy"),
    # tests/integration/test_workflow_lifecycle.py
    ("tests/integration/test_workflow_lifecycle.py", "setup"),
    # tests/integration/conftest.py
    ("tests/integration/conftest.py", "sample_tenant"),
    ("tests/integration/conftest.py", "sample_principal"),
    ("tests/integration/conftest.py", "add_required_setup_data"),
    ("tests/integration/conftest.py", "create_test_product_with_pricing"),
    ("tests/integration/conftest.py", "authenticated_admin_session"),
    ("tests/integration/conftest.py", "test_tenant_with_data"),
    # tests/integration/test_a2a_error_responses.py
    ("tests/integration/test_a2a_error_responses.py", "test_tenant"),
    ("tests/integration/test_a2a_error_responses.py", "test_principal"),
    # tests/integration/test_a2a_skill_invocation.py
    ("tests/integration/test_a2a_skill_invocation.py", "test_update_media_buy_skill"),
    ("tests/integration/test_a2a_skill_invocation.py", "test_list_authorized_properties_skill"),
    # tests/integration/test_admin_ui_data_validation.py
    ("tests/integration/test_admin_ui_data_validation.py", "test_products_list_no_duplicates_with_pricing_options"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_principals_list_no_duplicates_with_relationships"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_inventory_browser_no_duplicate_ad_units"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_dashboard_media_buy_count_accurate"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_media_buys_list_no_duplicates_with_packages"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_media_buys_list_shows_all_statuses"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_workflows_list_no_duplicate_steps"),
    # tests/integration/test_create_media_buy_roundtrip.py
    ("tests/integration/test_create_media_buy_roundtrip.py", "setup_test_tenant"),
    # tests/integration/test_create_media_buy_v24.py
    ("tests/integration/test_create_media_buy_v24.py", "setup_test_tenant"),
    # tests/integration/test_creative_lifecycle_mcp.py
    ("tests/integration/test_creative_lifecycle_mcp.py", "setup_test_data"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_sync_creatives_upsert_existing_creative"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_list_creatives_with_media_buy_assignments"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_validate_creatives_missing_required_fields"),
    # tests/integration/test_error_paths.py
    ("tests/integration/test_error_paths.py", "test_tenant_minimal"),
    ("tests/integration/test_error_paths.py", "test_tenant_with_principal"),
    # tests/integration/test_gam_automation_focused.py
    ("tests/integration/test_gam_automation_focused.py", "test_tenant_data"),
    # tests/integration/test_get_products_database_integration.py — migrated to factories
    # tests/integration/test_get_products_filters.py
    # tests/integration/test_get_products_filters.py — migrated to factories
    # tests/integration/test_get_products_format_id_filter.py — migrated to factories
    # tests/integration/test_mcp_endpoints_comprehensive.py
    ("tests/integration/test_mcp_endpoints_comprehensive.py", "setup_test_data"),
    # tests/integration/test_mcp_tool_roundtrip_validation.py
    ("tests/integration/test_mcp_tool_roundtrip_validation.py", "test_tenant_id"),
    # tests/integration/test_mcp_tools_audit.py
    ("tests/integration/test_mcp_tools_audit.py", "test_tenant_id"),
    ("tests/integration/test_mcp_tools_audit.py", "test_get_media_buy_delivery_roundtrip_safety"),
    # tests/integration/test_minimum_spend_validation.py
    ("tests/integration/test_minimum_spend_validation.py", "setup_test_data"),
    ("tests/integration/test_minimum_spend_validation.py", "test_no_minimum_when_not_set"),
    # tests/integration/test_pricing_helpers.py
    ("tests/integration/test_pricing_helpers.py", "test_create_product_with_cpm_pricing"),
    ("tests/integration/test_pricing_helpers.py", "test_create_auction_product"),
    ("tests/integration/test_pricing_helpers.py", "test_create_flat_rate_product"),
    ("tests/integration/test_pricing_helpers.py", "test_auto_generated_product_id"),
    ("tests/integration/test_pricing_helpers.py", "test_multiple_products_with_pricing"),
    # tests/integration/test_product_deletion.py (test_tenant_and_products migrated to factories)
    ("tests/integration/test_product_deletion.py", "setup_super_admin_config"),
    ("tests/integration/test_product_deletion.py", "test_delete_product_with_active_media_buy"),
    ("tests/integration/test_product_deletion.py", "test_delete_product_with_pending_media_buy"),
    ("tests/integration/test_product_deletion.py", "test_delete_product_with_completed_media_buy_allowed"),
    ("tests/integration/test_product_deletion.py", "test_delete_multiple_products_different_statuses"),
    # tests/integration/test_schema_database_mapping.py
    ("tests/integration/test_schema_database_mapping.py", "test_database_field_access_validation"),
    ("tests/integration/test_schema_database_mapping.py", "test_schema_to_database_conversion_safety"),
    ("tests/integration/test_schema_database_mapping.py", "test_database_json_field_handling"),
    ("tests/integration/test_schema_database_mapping.py", "test_schema_validation_with_database_data"),
    # tests/integration/test_session_json_validation.py
    ("tests/integration/test_session_json_validation.py", "test_context_manager_pattern"),
    ("tests/integration/test_session_json_validation.py", "test_get_or_404"),
    ("tests/integration/test_session_json_validation.py", "test_model_json_validation"),
    ("tests/integration/test_session_json_validation.py", "test_principal_platform_mappings"),
    ("tests/integration/test_session_json_validation.py", "test_workflow_step_comments"),
    # tests/integration/test_tool_result_format.py
    ("tests/integration/test_tool_result_format.py", "setup_test_data"),
    # tests/integration/test_creative_formats_aggregation.py
    ("tests/integration/test_creative_formats_aggregation.py", "test_broadstreet_formats_merged_with_agent_formats"),
    ("tests/integration/test_creative_formats_aggregation.py", "test_broadstreet_formats_are_non_standard"),
    # tests/integration/test_creative_formats_validation_a.py
    ("tests/integration/test_creative_formats_validation_a.py", "test_broadstreet_formats_merged_into_response"),
    ("tests/integration/test_creative_formats_validation_a.py", "test_broadstreet_formats_have_correct_structure"),
    ("tests/integration/test_creative_formats_validation_a.py", "test_non_broadstreet_adapter_no_extra_formats"),
    # tests/integration/test_dynamic_products.py
    ("tests/integration/test_dynamic_products.py", "_ensure_tenant"),
    ("tests/integration/test_dynamic_products.py", "_create_dynamic_template"),
    ("tests/integration/test_dynamic_products.py", "test_expired_variants_archived"),
    ("tests/integration/test_dynamic_products.py", "test_non_expired_variants_untouched"),
    ("tests/integration/test_dynamic_products.py", "test_already_archived_not_rearchived"),
    ("tests/integration/test_dynamic_products.py", "test_tenant_filter_scoping"),
    ("tests/integration/test_dynamic_products.py", "test_no_tenant_archives_all"),
}


def _find_impl_functions_with_db_session(file_path: str) -> list[tuple[str, str, int]]:
    """Find _impl functions that call get_db_session() directly.

    Returns list of (file_path, function_name, line_number).
    """
    source_path = ROOT / file_path
    if not source_path.exists():
        return []

    tree = ast.parse(source_path.read_text())
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Check all calls inside this function for get_db_session
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                # Match: get_db_session()
                if isinstance(func, ast.Name) and func.id == "get_db_session":
                    violations.append((file_path, node.name, child.lineno))
                    break  # One violation per function is enough
                # Match: database_session.get_db_session()
                if isinstance(func, ast.Attribute) and func.attr == "get_db_session":
                    violations.append((file_path, node.name, child.lineno))
                    break

    return violations


def _find_session_add_in_tests(file_path: str) -> list[tuple[str, str, int]]:
    """Find test functions/fixtures that call session.add() directly.

    Returns list of (file_path, function_name, line_number).
    """
    source_path = ROOT / file_path
    if not source_path.exists():
        return []

    tree = ast.parse(source_path.read_text())
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                # Match: session.add(...) or *.add(...)
                if isinstance(func, ast.Attribute) and func.attr == "add":
                    # Check it's likely a session (common var names)
                    if isinstance(func.value, ast.Name) and func.value.id in (
                        "session",
                        "db_session",
                        "mock_session",
                        "s",
                    ):
                        violations.append((file_path, node.name, child.lineno))
                        break  # One violation per function is enough

    return violations


class TestImplNoDirectDbSession:
    """_impl functions must not call get_db_session() directly.

    Data access belongs in repository classes. _impl functions receive
    repositories and call typed methods, not raw session operations.
    """

    def test_no_new_get_db_session_in_impl(self):
        """No _impl function calls get_db_session() outside the allowlist."""
        all_violations = []
        for file_path in IMPL_FILES:
            all_violations.extend(_find_impl_functions_with_db_session(file_path))

        new_violations = [(f, fn, line) for f, fn, line in all_violations if (f, fn) not in IMPL_SESSION_ALLOWLIST]

        if new_violations:
            msg_lines = [
                "New get_db_session() calls in business logic (use repository pattern instead):",
                "",
            ]
            for f, fn, line in new_violations:
                msg_lines.append(f"  {f}:{line} in {fn}()")
            msg_lines.append("")
            msg_lines.append(
                "Fix: Move DB access to a repository class. See CLAUDE.md Pattern #3 for the repository pattern."
            )
            raise AssertionError("\n".join(msg_lines))

    def test_allowlist_entries_still_exist(self):
        """Every allowlisted violation must still exist (stale entry detection)."""
        all_violations = set()
        for file_path in IMPL_FILES:
            for f, fn, _line in _find_impl_functions_with_db_session(file_path):
                all_violations.add((f, fn))

        stale = IMPL_SESSION_ALLOWLIST - all_violations
        if stale:
            msg_lines = [
                "Stale allowlist entries (violation was fixed — remove from allowlist):",
                "",
            ]
            for f, fn in sorted(stale):
                msg_lines.append(f"  ({f!r}, {fn!r}),")
            raise AssertionError("\n".join(msg_lines))


class TestIntegrationTestsNoInlineSessionAdd:
    """Integration tests must use factories/fixtures, not inline session.add().

    Test data setup belongs in polyfactory-based fixtures defined in conftest.py,
    not scattered across test bodies as raw ORM model construction.
    """

    def test_no_new_session_add_in_tests(self):
        """No test function calls session.add() outside the allowlist."""
        all_violations = []
        for file_path in INTEGRATION_TEST_FILES:
            all_violations.extend(_find_session_add_in_tests(file_path))

        new_violations = [
            (f, fn, line) for f, fn, line in all_violations if (f, fn) not in INTEGRATION_SESSION_ADD_ALLOWLIST
        ]

        if new_violations:
            msg_lines = [
                "New session.add() calls in integration tests (use factories instead):",
                "",
            ]
            for f, fn, line in new_violations:
                msg_lines.append(f"  {f}:{line} in {fn}()")
            msg_lines.append("")
            msg_lines.append(
                "Fix: Use a polyfactory fixture instead of inline model construction. "
                "See CLAUDE.md Pattern #8 for the factory pattern."
            )
            raise AssertionError("\n".join(msg_lines))

    def test_allowlist_entries_still_exist(self):
        """Every allowlisted violation must still exist (stale entry detection)."""
        all_violations = set()
        for file_path in INTEGRATION_TEST_FILES:
            for f, fn, _line in _find_session_add_in_tests(file_path):
                all_violations.add((f, fn))

        stale = INTEGRATION_SESSION_ADD_ALLOWLIST - all_violations
        if stale:
            msg_lines = [
                "Stale allowlist entries (violation was fixed — remove from allowlist):",
                "",
            ]
            for f, fn in sorted(stale):
                msg_lines.append(f"  ({f!r}, {fn!r}),")
            raise AssertionError("\n".join(msg_lines))
