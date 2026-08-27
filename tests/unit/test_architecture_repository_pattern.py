"""Guard: Business logic uses repositories, not inline DB access.

Three invariants:
1. _impl functions must not call get_db_session() — data access belongs in repositories
2. Integration test bodies must not call session.add() — use factories or fixtures
3. Integration test bodies must not call get_db_session() — use factories or the harness UoW

Scanning approach: AST — parse source files for function calls matching prohibited
patterns. All pre-existing violations are allowlisted; new code fails immediately.

beads: salesagent-qo8a (repository pattern enforcement)
"""

import ast
import glob
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist, iter_call_expressions

ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Invariant 1: No get_db_session() in _impl functions
# ---------------------------------------------------------------------------

# Directories scanned by discovery glob (not a hand-maintained file list) for
# _impl-adjacent get_db_session() calls. The hand-maintained list this replaced
# omitted accounts.py (the largest new tools module, R1-5/R1-6) entirely and
# never scanned helpers/ at all -- making a session-opening helper one call
# frame from _impl invisible to this guard (the guard even taught the
# workaround: adapter_helpers.py's _read_mock_test_behavior docstring used to
# describe the loophole as the sanctioned seam). #1721 M2.
_IMPL_DISCOVERY_DIRS = ("src/core/tools", "src/core/helpers")

# Two files outside the discovery dirs were already in the pre-glob list.
# Kept explicit so widening scan scope never NARROWS it for these.
_IMPL_LEGACY_EXTRA_FILES = frozenset({"src/core/context_manager.py", "src/admin/blueprints/creatives.py"})


def _glob_python_files(root: Path, dirs: tuple[str, ...]) -> set[str]:
    """``.py`` files under any of ``dirs`` (relative to ``root``), pycache excluded."""
    return {
        str(p.relative_to(root))
        for scan_dir in dirs
        for p in (root / scan_dir).rglob("*.py")
        if "__pycache__" not in str(p)
    }


def _discover_impl_files() -> list[str]:
    """_impl-adjacent files scanned for direct get_db_session() calls."""
    return sorted(_glob_python_files(ROOT, _IMPL_DISCOVERY_DIRS) | _IMPL_LEGACY_EXTRA_FILES)


# Production files that contain _impl functions to scan
IMPL_FILES = _discover_impl_files()

# Pre-existing violations: (file_path, function_name)
# These existed before the guard was created. Allowlist shrinks as repositories are introduced.
# FIXME(salesagent-qo8a): all _impl functions should use repositories instead of get_db_session()
IMPL_SESSION_ALLOWLIST: set[tuple[str, str]] = set()

# ---------------------------------------------------------------------------
# Invariant 2: No session.add() in integration test bodies
# ---------------------------------------------------------------------------


def _discover_integration_test_files() -> list[str]:
    """Dynamically discover all DB-backed test files via glob.

    Scans tests/integration*/, tests/admin/, and tests/e2e/ for test_*.py and
    conftest.py files. These suites all exercise real DB state and must use
    factories, not inline session.add() / get_db_session() in test bodies.

    Also scans every module under tests/helpers/. Shared DB-seed helpers there are
    not named test_*.py but must follow the same factory-only rule, so that new
    session.add() debt in helper code is caught at the source rather than hidden
    behind a module the guard never reads.
    """
    roots = ("tests/integration*", "tests/admin", "tests/e2e")
    test_files: list[str] = []
    conftest_files: list[str] = []
    for root in roots:
        test_files.extend(glob.glob(f"{root}/**/test_*.py", recursive=True))
        conftest_files.extend(glob.glob(f"{root}/conftest.py", recursive=True))
    helper_files = glob.glob("tests/helpers/**/*.py", recursive=True)
    return sorted(set(test_files + conftest_files + helper_files))


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
    (
        "tests/integration/test_media_buy_repository.py",
        "test_find_by_idempotency_key_returns_existing",
    ),  # FIXME(#1203): repo test uses make_media_buy helper
    (
        "tests/integration/test_media_buy_repository.py",
        "test_idempotency_key_scoped_to_tenant",
    ),  # FIXME(#1203): repo test uses make_media_buy helper
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
    # tests/integration/test_gam_automation_focused.py
    ("tests/integration/test_gam_automation_focused.py", "test_tenant_data"),
    # tests/integration/test_get_products_database_integration.py — migrated to factories
    # tests/integration/test_get_products_filters.py
    # tests/integration/test_get_products_filters.py — migrated to factories
    # tests/integration/test_get_products_format_id_filter.py — migrated to factories
    # tests/integration/test_mcp_endpoints_comprehensive.py — requires_server suite removed (#1233 D11)
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
    # tests/integration/test_tool_result_format.py — deleted (#1233 D11)
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
    # ── tests/admin/ — pre-existing violations from admin blueprint tests ──
    # FIXME(salesagent-e2e-admin-factories): migrate admin blueprint tests to factories.
    # Needs AuthorizedPropertyFactory, WorkflowStepFactory, ContextFactory; existing
    # TenantFactory/PrincipalFactory/CreativeFactory/InventoryProfileFactory/PropertyTagFactory
    # can be reused. Endpoint assertions don't change — only the setup.
    # tests/admin/test_accounts_blueprint.py
    ("tests/admin/test_accounts_blueprint.py", "test_tenant"),
    ("tests/admin/test_accounts_blueprint.py", "test_list_page_shows_created_account"),
    ("tests/admin/test_accounts_blueprint.py", "test_suspend_account"),
    # tests/admin/test_authorized_properties.py
    ("tests/admin/test_authorized_properties.py", "test_tenant"),
    ("tests/admin/test_authorized_properties.py", "test_list_page_shows_existing_property"),
    ("tests/admin/test_authorized_properties.py", "test_delete_property_removes_from_db"),
    # tests/admin/test_creatives_blueprint.py
    ("tests/admin/test_creatives_blueprint.py", "test_tenant"),
    ("tests/admin/test_creatives_blueprint.py", "_create_creative"),
    # tests/admin/test_inventory_profiles.py
    ("tests/admin/test_inventory_profiles.py", "test_tenant"),
    ("tests/admin/test_inventory_profiles.py", "_create_sample_profile"),
    # tests/admin/test_product_creation_integration.py
    ("tests/admin/test_product_creation_integration.py", "test_tenant"),
    ("tests/admin/test_product_creation_integration.py", "test_add_product_json_encoding"),
    ("tests/admin/test_product_creation_integration.py", "test_add_product_empty_json_fields"),
    ("tests/admin/test_product_creation_integration.py", "test_add_product_postgresql_validation"),
    ("tests/admin/test_product_creation_integration.py", "test_list_products_json_parsing"),
    # tests/admin/test_workflows_blueprint.py
    ("tests/admin/test_workflows_blueprint.py", "test_tenant"),
    ("tests/admin/test_workflows_blueprint.py", "_create_context_and_step"),
    # ── tests/e2e/ — pre-existing violations from e2e lifecycle test ──
    # FIXME(salesagent-e2e-admin-factories): migrate e2e seed helpers to factories.
    ("tests/e2e/test_gam_lifecycle.py", "_seed_lifecycle_test_data"),
    ("tests/e2e/test_gam_lifecycle.py", "_persist_media_buy"),
}


# ── Shared detection + assertion core (DRY across all three invariants) ──
def _find_matching_calls(file_path: str, predicate: Callable[[ast.expr], bool]) -> list[tuple[str, str, int]]:
    """Find functions where any call's ``.func`` satisfies ``predicate``.

    Returns list of (file_path, function_name, line_number) — one per function.
    """
    source_path = ROOT / file_path
    if not source_path.exists():
        return []

    tree = ast.parse(source_path.read_text())
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in iter_call_expressions(node):
            if predicate(child.func):
                violations.append((file_path, node.name, child.lineno))
                break  # One violation per function is enough
    return violations


def _is_get_db_session_call(func: ast.expr) -> bool:
    """Match ``get_db_session()`` or ``database_session.get_db_session()``."""
    if isinstance(func, ast.Name):
        return func.id == "get_db_session"
    return isinstance(func, ast.Attribute) and func.attr == "get_db_session"


def _is_session_add_call(func: ast.expr) -> bool:
    """Match ``session.add(...)`` on a session-like variable."""
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "add"
        and isinstance(func.value, ast.Name)
        and func.value.id in ("session", "db_session", "mock_session", "s")
    )


def _assert_no_new_violations(
    finder: Callable[[str], list[tuple[str, str, int]]],
    files: list[str],
    allowlist: set[tuple[str, str]],
    *,
    header: str,
    fix_hint: str,
) -> None:
    """Raise if any (file, func) violation from ``finder`` is outside ``allowlist``."""
    new_violations = [
        (f, fn, line) for file_path in files for f, fn, line in finder(file_path) if (f, fn) not in allowlist
    ]
    if new_violations:
        msg_lines = [header, ""]
        msg_lines += [f"  {f}:{line} in {fn}()" for f, fn, line in new_violations]
        msg_lines += ["", fix_hint]
        raise AssertionError("\n".join(msg_lines))


def _assert_allowlist_current(
    finder: Callable[[str], list[tuple[str, str, int]]],
    files: list[str],
    allowlist: set[tuple[str, str]],
    *,
    fix_hint: str,
) -> None:
    """Raise if the live violation set differs from ``allowlist`` (stale-entry detection)."""
    found = {(f, fn) for file_path in files for f, fn, _line in finder(file_path)}
    assert_violations_match_allowlist(found, allowlist, fix_hint=fix_hint)


def _find_impl_functions_with_db_session(file_path: str) -> list[tuple[str, str, int]]:
    """Find _impl functions that call get_db_session() directly."""
    return _find_matching_calls(file_path, _is_get_db_session_call)


def _find_session_add_in_tests(file_path: str) -> list[tuple[str, str, int]]:
    """Find test functions/fixtures that call session.add() directly."""
    return _find_matching_calls(file_path, _is_session_add_call)


class TestImplNoDirectDbSession:
    """_impl functions must not call get_db_session() directly.

    Data access belongs in repository classes. _impl functions receive
    repositories and call typed methods, not raw session operations.
    """

    @pytest.mark.arch_guard
    def test_no_new_get_db_session_in_impl(self):
        """No _impl function calls get_db_session() outside the allowlist."""
        _assert_no_new_violations(
            _find_impl_functions_with_db_session,
            IMPL_FILES,
            IMPL_SESSION_ALLOWLIST,
            header="New get_db_session() calls in business logic (use repository pattern instead):",
            fix_hint="Fix: Move DB access to a repository class. See CLAUDE.md Pattern #3 for the repository pattern.",
        )

    @pytest.mark.arch_guard
    def test_allowlist_entries_still_exist(self):
        """Every allowlisted violation must still exist (stale entry detection)."""
        _assert_allowlist_current(
            _find_impl_functions_with_db_session,
            IMPL_FILES,
            IMPL_SESSION_ALLOWLIST,
            fix_hint="Remove fixed entries from IMPL_SESSION_ALLOWLIST.",
        )

    @pytest.mark.arch_guard
    def test_discovery_glob_catches_a_synthetic_new_file(self, tmp_path):
        """Guard self-test: the discovery glob picks up a file it has never seen
        before, proving it is a LIVE glob (re-evaluated every run) and not a
        frozen snapshot masquerading as one -- the exact failure mode of the
        hand-maintained list this replaced (#1721 M2)."""
        scan_dir = tmp_path / "src" / "core" / "tools"
        scan_dir.mkdir(parents=True)
        (scan_dir / "_never_seen_before.py").write_text("def f():\n    pass\n")
        found = _glob_python_files(tmp_path, ("src/core/tools",))
        assert "src/core/tools/_never_seen_before.py" in found

    @pytest.mark.arch_guard
    def test_discovery_glob_covers_the_files_the_old_list_missed(self):
        """accounts.py and helpers/ were invisible to the hand-maintained
        IMPL_FILES list (R1-5/R1-6) -- confirm the discovery glob now sees them."""
        assert "src/core/tools/accounts.py" in IMPL_FILES
        assert "src/core/helpers/adapter_helpers.py" in IMPL_FILES
        assert "src/core/helpers/activity_helpers.py" in IMPL_FILES


class TestIntegrationTestsNoInlineSessionAdd:
    """Integration tests must use factories/fixtures, not inline session.add().

    Test data setup belongs in polyfactory-based fixtures defined in conftest.py,
    not scattered across test bodies as raw ORM model construction.
    """

    @pytest.mark.arch_guard
    def test_no_new_session_add_in_tests(self):
        """No test function calls session.add() outside the allowlist."""
        _assert_no_new_violations(
            _find_session_add_in_tests,
            INTEGRATION_TEST_FILES,
            INTEGRATION_SESSION_ADD_ALLOWLIST,
            header="New session.add() calls in integration tests (use factories instead):",
            fix_hint=(
                "Fix: Use a polyfactory fixture instead of inline model construction. "
                "See CLAUDE.md Pattern #8 for the factory pattern."
            ),
        )

    @pytest.mark.arch_guard
    def test_allowlist_entries_still_exist(self):
        """Every allowlisted violation must still exist (stale entry detection)."""
        _assert_allowlist_current(
            _find_session_add_in_tests,
            INTEGRATION_TEST_FILES,
            INTEGRATION_SESSION_ADD_ALLOWLIST,
            fix_hint="Remove fixed entries from INTEGRATION_SESSION_ADD_ALLOWLIST.",
        )


# ─────────────────────────────────────────────────────────────────────────
# Invariant 3: No get_db_session() in integration test bodies (#1417)
# ─────────────────────────────────────────────────────────────────────────
# Invariant 1 scans get_db_session() only in src/ (_impl) files, so a
# get_db_session() opened in a NEW test function inside an EXISTING test file
# slipped through (e.g. test_resolve_account.py's new natural-key test). This
# invariant closes that gap: it scans the same test scope as Invariant 2 and
# flags any get_db_session() in a test/fixture body outside the legacy
# allowlist. DB access in tests belongs in factories / the harness UoW
# (e.g. `with AccountUoW(...) as uow: uow.accounts`), never a raw inline session.
GET_DB_SESSION_IN_TESTS_ALLOWLIST: set[tuple[str, str]] = {
    ("tests/admin/test_accounts_blueprint.py", "test_create_account_via_post"),
    ("tests/admin/test_accounts_blueprint.py", "test_list_page_shows_created_account"),
    ("tests/admin/test_accounts_blueprint.py", "test_suspend_account"),
    ("tests/admin/test_accounts_blueprint.py", "test_tenant"),
    ("tests/admin/test_authorized_properties.py", "test_create_property_missing_required_fields_redirects"),
    ("tests/admin/test_authorized_properties.py", "test_create_property_saves_to_db"),
    ("tests/admin/test_authorized_properties.py", "test_create_tag_missing_fields_redirects_without_creation"),
    ("tests/admin/test_authorized_properties.py", "test_create_tag_saves_to_db"),
    ("tests/admin/test_authorized_properties.py", "test_delete_property_removes_from_db"),
    ("tests/admin/test_authorized_properties.py", "test_list_page_shows_existing_property"),
    ("tests/admin/test_authorized_properties.py", "test_tenant"),
    ("tests/admin/test_creatives_blueprint.py", "_create_creative"),
    ("tests/admin/test_creatives_blueprint.py", "test_approve_creates_review_record"),
    ("tests/admin/test_creatives_blueprint.py", "test_approve_creative_sets_status_approved"),
    ("tests/admin/test_creatives_blueprint.py", "test_reject_creative_sets_status_rejected"),
    ("tests/admin/test_creatives_blueprint.py", "test_tenant"),
    ("tests/admin/test_inventory_profiles.py", "_create_sample_profile"),
    ("tests/admin/test_inventory_profiles.py", "test_create_profile_missing_formats_redirects_without_creation"),
    ("tests/admin/test_inventory_profiles.py", "test_create_profile_missing_name_redirects_without_creation"),
    ("tests/admin/test_inventory_profiles.py", "test_create_profile_with_tags_saves_to_db"),
    ("tests/admin/test_inventory_profiles.py", "test_delete_profile_removes_from_db"),
    ("tests/admin/test_inventory_profiles.py", "test_tenant"),
    ("tests/admin/test_product_creation_integration.py", "test_add_product_empty_json_fields"),
    ("tests/admin/test_product_creation_integration.py", "test_add_product_json_encoding"),
    ("tests/admin/test_product_creation_integration.py", "test_add_product_postgresql_validation"),
    ("tests/admin/test_product_creation_integration.py", "test_list_products_json_parsing"),
    ("tests/admin/test_product_creation_integration.py", "test_tenant"),
    ("tests/admin/test_workflows_blueprint.py", "_create_context_and_step"),
    ("tests/admin/test_workflows_blueprint.py", "test_approve_step_sets_status_approved"),
    ("tests/admin/test_workflows_blueprint.py", "test_reject_step_sets_status_rejected"),
    ("tests/admin/test_workflows_blueprint.py", "test_reject_step_without_reason_uses_default"),
    ("tests/admin/test_workflows_blueprint.py", "test_tenant"),
    ("tests/e2e/test_gam_lifecycle.py", "_persist_media_buy"),
    ("tests/e2e/test_gam_lifecycle.py", "_seed_lifecycle_test_data"),
    ("tests/helpers/creative_test_helpers.py", "assert_stored_creative_assets"),
    ("tests/integration/conftest.py", "authenticated_admin_session"),
    ("tests/integration/conftest.py", "cleanup_tenant"),
    ("tests/integration/conftest.py", "sample_principal"),
    ("tests/integration/conftest.py", "sample_products"),
    ("tests/integration/conftest.py", "sample_tenant"),
    ("tests/integration/conftest.py", "test_media_buy_workflow"),
    ("tests/integration/conftest.py", "test_tenant_with_data"),
    ("tests/integration/test_a2a_skill_invocation.py", "test_explicit_skill_create_media_buy_manual_approval"),
    ("tests/integration/test_a2a_skill_invocation.py", "test_list_authorized_properties_skill"),
    ("tests/integration/test_a2a_skill_invocation.py", "test_update_media_buy_skill"),
    ("tests/integration/test_adapter_config_repository.py", "_tenants"),
    ("tests/integration/test_adapter_config_repository.py", "test_find_by_tenant_returns_config"),
    ("tests/integration/test_adapter_config_repository.py", "test_find_by_tenant_returns_none_for_unconfigured"),
    ("tests/integration/test_adapter_config_repository.py", "test_get_adapter_type_oauth"),
    ("tests/integration/test_adapter_config_repository.py", "test_get_adapter_type_unconfigured"),
    ("tests/integration/test_adapter_config_repository.py", "test_get_by_tenant_raises_for_unconfigured"),
    ("tests/integration/test_adapter_config_repository.py", "test_get_by_tenant_returns_config"),
    ("tests/integration/test_adapter_config_repository.py", "test_get_gam_config_oauth"),
    ("tests/integration/test_adapter_config_repository.py", "test_get_gam_config_raises_for_non_gam"),
    ("tests/integration/test_adapter_config_repository.py", "test_get_gam_config_service_account"),
    ("tests/integration/test_adapter_config_repository.py", "test_get_gam_naming_templates"),
    ("tests/integration/test_adapter_config_repository.py", "test_get_gam_naming_templates_none_when_unset"),
    ("tests/integration/test_adapter_config_repository.py", "test_get_gam_targeting_config"),
    ("tests/integration/test_adapter_config_repository.py", "test_has_gam_credentials_false_for_mock"),
    ("tests/integration/test_adapter_config_repository.py", "test_has_gam_credentials_oauth"),
    ("tests/integration/test_adapter_config_repository.py", "test_has_gam_credentials_service_account"),
    ("tests/integration/test_adapter_config_repository.py", "test_update_custom_targeting_keys_raises_when_missing"),
    ("tests/integration/test_adapter_factory.py", "setup_adapters"),
    ("tests/integration/test_adapter_factory.py", "test_gam_adapter_requires_network_code"),
    ("tests/integration/test_adapter_factory.py", "test_get_adapter_instantiates_all_adapter_types"),
    ("tests/integration/test_admin_ui_data_validation.py", "_bind_factories_for_sizes"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_dashboard_media_buy_count_accurate"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_inventory_browser_no_duplicate_ad_units"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_media_buys_list_no_duplicates_with_packages"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_media_buys_list_shows_all_statuses"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_principals_list_no_duplicates_with_relationships"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_products_list_no_duplicates_with_pricing_options"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_products_list_shows_all_products"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_products_list_with_single_pricing_option"),
    ("tests/integration/test_admin_ui_data_validation.py", "test_workflows_list_no_duplicate_steps"),
    ("tests/integration/test_admin_ui_pages.py", "test_cannot_access_other_tenant_data"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_extracts_custom_details"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_filters_password_fields"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_filters_sensitive_json_fields"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_handles_missing_session"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_logs_failed_actions"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_logs_successful_action"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_skips_logging_without_tenant_id"),
    ("tests/integration/test_audit_decorator.py", "test_decorator_truncates_long_values"),
    ("tests/integration/test_context_persistence.py", "test_simplified_context"),
    ("tests/integration/test_create_media_buy_roundtrip.py", "setup_test_tenant"),
    ("tests/integration/test_create_media_buy_v24.py", "setup_test_tenant"),
    ("tests/integration/test_creative_assignment_principal_id.py", "_query_assignments"),
    ("tests/integration/test_creative_assignment_principal_id.py", "ca_creatives"),
    ("tests/integration/test_creative_assignment_principal_id.py", "ca_tenant_with_approval"),
    (
        "tests/integration/test_creative_assignment_principal_id.py",
        "test_assignment_has_principal_id_on_update_creative_ids",
    ),
    ("tests/integration/test_creative_async_lifecycle_obligations.py", "test_async_input_required_response"),
    ("tests/integration/test_creative_async_lifecycle_obligations.py", "test_async_submitted_response"),
    ("tests/integration/test_creative_async_lifecycle_obligations.py", "test_async_working_response"),
    ("tests/integration/test_creative_formats_aggregation.py", "test_broadstreet_formats_are_non_standard"),
    ("tests/integration/test_creative_formats_aggregation.py", "test_broadstreet_formats_merged_with_agent_formats"),
    ("tests/integration/test_creative_formats_validation_a.py", "test_broadstreet_formats_have_correct_structure"),
    ("tests/integration/test_creative_formats_validation_a.py", "test_broadstreet_formats_merged_into_response"),
    ("tests/integration/test_creative_formats_validation_a.py", "test_non_broadstreet_adapter_no_extra_formats"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "setup_test_data"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_create_media_buy_with_creative_ids"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_list_creatives_no_filters"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_list_creatives_pagination_and_sorting"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_list_creatives_with_date_filters"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_list_creatives_with_format_filter"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_list_creatives_with_media_buy_assignments"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_list_creatives_with_search"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_list_creatives_with_status_filter"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_sync_creatives_create_new_creatives"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_sync_creatives_upsert_existing_creative"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_sync_creatives_validation_failures"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_sync_creatives_with_assignments_lookup"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_sync_creatives_with_package_assignments"),
    ("tests/integration/test_creative_lifecycle_mcp.py", "test_validate_creatives_missing_required_fields"),
    ("tests/integration/test_creative_review_model.py", "test_get_ai_review_stats_empty"),
    ("tests/integration/test_creative_review_model.py", "test_get_creative_reviews_filters_by_review_type"),
    ("tests/integration/test_creative_review_model.py", "test_get_creative_reviews_query"),
    ("tests/integration/test_creative_review_model.py", "test_get_creative_reviews_tenant_isolation"),
    ("tests/integration/test_creative_review_model.py", "test_get_creative_with_latest_review_tenant_isolation"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_assignment_persists_to_db"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_auto_approve_sets_approved_status"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_creative_visible_only_to_owning_principal"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_default_approval_mode_is_require_human"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_delete_missing_archives_unlisted_creatives"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_delete_missing_false_preserves_unlisted"),
    (
        "tests/integration/test_creative_sync_behavioral.py",
        "test_draft_with_approved_at_transitions_to_pending_creatives",
    ),
    ("tests/integration/test_creative_sync_behavioral.py", "test_draft_without_approved_at_stays_draft"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_dry_run_does_not_persist"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_existing_creative_updates_in_place"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_idempotent_assignment_upsert"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_lenient_savepoint_isolation_with_real_db"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_new_creative_creates_db_record"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_new_creative_stamped_with_correct_principal"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_non_draft_status_unchanged"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_none_assignments_produces_no_records"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_require_human_sets_pending_review"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_same_creative_id_different_principals_are_separate"),
    ("tests/integration/test_creative_sync_behavioral.py", "test_upsert_assignment_still_transitions"),
    ("tests/integration/test_creative_sync_data_preservation.py", "test_generative_output_preserves_user_assets"),
    ("tests/integration/test_creative_sync_data_preservation.py", "test_generative_output_preserves_user_url"),
    (
        "tests/integration/test_creative_sync_data_preservation.py",
        "test_sync_preserves_dimensions_when_preview_has_different_size",
    ),
    (
        "tests/integration/test_creative_sync_data_preservation.py",
        "test_sync_preserves_user_url_when_preview_available",
    ),
    (
        "tests/integration/test_creative_sync_data_preservation.py",
        "test_update_preserves_user_url_when_preview_changes",
    ),
    ("tests/integration/test_creative_sync_processing.py", "test_create_ai_powered_submits_task"),
    ("tests/integration/test_creative_sync_processing.py", "test_create_auto_approve_status"),
    ("tests/integration/test_creative_sync_processing.py", "test_create_preview_dimensions_extracted"),
    ("tests/integration/test_creative_sync_processing.py", "test_format_change_detected"),
    ("tests/integration/test_creative_sync_processing.py", "test_update_ai_powered_submits_review"),
    ("tests/integration/test_creative_sync_processing.py", "test_update_auto_approve_sets_approved"),
    ("tests/integration/test_creative_sync_processing.py", "test_update_preview_dimensions_extracted"),
    ("tests/integration/test_creative_sync_processing.py", "test_update_user_assets_not_overwritten"),
    ("tests/integration/test_creative_sync_transport.py", "test_ai_review_submitted"),
    ("tests/integration/test_creative_sync_transport.py", "test_dry_run_does_not_persist"),
    ("tests/integration/test_creative_sync_transport.py", "test_generative_format_calls_build_creative"),
    ("tests/integration/test_creative_sync_transport.py", "test_good_creative_persists_despite_bad_in_batch"),
    ("tests/integration/test_creative_sync_transport.py", "test_new_creative_created"),
    ("tests/integration/test_creative_sync_transport.py", "test_notification_deferred_for_ai_powered"),
    ("tests/integration/test_creative_sync_transport.py", "test_update_without_prompt_skips_build"),
    ("tests/integration/test_creative_sync_transport.py", "test_upsert_existing_creative_reports_updated"),
    ("tests/integration/test_creative_sync_transport.py", "test_user_assets_not_overwritten"),
    ("tests/integration/test_creative_v3.py", "_get_db_status"),
    ("tests/integration/test_creative_v3.py", "setup_tenant"),
    ("tests/integration/test_creative_v3.py", "test_batch_sync_multiple_creatives"),
    ("tests/integration/test_creative_v3.py", "test_creative_lookup_filters_by_principal"),
    ("tests/integration/test_creative_v3.py", "test_draft_with_approved_at_transitions"),
    ("tests/integration/test_creative_v3.py", "test_new_creative_stamped_with_principal_id"),
    ("tests/integration/test_creative_v3.py", "test_same_creative_id_different_principal_creates_new"),
    ("tests/integration/test_creative_v3.py", "test_upsert_by_triple_key"),
    ("tests/integration/test_cross_principal_security.py", "setup_test_data"),
    ("tests/integration/test_cross_principal_security.py", "test_cross_tenant_isolation_also_enforced"),
    (
        "tests/integration/test_cross_principal_security.py",
        "test_update_media_buy_cannot_modify_other_principals_media_buy",
    ),
    ("tests/integration/test_dashboard_integration.py", "test_db"),
    ("tests/integration/test_database_health_integration.py", "test_health_check_performance_with_real_database"),
    ("tests/integration/test_database_health_integration.py", "test_health_check_table_existence_validation"),
    ("tests/integration/test_database_health_integration.py", "test_health_check_with_real_schema_validation"),
    ("tests/integration/test_database_integration.py", "test_settings_queries"),
    ("tests/integration/test_database_timeouts.py", "test_circuit_breaker_fail_fast"),
    ("tests/integration/test_database_timeouts.py", "test_circuit_breaker_recovery"),
    ("tests/integration/test_database_timeouts.py", "test_connection_timeout_configuration"),
    ("tests/integration/test_database_timeouts.py", "test_query_timeout_configuration"),
    ("tests/integration/test_database_timeouts.py", "test_statement_timeout_enforced"),
    ("tests/integration/test_delivery_poll_behavioral.py", "test_get_pricing_options_uses_string_id_not_integer_pk"),
    ("tests/integration/test_delivery_poll_behavioral.py", "test_integer_pk_lookup_returns_none"),
    (
        "tests/integration/test_delivery_poll_behavioral.py",
        "test_non_numeric_pricing_option_id_is_not_silently_discarded",
    ),
    ("tests/integration/test_delivery_poll_behavioral.py", "test_pricing_options_keyed_by_string_id_not_integer_pk"),
    ("tests/integration/test_delivery_repository.py", "_cleanup_tenant"),
    ("tests/integration/test_delivery_repository.py", "media_buy_a"),
    ("tests/integration/test_delivery_repository.py", "media_buy_b"),
    ("tests/integration/test_delivery_repository.py", "principal_a"),
    ("tests/integration/test_delivery_repository.py", "principal_b"),
    ("tests/integration/test_delivery_repository.py", "tenant_a"),
    ("tests/integration/test_delivery_repository.py", "tenant_b"),
    ("tests/integration/test_delivery_repository.py", "test_cannot_update_other_tenant_record"),
    ("tests/integration/test_delivery_repository.py", "test_creates_log_with_all_optional_fields"),
    ("tests/integration/test_delivery_repository.py", "test_creates_log_with_required_fields"),
    ("tests/integration/test_delivery_repository.py", "test_creates_record_with_optional_fields"),
    ("tests/integration/test_delivery_repository.py", "test_creates_record_with_required_fields"),
    ("tests/integration/test_delivery_repository.py", "test_does_not_return_other_tenant_record"),
    ("tests/integration/test_delivery_repository.py", "test_excludes_other_tenant"),
    ("tests/integration/test_delivery_repository.py", "test_filters_by_event_type"),
    ("tests/integration/test_delivery_repository.py", "test_filters_by_status"),
    ("tests/integration/test_delivery_repository.py", "test_filters_by_task_type"),
    ("tests/integration/test_delivery_repository.py", "test_finds_recent_successful_log"),
    ("tests/integration/test_delivery_repository.py", "test_ignores_failed_logs"),
    ("tests/integration/test_delivery_repository.py", "test_lists_all_tenant_records"),
    ("tests/integration/test_delivery_repository.py", "test_respects_limit"),
    ("tests/integration/test_delivery_repository.py", "test_returns_logs_for_media_buy"),
    ("tests/integration/test_delivery_repository.py", "test_returns_max_sequence"),
    ("tests/integration/test_delivery_repository.py", "test_returns_none_for_nonexistent"),
    ("tests/integration/test_delivery_repository.py", "test_returns_none_when_no_recent_log"),
    ("tests/integration/test_delivery_repository.py", "test_returns_own_tenant_record"),
    ("tests/integration/test_delivery_repository.py", "test_returns_zero_when_no_logs"),
    ("tests/integration/test_delivery_repository.py", "test_scoped_to_task_type"),
    ("tests/integration/test_delivery_repository.py", "test_tenant_isolation"),
    ("tests/integration/test_delivery_repository.py", "test_updates_error_fields"),
    ("tests/integration/test_delivery_repository.py", "test_updates_status_and_attempts"),
    ("tests/integration/test_delivery_repository.py", "test_upsert_updates_existing_log"),
    ("tests/integration/test_delivery_simulator_restart.py", "test_principal"),
    ("tests/integration/test_delivery_simulator_restart.py", "test_product"),
    ("tests/integration/test_delivery_simulator_restart.py", "test_restart_finds_media_buys_with_principal_webhook"),
    ("tests/integration/test_delivery_simulator_restart.py", "test_restart_ignores_media_buys_without_webhook"),
    ("tests/integration/test_delivery_simulator_restart.py", "test_restart_join_cardinality"),
    ("tests/integration/test_delivery_simulator_restart.py", "test_tenant"),
    ("tests/integration/test_delivery_simulator_restart.py", "test_webhook_config"),
    ("tests/integration/test_delivery_v3.py", "test_default_filter_active_only"),
    ("tests/integration/test_delivery_v3.py", "test_media_buy_ids_lookup"),
    ("tests/integration/test_delivery_v3.py", "test_mixed_ownership"),
    ("tests/integration/test_delivery_v3.py", "test_nested_serialization_roundtrip"),
    ("tests/integration/test_delivery_v3.py", "test_ownership_isolation"),
    ("tests/integration/test_delivery_v3.py", "test_ownership_no_info_leakage"),
    ("tests/integration/test_delivery_v3.py", "test_pricing_option_roundtrip"),
    ("tests/integration/test_delivery_v3.py", "test_single_buy_delivery_via_media_buy_id"),
    ("tests/integration/test_delivery_v3.py", "test_status_filter_all_via_explicit_ids"),
    ("tests/integration/test_delivery_v3.py", "test_status_filter_no_match_returns_empty"),
    (
        "tests/integration/test_delivery_webhooks_force.py",
        "test_force_trigger_delivery_webhook_bypasses_duplicate_check",
    ),
    ("tests/integration/test_delivery_webhooks_force.py", "test_trigger_report_fails_gracefully_no_webhook"),
    ("tests/integration/test_delivery_webhooks_force.py", "test_trigger_report_for_media_buy_public_method"),
    ("tests/integration/test_delivery_webhooks_integration.py", "_create_basic_media_buy_with_webhook"),
    ("tests/integration/test_delivery_webhooks_integration.py", "_create_test_tenant_and_principal"),
    ("tests/integration/test_dynamic_products.py", "_create_dynamic_template"),
    ("tests/integration/test_dynamic_products.py", "_ensure_tenant"),
    ("tests/integration/test_dynamic_products.py", "_get_variants"),
    ("tests/integration/test_dynamic_products.py", "test_already_archived_not_rearchived"),
    ("tests/integration/test_dynamic_products.py", "test_existing_variant_updated_not_duplicated"),
    ("tests/integration/test_dynamic_products.py", "test_expired_variants_archived"),
    ("tests/integration/test_dynamic_products.py", "test_no_tenant_archives_all"),
    ("tests/integration/test_dynamic_products.py", "test_non_expired_variants_untouched"),
    ("tests/integration/test_dynamic_products.py", "test_templates_with_signals_generates_variants"),
    ("tests/integration/test_dynamic_products.py", "test_tenant_filter_scoping"),
    ("tests/integration/test_dynamic_products_integration.py", "test_archives_expired_variant"),
    ("tests/integration/test_dynamic_products_integration.py", "test_templates_with_signals_creates_variants"),
    ("tests/integration/test_error_paths.py", "test_rest_error_with_valid_token_writes_audit_row"),
    ("tests/integration/test_execute_approved_platform_ids.py", "test_empty_platform_line_item_ids_dict"),
    (
        "tests/integration/test_execute_approved_platform_ids.py",
        "test_manual_approval_enriches_concept_and_is_filterable",
    ),
    ("tests/integration/test_execute_approved_platform_ids.py", "test_multiple_packages_all_persisted"),
    ("tests/integration/test_execute_approved_platform_ids.py", "test_no_platform_line_item_ids_attr"),
    ("tests/integration/test_execute_approved_platform_ids.py", "test_platform_line_item_ids_persisted_after_approval"),
    ("tests/integration/test_format_conversion_approval.py", "create_media_package"),
    ("tests/integration/test_format_conversion_approval.py", "test_currency_limit"),
    ("tests/integration/test_format_conversion_approval.py", "test_empty_formats_list_fails"),
    ("tests/integration/test_format_conversion_approval.py", "test_invalid_agent_url_not_http"),
    ("tests/integration/test_format_conversion_approval.py", "test_invalid_dict_missing_id"),
    ("tests/integration/test_format_conversion_approval.py", "test_invalid_format_empty_agent_url"),
    ("tests/integration/test_format_conversion_approval.py", "test_invalid_format_missing_agent_url"),
    ("tests/integration/test_format_conversion_approval.py", "test_invalid_format_missing_format_id"),
    ("tests/integration/test_format_conversion_approval.py", "test_invalid_format_unknown_type"),
    ("tests/integration/test_format_conversion_approval.py", "test_mixed_valid_format_types"),
    ("tests/integration/test_format_conversion_approval.py", "test_principal"),
    ("tests/integration/test_format_conversion_approval.py", "test_property_tag"),
    ("tests/integration/test_format_conversion_approval.py", "test_tenant"),
    ("tests/integration/test_format_conversion_approval.py", "test_valid_format_id_dict_conversion"),
    ("tests/integration/test_format_conversion_approval.py", "test_valid_format_reference_dict_conversion"),
    ("tests/integration/test_gam_adapter_auth.py", "_load_principal"),
    ("tests/integration/test_gam_adapter_auth.py", "_set_tenant_context"),
    ("tests/integration/test_gam_adapter_auth.py", "oauth_tenant"),
    ("tests/integration/test_gam_adapter_auth.py", "sa_tenant"),
    ("tests/integration/test_gam_adapter_auth.py", "test_oauth_config_includes_refresh_token"),
    ("tests/integration/test_gam_adapter_auth.py", "test_sa_config_includes_service_account_json"),
    ("tests/integration/test_gam_adapter_auth.py", "test_sa_tenant_config_dict_has_correct_keys"),
    ("tests/integration/test_gam_automation_focused.py", "test_product_automation_config_parsing"),
    ("tests/integration/test_gam_automation_focused.py", "test_tenant_data"),
    ("tests/integration/test_gam_pricing_models_integration.py", "setup_gam_tenant_with_all_pricing_models"),
    ("tests/integration/test_gam_pricing_models_integration.py", "test_gam_auction_cpc_creates_price_priority"),
    ("tests/integration/test_gam_pricing_restriction.py", "setup_gam_tenant_with_non_cpm_product"),
    ("tests/integration/test_gam_tenant_setup.py", "test_gam_tenant_creation_with_network_code"),
    ("tests/integration/test_gam_tenant_setup.py", "test_gam_tenant_creation_without_network_code"),
    ("tests/integration/test_generative_creatives.py", "test_generative_format_detection_calls_build_creative"),
    ("tests/integration/test_get_products_database_integration.py", "access_fields"),
    ("tests/integration/test_get_products_database_integration.py", "database_operation"),
    ("tests/integration/test_get_products_database_integration.py", "test_concurrent_field_access"),
    ("tests/integration/test_get_products_database_integration.py", "test_database_connection_pooling_efficiency"),
    ("tests/integration/test_get_products_database_integration.py", "test_database_field_access_validation"),
    (
        "tests/integration/test_get_products_database_integration.py",
        "test_database_model_to_schema_conversion_without_mocking",
    ),
    ("tests/integration/test_get_products_database_integration.py", "test_large_dataset_conversion_performance"),
    ("tests/integration/test_get_products_database_integration.py", "test_multiple_products_database_conversion"),
    (
        "tests/integration/test_inventory_profile_effective_properties.py",
        "test_effective_formats_returns_custom_formats_when_profile_not_set",
    ),
    (
        "tests/integration/test_inventory_profile_effective_properties.py",
        "test_effective_formats_returns_profile_formats_when_profile_set",
    ),
    (
        "tests/integration/test_inventory_profile_effective_properties.py",
        "test_effective_implementation_config_builds_from_profile_inventory",
    ),
    (
        "tests/integration/test_inventory_profile_effective_properties.py",
        "test_effective_implementation_config_returns_custom_config_when_profile_not_set",
    ),
    (
        "tests/integration/test_inventory_profile_effective_properties.py",
        "test_effective_properties_handle_none_profile_relationship",
    ),
    (
        "tests/integration/test_inventory_profile_effective_properties.py",
        "test_effective_properties_returns_custom_properties_when_profile_not_set",
    ),
    (
        "tests/integration/test_inventory_profile_effective_properties.py",
        "test_effective_properties_returns_profile_properties_when_profile_set",
    ),
    (
        "tests/integration/test_inventory_profile_effective_properties.py",
        "test_effective_property_tags_returns_custom_tags_when_profile_not_set",
    ),
    (
        "tests/integration/test_inventory_profile_effective_properties.py",
        "test_effective_property_tags_returns_none_when_profile_set",
    ),
    ("tests/integration/test_inventory_profile_effective_properties.py", "test_product_custom"),
    ("tests/integration/test_inventory_profile_effective_properties.py", "test_product_with_profile"),
    ("tests/integration/test_inventory_profile_effective_properties.py", "test_profile"),
    ("tests/integration/test_inventory_profile_effective_properties.py", "test_tenant"),
    ("tests/integration/test_inventory_profile_media_buy.py", "test_create_media_buy_with_profile_based_product"),
    ("tests/integration/test_inventory_profile_media_buy.py", "test_create_media_buy_with_profile_formats"),
    ("tests/integration/test_inventory_profile_media_buy.py", "test_media_buy_reflects_profile_updates"),
    ("tests/integration/test_inventory_profile_media_buy.py", "test_multiple_products_same_profile_in_media_buy"),
    ("tests/integration/test_inventory_profile_security.py", "profile_a"),
    ("tests/integration/test_inventory_profile_security.py", "profile_b"),
    ("tests/integration/test_inventory_profile_security.py", "tenant_a"),
    ("tests/integration/test_inventory_profile_security.py", "tenant_b"),
    ("tests/integration/test_inventory_profile_security.py", "test_get_products_filters_profiles_by_tenant"),
    (
        "tests/integration/test_inventory_profile_security.py",
        "test_product_cannot_reference_profile_from_different_tenant",
    ),
    ("tests/integration/test_inventory_profile_security.py", "test_profile_updates_only_affect_same_tenant_products"),
    ("tests/integration/test_inventory_profile_transitions.py", "create_product"),
    ("tests/integration/test_inventory_profile_transitions.py", "profile_a"),
    ("tests/integration/test_inventory_profile_transitions.py", "profile_b"),
    ("tests/integration/test_inventory_profile_transitions.py", "tenant"),
    (
        "tests/integration/test_inventory_profile_transitions.py",
        "test_clearing_profile_without_custom_config_has_sensible_behavior",
    ),
    ("tests/integration/test_inventory_profile_transitions.py", "test_profile_deletion_handles_dependent_products"),
    (
        "tests/integration/test_inventory_profile_transitions.py",
        "test_switching_from_custom_to_profile_uses_profile_config",
    ),
    (
        "tests/integration/test_inventory_profile_transitions.py",
        "test_switching_from_profile_to_custom_uses_custom_config",
    ),
    ("tests/integration/test_inventory_profile_transitions.py", "test_switching_profiles_updates_effective_properties"),
    ("tests/integration/test_inventory_profile_updates.py", "test_updating_profile_formats_affects_all_products"),
    (
        "tests/integration/test_inventory_profile_updates.py",
        "test_updating_profile_inventory_affects_product_implementation_config",
    ),
    ("tests/integration/test_inventory_profile_updates.py", "test_updating_profile_properties_affects_all_products"),
    ("tests/integration/test_inventory_tree_lazy_loading.py", "_bind_factories"),
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
    (
        "tests/integration/test_list_authorized_properties_integration.py",
        "test_list_authorized_properties_tenant_isolation",
    ),
    ("tests/integration/test_mcp_tool_roundtrip_validation.py", "real_products_in_db"),
    ("tests/integration/test_mcp_tool_roundtrip_validation.py", "test_tenant_id"),
    ("tests/integration/test_mcp_tools_audit.py", "test_get_media_buy_delivery_roundtrip_safety"),
    ("tests/integration/test_mcp_tools_audit.py", "test_tenant_id"),
    ("tests/integration/test_media_buy_readiness.py", "test_completed_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_draft_state_no_packages"),
    ("tests/integration/test_media_buy_readiness.py", "test_live_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_needs_approval_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_needs_creatives_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_principal"),
    ("tests/integration/test_media_buy_readiness.py", "test_scheduled_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_tenant"),
    ("tests/integration/test_media_buy_readiness.py", "test_tenant_readiness_summary"),
    ("tests/integration/test_media_buy_repository.py", "principal_a"),
    ("tests/integration/test_media_buy_repository.py", "principal_b"),
    ("tests/integration/test_media_buy_repository.py", "seed_data"),
    ("tests/integration/test_media_buy_repository.py", "tenant_a"),
    ("tests/integration/test_media_buy_repository.py", "tenant_b"),
    ("tests/integration/test_media_buy_repository.py", "test_create_from_request_stores_idempotency_key"),
    ("tests/integration/test_media_buy_repository.py", "test_does_not_return_other_tenant_media_buy"),
    ("tests/integration/test_media_buy_repository.py", "test_excludes_other_tenant_packages"),
    ("tests/integration/test_media_buy_repository.py", "test_find_by_idempotency_key_returns_existing"),
    ("tests/integration/test_media_buy_repository.py", "test_find_by_idempotency_key_returns_none_when_missing"),
    ("tests/integration/test_media_buy_repository.py", "test_get_by_id_or_idempotency_key_threads_account_id"),
    ("tests/integration/test_media_buy_repository.py", "test_groups_packages_by_media_buy"),
    ("tests/integration/test_media_buy_repository.py", "test_idempotency_key_scoped_to_tenant"),
    ("tests/integration/test_media_buy_repository.py", "test_media_buy_ids_filter"),
    ("tests/integration/test_media_buy_repository.py", "test_relationship_empty_when_no_packages"),
    ("tests/integration/test_media_buy_repository.py", "test_relationship_loads_packages"),
    ("tests/integration/test_media_buy_repository.py", "test_returns_all_for_principal"),
    ("tests/integration/test_media_buy_repository.py", "test_returns_empty_for_other_tenant_buy"),
    ("tests/integration/test_media_buy_repository.py", "test_returns_none_for_nonexistent"),
    ("tests/integration/test_media_buy_repository.py", "test_returns_none_for_other_tenant"),
    ("tests/integration/test_media_buy_repository.py", "test_returns_own_tenant_media_buy"),
    ("tests/integration/test_media_buy_repository.py", "test_returns_packages_for_own_buy"),
    ("tests/integration/test_media_buy_repository.py", "test_returns_specific_package"),
    ("tests/integration/test_media_buy_repository.py", "test_status_filter"),
    ("tests/integration/test_media_buy_repository.py", "test_uow_commits_on_clean_exit"),
    ("tests/integration/test_media_buy_repository.py", "test_uow_rolls_back_on_exception"),
    ("tests/integration/test_media_buy_repository_writes.py", "principal_a"),
    ("tests/integration/test_media_buy_repository_writes.py", "principal_b"),
    ("tests/integration/test_media_buy_repository_writes.py", "tenant_a"),
    ("tests/integration/test_media_buy_repository_writes.py", "tenant_b"),
    ("tests/integration/test_media_buy_repository_writes.py", "test_roundtrip_bulk_create"),
    ("tests/integration/test_media_buy_repository_writes.py", "test_roundtrip_create_and_read_back"),
    ("tests/integration/test_media_buy_repository_writes.py", "test_roundtrip_create_package"),
    ("tests/integration/test_media_buy_repository_writes.py", "test_roundtrip_update_config"),
    ("tests/integration/test_media_buy_repository_writes.py", "test_roundtrip_update_fields"),
    ("tests/integration/test_media_buy_repository_writes.py", "test_roundtrip_update_package_fields"),
    ("tests/integration/test_media_buy_repository_writes.py", "test_roundtrip_update_status"),
    ("tests/integration/test_media_buy_repository_writes.py", "test_tenant_isolation_on_create"),
    ("tests/integration/test_media_buy_repository_writes.py", "test_uow_rollback_on_exception"),
    ("tests/integration/test_media_buy_repository_writes.py", "test_update_config_other_tenant_returns_none"),
    ("tests/integration/test_media_buy_repository_writes.py", "test_update_fields_tenant_isolation"),
    ("tests/integration/test_media_buy_repository_writes.py", "test_update_status_other_tenant_returns_none"),
    ("tests/integration/test_media_buy_status_scheduler.py", "_create_creative"),
    ("tests/integration/test_media_buy_status_scheduler.py", "_create_creative_assignment"),
    ("tests/integration/test_media_buy_status_scheduler.py", "_create_media_buy"),
    ("tests/integration/test_media_buy_status_scheduler.py", "_create_test_principal"),
    ("tests/integration/test_media_buy_status_scheduler.py", "_create_test_tenant"),
    ("tests/integration/test_media_buy_status_scheduler.py", "_get_media_buy_status"),
    ("tests/integration/test_media_buy_v3.py", "mb_creatives"),
    ("tests/integration/test_media_buy_v3.py", "mb_tenant_with_approval"),
    ("tests/integration/test_media_buy_v3.py", "test_adapter_failure_no_db_changes"),
    ("tests/integration/test_media_buy_v3.py", "test_adapter_success_persists_records"),
    ("tests/integration/test_media_buy_v3.py", "test_create_roundtrip_db_persistence"),
    ("tests/integration/test_media_buy_v3.py", "test_execute_approved_calls_adapter"),
    ("tests/integration/test_media_buy_v3.py", "test_manual_approval_creates_pending_workflow_step"),
    ("tests/integration/test_media_buy_v3.py", "test_manual_approval_stores_raw_request"),
    ("tests/integration/test_media_buy_v3.py", "test_ownership_mismatch_rejected"),
    ("tests/integration/test_media_buy_v3.py", "test_unsupported_currency_rejected"),
    ("tests/integration/test_minimum_spend_validation.py", "setup_test_data"),
    ("tests/integration/test_minimum_spend_validation.py", "test_no_minimum_when_not_set"),
    ("tests/integration/test_mock_adapter_publisher_sync.py", "mock_tenant"),
    ("tests/integration/test_mock_adapter_publisher_sync.py", "publisher_partner"),
    ("tests/integration/test_mock_adapter_publisher_sync.py", "test_sync_creates_authorized_property"),
    ("tests/integration/test_mock_adapter_publisher_sync.py", "test_sync_creates_property_tag"),
    ("tests/integration/test_mock_adapter_publisher_sync.py", "test_sync_is_idempotent"),
    ("tests/integration/test_mock_adapter_publisher_sync.py", "test_sync_property_has_verified_status"),
    ("tests/integration/test_mock_ai_per_creative.py", "mock_adapter"),
    ("tests/integration/test_pricing_helpers.py", "test_auto_generated_product_id"),
    ("tests/integration/test_pricing_helpers.py", "test_create_auction_product"),
    ("tests/integration/test_pricing_helpers.py", "test_create_flat_rate_product"),
    ("tests/integration/test_pricing_helpers.py", "test_create_product_with_cpm_pricing"),
    ("tests/integration/test_pricing_helpers.py", "test_multiple_products_with_pricing"),
    ("tests/integration/test_pricing_models_integration.py", "setup_tenant_with_pricing_products"),
    (
        "tests/integration/test_product_delete_with_pricing.py",
        "test_pricing_option_direct_deletion_bypasses_trigger_due_to_cascade",
    ),
    ("tests/integration/test_product_delete_with_pricing.py", "test_product_deletion_with_pricing_options"),
    ("tests/integration/test_product_deletion.py", "setup_super_admin_config"),
    ("tests/integration/test_product_deletion.py", "test_delete_multiple_products_different_statuses"),
    ("tests/integration/test_product_deletion.py", "test_delete_product_success"),
    ("tests/integration/test_product_deletion.py", "test_delete_product_with_active_media_buy"),
    ("tests/integration/test_product_deletion.py", "test_delete_product_with_completed_media_buy_allowed"),
    ("tests/integration/test_product_deletion.py", "test_delete_product_with_pending_media_buy"),
    ("tests/integration/test_product_deletion_with_trigger.py", "test_product_deletion_cascades_pricing_options"),
    ("tests/integration/test_product_deletion_with_trigger.py", "test_product_deletion_with_multiple_pricing_options"),
    (
        "tests/integration/test_product_deletion_with_trigger.py",
        "test_trigger_still_blocks_manual_deletion_of_last_pricing_option",
    ),
    ("tests/integration/test_product_format_validation.py", "app_client"),
    ("tests/integration/test_product_format_validation.py", "tenant_with_prereqs"),
    ("tests/integration/test_product_format_validation.py", "test_agent_down_saves_without_validation"),
    ("tests/integration/test_product_format_validation.py", "test_empty_agent_rejects_format_ids"),
    ("tests/integration/test_product_format_validation.py", "test_invalid_format_ids_rejected"),
    ("tests/integration/test_product_format_validation.py", "test_partial_failure_validates_against_available"),
    ("tests/integration/test_product_format_validation.py", "test_valid_format_ids_accepted"),
    ("tests/integration/test_product_formats_update.py", "sample_product"),
    ("tests/integration/test_product_formats_update.py", "test_product_countries_update_with_flag_modified"),
    ("tests/integration/test_product_formats_update.py", "test_product_formats_update_with_flag_modified"),
    ("tests/integration/test_product_formats_update.py", "test_product_formats_update_without_flag_modified_fails"),
    ("tests/integration/test_product_multiple_format_ids.py", "test_create_product_with_multiple_format_ids"),
    ("tests/integration/test_product_multiple_format_ids.py", "test_product_format_ids_migration_compatibility"),
    ("tests/integration/test_product_multiple_format_ids.py", "test_tenant"),
    ("tests/integration/test_product_multiple_format_ids.py", "test_update_product_format_ids_preserves_all_formats"),
    ("tests/integration/test_product_pricing_options_required.py", "test_create_media_buy_loads_pricing_options"),
    ("tests/integration/test_product_pricing_options_required.py", "test_get_product_catalog_loads_pricing_options"),
    ("tests/integration/test_product_pricing_options_required.py", "test_product_query_with_eager_loading"),
    (
        "tests/integration/test_product_pricing_options_required.py",
        "test_product_without_eager_loading_fails_validation",
    ),
    ("tests/integration/test_product_principal_access.py", "test_allowed_principal_ids_excluded_from_serialization"),
    ("tests/integration/test_product_principal_access.py", "test_convert_product_includes_allowed_principal_ids"),
    ("tests/integration/test_product_principal_access.py", "test_principal_model_exists_for_access_control"),
    ("tests/integration/test_product_principal_access.py", "test_product_stores_and_retrieves_allowed_principal_ids"),
    ("tests/integration/test_product_principal_access.py", "test_product_with_null_allowed_principal_ids"),
    ("tests/integration/test_product_repository.py", "test_eager_loads_pricing_options"),
    ("tests/integration/test_product_repository.py", "test_empty_tenant_returns_empty_list"),
    ("tests/integration/test_product_repository.py", "test_invalid_attribute_raises_valueerror"),
    ("tests/integration/test_product_repository.py", "test_nonexistent_returns_none"),
    ("tests/integration/test_product_repository.py", "test_products_convertible_to_schema"),
    ("tests/integration/test_product_repository.py", "test_returns_all_products_for_tenant"),
    ("tests/integration/test_product_repository.py", "test_returns_empty_list_for_empty_input"),
    ("tests/integration/test_product_repository.py", "test_returns_empty_list_for_nonexistent_ids"),
    ("tests/integration/test_product_repository.py", "test_returns_matching_products"),
    ("tests/integration/test_product_repository.py", "test_returns_none_for_nonexistent"),
    ("tests/integration/test_product_repository.py", "test_returns_none_for_nonexistent_product"),
    ("tests/integration/test_product_repository.py", "test_returns_product_by_id"),
    ("tests/integration/test_product_repository.py", "test_returns_product_with_pricing_loaded"),
    ("tests/integration/test_product_repository.py", "test_returns_products_with_pricing_loaded"),
    ("tests/integration/test_product_repository.py", "test_roundtrip_create_and_read_back"),
    ("tests/integration/test_product_repository.py", "test_roundtrip_update_fields"),
    ("tests/integration/test_product_repository.py", "test_tenant_isolation"),
    ("tests/integration/test_product_repository.py", "test_tenant_isolation_on_create"),
    ("tests/integration/test_product_repository.py", "test_tenant_isolation_on_get_by_id"),
    ("tests/integration/test_product_repository.py", "test_tenant_isolation_on_update"),
    ("tests/integration/test_product_repository.py", "test_tenant_mismatch_raises_valueerror"),
    ("tests/integration/test_product_with_inventory_profile.py", "test_create_product_with_inventory_profile"),
    (
        "tests/integration/test_product_with_inventory_profile.py",
        "test_product_creation_validates_profile_belongs_to_tenant",
    ),
    ("tests/integration/test_property_targeting_allowed_enforcement.py", "_seed_media_buy"),
    ("tests/integration/test_property_targeting_allowed_enforcement.py", "property_targeting_tenant"),
    ("tests/integration/test_resolve_account.py", "test_natural_key_not_found_raises"),
    ("tests/integration/test_resolve_account.py", "test_no_access_raises"),
    ("tests/integration/test_resolve_account.py", "test_not_found_raises"),
    ("tests/integration/test_resolve_account.py", "test_resolves_by_account_id"),
    ("tests/integration/test_resolve_account.py", "test_resolves_by_natural_key"),
    ("tests/integration/test_schema_database_mapping.py", "test_database_field_access_validation"),
    ("tests/integration/test_schema_database_mapping.py", "test_database_json_field_handling"),
    ("tests/integration/test_schema_database_mapping.py", "test_schema_to_database_conversion_safety"),
    ("tests/integration/test_schema_database_mapping.py", "test_schema_validation_with_database_data"),
    ("tests/integration/test_self_service_signup.py", "test_provision_tenant_gam_adapter_without_oauth"),
    ("tests/integration/test_self_service_signup.py", "test_provision_tenant_kevel_adapter_with_credentials"),
    ("tests/integration/test_self_service_signup.py", "test_provision_tenant_mock_adapter"),
    ("tests/integration/test_self_service_signup.py", "test_session_cleanup_after_provisioning"),
    ("tests/integration/test_self_service_signup.py", "test_signup_completion_page_renders"),
    ("tests/integration/test_self_service_signup.py", "test_subdomain_auto_generation"),
    ("tests/integration/test_self_service_signup.py", "test_subdomain_uniqueness_extremely_rare_collision"),
    ("tests/integration/test_session_json_validation.py", "test_context_manager_pattern"),
    ("tests/integration/test_session_json_validation.py", "test_database_manager_class"),
    ("tests/integration/test_session_json_validation.py", "test_full_workflow"),
    ("tests/integration/test_session_json_validation.py", "test_get_or_404"),
    ("tests/integration/test_session_json_validation.py", "test_get_or_create"),
    ("tests/integration/test_session_json_validation.py", "test_model_json_validation"),
    ("tests/integration/test_session_json_validation.py", "test_principal_platform_mappings"),
    ("tests/integration/test_session_json_validation.py", "test_workflow_step_comments"),
    ("tests/integration/test_setup_checklist_service.py", "setup_complete_tenant"),
    ("tests/integration/test_setup_checklist_service.py", "setup_minimal_tenant"),
    ("tests/integration/test_setup_checklist_service.py", "test_bulk_setup_status_for_multiple_tenants"),
    ("tests/integration/test_setup_checklist_service.py", "test_currency_count_in_details"),
    ("tests/integration/test_setup_checklist_service.py", "test_gemini_api_key_detection"),
    ("tests/integration/test_setup_checklist_service.py", "test_progress_calculation"),
    ("tests/integration/test_setup_checklist_service.py", "test_ready_for_orders_without_sso_in_multi_tenant_mode"),
    ("tests/integration/test_setup_checklist_service.py", "test_sso_is_optional_not_critical_in_multi_tenant_mode"),
    ("tests/integration/test_sync_accounts.py", "test_set_approval_mode_writes_to_account_approval_mode_column"),
    ("tests/integration/test_sync_job_model.py", "test_sync_job_id_length"),
    ("tests/integration/test_targeting_api.py", "test_get_targeting_data_returns_audience_type"),
    ("tests/integration/test_targeting_overlay_roundtrip.py", "_persist_targeting_overlay"),
    ("tests/integration/test_targeting_overlay_roundtrip.py", "roundtrip_tenant"),
    ("tests/integration/test_targeting_overlay_roundtrip.py", "test_both_lists_coexist_in_single_package"),
    ("tests/integration/test_targeting_overlay_roundtrip.py", "test_collection_list_roundtrips_through_postgres"),
    ("tests/integration/test_targeting_overlay_roundtrip.py", "test_property_list_roundtrips_through_postgres"),
    ("tests/integration/test_targeting_validation_chain.py", "targeting_tenant"),
    ("tests/integration/test_targeting_values_endpoint.py", "test_get_targeting_values_empty_result"),
    ("tests/integration/test_targeting_values_endpoint.py", "test_get_targeting_values_endpoint"),
    ("tests/integration/test_targeting_values_endpoint.py", "test_get_targeting_values_requires_auth"),
    ("tests/integration/test_targeting_values_endpoint.py", "test_get_targeting_values_tenant_isolation"),
    ("tests/integration/test_tenant_dashboard.py", "test_dashboard_metrics_calculation"),
    ("tests/integration/test_tenant_dashboard.py", "test_dashboard_with_empty_tenant"),
    ("tests/integration/test_tenant_dashboard.py", "test_dashboard_with_media_buys"),
    ("tests/integration/test_tenant_dashboard.py", "test_tenant_config_building"),
    ("tests/integration/test_tenant_isolation_breach_fix.py", "test_cross_tenant_token_rejected"),
    ("tests/integration/test_tenant_isolation_breach_fix.py", "test_no_fallback_to_first_tenant"),
    ("tests/integration/test_tenant_isolation_breach_fix.py", "test_tenant_isolation_with_valid_subdomain"),
    ("tests/integration/test_tenant_isolation_fix.py", "test_admin_token_with_subdomain_preserves_tenant_context"),
    ("tests/integration/test_tenant_isolation_fix.py", "test_global_token_lookup_sets_tenant_from_principal"),
    ("tests/integration/test_tenant_isolation_fix.py", "test_tenant_isolation_with_subdomain_and_cross_tenant_token"),
    ("tests/integration/test_tenant_management_api_integration.py", "mock_api_key_auth"),
    ("tests/integration/test_tenant_management_api_integration.py", "test_tenant"),
    ("tests/integration/test_tenant_settings_comprehensive.py", "test_database_queries"),
    ("tests/integration/test_tenant_utils.py", "test_serialize_tenant_json_fields_are_deserialized"),
    ("tests/integration/test_tenant_utils.py", "test_serialize_tenant_nullable_fields_have_defaults"),
    ("tests/integration/test_update_media_buy_creative_assignment.py", "test_creative_assignments_replaces_all"),
    ("tests/integration/test_update_media_buy_creative_assignment.py", "test_creative_assignments_with_weights"),
    (
        "tests/integration/test_update_media_buy_creative_assignment.py",
        "test_update_media_buy_assigns_creatives_to_package",
    ),
    (
        "tests/integration/test_update_media_buy_creative_assignment.py",
        "test_update_media_buy_rejects_missing_creatives",
    ),
    ("tests/integration/test_update_media_buy_creative_assignment.py", "test_update_media_buy_replaces_creatives"),
    ("tests/integration/test_update_media_buy_persistence.py", "test_tenant_setup"),
    ("tests/integration/test_update_media_buy_persistence.py", "test_update_media_buy_with_database_persisted_buy"),
    ("tests/integration/test_workflow_approval.py", "test_approve_workflow_step"),
    ("tests/integration/test_workflow_approval.py", "test_create_approval_workflow"),
    ("tests/integration/test_workflow_approval.py", "test_get_pending_approvals"),
    ("tests/integration/test_workflow_approval.py", "test_reject_workflow_step"),
    ("tests/integration/test_workflow_approval.py", "test_workflow_lifecycle_tracking"),
    ("tests/integration/test_workflow_architecture.py", "test_workflow_architecture"),
    ("tests/integration/test_workflow_lifecycle.py", "setup"),
    ("tests/integration/test_workflow_lifecycle.py", "test_async_operation_creates_workflow"),
    ("tests/integration/test_workflow_lifecycle.py", "test_manual_approval_workflow"),
    ("tests/integration/test_workflow_lifecycle.py", "test_parallel_workflow_steps"),
    ("tests/integration/test_workflow_lifecycle.py", "test_sync_operation_no_workflow"),
    ("tests/integration/test_workflow_lifecycle.py", "test_workflow_failure_handling"),
}


def _find_get_db_session_in_tests(file_path: str) -> list[tuple[str, str, int]]:
    """Find test functions/fixtures that call get_db_session() directly."""
    return _find_matching_calls(file_path, _is_get_db_session_call)


class TestIntegrationTestsNoGetDbSession:
    """Integration tests must not open a raw get_db_session() in the test body.

    Pattern #8 (tests/CLAUDE.md): DB access in tests goes through factories and
    the harness (AccountUoW / IntegrationEnv), not a session opened inline. The
    legacy allowlist captures pre-existing debt; a NEW test-body get_db_session()
    fails immediately — including a new function in an EXISTING file, which is how
    the #1417 test_resolve_account.py natural-key test slipped when this guard was
    src-only.
    """

    @pytest.mark.arch_guard
    def test_no_new_get_db_session_in_tests(self):
        """No test function/fixture calls get_db_session() outside the allowlist."""
        _assert_no_new_violations(
            _find_get_db_session_in_tests,
            INTEGRATION_TEST_FILES,
            GET_DB_SESSION_IN_TESTS_ALLOWLIST,
            header="New get_db_session() calls in test bodies (use factories / the harness UoW instead):",
            fix_hint=(
                "Fix: obtain the session/repo via the harness "
                "(e.g. with AccountUoW(tenant_id) as uow: uow.accounts). See CLAUDE.md Pattern #8."
            ),
        )

    @pytest.mark.arch_guard
    def test_allowlist_entries_still_exist(self):
        """Every allowlisted violation must still exist (stale entry detection)."""
        _assert_allowlist_current(
            _find_get_db_session_in_tests,
            INTEGRATION_TEST_FILES,
            GET_DB_SESSION_IN_TESTS_ALLOWLIST,
            fix_hint="Remove fixed entries from GET_DB_SESSION_IN_TESTS_ALLOWLIST.",
        )


# ---------------------------------------------------------------------------
# Invariant 4: Repositories may not import SDK protocol/response types (reverse layer)
# ---------------------------------------------------------------------------

# The generated-response-shape family: types describing a WIRE response, not a
# DB row. IdempotencyPosture/to_sdk_union() imported Idempotency/Idempotency3
# from here while living in repositories/idempotency_attempt.py (D2, salesagent-
# c0ia.11 M2) -- business/wire-shaping logic belongs above the repository,
# never inside it. Scoped to this specific submodule (not bare `adcp.types`,
# whose top-level re-exports like ContextObject/Error are generic shared types
# used legitimately in a handful of repository method signatures today) so the
# check is crisp with zero false positives.
_FORBIDDEN_REPOSITORY_IMPORT_PREFIX = "adcp.types.generated_poc.protocol"

REPOSITORIES_DIR = "src/core/database/repositories"


def _scan_for_protocol_imports(tree: ast.Module) -> list[tuple[str, int]]:
    """(imported_module, line) for every ``from adcp.types.generated_poc.protocol...
    import ...`` in ``tree``."""
    return [
        (node.module, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith(_FORBIDDEN_REPOSITORY_IMPORT_PREFIX)
    ]


def _find_repository_protocol_imports() -> list[tuple[str, str, int]]:
    """(file_path, imported_module, line) for every repositories/*.py file."""
    violations: list[tuple[str, str, int]] = []
    for py_file in sorted((ROOT / REPOSITORIES_DIR).rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        rel_path = str(py_file.relative_to(ROOT))
        tree = ast.parse(py_file.read_text())
        violations.extend((rel_path, module, lineno) for module, lineno in _scan_for_protocol_imports(tree))
    return violations


class TestRepositoriesDoNotImportProtocolResponseTypes:
    """The repository layer sits BELOW schemas/tools -- importing an SDK
    protocol-response type here is the reverse-direction violation of the same
    layering rule CLAUDE.md Pattern #3 enforces one direction of (business
    logic must not reach INTO the repository's session; a repository must not
    reach UP into wire-response typing). Allowlist is empty and stays empty:
    after M2's IdempotencyPosture relocation there are zero legitimate cases.
    """

    @pytest.mark.arch_guard
    def test_no_repository_imports_adcp_protocol_response_types(self):
        violations = _find_repository_protocol_imports()
        assert not violations, (
            "repositories/*.py importing adcp.types.generated_poc.protocol.* "
            "(a response-shaped SDK type) -- move the business/wire-shaping logic "
            "that needs this type OUT of the repository layer (see "
            "src/core/idempotency_policy.py for the pattern):\n"
            + "\n".join(f"  {f}:{line} imports {mod}" for f, mod, line in violations)
        )

    @pytest.mark.arch_guard
    def test_guard_catches_a_reintroduced_protocol_import(self):
        """Positive meta-test: reproduces the exact pre-M2 idempotency_attempt.py shape."""
        drifted = ast.parse(
            "from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import Idempotency\n"
        )
        assert _scan_for_protocol_imports(drifted), "a reintroduced protocol-response import must be flagged"

    @pytest.mark.arch_guard
    def test_guard_ignores_generic_adcp_types_imports(self):
        """Negative meta-test: bare `adcp.types` re-exports (ContextObject, Error, ...)
        are generic shared types legitimately used in repository method signatures
        today (e.g. src/core/database/repositories/media_buy.py) -- not a violation."""
        generic = ast.parse("from adcp.types import ContextObject, Error\n")
        assert _scan_for_protocol_imports(generic) == []


# ---------------------------------------------------------------------------
# Signed outbound work owns its connection — enforced by SHAPE, not by scan
# ---------------------------------------------------------------------------
#
# These two grade the same fault from opposite ends: a POOLED DB CONNECTION HELD ACROSS
# time.sleep() AND A POST TO A BUYER-SUPPLIED URL, so a hanging receiver consumes a
# database connection.
#
# A LEXICAL GUARD FOR THAT WAS WRITTEN AND DISCARDED, and the reason belongs here so it
# is not helpfully re-added. The obvious invariant — "a `with get_db_session()` block
# must not enclose a sleep or an outbound POST" — was GREEN ON ITS OWN MOTIVATING
# DEFECT: the session block called ``self._deliver_with_backoff(...)`` and the
# ``time.sleep`` lived one call frame deeper, inside that method, so a body-subtree scan
# saw nothing. Any such scan is defeated by ordinary helper extraction. Measured on the
# tree at the time it found 2 violations, neither of them the defect it existed for.
#
# So the API SHAPE is the guard instead. Together these make the defect UNREPRESENTABLE
# rather than detectable after the fact, and neither needs a call graph:
#
#   (i)  a caller cannot DONATE a session lifetime, because no parameter accepts one;
#   (ii) the retry loop cannot REACH a session, because the only thing it can see is a
#        frozen dataclass of primitives — true at any call depth.
#
# Both allowlists are EMPTY and stay empty (#1757, salesagent-n78j0.4).

#: Parameter names by which a caller could hand its own connection lifetime to the
#: delivery boundary. ``repo=`` was the real one; the siblings are named so the next
#: shape of the same mistake is covered rather than only the one we removed.
_DONATED_SESSION_PARAMS = frozenset({"repo", "session", "db", "db_session", "uow"})

#: The connection-lifetime boundary: the functions a caller reaches to do signed outbound
#: work. Named for the INVARIANT rather than for the webhook path, because the outbound
#: client seam joined it and sends no webhook.
_DELIVERY_BOUNDARY = (
    ("src/core/signing/webhook_sender_factory.py", "deliver_adcp_webhook"),
    ("src/core/signing/webhook_sender_factory.py", "deliver_adcp_webhook_sync"),
    ("src/core/signing/webhook_sender_factory.py", "adcp_webhook_sender"),
    ("src/core/signing/webhook_sender_factory.py", "signing_repo"),
    ("src/core/helpers/adapter_helpers.py", "build_adcp_multi_agent_client"),
)

#: The queued-delivery entry, which the retry loop is the sole consumer of.
_QUEUE_ENTRY = ("src/services/webhook_delivery_service.py", "QueuedWebhook")


def _function_def(rel_path: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """The named top-level function in *rel_path*, or fail loudly naming it.

    Failing loudly matters: if a rename made this lookup return None and the test
    silently passed, the guard would grade a function that no longer exists.
    """
    tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8"), rel_path)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(
        f"{rel_path} defines no top-level function {name!r} — this guard grades the "
        "connection-lifetime boundary and cannot silently stop grading it because something "
        "was renamed."
    )


def _class_def(rel_path: str, name: str) -> ast.ClassDef:
    """The named top-level class in *rel_path*, or fail loudly naming it."""
    tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8"), rel_path)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{rel_path} defines no top-level class {name!r} — see {_QUEUE_ENTRY!r}.")


class TestDeliveryBoundaryTakesNoDonatedSession:
    """(i) No boundary entry point accepts a caller's session, repository or UoW.

    ``deliver_adcp_webhook*`` used to take ``repo=``, and the webhook delivery service
    passed one built on ITS OWN open session. That is how a pooled connection came to be
    held across the retry loop's ``time.sleep`` and its POST to a buyer-supplied URL: the
    connection's lifetime belonged to the caller, not to the work.

    The parameter is gone, so the defect is unrepresentable at this boundary —
    ``signing_repo`` always opens its own short session, and
    ``build_adcp_multi_agent_client`` now enters it rather than taking one.

    MUTATION: add ``repo: SigningKeyRepository | None = None`` back to any signature in
    ``_DELIVERY_BOUNDARY`` and this goes RED.
    """

    @pytest.mark.arch_guard
    def test_no_delivery_entry_point_accepts_a_session(self) -> None:
        found: set[tuple[str, str]] = set()
        for rel_path, name in _DELIVERY_BOUNDARY:
            func = _function_def(rel_path, name)
            args = func.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                if arg.arg in _DONATED_SESSION_PARAMS:
                    found.add((f"{rel_path}::{name}", arg.arg))
        assert found == set(), (
            "the connection-lifetime boundary must not accept a caller's connection "
            f"lifetime; found {sorted(found)}. A donated session is held for the whole "
            "retry loop — for a delivery, across time.sleep and a POST to a buyer-supplied "
            "URL — so a hanging receiver consumes a database connection. The callee opens its own "
            "short session instead (signing_repo)."
        )


class TestQueuedWebhookCarriesPrimitivesOnly:
    """(ii) The queue entry is a frozen dataclass of primitives — no ORM, no session.

    The retry loop's only input is this entry. If it can hold nothing but plain values,
    it cannot lazy-load a relationship, cannot touch a session and cannot keep a
    connection alive across a sleep — REGARDLESS of how many call frames deep the sleep
    is, which is exactly what the discarded lexical scan could not express.

    MUTATION: annotate any field with an ORM model (``PushNotificationConfig``), a
    ``Mapped[...]``, or a repository/session type, or drop ``frozen=True``, and this
    goes RED.
    """

    #: Annotations a queued entry may carry. Anything else — an ORM model, a
    #: ``Mapped[...]``, a repository, a session — fails.
    #: ``Any`` IS DELIBERATELY ABSENT. It was admitted for one revision, because the
    #: payload is arbitrary JSON and ``Any`` is the ABSENCE of a type rather than an ORM
    #: type — but that left the one hole this guard exists to close: an ``Any``-annotated
    #: field can be handed an ORM row at runtime, which is precisely the shape that
    #: occurred (a live ``PushNotificationConfig`` on the queue entry).
    #:
    #: ``pydantic.JsonValue`` closes it with no schema project: it is the recursive JSON
    #: type (``str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]``),
    #: so an ORM object in the payload is a TYPE ERROR at every producer, enforced by mypy,
    #: rather than a runtime possibility this guard merely hopes against.
    _PRIMITIVE_ROOTS = frozenset(
        {"str", "int", "float", "bool", "bytes", "dict", "list", "tuple", "datetime", "None", "JsonValue"}
    )

    def _annotation_roots(self, node: ast.expr) -> set[str]:
        """Every bare name appearing in an annotation, unions and subscripts included."""
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
        }

    @pytest.mark.arch_guard
    def test_every_field_is_a_primitive(self) -> None:
        rel_path, name = _QUEUE_ENTRY
        cls = _class_def(rel_path, name)

        offenders: set[tuple[str, str]] = set()
        for stmt in cls.body:
            if not isinstance(stmt, ast.AnnAssign) or stmt.annotation is None:
                continue
            field = stmt.target.id if isinstance(stmt.target, ast.Name) else "<expr>"
            for root in self._annotation_roots(stmt.annotation) - self._PRIMITIVE_ROOTS:
                offenders.add((field, root))

        assert offenders == set(), (
            f"{name} must carry PRIMITIVES ONLY; found {sorted(offenders)}. The retry loop's "
            "sole input is this entry — an ORM row or a repository on it can lazy-load or hold "
            "a session open across the loop's sleep and its outbound POST. Project the values "
            "you need off the ORM row while the session is open, and queue those."
        )

    @pytest.mark.arch_guard
    def test_it_is_frozen(self) -> None:
        rel_path, name = _QUEUE_ENTRY
        cls = _class_def(rel_path, name)
        frozen = any(
            isinstance(d, ast.Call)
            and any(kw.arg == "frozen" and getattr(kw.value, "value", False) is True for kw in d.keywords)
            for d in cls.decorator_list
        )
        assert frozen, (
            f"{name} must be @dataclass(frozen=True): a mutable queue entry can be handed a "
            "session-bound object after enqueue, which reintroduces exactly what the primitive "
            "field rule removes."
        )
