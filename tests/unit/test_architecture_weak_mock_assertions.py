"""Guard: Tests must not use weak mock assertions.

Both the sync (``assert_called()`` / ``assert_called_once()``) and async
(``assert_awaited()`` / ``assert_awaited_once()``) bare forms are matched, since
none verifies the arguments a mock was called with. Each has an atomic ``*_with``
form (``assert_called_once_with`` / ``assert_awaited_once_with``) that does. The two
scanners share one matcher (``_function_flags``); ``TestMatcherCompleteness`` pins the
sync+async forms so the matcher cannot silently narrow.

Two anti-patterns are guarded:

1. **Split assertion** (bare assert + call_args):

    mock.assert_called_once()               # only checks call count
    assert mock.call_args.kwargs["x"] == y  # separately checks args

   Weaker than the atomic form: mock.assert_called_once_with(x=y)

2. **Bare assertion** (bare assert without ANY arg verification):

    mock.assert_called_once()               # only checks call count
    # no call_args check at all — args completely unverified

   Should use assert_called_once_with() to verify arguments, or be
   explicitly allowlisted if the test genuinely only cares about call count.

Scanning approach: AST — detect (FunctionDef, AsyncFunctionDef) nodes.

beads: #1370 (split assertion guard), #1370 (bare assertion guard)
"""

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = (ROOT / "tests",)

# Bare "only checks the call happened" assertions — sync (Mock) and async (AsyncMock).
# Each has a *_with form that verifies arguments atomically; the bare form does not.
_BARE_ASSERT_METHODS = frozenset({"assert_called", "assert_called_once", "assert_awaited", "assert_awaited_once"})


def _function_flags(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[bool, bool]:
    """Return ``(has_bare_assert, has_call_args)`` for a function node.

    ``has_bare_assert``: a bare (zero-arg) call to any ``_BARE_ASSERT_METHODS`` member.
    ``has_call_args``: any ``.call_args`` attribute access.

    Matching is function-level (not per-mock), matching the original guard's
    granularity — a function that bare-asserts one mock and inspects another's
    ``call_args`` is flagged; such cases are tracked in the allowlists.
    """
    has_bare = False
    has_call_args = False
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _BARE_ASSERT_METHODS
                and len(child.args) == 0
                and len(child.keywords) == 0
            ):
                has_bare = True
        if isinstance(child, ast.Attribute) and child.attr == "call_args":
            has_call_args = True
    return has_bare, has_call_args


def _walk_functions(source: str):
    """Yield every (Async)FunctionDef node in *source*."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


# Pre-existing violations: (file_path, function_name)
# These existed before the guard was introduced. Allowlist shrinks as tests
# are upgraded to assert_called_once_with().
# FIXME(#1370): each entry below should be upgraded to assert_called_once_with()
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
    ("tests/unit/test_auth_bearer_header.py", "test_x_adcp_auth_takes_precedence_over_authorization_bearer"),
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
    ("tests/unit/test_delivery.py", "test_adapter_failure_audit_logged"),
    ("tests/unit/test_external_domain_routing.py", "test_index_route_external_domain_with_tenant"),
    ("tests/unit/test_gam_creative_rotation.py", "test_lica_payload_excludes_weight_when_default"),
    ("tests/unit/test_gam_creative_rotation.py", "test_lica_payload_includes_weight_when_non_default"),
    ("tests/unit/test_gam_creatives_manager.py", "test_line_item_matching_no_match_logs_warning"),
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
    ("tests/unit/test_sync_creatives_behavioral.py", "test_slack_notification_only_when_webhook_configured"),
    ("tests/unit/test_transport_tenant_resolution.py", "test_ensure_resolved_sets_current_tenant"),
    ("tests/unit/test_update_media_buy_behavioral.py", "test_update_both_start_and_end_time"),
    # FIXME(#1370): pre-existing split assertions outside tests/unit/ (surfaced by SCAN_DIRS widen)
    ("tests/bdd/steps/generic/then_media_buy.py", "then_slack_notification_sent"),
    ("tests/integration/test_auth_header_propagation.py", "test_creative_agent_custom_auth_header_propagation"),
    ("tests/integration/test_auth_header_propagation.py", "test_signals_agent_custom_auth_header_propagation"),
    ("tests/integration/test_creative_async_lifecycle_obligations.py", "test_async_input_required_response"),
    (
        "tests/integration/test_delivery_service_behavioral.py",
        "test_hmac_signature_header_present_when_secret_configured",
    ),
    ("tests/integration/test_delivery_service_behavioral.py", "test_bearer_token_sent_in_authorization_header"),
    (
        "tests/integration/test_delivery_service_behavioral.py",
        "test_happy_path_delivers_payload_to_configured_endpoint",
    ),
    ("tests/integration/test_delivery_webhooks_force.py", "test_trigger_report_for_media_buy_public_method"),
    ("tests/integration/test_gam_tenant_setup.py", "test_command_line_parsing_network_code_optional"),
    ("tests/integration/test_targeting_values_endpoint.py", "test_get_targeting_values_endpoint"),
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

        has_bare, has_call_args = _function_flags(node)
        if has_bare and has_call_args:
            violations.append((file_path, node.name, node.lineno))

    return violations


def _collect_split_assertion_violations() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for scan_dir in SCAN_DIRS:
        for test_file in sorted(scan_dir.rglob("*.py")):
            rel = str(test_file.relative_to(ROOT))
            for f, fn, _line in _find_split_assertions(rel):
                found.add((f, fn))
    return found


def _collect_bare_assertion_violations() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for scan_dir in SCAN_DIRS:
        for test_file in sorted(scan_dir.rglob("*.py")):
            if "test_architecture_" in test_file.name:
                continue
            rel = str(test_file.relative_to(ROOT))
            for f, fn, _line in _find_bare_assertions(rel):
                found.add((f, fn))
    return found


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

    @pytest.mark.arch_guard
    def test_split_assertion_allowlist_matches_violations(self):
        """Split-assertion violations must exactly match WEAK_ASSERTION_ALLOWLIST."""
        assert_violations_match_allowlist(
            _collect_split_assertion_violations(),
            WEAK_ASSERTION_ALLOWLIST,
            fix_hint=(
                "Fix: Replace assert_called_once() + call_args inspection with "
                "assert_called_once_with(expected_arg, keyword=expected_value)."
            ),
        )


# ---------------------------------------------------------------------------
# Guard 2: Bare assert_called_once() without ANY argument verification
# ---------------------------------------------------------------------------

# Pre-existing violations: bare assert_called_once() with no call_args check at all.
# These tests verify call count but not arguments — should be upgraded to
# assert_called_once_with() or explicitly kept if only call count matters.
# FIXME(#1370): each entry below should be reviewed and upgraded
BARE_ASSERTION_ALLOWLIST: set[tuple[str, str]] = {
    ("tests/unit/adapters/broadstreet/test_client.py", "test_get_network"),
    ("tests/unit/test_a2a_auth_optional.py", "test_get_products_with_auth"),
    ("tests/unit/test_a2a_auth_optional.py", "test_get_products_without_auth"),
    ("tests/unit/test_a2a_auth_optional.py", "test_list_authorized_properties_with_auth"),
    ("tests/unit/test_a2a_auth_optional.py", "test_list_authorized_properties_without_auth"),
    ("tests/unit/test_a2a_auth_optional.py", "test_list_creative_formats_with_auth"),
    ("tests/unit/test_a2a_auth_optional.py", "test_list_creative_formats_without_auth"),
    ("tests/unit/test_auth_setup_mode.py", "test_disable_setup_mode_succeeds_when_sso_enabled"),
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
    ("tests/unit/test_products_transport_wrappers.py", "test_rest_applies_version_compat"),
    ("tests/unit/test_review_agent.py", "test_returns_approval"),
    ("tests/unit/test_transport_tenant_resolution.py", "test_db_queried_only_once"),
    ("tests/unit/test_update_media_buy_behavioral.py", "test_positive_budget_persists_to_db"),
    ("tests/unit/test_update_media_buy_behavioral.py", "test_valid_date_range_persists_to_db"),
    # FIXME(#1370): pre-existing bare assertions outside tests/unit/ (surfaced by SCAN_DIRS widen)
    ("tests/bdd/steps/domain/uc006_sync_creatives.py", "then_background_ai_review_submitted"),
    ("tests/harness/test_harness_delivery_poll.py", "test_pricing_options"),
    ("tests/integration/test_auth_header_propagation.py", "test_auth_header_used_in_actual_request"),
    ("tests/integration/test_delivery_poll_behavioral.py", "test_adapter_failure_writes_audit_log"),
    ("tests/integration/test_delivery_webhook_behavioral.py", "test_ssrf_validation_records_failure_metrics"),
    ("tests/integration/test_gam_tenant_setup.py", "test_admin_ui_network_detection_endpoint"),
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

        has_bare, has_call_args = _function_flags(node)
        # Only flag if bare assertion WITHOUT call_args
        # (with call_args is the split pattern, handled by the other guard)
        if has_bare and not has_call_args:
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

    @pytest.mark.arch_guard
    def test_bare_assertion_allowlist_matches_violations(self):
        """Bare assert_called_once violations must exactly match BARE_ASSERTION_ALLOWLIST."""
        assert_violations_match_allowlist(
            _collect_bare_assertion_violations(),
            BARE_ASSERTION_ALLOWLIST,
            fix_hint=(
                "Fix: Replace assert_called_once() with "
                "assert_called_once_with(expected_arg, keyword=expected_value). "
                "Use unittest.mock.ANY for arguments you don't care about."
            ),
        )


class TestMatcherCompleteness:
    """The matcher recognizes the sync AND async bare forms, and skips atomic forms.

    Positive/negative self-tests so a future edit cannot silently narrow the matcher
    (e.g. drop the async forms, or start matching an atomic ``*_with`` form) without a
    failing test. Guards the guard.
    """

    @staticmethod
    def _flags(source: str) -> tuple[bool, bool]:
        return _function_flags(next(_walk_functions(source)))

    def test_sync_split_detected(self):
        src = "def t():\n    m.assert_called_once()\n    assert m.call_args.args[0] == 1\n"
        assert self._flags(src) == (True, True)

    def test_async_split_detected(self):
        src = "async def t():\n    m.assert_awaited_once()\n    _, x = m.call_args.args\n"
        assert self._flags(src) == (True, True)

    def test_async_bare_detected_without_call_args(self):
        src = "async def t():\n    m.assert_awaited_once()\n"
        assert self._flags(src) == (True, False)

    def test_sync_bare_detected_without_call_args(self):
        src = "def t():\n    m.assert_called()\n"
        assert self._flags(src) == (True, False)

    def test_atomic_async_form_not_flagged(self):
        src = "async def t():\n    m.assert_awaited_once_with(ANY, identity)\n"
        assert self._flags(src) == (False, False)

    def test_atomic_sync_form_not_flagged(self):
        src = "def t():\n    m.assert_called_once_with(1, k=2)\n"
        assert self._flags(src) == (False, False)
