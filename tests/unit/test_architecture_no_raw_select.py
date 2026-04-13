"""Guard: No raw select(OrmModel) outside repository/infrastructure files.

All ORM model data access in production code must go through repository classes.
Direct select() calls bypass tenant isolation, skip business logic, and violate
the repository pattern (CLAUDE.md Pattern #3).

ORM model names are auto-discovered from src/core/database/models.py — any new
model gets coverage automatically without manual listing.

Repository files (src/core/database/repositories/*.py) are exempt because they
ARE the abstraction layer. Infrastructure files that manage sessions/connections
are also exempt.

beads: beads-xw7 (universal no-raw-select guard)
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ── Exempt directories and files ────────────────────────────────────
# These are ALLOWED to use raw select() — they are the abstraction layer.
REPOSITORY_DIR = "src/core/database/repositories"

INFRASTRUCTURE_FILES = {
    "src/core/database/database_session.py",
    "src/core/database/database.py",
}

# ── Auto-discover ORM models ───────────────────────────────────────


def _discover_orm_model_names() -> set[str]:
    """Parse src/core/database/models.py and return all ORM model class names.

    A class is an ORM model if it inherits from Base (directly or with mixins).
    The Base class itself is excluded.
    """
    models_file = ROOT / "src" / "core" / "database" / "models.py"
    tree = ast.parse(models_file.read_text())
    model_names: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name == "Base":
            continue
        for base in node.bases:
            base_name = ""
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr
            if base_name in ("Base", "JSONValidatorMixin"):
                model_names.add(node.name)
                break

    return model_names


# Cache the model names at module load (fast — single file parse)
ORM_MODEL_NAMES = _discover_orm_model_names()

# ── Pre-existing violations (allowlist) ─────────────────────────────
# These existed before the guard was created. The allowlist can only SHRINK.
# When you fix a violation, remove it from here — the stale-entry test will
# remind you if you forget.
#
# IMPORTANT: This allowlist is DEBT, not permission. New code must use
# repository methods. See memory/feedback-allowlist-antipattern.md.
#
# FIXME(salesagent-xw7): migrate each of these to repository calls
ALLOWLIST: set[tuple[str, str]] = {
    # ── Adapters ──
    # create_line_items removed — uses pre-loaded template param (salesagent-zj9)
    ("src/adapters/gam/managers/sync.py", "_get_recent_sync"),
    ("src/adapters/gam/managers/sync.py", "get_sync_history"),
    ("src/adapters/gam/managers/sync.py", "get_sync_stats"),
    ("src/adapters/gam/managers/sync.py", "get_sync_status"),
    ("src/adapters/gam/managers/sync.py", "needs_sync"),
    ("src/adapters/gam/managers/targeting.py", "_load_axe_keys"),
    ("src/adapters/gam/managers/targeting.py", "_load_custom_targeting_key_ids"),
    # sync_custom_targeting_keys removed — uses AdapterConfigRepository (salesagent-zj9)
    ("src/adapters/gam/managers/workflow.py", "create_manual_order_workflow_step"),
    ("src/adapters/gam_reporting_api.py", "get_ad_unit_breakdown"),
    ("src/adapters/gam_reporting_api.py", "get_advertiser_summary"),
    ("src/adapters/gam_reporting_api.py", "get_country_breakdown"),
    ("src/adapters/gam_reporting_api.py", "get_gam_reporting"),
    ("src/adapters/gam_reporting_api.py", "get_principal_reporting"),
    ("src/adapters/gam_reporting_api.py", "get_principal_summary"),
    ("src/adapters/google_ad_manager.py", "create_media_buy"),
    ("src/adapters/google_ad_manager.py", "get_media_buy_delivery"),
    ("src/adapters/google_ad_manager.py", "get_packages_snapshot"),
    ("src/adapters/mock_ad_server.py", "_create_media_buy_immediate"),
    ("src/adapters/mock_ad_server.py", "mock_product_config"),
    ("src/adapters/mock_ad_server.py", "register_ui_routes"),
    ("src/adapters/mock_ad_server.py", "wrapped_view"),
    ("src/adapters/xandr.py", "_create_human_task"),
    # ── Admin app ──
    ("src/admin/app.py", "create_app"),
    ("src/admin/app.py", "inject_context"),
    # ── Admin blueprints ──
    ("src/admin/blueprints/activity_stream.py", "get_recent_activities"),
    ("src/admin/blueprints/adapters.py", "list_broadstreet_zones"),
    ("src/admin/blueprints/adapters.py", "mock_config"),
    ("src/admin/blueprints/adapters.py", "save_adapter_config"),
    ("src/admin/blueprints/api.py", "get_tenant_products"),
    ("src/admin/blueprints/auth.py", "gam_authorize"),
    ("src/admin/blueprints/auth.py", "gam_callback"),
    ("src/admin/blueprints/auth.py", "google_callback"),
    ("src/admin/blueprints/auth.py", "login"),
    ("src/admin/blueprints/auth.py", "logout"),
    ("src/admin/blueprints/auth.py", "tenant_login"),
    ("src/admin/blueprints/auth.py", "test_auth"),
    ("src/admin/blueprints/authorized_properties.py", "_construct_agent_url"),
    ("src/admin/blueprints/authorized_properties.py", "_save_properties_batch"),
    ("src/admin/blueprints/authorized_properties.py", "create_property"),
    ("src/admin/blueprints/authorized_properties.py", "create_property_tag"),
    ("src/admin/blueprints/authorized_properties.py", "delete_property"),
    ("src/admin/blueprints/authorized_properties.py", "edit_property"),
    ("src/admin/blueprints/authorized_properties.py", "list_authorized_properties"),
    ("src/admin/blueprints/authorized_properties.py", "list_authorized_properties_api"),
    ("src/admin/blueprints/authorized_properties.py", "list_property_tags"),
    ("src/admin/blueprints/authorized_properties.py", "sync_properties_from_adagents"),
    ("src/admin/blueprints/authorized_properties.py", "upload_authorized_properties"),
    ("src/admin/blueprints/core.py", "create_tenant"),
    ("src/admin/blueprints/core.py", "get_tenant_from_hostname"),
    ("src/admin/blueprints/core.py", "index"),
    ("src/admin/blueprints/core.py", "reactivate_tenant"),
    ("src/admin/blueprints/core.py", "render_super_admin_index"),
    ("src/admin/blueprints/creative_agents.py", "add_creative_agent"),
    ("src/admin/blueprints/creative_agents.py", "delete_creative_agent"),
    ("src/admin/blueprints/creative_agents.py", "edit_creative_agent"),
    ("src/admin/blueprints/creative_agents.py", "list_creative_agents"),
    ("src/admin/blueprints/creative_agents.py", "test_creative_agent"),
    ("src/admin/blueprints/gam.py", "configure_gam"),
    ("src/admin/blueprints/gam.py", "get_gam_custom_targeting_keys"),
    ("src/admin/blueprints/gam.py", "get_gam_line_item_api"),
    ("src/admin/blueprints/gam.py", "get_latest_sync_status"),
    ("src/admin/blueprints/gam.py", "get_sync_status"),
    ("src/admin/blueprints/gam.py", "reset_stuck_sync"),
    ("src/admin/blueprints/gam.py", "test_gam_connection"),
    ("src/admin/blueprints/gam.py", "view_gam_line_item"),
    ("src/admin/blueprints/inventory.py", "analyze_ad_server_inventory"),
    ("src/admin/blueprints/inventory.py", "check_inventory_sync"),
    ("src/admin/blueprints/inventory.py", "get_inventory_list"),
    ("src/admin/blueprints/inventory.py", "get_inventory_sizes"),
    ("src/admin/blueprints/inventory.py", "get_inventory_tree"),
    ("src/admin/blueprints/inventory.py", "get_order_details"),
    ("src/admin/blueprints/inventory.py", "get_orders"),
    ("src/admin/blueprints/inventory.py", "get_sync_status"),
    ("src/admin/blueprints/inventory.py", "get_targeting_data"),
    ("src/admin/blueprints/inventory.py", "get_targeting_values"),
    ("src/admin/blueprints/inventory.py", "inventory_browser"),
    ("src/admin/blueprints/inventory.py", "orders_browser"),
    ("src/admin/blueprints/inventory.py", "sync_inventory"),
    ("src/admin/blueprints/inventory.py", "sync_orders"),
    ("src/admin/blueprints/inventory.py", "targeting_browser"),
    ("src/admin/blueprints/inventory_profiles.py", "add_inventory_profile"),
    ("src/admin/blueprints/inventory_profiles.py", "edit_inventory_profile"),
    ("src/admin/blueprints/inventory_profiles.py", "list_inventory_profiles"),
    ("src/admin/blueprints/inventory_profiles.py", "list_inventory_profiles_api"),
    ("src/admin/blueprints/oidc.py", "callback"),
    ("src/admin/blueprints/oidc.py", "enable"),
    ("src/admin/blueprints/oidc.py", "login"),
    ("src/admin/blueprints/oidc.py", "test_initiate"),
    ("src/admin/blueprints/operations.py", "approve_media_buy"),
    ("src/admin/blueprints/operations.py", "media_buy_detail"),
    ("src/admin/blueprints/operations.py", "reporting"),
    ("src/admin/blueprints/policy.py", "index"),
    ("src/admin/blueprints/policy.py", "review_task"),
    ("src/admin/blueprints/policy.py", "update"),
    ("src/admin/blueprints/principals.py", "create_principal"),
    ("src/admin/blueprints/principals.py", "delete_principal"),
    ("src/admin/blueprints/principals.py", "delete_webhook"),
    ("src/admin/blueprints/principals.py", "edit_principal"),
    ("src/admin/blueprints/principals.py", "get_gam_advertisers"),
    ("src/admin/blueprints/principals.py", "get_principal"),
    ("src/admin/blueprints/principals.py", "get_principal_config"),
    ("src/admin/blueprints/principals.py", "list_principals"),
    ("src/admin/blueprints/principals.py", "manage_webhooks"),
    ("src/admin/blueprints/principals.py", "register_webhook"),
    ("src/admin/blueprints/principals.py", "save_testing_config"),
    ("src/admin/blueprints/principals.py", "toggle_webhook"),
    ("src/admin/blueprints/principals.py", "update_mappings"),
    ("src/admin/blueprints/products.py", "_render_add_product_form"),
    ("src/admin/blueprints/products.py", "add_product"),
    ("src/admin/blueprints/products.py", "assign_inventory_to_product"),
    ("src/admin/blueprints/products.py", "delete_product"),
    ("src/admin/blueprints/products.py", "edit_product"),
    ("src/admin/blueprints/products.py", "get_product_inventory"),
    ("src/admin/blueprints/products.py", "list_products"),
    ("src/admin/blueprints/products.py", "unassign_inventory_from_product"),
    ("src/admin/blueprints/public.py", "landing"),
    ("src/admin/blueprints/public.py", "provision_tenant"),
    ("src/admin/blueprints/public.py", "signup_complete"),
    ("src/admin/blueprints/publisher_partners.py", "add_publisher_partner"),
    ("src/admin/blueprints/publisher_partners.py", "delete_publisher_partner"),
    ("src/admin/blueprints/publisher_partners.py", "get_publisher_properties"),
    ("src/admin/blueprints/publisher_partners.py", "list_publisher_partners"),
    ("src/admin/blueprints/publisher_partners.py", "sync_publisher_partners"),
    ("src/admin/blueprints/settings.py", "get_approximated_token"),
    ("src/admin/blueprints/settings.py", "register_approximated_domain"),
    ("src/admin/blueprints/settings.py", "test_ai_connection"),
    ("src/admin/blueprints/settings.py", "update_adapter"),
    ("src/admin/blueprints/settings.py", "update_ai"),
    ("src/admin/blueprints/settings.py", "update_business_rules"),
    ("src/admin/blueprints/settings.py", "update_general"),
    ("src/admin/blueprints/settings.py", "update_slack"),
    ("src/admin/blueprints/signals_agents.py", "add_signals_agent"),
    ("src/admin/blueprints/signals_agents.py", "delete_signals_agent"),
    ("src/admin/blueprints/signals_agents.py", "edit_signals_agent"),
    ("src/admin/blueprints/signals_agents.py", "list_signals_agents"),
    ("src/admin/blueprints/signals_agents.py", "test_signals_agent"),
    ("src/admin/blueprints/tenants.py", "deactivate_tenant"),
    ("src/admin/blueprints/tenants.py", "media_buys_list"),
    ("src/admin/blueprints/tenants.py", "remove_favicon"),
    ("src/admin/blueprints/tenants.py", "setup_checklist"),
    ("src/admin/blueprints/tenants.py", "tenant_settings"),
    ("src/admin/blueprints/tenants.py", "test_slack"),
    ("src/admin/blueprints/tenants.py", "update"),
    ("src/admin/blueprints/tenants.py", "update_favicon_url"),
    ("src/admin/blueprints/tenants.py", "update_slack"),
    ("src/admin/blueprints/tenants.py", "upload_favicon"),
    ("src/admin/blueprints/users.py", "add_domain"),
    ("src/admin/blueprints/users.py", "add_user"),
    ("src/admin/blueprints/users.py", "disable_setup_mode"),
    ("src/admin/blueprints/users.py", "enable_setup_mode"),
    ("src/admin/blueprints/users.py", "list_users"),
    ("src/admin/blueprints/users.py", "remove_domain"),
    ("src/admin/blueprints/users.py", "toggle_user"),
    ("src/admin/blueprints/users.py", "update_role"),
    ("src/admin/blueprints/workflows.py", "approve_workflow_step"),  # select(CreativeAssignment) — no creative repo yet
    ("src/admin/blueprints/workflows.py", "list_workflows"),  # select(Tenant) — no tenant repo yet
    ("src/admin/blueprints/workflows.py", "review_workflow_step"),  # select(Context) — context lookup
    # ── Admin services / utils ──
    ("src/admin/domain_access.py", "add_authorized_domain"),
    ("src/admin/domain_access.py", "add_authorized_email"),
    ("src/admin/domain_access.py", "ensure_user_in_tenant"),
    ("src/admin/domain_access.py", "find_tenant_by_authorized_domain"),
    ("src/admin/domain_access.py", "find_tenants_by_authorized_email"),
    ("src/admin/domain_access.py", "find_tenants_by_user_record"),
    ("src/admin/domain_access.py", "remove_authorized_domain"),
    ("src/admin/domain_access.py", "remove_authorized_email"),
    ("src/admin/services/business_activity_service.py", "get_business_activities"),
    ("src/admin/services/dashboard_service.py", "get_tenant"),
    ("src/admin/services/media_buy_readiness_service.py", "get_readiness_state"),
    ("src/admin/sync_api.py", "get_sync_history"),
    ("src/admin/sync_api.py", "get_sync_stats"),
    ("src/admin/sync_api.py", "get_sync_status"),
    ("src/admin/auth_helpers.py", "get_api_key_from_config"),  # Shared auth helper — single select for API key lookup
    ("src/admin/sync_api.py", "initialize_tenant_management_api_key"),
    ("src/admin/sync_api.py", "list_tenants"),
    ("src/admin/sync_api.py", "sync_tenant_orders"),
    ("src/admin/sync_api.py", "trigger_sync"),
    ("src/admin/tenant_management_api.py", "delete_tenant"),
    ("src/admin/tenant_management_api.py", "get_tenant"),
    ("src/admin/tenant_management_api.py", "update_tenant"),
    ("src/admin/utils/helpers.py", "decorated_function"),
    ("src/admin/utils/helpers.py", "decorator"),
    ("src/admin/utils/helpers.py", "get_custom_targeting_mappings"),
    ("src/admin/utils/helpers.py", "get_tenant_config_from_db"),
    ("src/admin/utils/helpers.py", "is_super_admin"),
    ("src/admin/utils/helpers.py", "is_tenant_admin"),
    ("src/admin/utils/helpers.py", "require_tenant_access"),
    # ── Core ──
    ("src/core/audit_logger.py", "log_operation"),
    ("src/core/audit_logger.py", "log_security_violation"),
    ("src/core/auth_utils.py", "_lookup_principal"),
    ("src/core/auth_utils.py", "get_principal_from_token"),
    ("src/core/config_loader.py", "ensure_default_tenant_exists"),
    ("src/core/config_loader.py", "get_default_tenant"),
    ("src/core/config_loader.py", "get_tenant_by_id"),
    ("src/core/config_loader.py", "get_tenant_by_subdomain"),
    ("src/core/config_loader.py", "get_tenant_by_virtual_host"),
    ("src/core/context_manager.py", "_send_push_notifications"),
    ("src/core/context_manager.py", "add_message"),
    ("src/core/context_manager.py", "get_context"),
    ("src/core/context_manager.py", "get_context_status"),
    ("src/core/context_manager.py", "get_contexts_for_principal"),
    ("src/core/context_manager.py", "get_object_lifecycle"),
    ("src/core/context_manager.py", "get_pending_steps"),
    ("src/core/context_manager.py", "link_workflow_to_object"),
    ("src/core/context_manager.py", "update_activity"),
    ("src/core/context_manager.py", "update_workflow_step"),
    ("src/core/database/queries.py", "get_ai_accuracy_metrics"),
    ("src/core/database/queries.py", "get_ai_review_stats"),
    ("src/core/database/queries.py", "get_creative_reviews"),
    ("src/core/database/queries.py", "get_creative_with_latest_review"),
    ("src/core/database/queries.py", "get_creatives_needing_human_review"),
    ("src/core/database/queries.py", "get_recent_reviews"),
    # adapter_helpers.py removed — now uses AdapterConfigRepository (salesagent-zj9)
    ("src/core/strategy.py", "_load_state"),
    ("src/core/strategy.py", "_upsert_state"),
    ("src/core/tenant_status.py", "get_tenant_status"),
    ("src/core/tenant_status.py", "is_tenant_ad_server_configured"),
    # ── Core tools ──
    ("src/core/tools/media_buy_create.py", "_create_media_buy_impl"),
    ("src/core/tools/media_buy_create.py", "execute_approved_media_buy"),
    ("src/core/tools/media_buy_list.py", "_fetch_creative_approvals"),
    ("src/core/tools/media_buy_update.py", "_update_media_buy_impl"),
    # ── Routes ──
    ("src/routes/health.py", "debug_db_state"),
    ("src/routes/health.py", "debug_root_logic"),
    # ── Services ──
    ("src/services/auth_config_service.py", "delete_oidc_config"),
    ("src/services/auth_config_service.py", "disable_oidc"),
    ("src/services/auth_config_service.py", "enable_oidc"),
    ("src/services/auth_config_service.py", "get_auth_config_summary"),
    ("src/services/auth_config_service.py", "get_oidc_config_for_auth"),
    ("src/services/auth_config_service.py", "get_or_create_auth_config"),
    ("src/services/auth_config_service.py", "get_tenant_auth_config"),
    ("src/services/auth_config_service.py", "is_oidc_config_valid"),
    ("src/services/auth_config_service.py", "mark_oidc_verified"),
    ("src/services/auth_config_service.py", "save_oidc_config"),
    # _run_approval_polling_thread removed — uses AdapterConfigRepository (salesagent-zj9)
    ("src/services/background_sync_service.py", "_mark_sync_complete"),
    ("src/services/background_sync_service.py", "_mark_sync_failed"),
    ("src/services/background_sync_service.py", "_run_sync_thread"),
    ("src/services/background_sync_service.py", "_update_sync_progress"),
    ("src/services/background_sync_service.py", "start_inventory_sync_background"),
    ("src/services/delivery_simulator.py", "restart_active_simulations"),
    ("src/services/delivery_webhook_scheduler.py", "_send_report_for_media_buy"),
    ("src/services/dynamic_pricing_service.py", "_calculate_product_pricing"),
    ("src/services/dynamic_products.py", "archive_expired_variants"),
    ("src/services/dynamic_products.py", "generate_variants_for_brief"),
    ("src/services/dynamic_products.py", "generate_variants_from_signals"),
    ("src/services/format_metrics_service.py", "_process_and_store_metrics"),
    ("src/services/format_metrics_service.py", "aggregate_all_tenants"),
    # _update_adapter_config_targeting_keys removed — uses AdapterConfigRepository (salesagent-zj9)
    ("src/services/gam_inventory_service.py", "_upsert_inventory_item"),
    ("src/services/gam_inventory_service.py", "create_inventory_endpoints"),
    ("src/services/gam_inventory_service.py", "fetch_custom_targeting_values"),
    ("src/services/gam_inventory_service.py", "get_ad_unit_tree"),
    ("src/services/gam_inventory_service.py", "get_all_targeting_data"),
    ("src/services/gam_inventory_service.py", "get_product_inventory"),
    ("src/services/gam_inventory_service.py", "search_inventory"),
    ("src/services/gam_inventory_service.py", "suggest_inventory_for_product"),
    ("src/services/gam_inventory_service.py", "sync_inventory"),
    ("src/services/gam_inventory_service.py", "update_product_inventory"),
    ("src/services/gam_orders_service.py", "_order_to_dict"),
    ("src/services/gam_orders_service.py", "_upsert_line_item"),
    ("src/services/gam_orders_service.py", "_upsert_order"),
    ("src/services/gam_orders_service.py", "get_line_items"),
    ("src/services/gam_orders_service.py", "get_order_details"),
    ("src/services/gam_orders_service.py", "get_orders"),
    ("src/services/gcp_service_account_service.py", "create_service_account_for_tenant"),
    ("src/services/gcp_service_account_service.py", "delete_service_account"),
    ("src/services/gcp_service_account_service.py", "get_service_account_email"),
    ("src/services/media_buy_status_scheduler.py", "_are_creatives_approved"),
    ("src/services/order_approval_service.py", "_mark_approval_complete"),
    ("src/services/order_approval_service.py", "_mark_approval_failed"),
    # _run_approval_thread removed — uses AdapterConfigRepository (salesagent-zj9)
    ("src/services/order_approval_service.py", "_send_approval_webhook"),
    ("src/services/order_approval_service.py", "_update_approval_progress"),
    ("src/services/order_approval_service.py", "get_approval_status"),
    ("src/services/order_approval_service.py", "start_order_approval_background"),
    ("src/services/policy_service.py", "_update_currencies"),
    ("src/services/policy_service.py", "get_policies"),
    ("src/services/policy_service.py", "update_policies"),
    ("src/services/property_discovery_service.py", "_create_or_update_property"),
    ("src/services/property_discovery_service.py", "_create_or_update_tag"),
    ("src/services/property_discovery_service.py", "sync_properties_from_adagents"),
    ("src/services/property_verification_service.py", "_verify_property_async"),
    ("src/services/property_verification_service.py", "verify_all_properties"),
    ("src/services/setup_checklist_service.py", "_check_critical_tasks"),
    ("src/services/setup_checklist_service.py", "_check_optional_tasks"),
    ("src/services/setup_checklist_service.py", "get_bulk_setup_status"),
    ("src/services/setup_checklist_service.py", "get_setup_status"),
    ("src/services/webhook_delivery_service.py", "_send_webhook_enhanced"),
}

EXPECTED_VIOLATION_COUNT = len(ALLOWLIST)


# ── Scanner ─────────────────────────────────────────────────────────


def _find_raw_selects() -> list[tuple[str, str, str, int]]:
    """Find select(OrmModel) calls in src/ outside repository/infrastructure files.

    Returns list of (file_path, function_name, model_name, line_number).
    """
    violations: list[tuple[str, str, str, int]] = []
    src_dir = ROOT / "src"

    for py_file in src_dir.rglob("*.py"):
        rel_path = str(py_file.relative_to(ROOT))

        # Skip repository files — they ARE the abstraction layer
        if rel_path.startswith(REPOSITORY_DIR):
            continue

        # Skip infrastructure files
        if rel_path in INFRASTRUCTURE_FILES:
            continue

        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue

                func = child.func
                if not (isinstance(func, ast.Name) and func.id == "select"):
                    continue

                if not child.args:
                    continue

                model_arg = child.args[0]
                model_name = None
                if isinstance(model_arg, ast.Name):
                    model_name = model_arg.id
                elif isinstance(model_arg, ast.Attribute):
                    model_name = model_arg.attr

                if model_name and model_name in ORM_MODEL_NAMES:
                    violations.append((rel_path, node.name, model_name, child.lineno))
                    break  # One violation per function is enough

    return violations


# ── Tests ───────────────────────────────────────────────────────────


class TestNoRawSelectOutsideRepositories:
    """No raw select(OrmModel) outside repository/infrastructure files."""

    def test_no_new_raw_selects(self):
        """New raw select(OrmModel) calls fail immediately.

        If you need to query a model, use the appropriate repository class.
        If no repository method exists for your use case, add one to the
        repository FIRST, then call it from your code.
        """
        all_violations = _find_raw_selects()

        new_violations = [(f, fn, model, line) for f, fn, model, line in all_violations if (f, fn) not in ALLOWLIST]

        if new_violations:
            msg_lines = [
                "New raw select(OrmModel) calls found outside repositories:",
                "",
            ]
            for f, fn, model, line in new_violations:
                msg_lines.append(f"  {f}:{line} in {fn}() — select({model})")
            msg_lines.extend(
                [
                    "",
                    "Fix: Use the appropriate repository class instead of raw select().",
                    "If no repository method exists, add one to src/core/database/repositories/",
                    "FIRST, then call it from your code.",
                    "",
                    "This allowlist is DEBT, not permission. See CLAUDE.md Pattern #3.",
                ]
            )
            raise AssertionError("\n".join(msg_lines))

    def test_allowlist_entries_still_exist(self):
        """Every allowlisted violation must still exist (stale entry detection).

        When you fix a violation (migrate to repository), remove it from ALLOWLIST.
        This test catches stale entries so the allowlist stays honest.
        """
        all_violations = {(f, fn) for f, fn, _model, _line in _find_raw_selects()}

        stale = ALLOWLIST - all_violations
        if stale:
            msg_lines = [
                "Stale allowlist entries (violation was fixed — remove from ALLOWLIST):",
                "",
            ]
            for f, fn in sorted(stale):
                msg_lines.append(f"  ({f!r}, {fn!r}),")
            raise AssertionError("\n".join(msg_lines))

    def test_violation_count_matches(self):
        """Total violations match expected count.

        If you fixed a violation, remove it from ALLOWLIST.
        If you added one, DON'T — use a repository instead.
        """
        all_violations = _find_raw_selects()
        actual = len(all_violations)
        assert actual == EXPECTED_VIOLATION_COUNT, (
            f"Expected {EXPECTED_VIOLATION_COUNT} allowlisted violations, "
            f"found {actual}. If you fixed a violation, remove it from ALLOWLIST. "
            f"If you added one, DON'T — use the repository instead."
        )
