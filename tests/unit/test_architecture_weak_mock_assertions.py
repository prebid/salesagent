"""Guard: Tests must not use weak mock assertions.

Two anti-patterns are guarded:

1. **Split assertion** (assert_called_once + call_args):

    mock.assert_called_once()               # only checks call count
    assert mock.call_args.kwargs["x"] == y  # separately checks args

   Weaker than the atomic form: mock.assert_called_once_with(x=y)

2. **Bare assertion** (assert_called_once without ANY arg verification):

    mock.assert_called_once()               # only checks call count
    # no call_args check at all — args completely unverified

   Should use assert_called_once_with() to verify arguments, or be
   explicitly allowlisted if the test genuinely only cares about call count.

Scanning approach: AST — detect (FunctionDef, AsyncFunctionDef) nodes.

beads: beads-bou.5 (split assertion guard), beads-6kh (bare assertion guard)
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Pre-existing violations: (file_path, function_name)
# These existed before the guard was introduced. Allowlist shrinks as tests
# are upgraded to assert_called_once_with().
# FIXME(beads-bou.5): each entry below should be upgraded to assert_called_once_with()
WEAK_ASSERTION_ALLOWLIST: set[tuple[str, str]] = {
    ("tests/unit/test_a2a_brand_manifest_parameter.py", "test_handle_get_products_skill_brand_manifest_not_converted"),
    ("tests/unit/test_a2a_brand_manifest_parameter.py", "test_handle_get_products_skill_extracts_all_parameters"),
    ("tests/unit/test_a2a_brand_manifest_parameter.py", "test_handle_get_products_skill_forwards_property_list"),
    ("tests/unit/test_a2a_brand_manifest_parameter.py", "test_handle_get_products_skill_passes_brand"),
    ("tests/unit/test_a2a_parameter_mapping.py", "test_get_media_buy_delivery_optional_media_buy_ids"),
    ("tests/unit/test_a2a_parameter_mapping.py", "test_get_media_buy_delivery_uses_plural_media_buy_ids"),
    ("tests/unit/test_a2a_parameter_mapping.py", "test_update_media_buy_backward_compatibility_with_updates"),
    ("tests/unit/test_a2a_parameter_mapping.py", "test_update_media_buy_uses_packages_parameter"),
    ("tests/unit/test_a2a_tenant_detection_order.py", "test_a2a_delegates_to_resolve_identity"),
    ("tests/unit/test_a2a_testing_context_extraction.py", "test_dry_run_header_passed_to_resolve_identity"),
    ("tests/unit/test_auth_context_middleware_population.py", "test_resolve_auth_passes_extracted_token"),
    ("tests/unit/test_authorized_properties_behavioral.py", "test_audit_called_on_failure"),
    ("tests/unit/test_authorized_properties_behavioral.py", "test_audit_called_on_success"),
    ("tests/unit/test_authorized_properties_behavioral.py", "test_passes_none_identity_when_no_ctx"),
    ("tests/unit/test_authorized_properties_behavioral.py", "test_properties_error_calls_audit_with_failure"),
    ("tests/unit/test_authorized_properties_behavioral.py", "test_resolves_identity_from_context"),
    ("tests/unit/test_creative.py", "test_list_creatives_raw_boundary"),
    ("tests/unit/test_creative.py", "test_raw_forwards_filters_to_impl"),
    ("tests/unit/test_creative.py", "test_raw_forwards_include_assignments"),
    ("tests/unit/test_creative.py", "test_raw_forwards_include_performance"),
    ("tests/unit/test_creative.py", "test_webhook_delivered_on_approval"),
    ("tests/unit/test_creative_coverage_gaps.py", "test_slack_notification_for_rejected_creative"),
    ("tests/unit/test_creative_repository.py", "test_creates_and_flushes"),
    ("tests/unit/test_creative_repository.py", "test_creates_assignment"),
    ("tests/unit/test_delivery.py", "test_media_buy_ids_only"),
    ("tests/unit/test_external_domain_routing.py", "test_index_route_external_domain_with_tenant"),
    ("tests/unit/test_gam_creative_rotation.py", "test_lica_payload_excludes_weight_when_default"),
    ("tests/unit/test_gam_creative_rotation.py", "test_lica_payload_includes_weight_when_non_default"),
    ("tests/unit/test_gam_service_account_auth.py", "test_service_account_credentials_creation"),
    ("tests/unit/test_get_media_buys.py", "test_snapshot_requested_calls_adapter"),
    ("tests/unit/test_mcp_auth_middleware.py", "test_auth_required_tool_stores_identity"),
    ("tests/unit/test_mcp_auth_middleware.py", "test_discovery_tool_stores_identity_without_requiring_auth"),
    ("tests/unit/test_order_approval_service.py", "test_start_approval_creates_sync_job"),
    ("tests/unit/test_order_approval_service.py", "test_webhook_notification_sent_on_success"),
    ("tests/unit/test_performance_index_behavioral.py", "test_batch_multiple_products"),
    ("tests/unit/test_performance_index_behavioral.py", "test_empty_performance_data_succeeds"),
    ("tests/unit/test_performance_index_behavioral.py", "test_product_to_package_mapping"),
    ("tests/unit/test_pr1071_review_fixes.py", "test_audit_log_records_has_brand_not_has_brand_manifest"),
    ("tests/unit/test_push_notification_forwarding.py", "test_a2a_wrapper_forwards_push_notification_config"),
    ("tests/unit/test_push_notification_forwarding.py", "test_mcp_wrapper_forwards_push_notification_config"),
    ("tests/unit/test_rest_depends_auth.py", "test_passes_auth_token_to_resolve_identity"),
    ("tests/unit/test_sync_creatives_behavioral.py", "test_slack_notification_only_when_webhook_configured"),
    ("tests/unit/test_transport_tenant_resolution.py", "test_ensure_resolved_sets_current_tenant"),
}


def _find_split_assertions(file_path: str) -> list[tuple[str, str, int]]:
    """Find test functions that use assert_called_once() + call_args together.

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

        has_bare_called_once = False
        has_call_args = False

        for child in ast.walk(node):
            # Bare assert_called_once() — exactly zero arguments
            if isinstance(child, ast.Call):
                func = child.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "assert_called_once"
                    and len(child.args) == 0
                    and len(child.keywords) == 0
                ):
                    has_bare_called_once = True

            # .call_args attribute access (any object)
            if isinstance(child, ast.Attribute) and child.attr == "call_args":
                has_call_args = True

        if has_bare_called_once and has_call_args:
            violations.append((file_path, node.name, node.lineno))

    return violations


class TestNoWeakMockAssertions:
    """Test functions must not combine assert_called_once() with manual call_args checks.

    When a test both calls assert_called_once() (bare, no args) AND accesses
    .call_args to inspect arguments, it should use assert_called_once_with()
    instead. The combined pattern is non-atomic: argument checking happens
    outside the assertion, so a call with wrong arguments can silently pass
    the assert_called_once() check.

    Example violation:
        mock_impl.assert_called_once()          # ← only checks count
        assert mock_impl.call_args[0][0] == x   # ← separately checks args

    Correct form:
        mock_impl.assert_called_once_with(x, identity=identity)
    """

    def test_no_new_split_assertions(self):
        """No new test functions use assert_called_once() + call_args together."""
        all_violations = []
        for test_file in sorted((ROOT / "tests" / "unit").rglob("*.py")):
            rel = str(test_file.relative_to(ROOT))
            all_violations.extend(_find_split_assertions(rel))

        new_violations = [(f, fn, line) for f, fn, line in all_violations if (f, fn) not in WEAK_ASSERTION_ALLOWLIST]

        if new_violations:
            msg_lines = [
                "New tests use assert_called_once() + call_args (use assert_called_once_with() instead):",
                "",
            ]
            for f, fn, line in new_violations:
                msg_lines.append(f"  {f}:{line} in {fn}()")
            msg_lines.append("")
            msg_lines.append(
                "Fix: Replace assert_called_once() + call_args inspection with "
                "assert_called_once_with(expected_arg, keyword=expected_value)."
            )
            raise AssertionError("\n".join(msg_lines))

    def test_allowlist_entries_still_exist(self):
        """Every allowlisted violation must still exist (stale entry detection).

        When you upgrade a test to assert_called_once_with(), remove it from
        WEAK_ASSERTION_ALLOWLIST — this test enforces that.
        """
        all_violations: set[tuple[str, str]] = set()
        for test_file in sorted((ROOT / "tests" / "unit").rglob("*.py")):
            rel = str(test_file.relative_to(ROOT))
            for f, fn, _line in _find_split_assertions(rel):
                all_violations.add((f, fn))

        stale = WEAK_ASSERTION_ALLOWLIST - all_violations
        if stale:
            msg_lines = [
                "Stale allowlist entries (test was fixed — remove from WEAK_ASSERTION_ALLOWLIST):",
                "",
            ]
            for f, fn in sorted(stale):
                msg_lines.append(f"    ({f!r}, {fn!r}),")
            raise AssertionError("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# Guard 2: Bare assert_called_once() without ANY argument verification
# ---------------------------------------------------------------------------

# Pre-existing violations: bare assert_called_once() with no call_args check at all.
# These tests verify call count but not arguments — should be upgraded to
# assert_called_once_with() or explicitly kept if only call count matters.
# FIXME(beads-6kh): each entry below should be reviewed and upgraded
BARE_ASSERTION_ALLOWLIST: set[tuple[str, str]] = {
    ("tests/unit/adapters/broadstreet/test_client.py", "test_get_network"),
    ("tests/unit/test_a2a_auth_optional.py", "test_get_products_with_auth"),
    ("tests/unit/test_a2a_auth_optional.py", "test_get_products_without_auth"),
    ("tests/unit/test_a2a_auth_optional.py", "test_list_authorized_properties_with_auth"),
    ("tests/unit/test_a2a_auth_optional.py", "test_list_authorized_properties_without_auth"),
    ("tests/unit/test_a2a_auth_optional.py", "test_list_creative_formats_with_auth"),
    ("tests/unit/test_a2a_auth_optional.py", "test_list_creative_formats_without_auth"),
    ("tests/unit/test_creative.py", "test_a2a_slack_notification_require_human"),
    ("tests/unit/test_creative.py", "test_audit_log_sync_succeeds_without_principal_in_db"),
    ("tests/unit/test_creative_repository.py", "test_flushes_session"),
    ("tests/unit/test_creative_repository.py", "test_returns_list"),
    ("tests/unit/test_creative_repository.py", "test_returns_matching_assignments"),
    ("tests/unit/test_creative_repository.py", "test_returns_matching_creative"),
    ("tests/unit/test_dashboard_service.py", "test_get_tenant_caches_result"),
    ("tests/unit/test_delivery_service_behavioral.py", "test_401_causes_immediate_failure_no_retry"),
    ("tests/unit/test_delivery_service_behavioral.py", "test_403_causes_immediate_failure_no_retry"),
    ("tests/unit/test_gam_update_media_buy.py", "test_update_package_budget_persists_to_database"),
    ("tests/unit/test_incremental_sync_stale_marking.py", "test_full_sync_should_call_mark_stale"),
    ("tests/unit/test_naming_agent.py", "test_generates_name_successfully"),
    ("tests/unit/test_no_model_dump_in_impl_fixes.py", "test_create_from_request_adds_to_session"),
    ("tests/unit/test_performance_index_behavioral.py", "test_a2a_happy_path_correct_params"),
    ("tests/unit/test_products_transport_wrappers.py", "test_mcp_wrapper_version_compat_v2"),
    ("tests/unit/test_products_transport_wrappers.py", "test_rest_applies_version_compat"),
    ("tests/unit/test_review_agent.py", "test_returns_approval"),
    ("tests/unit/test_transport_tenant_resolution.py", "test_db_queried_only_once"),
}


def _find_bare_assertions(file_path: str) -> list[tuple[str, str, int]]:
    """Find test functions that use bare assert_called_once() without any call_args check.

    Returns list of (file_path, function_name, line_number).
    Unlike _find_split_assertions, this catches functions that don't inspect
    arguments at all — not even via call_args.
    """
    source_path = ROOT / file_path
    if not source_path.exists():
        return []

    tree = ast.parse(source_path.read_text())
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        has_bare_called_once = False
        has_call_args = False

        for child in ast.walk(node):
            # Bare assert_called_once() — exactly zero arguments
            if isinstance(child, ast.Call):
                func = child.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "assert_called_once"
                    and len(child.args) == 0
                    and len(child.keywords) == 0
                ):
                    has_bare_called_once = True

            # .call_args attribute access (any object)
            if isinstance(child, ast.Attribute) and child.attr == "call_args":
                has_call_args = True

        # Only flag if bare assert_called_once() WITHOUT call_args
        # (with call_args is the split pattern, handled by the other guard)
        if has_bare_called_once and not has_call_args:
            violations.append((file_path, node.name, node.lineno))

    return violations


class TestNoBareAssertCalledOnce:
    """Test functions should use assert_called_once_with() instead of bare assert_called_once().

    Bare assert_called_once() only verifies the mock was called — not WHAT it was
    called with. A refactor that changes arguments passes the test silently.

    Example violation:
        mock_repo.update_status.assert_called_once()  # ← doesn't check args

    Correct form:
        mock_repo.update_status.assert_called_once_with("step_123", status="completed")
    """

    def test_no_new_bare_assertions(self):
        """No new test functions use bare assert_called_once() without arg verification."""
        all_violations = []
        for test_file in sorted((ROOT / "tests" / "unit").rglob("*.py")):
            if "test_architecture_" in test_file.name:
                continue
            rel = str(test_file.relative_to(ROOT))
            all_violations.extend(_find_bare_assertions(rel))

        new_violations = [(f, fn, line) for f, fn, line in all_violations if (f, fn) not in BARE_ASSERTION_ALLOWLIST]

        if new_violations:
            msg_lines = [
                "New tests use bare assert_called_once() without argument verification:",
                "",
            ]
            for f, fn, line in new_violations:
                msg_lines.append(f"  {f}:{line} in {fn}()")
            msg_lines.append("")
            msg_lines.append(
                "Fix: Replace assert_called_once() with "
                "assert_called_once_with(expected_arg, keyword=expected_value). "
                "Use unittest.mock.ANY for arguments you don't care about."
            )
            raise AssertionError("\n".join(msg_lines))

    def test_allowlist_entries_still_exist(self):
        """Every allowlisted violation must still exist (stale entry detection).

        When you upgrade a test to assert_called_once_with(), remove it from
        BARE_ASSERTION_ALLOWLIST — this test enforces that.
        """
        all_violations: set[tuple[str, str]] = set()
        for test_file in sorted((ROOT / "tests" / "unit").rglob("*.py")):
            if "test_architecture_" in test_file.name:
                continue
            rel = str(test_file.relative_to(ROOT))
            for f, fn, _line in _find_bare_assertions(rel):
                all_violations.add((f, fn))

        stale = BARE_ASSERTION_ALLOWLIST - all_violations
        if stale:
            msg_lines = [
                "Stale allowlist entries (test was fixed — remove from BARE_ASSERTION_ALLOWLIST):",
                "",
            ]
            for f, fn in sorted(stale):
                msg_lines.append(f"    ({f!r}, {fn!r}),")
            raise AssertionError("\n".join(msg_lines))
