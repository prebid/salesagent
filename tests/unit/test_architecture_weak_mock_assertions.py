"""Guard: Tests must not use weak mock assertions.

Both ``assert_called()`` and ``assert_called_once()`` are matched, since
neither verifies the arguments a mock was called with.

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

from tests.unit._architecture_helpers import assert_violations_match_allowlist, iter_call_expressions

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = (ROOT / "tests",)

# Pre-existing violations: (file_path, function_name)
# These existed before the guard was introduced. Allowlist shrinks as tests
# are upgraded to assert_called_once_with().
# FIXME(#1370): each entry below should be upgraded to assert_called_once_with()
WEAK_ASSERTION_ALLOWLIST: set[tuple[str, str]] = {
    ("tests/unit/test_creative_repository.py", "test_creates_and_flushes"),
    ("tests/unit/test_creative_repository.py", "test_creates_assignment"),
    ("tests/unit/test_delivery.py", "test_adapter_failure_audit_logged"),
    ("tests/unit/test_gam_creative_rotation.py", "test_lica_payload_excludes_weight_when_default"),
    ("tests/unit/test_gam_creative_rotation.py", "test_lica_payload_includes_weight_when_non_default"),
    ("tests/unit/test_gam_service_account_auth.py", "test_service_account_credentials_creation"),
    ("tests/unit/test_get_media_buys.py", "test_snapshot_requested_calls_adapter"),
    ("tests/unit/test_pr1071_review_fixes.py", "test_audit_log_records_has_brand_not_has_brand_manifest"),
    ("tests/unit/test_update_media_buy_behavioral.py", "test_update_both_start_and_end_time"),
    # FIXME(#1370): pre-existing split assertions outside tests/unit/ (surfaced by SCAN_DIRS widen)
    ("tests/integration/test_creative_async_lifecycle_obligations.py", "test_async_input_required_response"),
    ("tests/integration/test_delivery_webhooks_force.py", "test_trigger_report_for_media_buy_public_method"),
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

        has_bare_called_once = False
        has_call_args = False

        for child in iter_call_expressions(node):
            func = child.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr in {"assert_called", "assert_called_once"} and not child.args and not child.keywords:
                has_bare_called_once = True
            elif func.attr in {"assert_called_with", "assert_called_once_with"} and _asserts_only_any(child):
                # assert_called_once_with(ANY) is the bare form wearing a
                # disguise: it pins the call COUNT and nothing about the
                # arguments, so pairing it with a call_args dissection is the
                # same split assertion the guard exists to ban. Laundering it
                # through ANY must not buy an exemption.
                has_bare_called_once = True

        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr == "call_args":
                has_call_args = True

        if has_bare_called_once and has_call_args:
            violations.append((file_path, node.name, node.lineno))

    return violations


def _is_any(node: ast.expr) -> bool:
    """True for ``ANY`` / ``mock.ANY`` — the wildcard that asserts nothing."""
    return (isinstance(node, ast.Name) and node.id == "ANY") or (isinstance(node, ast.Attribute) and node.attr == "ANY")


def _asserts_only_any(call: ast.Call) -> bool:
    """True when EVERY argument of an assert_called*_with is ``ANY``.

    Requires at least one argument, deliberately: a zero-argument
    ``assert_called_once_with()`` is a PRECISE assertion — "called with no
    arguments at all" — and would otherwise be vacuously "every argument is
    ANY". One sits in the very function this rule was written against.

    A call that passes ANY for one argument while pinning the others is NOT
    matched: that is a targeted wildcard, not an absent assertion.
    """
    args = list(call.args) + [kw.value for kw in call.keywords]
    return bool(args) and all(_is_any(a) for a in args)


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
    ("tests/unit/test_auth_setup_mode.py", "test_disable_setup_mode_succeeds_when_sso_enabled"),
    ("tests/unit/test_creative_repository.py", "test_flushes_session"),
    ("tests/unit/test_creative_repository.py", "test_returns_list"),
    ("tests/unit/test_creative_repository.py", "test_returns_matching_assignments"),
    ("tests/unit/test_creative_repository.py", "test_returns_matching_creative"),
    ("tests/unit/test_dashboard_service.py", "test_get_tenant_caches_result"),
    ("tests/unit/test_gam_update_media_buy.py", "test_update_package_budget_persists_to_database"),
    ("tests/unit/test_incremental_sync_stale_marking.py", "test_full_sync_should_call_mark_stale"),
    ("tests/unit/test_naming_agent.py", "test_generates_name_successfully"),
    ("tests/unit/test_serialization_at_the_persistence_edge.py", "test_create_from_request_adds_to_session"),
    ("tests/unit/test_review_agent.py", "test_returns_approval"),
    ("tests/unit/test_update_media_buy_behavioral.py", "test_valid_date_range_persists_to_db"),
    # FIXME(#1370): pre-existing bare assertions outside tests/unit/ (surfaced by SCAN_DIRS widen)
    ("tests/integration/test_delivery_poll_behavioral.py", "test_adapter_failure_writes_audit_log"),
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

        has_bare_called_once = False
        has_call_args = False

        for child in iter_call_expressions(node):
            func = child.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in {"assert_called", "assert_called_once"}
                and len(child.args) == 0
                and len(child.keywords) == 0
            ):
                has_bare_called_once = True

        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr == "call_args":
                has_call_args = True

        # Only flag if bare assertion WITHOUT call_args
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
