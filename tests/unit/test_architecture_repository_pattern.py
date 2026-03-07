"""Guard: Business logic uses repositories, not inline DB access.

Two invariants:
1. _impl functions must not call get_db_session() — data access belongs in repositories
2. Integration test bodies must not call session.add() — use factories or fixtures

Scanning approach: AST — parse source files for function calls matching prohibited
patterns. All pre-existing violations are allowlisted; new code fails immediately.

beads: salesagent-qo8a (repository pattern enforcement)
"""

import ast
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
IMPL_SESSION_ALLOWLIST = {
    # products.py — 2 functions with get_db_session()
    ("src/core/tools/products.py", "_get_products_impl"),
    ("src/core/tools/products.py", "get_product_catalog"),
    # creatives — 5 functions with get_db_session()
    ("src/core/tools/creatives/listing.py", "_list_creatives_impl"),
    ("src/core/tools/creatives/_sync.py", "_sync_creatives_impl"),
    ("src/core/tools/creatives/_assignments.py", "_process_assignments"),
    ("src/core/tools/creatives/_workflow.py", "_create_sync_workflow_steps"),
    ("src/core/tools/creatives/_workflow.py", "_audit_log_sync"),
    # task management — 3 functions with get_db_session()
    ("src/core/tools/task_management.py", "list_tasks"),
    ("src/core/tools/task_management.py", "get_task"),
    ("src/core/tools/task_management.py", "complete_task"),
    # admin blueprints — 5 functions with get_db_session()
    ("src/admin/blueprints/creatives.py", "review_creatives"),
    ("src/admin/blueprints/creatives.py", "approve_creative"),
    ("src/admin/blueprints/creatives.py", "reject_creative"),
    ("src/admin/blueprints/creatives.py", "_ai_review_creative_async"),
    ("src/admin/blueprints/creatives.py", "_ai_review_creative_impl"),
}

# ---------------------------------------------------------------------------
# Invariant 2: No session.add() in integration test bodies
# ---------------------------------------------------------------------------

# Integration test files to scan
INTEGRATION_TEST_FILES = [
    "tests/integration/conftest.py",
    "tests/integration/test_creative_v3.py",
    "tests/integration/test_media_buy_v3.py",
    "tests/integration/test_delivery_v3.py",
    "tests/integration/test_update_media_buy_creative_assignment.py",
    "tests/integration/test_creative_review_model.py",
    "tests/integration/test_media_buy_readiness.py",
    "tests/integration/test_format_conversion_approval.py",
    "tests/integration/test_setup_checklist_service.py",
    "tests/integration/test_product_principal_access.py",
]

# Pre-existing violations: (file_path, function_or_fixture_name)
# FIXME(salesagent-qo8a): integration tests should use polyfactory fixtures
INTEGRATION_SESSION_ADD_ALLOWLIST = {
    # conftest.py
    ("tests/integration/conftest.py", "authenticated_admin_session"),
    ("tests/integration/conftest.py", "test_tenant_with_data"),
    ("tests/integration/conftest.py", "sample_tenant"),
    ("tests/integration/conftest.py", "sample_principal"),
    ("tests/integration/conftest.py", "sample_products"),
    ("tests/integration/conftest.py", "test_media_buy_workflow"),
    # test_creative_v3.py (multiple classes share setup_tenant name)
    ("tests/integration/test_creative_v3.py", "setup_tenant"),
    # test_media_buy_v3.py
    ("tests/integration/test_media_buy_v3.py", "mb_creatives"),
    ("tests/integration/test_media_buy_v3.py", "test_unsupported_currency_rejected"),
    ("tests/integration/test_media_buy_v3.py", "test_ownership_mismatch_rejected"),
    # test_delivery_v3.py
    ("tests/integration/test_delivery_v3.py", "_setup_base_state"),
    ("tests/integration/test_delivery_v3.py", "_create_media_buy"),
    ("tests/integration/test_delivery_v3.py", "test_ownership_isolation"),
    ("tests/integration/test_delivery_v3.py", "test_ownership_no_info_leakage"),
    ("tests/integration/test_delivery_v3.py", "test_mixed_ownership"),
    # test_update_media_buy_creative_assignment.py
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
    # test_creative_review_model.py
    ("tests/integration/test_creative_review_model.py", "_create_test_tenant_with_creative"),
    ("tests/integration/test_creative_review_model.py", "test_get_creative_reviews_query"),
    ("tests/integration/test_creative_review_model.py", "test_get_creative_reviews_filters_by_review_type"),
    ("tests/integration/test_creative_review_model.py", "test_get_creative_reviews_tenant_isolation"),
    ("tests/integration/test_creative_review_model.py", "test_get_creative_with_latest_review_tenant_isolation"),
    # test_media_buy_readiness.py
    ("tests/integration/test_media_buy_readiness.py", "test_tenant"),
    ("tests/integration/test_media_buy_readiness.py", "test_principal"),
    ("tests/integration/test_media_buy_readiness.py", "test_draft_state_no_packages"),
    ("tests/integration/test_media_buy_readiness.py", "test_needs_creatives_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_needs_approval_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_scheduled_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_live_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_completed_state"),
    ("tests/integration/test_media_buy_readiness.py", "test_tenant_readiness_summary"),
    # test_format_conversion_approval.py
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
    # test_setup_checklist_service.py
    ("tests/integration/test_setup_checklist_service.py", "setup_minimal_tenant"),
    ("tests/integration/test_setup_checklist_service.py", "setup_complete_tenant"),
    ("tests/integration/test_setup_checklist_service.py", "test_progress_calculation"),
    ("tests/integration/test_setup_checklist_service.py", "test_bulk_setup_status_for_multiple_tenants"),
    ("tests/integration/test_setup_checklist_service.py", "test_currency_count_in_details"),
    ("tests/integration/test_setup_checklist_service.py", "test_sso_is_optional_not_critical_in_multi_tenant_mode"),
    ("tests/integration/test_setup_checklist_service.py", "test_ready_for_orders_without_sso_in_multi_tenant_mode"),
    # test_product_principal_access.py
    ("tests/integration/test_product_principal_access.py", "test_product_stores_and_retrieves_allowed_principal_ids"),
    ("tests/integration/test_product_principal_access.py", "test_product_with_null_allowed_principal_ids"),
    ("tests/integration/test_product_principal_access.py", "test_convert_product_includes_allowed_principal_ids"),
    ("tests/integration/test_product_principal_access.py", "test_allowed_principal_ids_excluded_from_serialization"),
    ("tests/integration/test_product_principal_access.py", "test_principal_model_exists_for_access_control"),
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
