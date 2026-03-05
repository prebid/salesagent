"""Tests to prevent tenant context ordering regressions.

This test suite ensures that all MCP tools follow the correct pattern:
1. Extract principal_id from identity (ResolvedIdentity) FIRST
2. Only then call get_current_tenant()

The bug fixed in update_media_buy (calling get_current_tenant() before auth)
must never happen again. With the ResolvedIdentity migration, _impl functions
receive an already-resolved identity parameter, so the pattern is now:
  identity.principal_id  (before)  get_current_tenant()
"""

import pytest

from src.core.config_loader import get_current_tenant, set_current_tenant


def test_get_current_tenant_raises_if_not_set():
    """Test that get_current_tenant() raises RuntimeError if context not set."""
    # Clear any existing tenant context
    from src.core.config_loader import current_tenant

    current_tenant.set(None)

    # Should raise RuntimeError with helpful message
    with pytest.raises(RuntimeError) as exc_info:
        get_current_tenant()

    error_msg = str(exc_info.value)
    assert "No tenant context set" in error_msg
    assert "get_principal_id_from_context(ctx)" in error_msg
    assert "BEFORE get_current_tenant()" in error_msg


def test_get_current_tenant_includes_caller_info():
    """Test that error message includes caller information for debugging."""
    from src.core.config_loader import current_tenant

    current_tenant.set(None)

    try:
        get_current_tenant()
        pytest.fail("Should have raised RuntimeError")
    except RuntimeError as e:
        error_msg = str(e)
        # Should include file, line, and function name
        assert "Called from:" in error_msg
        assert "test_tenant_context_ordering.py" in error_msg
        assert "test_get_current_tenant_includes_caller_info" in error_msg


def test_get_current_tenant_succeeds_after_set_current_tenant():
    """Test that get_current_tenant() works after set_current_tenant()."""
    test_tenant = {"tenant_id": "test_tenant", "name": "Test Tenant"}

    set_current_tenant(test_tenant)
    tenant = get_current_tenant()

    assert tenant == test_tenant
    assert tenant["tenant_id"] == "test_tenant"


def test_set_current_tenant_normalizes_tenant_context_to_dict():
    """set_current_tenant must convert TenantContext to plain dict.

    This is the fix for the TenantContext vs dict type mismatch bug:
    resolve_identity() wraps tenant dicts in TenantContext, but downstream
    isinstance(tenant, dict) checks failed silently, causing redundant DB
    queries. set_current_tenant is the SINGLE conversion point.
    """
    from src.core.tenant_context import TenantContext

    tc = TenantContext(tenant_id="norm_test", name="Normalize Test")
    set_current_tenant(tc)
    tenant = get_current_tenant()

    assert isinstance(tenant, dict), (
        f"ContextVar holds {type(tenant).__name__}, not dict — set_current_tenant failed to normalize TenantContext"
    )
    assert tenant["tenant_id"] == "norm_test"
    assert tenant["name"] == "Normalize Test"


def test_set_current_tenant_normalizes_lazy_tenant_context_to_dict():
    """set_current_tenant must convert LazyTenantContext to plain dict."""
    from unittest.mock import patch

    from src.core.tenant_context import LazyTenantContext

    # Patch get_tenant_by_id so LazyTenantContext._resolve() returns a minimal context
    with patch("src.core.config_loader.get_tenant_by_id", return_value=None):
        ltc = LazyTenantContext("lazy_test")
        set_current_tenant(ltc)
        tenant = get_current_tenant()

    assert isinstance(tenant, dict), (
        f"ContextVar holds {type(tenant).__name__}, not dict — set_current_tenant failed to normalize LazyTenantContext"
    )
    assert tenant["tenant_id"] == "lazy_test"


def test_set_current_tenant_passes_dict_through():
    """set_current_tenant must pass plain dicts through unchanged."""
    test_dict = {"tenant_id": "dict_test", "name": "Dict Test"}
    set_current_tenant(test_dict)
    tenant = get_current_tenant()

    assert tenant is test_dict, "Plain dict should be stored without conversion"


def test_update_media_buy_calls_auth_before_tenant():
    """Regression test: update_media_buy must use identity.principal_id before identity.tenant.

    With the ResolvedIdentity migration, _impl receives an already-resolved identity.
    This test verifies that identity.principal_id is accessed before identity.tenant.
    Tenant resolution happens at the transport boundary — _impl must NOT call
    ensure_tenant_context() or get_current_tenant().
    """
    from pathlib import Path

    # Read the _update_media_buy_impl source
    file_path = Path(__file__).parent.parent.parent / "src" / "core" / "tools" / "media_buy_update.py"
    source = file_path.read_text()

    # Find the _update_media_buy_impl function
    impl_start = source.find("def _update_media_buy_impl(")
    assert impl_start != -1, "_update_media_buy_impl function not found"

    # Extract just the implementation function (up to next top-level function definition)
    impl_end = source.find("\ndef ", impl_start + 1)
    impl_source = source[impl_start:impl_end] if impl_end != -1 else source[impl_start:]

    # Verify identity.principal_id is accessed before identity.tenant
    auth_pos = impl_source.find("identity.principal_id")
    tenant_pos = impl_source.find("identity.tenant")

    assert auth_pos != -1, "identity.principal_id not found in _update_media_buy_impl"
    assert tenant_pos != -1, "identity.tenant not found in _update_media_buy_impl"

    # Auth (identity.principal_id) must come before tenant access
    assert auth_pos < tenant_pos, (
        f"BUG: identity.tenant accessed before identity.principal_id in _update_media_buy_impl\n"
        f"  Auth access at position {auth_pos}\n"
        f"  Tenant access at position {tenant_pos}\n"
        f"  identity.principal_id must be checked BEFORE identity.tenant!"
    )

    # Ensure ensure_tenant_context is NOT used (tenant comes from transport boundary)
    assert "ensure_tenant_context" not in impl_source, (
        "_update_media_buy_impl still uses ensure_tenant_context — "
        "tenant should come from identity.tenant (resolved at transport boundary)"
    )


def test_create_media_buy_has_correct_pattern_in_source():
    """Verify create_media_buy source code follows correct pattern.

    With the ResolvedIdentity migration, _impl functions now access
    identity.principal_id instead of calling get_principal_id_from_context(),
    and use ensure_tenant_context() instead of get_current_tenant().
    """
    from pathlib import Path

    # Read the create_media_buy_impl source
    file_path = Path(__file__).parent.parent.parent / "src" / "core" / "tools" / "media_buy_create.py"
    source = file_path.read_text()

    # Find the _create_media_buy_impl function
    impl_start = source.find("async def _create_media_buy_impl(")
    assert impl_start != -1, "_create_media_buy_impl function not found"

    # Extract just the implementation function (up to next function definition)
    impl_end = source.find("\nasync def ", impl_start + 1)
    if impl_end == -1:
        impl_end = source.find("\ndef ", impl_start + 1)
    impl_source = source[impl_start:impl_end] if impl_end != -1 else source[impl_start:]

    # Find first occurrence of identity.principal_id (new pattern)
    auth_pos = impl_source.find("identity.principal_id")

    # Both should be present
    assert auth_pos != -1, "identity.principal_id not found in _create_media_buy_impl"

    # Check if ensure_tenant_context() is used; if so, identity access must come first
    tenant_pos = impl_source.find("ensure_tenant_context(")
    if tenant_pos != -1:
        assert auth_pos < tenant_pos, (
            f"BUG: ensure_tenant_context() called before identity.principal_id in create_media_buy\n"
            f"  Auth access at position {auth_pos}\n"
            f"  Tenant call at position {tenant_pos}\n"
            f"  identity.principal_id must be checked BEFORE ensure_tenant_context()!"
        )

    # Also verify the function accepts identity parameter
    assert "identity" in impl_source[:200], "identity parameter not found in _create_media_buy_impl signature"


def test_all_tools_have_auth_before_tenant_pattern():
    """Verify all tool _impl functions use identity for auth, not raw context calls.

    With the ResolvedIdentity migration, _impl functions receive identity as a parameter
    and access identity.principal_id / identity.tenant instead of calling
    get_principal_id_from_context() or get_principal_from_context().

    This test verifies that all tool files with tenant usage also have identity-based
    auth patterns (identity.principal_id, identity.tenant, or identity parameter).
    """
    from pathlib import Path

    tools_dir = Path(__file__).parent.parent.parent / "src" / "core" / "tools"
    tool_files = [
        "products.py",
        "creative_formats.py",
        "creatives/_sync.py",
        "media_buy_create.py",
        "media_buy_update.py",
        "media_buy_delivery.py",
        "performance.py",
        "properties.py",
        "signals.py",
    ]

    issues = []
    for tool_file in tool_files:
        file_path = tools_dir / tool_file
        if not file_path.exists():
            issues.append(f"{tool_file}: File not found")
            continue

        content = file_path.read_text()

        # Check for identity-based authentication patterns (new ResolvedIdentity pattern)
        has_identity_auth = any(
            pattern in content
            for pattern in [
                "identity.principal_id",
                "identity.tenant",
                "identity: ResolvedIdentity",
            ]
        )

        # Also accept legacy patterns if still present in helper functions
        has_legacy_auth = any(
            pattern in content
            for pattern in [
                "get_principal_id_from_context",
                "get_principal_from_context",
            ]
        )

        has_auth = has_identity_auth or has_legacy_auth

        # Check for tenant usage (either direct get_current_tenant or ensure_tenant_context)
        has_tenant = "get_current_tenant" in content or "ensure_tenant_context" in content

        # If tool uses tenant context, it MUST have auth (identity or legacy)
        if has_tenant and not has_auth:
            issues.append(f"{tool_file}: Uses tenant context but missing identity auth pattern")

    if issues:
        pytest.fail("Tool files with tenant context issues:\n" + "\n".join(f"  - {issue}" for issue in issues))


def test_helper_function_sets_tenant_context():
    """Test that get_principal_id_from_context() actually sets tenant context."""
    from datetime import UTC, datetime

    # Clear tenant context
    from src.core.config_loader import current_tenant
    from src.core.helpers.context_helpers import get_principal_id_from_context
    from src.core.tool_context import ToolContext

    current_tenant.set(None)

    # Create context with tenant
    ctx = ToolContext(
        context_id="test_ctx",
        principal_id="test_principal",
        tenant_id="test_tenant",
        tool_name="test",
        request_timestamp=datetime.now(UTC),
    )

    # Call helper
    principal_id = get_principal_id_from_context(ctx)

    assert principal_id == "test_principal"

    # Verify tenant context was set
    tenant = get_current_tenant()
    assert tenant["tenant_id"] == "test_tenant"
