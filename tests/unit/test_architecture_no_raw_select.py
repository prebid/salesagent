"""Guard: No raw select(OrmModel) outside repository/infrastructure files.

All ORM model data access in production code must go through repository classes.
Direct select() calls bypass tenant isolation, skip business logic, and violate
the repository pattern (CLAUDE.md Pattern #3).

ORM model names come from the live mapper registry (``orm_classes``) — any new
model gets coverage automatically without manual listing.

Repository files (src/core/database/repositories/*.py) are exempt because they
ARE the abstraction layer. Infrastructure files that manage sessions/connections
are also exempt.

"""

import ast

import pytest

from tests.unit._architecture_helpers import (
    assert_violations_match_allowlist,
    find_raw_select_violations,
    mapped,
    orm_classes,
    raw_select_rows,
)

# ── Exempt directories and files ────────────────────────────────────
# These are ALLOWED to use raw select() — they are the abstraction layer.
REPOSITORY_DIR = "src/core/database/repositories"

INFRASTRUCTURE_FILES = {
    "src/core/database/database_session.py",
    "src/core/database/database.py",
}

# ── ORM models: every mapped class, from the one shared inventory ──
ORM_MODEL_NAMES = set(orm_classes())

# ── Pre-existing violations (allowlist) ─────────────────────────────
# These existed before the guard was created. The allowlist can only SHRINK.
# When you fix a violation, remove it from here — the stale-entry test will
# remind you if you forget.
#
# IMPORTANT: This allowlist is DEBT, not permission. New code must use
# repository methods. See memory/feedback-allowlist-antipattern.md.
#
# FIXME(#1119): migrate each of these to repository calls
ALLOWLIST: set[tuple[str, str]] = {
    # ── Adapters ──
    # create_line_items removed — uses pre-loaded template param
    ("src/adapters/gam/managers/sync.py", "_get_recent_sync"),
    ("src/adapters/gam/managers/sync.py", "get_sync_history"),
    ("src/adapters/gam/managers/sync.py", "get_sync_stats"),
    ("src/adapters/gam/managers/sync.py", "get_sync_status"),
    ("src/adapters/gam/managers/sync.py", "needs_sync"),
    ("src/adapters/gam/managers/targeting.py", "_load_axe_keys"),
    ("src/adapters/gam/managers/targeting.py", "_load_custom_targeting_key_ids"),
    # sync_custom_targeting_keys removed — uses AdapterConfigRepository
    ("src/adapters/google_ad_manager.py", "create_media_buy"),
    ("src/adapters/google_ad_manager.py", "get_packages_snapshot"),
    ("src/adapters/xandr.py", "_create_human_task"),
    # ── Admin app ──
    # create_app removed — its one select is in the nested inject_context, which holds it
    ("src/admin/app.py", "inject_context"),
    # ── Admin blueprints ──
    ("src/admin/blueprints/activity_stream.py", "get_recent_activities"),
    ("src/admin/blueprints/adapters.py", "list_broadstreet_zones"),
    ("src/admin/blueprints/adapters.py", "save_adapter_config"),
    ("src/admin/blueprints/api.py", "get_tenant_products"),
    ("src/admin/blueprints/auth.py", "gam_authorize"),
    ("src/admin/blueprints/auth.py", "gam_callback"),
    ("src/admin/blueprints/auth.py", "google_callback"),
    ("src/admin/blueprints/auth.py", "login"),
    ("src/admin/blueprints/auth.py", "logout"),
    ("src/admin/blueprints/auth.py", "tenant_login"),
    # _construct_agent_url removed — the agent URL is stored tenant state, reached
    # through a repository instead of selected here
    ("src/admin/blueprints/authorized_properties.py", "create_property_tag"),
    ("src/admin/blueprints/authorized_properties.py", "list_property_tags"),
    # create_tenant fixed — its duplicate check goes through TenantLookupRepository
    # get_tenant_from_hostname fixed — the admin plane's one host -> tenant lookup now goes
    # through TenantLookupRepository.find_active_by_virtual_host, and public.landing and
    # auth.login call it instead of each hand-rolling the same select (#2263)
    ("src/admin/blueprints/core.py", "index"),
    ("src/admin/blueprints/core.py", "reactivate_tenant"),
    # render_super_admin_index removed — its tenant page and count come from
    # TenantLookupRepository.list_active / count_active
    ("src/admin/blueprints/creative_agents.py", "add_creative_agent"),
    ("src/admin/blueprints/creative_agents.py", "delete_creative_agent"),
    ("src/admin/blueprints/creative_agents.py", "edit_creative_agent"),
    ("src/admin/blueprints/creative_agents.py", "list_creative_agents"),
    ("src/admin/blueprints/creative_agents.py", "test_creative_agent"),
    ("src/admin/blueprints/gam.py", "configure_gam"),
    ("src/admin/blueprints/gam.py", "get_gam_custom_targeting_keys"),
    ("src/admin/blueprints/gam.py", "get_gam_line_item_api"),
    ("src/admin/blueprints/gam.py", "get_latest_sync_status"),
    ("src/admin/blueprints/gam.py", "reset_stuck_sync"),
    ("src/admin/blueprints/gam.py", "test_gam_connection"),
    ("src/admin/blueprints/gam.py", "view_gam_line_item"),
    ("src/admin/blueprints/inventory.py", "analyze_ad_server_inventory"),
    # check_inventory_sync removed — its count and last sync come from GAMInventoryRepository
    ("src/admin/blueprints/inventory.py", "get_inventory_list"),
    ("src/admin/blueprints/inventory.py", "get_inventory_sizes"),
    ("src/admin/blueprints/inventory.py", "_batch_fetch_ancestors"),  # FIXME(#1119): extract to repository
    ("src/admin/blueprints/inventory.py", "get_inventory_tree"),
    ("src/admin/blueprints/inventory.py", "get_orders"),
    # get_sync_status removed — its last inventory job comes from SyncJobRepository.latest
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
    ("src/admin/blueprints/principals.py", "create_principal"),
    # delete_principal removed — the lookup goes through PrincipalRepository.get()
    # delete_webhook / register_webhook / toggle_webhook removed — rewired onto
    # PushNotificationConfigRepository via PushNotificationConfigUoW (salesagent-tayg;
    # both sides removed these independently). manage_webhooks below deliberately KEEPS
    # its raw select — it must list INACTIVE rows, which list_active_by_principal cannot.
    ("src/admin/blueprints/principals.py", "edit_principal"),
    ("src/admin/blueprints/principals.py", "get_gam_advertisers"),
    ("src/admin/blueprints/principals.py", "list_principals"),
    ("src/admin/blueprints/principals.py", "manage_webhooks"),
    ("src/admin/blueprints/products.py", "_render_add_product_form"),
    ("src/admin/blueprints/products.py", "add_product"),
    ("src/admin/blueprints/products.py", "assign_inventory_to_product"),
    ("src/admin/blueprints/products.py", "delete_product"),
    ("src/admin/blueprints/products.py", "edit_product"),
    ("src/admin/blueprints/products.py", "get_product_inventory"),
    ("src/admin/blueprints/products.py", "list_products"),
    ("src/admin/blueprints/products.py", "unassign_inventory_from_product"),
    # landing fixed — it asks core.get_tenant_from_hostname instead of selecting tenants
    ("src/admin/blueprints/public.py", "provision_tenant"),
    ("src/admin/blueprints/public.py", "signup_complete"),
    ("src/admin/blueprints/settings.py", "test_ai_connection"),
    ("src/admin/blueprints/settings.py", "update_adapter"),
    ("src/admin/blueprints/settings.py", "update_ai"),
    ("src/admin/blueprints/settings.py", "update_business_rules"),
    ("src/admin/blueprints/settings.py", "update_general"),
    ("src/admin/blueprints/signals_agents.py", "add_signals_agent"),
    ("src/admin/blueprints/signals_agents.py", "edit_signals_agent"),
    ("src/admin/blueprints/signals_agents.py", "list_signals_agents"),
    ("src/admin/blueprints/signals_agents.py", "test_signals_agent"),
    ("src/admin/blueprints/tenants.py", "deactivate_tenant"),
    ("src/admin/blueprints/tenants.py", "media_buys_list"),
    ("src/admin/blueprints/tenants.py", "remove_favicon"),
    ("src/admin/blueprints/tenants.py", "setup_checklist"),
    ("src/admin/blueprints/tenants.py", "tenant_settings"),
    ("src/admin/blueprints/tenants.py", "update"),
    ("src/admin/blueprints/tenants.py", "update_favicon_url"),
    ("src/admin/blueprints/tenants.py", "upload_favicon"),
    # add_domain/remove_domain removed — atomic authorized-list mutation via
    # TenantConfigRepository/TenantConfigUoW (salesagent-v8dt); ditto the four
    # domain_access add/remove_authorized_* helpers.
    ("src/admin/blueprints/users.py", "add_user"),
    ("src/admin/blueprints/users.py", "disable_setup_mode"),
    ("src/admin/blueprints/users.py", "enable_setup_mode"),
    ("src/admin/blueprints/users.py", "list_users"),
    ("src/admin/blueprints/workflows.py", "list_workflows"),  # select(Tenant) — no tenant repo yet
    ("src/admin/blueprints/workflows.py", "review_workflow_step"),  # select(Context) — context lookup
    # ── Alias-hidden, now visible (#2128) ──
    # It wrote `from ...models import X as ModelX` and then `select(ModelX)`, which the
    # finder judged by the local name and so never counted. The finder resolves aliases
    # now, so it is debt that shows rather than debt that hides.
    ("src/routes/health.py", "debug_db_state"),
    # ── Admin services / utils ──
    ("src/admin/domain_access.py", "ensure_user_in_tenant"),
    ("src/admin/domain_access.py", "find_tenant_by_authorized_domain"),
    ("src/admin/domain_access.py", "find_tenants_by_authorized_email"),
    ("src/admin/domain_access.py", "find_tenants_by_user_record"),
    ("src/admin/services/business_activity_service.py", "get_business_activities"),
    ("src/admin/services/dashboard_service.py", "get_tenant"),
    ("src/admin/services/media_buy_readiness_service.py", "get_readiness_state"),
    ("src/admin/sync_api.py", "get_sync_history"),
    ("src/admin/sync_api.py", "get_sync_stats"),
    ("src/admin/sync_api.py", "get_sync_status"),
    # auth_helpers.get_api_key_from_config and sync_api.initialize_tenant_management_api_key
    # fixed — both went through TenantManagementConfigRepository when the operator API key
    # stopped being stored in plaintext (salesagent-3cs7o.18)
    ("src/admin/sync_api.py", "list_tenants"),
    ("src/admin/sync_api.py", "sync_tenant_orders"),
    ("src/admin/sync_api.py", "trigger_sync"),
    ("src/admin/tenant_management_api.py", "delete_tenant"),
    ("src/admin/tenant_management_api.py", "get_tenant"),
    ("src/admin/tenant_management_api.py", "update_tenant"),
    # is_super_admin and require_tenant_access fixed — the super-admin rows go through
    # TenantManagementConfigRepository, the caller's User row through TenantConfigRepository
    ("src/admin/utils/helpers.py", "get_tenant_config_from_db"),
    # ── Core ──
    ("src/core/audit_logger.py", "log_operation"),
    ("src/core/audit_logger.py", "log_security_violation"),
    # config_loader's tenant lookups fixed — every lookup it still has delegates to
    # TenantLookupRepository, which owns every cross-tenant query on ``tenants`` (#2263).
    # ensure_default_tenant_exists still CREATES the row; only its lookup moved.
    # add_message removed — now sa_update + jsonb_list_append, zero selects.
    # _send_push_notifications removed — its config lookup routes through
    # PushNotificationConfigRepository.
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
    # adapter_helpers.py removed — now uses AdapterConfigRepository
    ("src/core/tenant_status.py", "get_tenant_status"),
    ("src/core/tenant_status.py", "is_tenant_ad_server_configured"),
    # ── Core tools ──
    ("src/core/tools/media_buy_create.py", "_create_media_buy_impl"),
    ("src/core/tools/media_buy_create.py", "execute_approved_media_buy"),
    ("src/core/tools/media_buy_list.py", "_fetch_creative_approvals"),
    # ── Routes ──
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
    # _run_approval_polling_thread removed — uses AdapterConfigRepository
    ("src/services/background_sync_service.py", "_mark_sync_complete"),
    ("src/services/background_sync_service.py", "_run_sync_thread"),
    ("src/services/background_sync_service.py", "_update_sync_progress"),
    ("src/services/background_sync_service.py", "start_inventory_sync_background"),
    ("src/services/delivery_simulator.py", "restart_active_simulations"),
    ("src/services/delivery_webhook_scheduler.py", "_send_report_for_media_buy"),
    ("src/services/dynamic_pricing_service.py", "_calculate_product_pricing"),
    ("src/services/dynamic_products.py", "archive_expired_variants"),
    ("src/services/format_metrics_service.py", "_process_and_store_metrics"),
    ("src/services/format_metrics_service.py", "aggregate_all_tenants"),
    # _save_inventory_to_db, _update_adapter_config_targeting_keys, _write_custom_targeting_keys and
    # _write_inventory_batch removed — the sync reads gam_inventory through GAMInventoryRepository.
    # get_ad_unit_tree and get_all_targeting_data deleted: #2571 dropped the endpoints that called them
    ("src/services/gam_inventory_service.py", "_upsert_inventory_item"),
    ("src/services/gam_inventory_service.py", "get_product_inventory"),
    ("src/services/gam_inventory_service.py", "search_inventory"),
    ("src/services/gam_inventory_service.py", "suggest_inventory_for_product"),
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
    # _run_approval_thread removed — uses AdapterConfigRepository (salesagent-zj9)
    # _send_approval_webhook removed — auth lookup moved to _build_approval_webhook_headers
    # which routes through PushNotificationConfigRepository (C901 ratchet fix)
    ("src/services/order_approval_service.py", "_update_approval_progress"),
    ("src/services/order_approval_service.py", "get_approval_status"),
    ("src/services/order_approval_service.py", "start_order_approval_background"),
    ("src/services/policy_service.py", "_update_currencies"),
    ("src/services/policy_service.py", "get_policies"),
    ("src/services/policy_service.py", "update_policies"),
    # _check_critical_tasks, _check_optional_tasks and get_bulk_setup_status removed — the
    # tenant, its auth config and its verified partners come through the repositories
    ("src/services/setup_checklist_service.py", "get_setup_status"),
}

EXPECTED_VIOLATION_COUNT = len(ALLOWLIST)


# ── Scanner ─────────────────────────────────────────────────────────


def _find_raw_selects() -> list[tuple[str, str, str, int]]:
    """Find select(OrmModel) calls in src/ outside repository/infrastructure files.

    Returns list of (file_path, function_name, model_name, line_number).
    """
    return find_raw_select_violations(
        skip=lambda rel_path: rel_path.startswith(REPOSITORY_DIR) or rel_path in INFRASTRUCTURE_FILES,
        models=ORM_MODEL_NAMES,
    )


_PREAMBLE = "from sqlalchemy import select\nfrom src.core.database.models import Tenant, User\n"

# label -> (source after the preamble, the (function, model) rows the detector gives, in
# scan order). At most one row per function, keyed by the function that holds the call.
_TABLE: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "whole-class": ("def f():\n    return select(Tenant)\n", [("f", "Tenant")]),
    "column-projection": ("def f():\n    return select(Tenant.tenant_id)\n", [("f", "Tenant")]),
    "module-level": ("q = select(Tenant)\n", [("<module>", "Tenant")]),
    "nested-def-counts-for-itself": (
        "def f():\n    def g():\n        return select(Tenant)\n\n    return g\n",
        [("g", "Tenant")],
    ),
    "lambda-counts-for-its-function": ("def f():\n    return lambda: select(Tenant)\n", [("f", "Tenant")]),
    "comprehension-counts-for-its-function": (
        "def f(xs):\n    return [select(Tenant) for x in xs]\n",
        [("f", "Tenant")],
    ),
    "aliased-import": (
        "from src.core.database.models import Tenant as DBTenant\ndef f():\n    return select(DBTenant)\n",
        [("f", "Tenant")],
    ),
    "module-attribute": (
        "from src.core.database import models\ndef f():\n    return select(models.Tenant)\n",
        [("f", "Tenant")],
    ),
    "one-row-per-function": ("def f():\n    select(User)\n    return select(Tenant)\n", [("f", "User")]),
    "two-functions-of-one-name-are-two-rows": (
        "class A:\n    def load(self):\n        return select(Tenant)\n\n"
        "class B:\n    def load(self):\n        return select(Tenant)\n",
        [("load", "Tenant"), ("load", "Tenant")],
    ),
    # Not a raw select on a mapped class: nothing reported.
    "pydantic-namesake": ("from src.core.schemas import Product\ndef f():\n    return select(Product)\n", []),
    "select-parameter": ("def f(select):\n    return select(Tenant)\n", []),
    "legacy-query": ("def f(session):\n    return session.query(Tenant)\n", []),
}


@pytest.mark.arch_guard
@pytest.mark.parametrize("label", sorted(_TABLE))
def test_detector_rows_per_shape(label: str) -> None:
    """The LIVE detector, on one module: what it flags and what it leaves alone."""
    source, rows = _TABLE[label]
    found = raw_select_rows(ast.parse(_PREAMBLE + source), "src/x.py", mapped(*ORM_MODEL_NAMES))
    assert [(function, model) for _, function, model, _ in found] == rows


# ── Tests ───────────────────────────────────────────────────────────


class TestNoRawSelectOutsideRepositories:
    """No raw select(OrmModel) outside repository/infrastructure files."""

    @pytest.mark.arch_guard
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

    @pytest.mark.arch_guard
    def test_allowlist_entries_still_exist(self):
        """Every allowlisted violation must still exist (stale entry detection).

        When you fix a violation (migrate to repository), remove it from ALLOWLIST.
        This test catches stale entries so the allowlist stays honest.
        """
        all_violations = {(f, fn) for f, fn, _model, _line in _find_raw_selects()}
        assert_violations_match_allowlist(
            all_violations,
            ALLOWLIST,
            fix_hint="Remove fixed entries from ALLOWLIST.",
        )

    @pytest.mark.arch_guard
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
