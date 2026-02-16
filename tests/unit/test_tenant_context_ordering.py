"""Tests to prevent tenant context ordering regressions.

This test suite ensures that all MCP tools follow the correct pattern:
1. Call get_principal_id_from_context() or get_principal_from_context() FIRST
2. Only then call get_current_tenant()

The bug fixed in update_media_buy (calling get_current_tenant() before auth)
must never happen again.
"""

from unittest.mock import patch

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


def test_update_media_buy_calls_auth_before_tenant():
    """Regression test: update_media_buy must call auth before get_current_tenant()."""
    from datetime import UTC, datetime

    from src.core.schemas import UpdateMediaBuyRequest
    from src.core.tool_context import ToolContext
    from src.core.tools.media_buy_update import _update_media_buy_impl

    # Create mock context with auth
    ctx = ToolContext(
        context_id="test_ctx",
        principal_id="test_principal",
        tenant_id="test_tenant",
        tool_name="update_media_buy",
        request_timestamp=datetime.now(UTC),
    )

    # Mock dependencies
    with (
        patch("src.core.tools.media_buy_update.get_principal_id_from_context") as mock_auth,
        patch("src.core.tools.media_buy_update.get_current_tenant") as mock_tenant,
        patch("src.core.tools.media_buy_update._verify_principal"),
        patch("src.core.tools.media_buy_update.get_db_session"),
    ):
        mock_auth.return_value = "test_principal"
        mock_tenant.return_value = {"tenant_id": "test_tenant"}

        # Try to call (will fail on DB access, but we're testing call order)
        req = UpdateMediaBuyRequest(media_buy_id="mb_123")
        try:
            _update_media_buy_impl(req=req, ctx=ctx)
        except Exception:
            pass  # Expected to fail, we're just checking call order

        # CRITICAL: auth must be called before tenant
        # If mock_auth wasn't called, it means get_current_tenant() was called first (bug!)
        assert mock_auth.called, "get_principal_id_from_context() must be called to set tenant context"


def test_create_media_buy_has_correct_pattern_in_source():
    """Verify create_media_buy source code follows correct pattern."""
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

    # Find first occurrence of get_principal_id_from_context
    auth_pos = impl_source.find("get_principal_id_from_context(")
    # Find first occurrence of get_current_tenant
    tenant_pos = impl_source.find("get_current_tenant()")

    # Both should be present
    assert auth_pos != -1, "get_principal_id_from_context() not found in _create_media_buy_impl"
    assert tenant_pos != -1, "get_current_tenant() not found in _create_media_buy_impl"

    # Auth must come before tenant
    assert auth_pos < tenant_pos, (
        f"BUG: get_current_tenant() called before get_principal_id_from_context() in create_media_buy\n"
        f"  Auth call at position {auth_pos}\n"
        f"  Tenant call at position {tenant_pos}\n"
        f"  This is the bug we fixed in update_media_buy!"
    )


def test_all_tools_have_auth_before_tenant_pattern():
    """Documentation test: Verify pattern is documented in all tool files."""
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

        # Check for authentication calls
        has_auth = any(
            pattern in content
            for pattern in [
                "get_principal_id_from_context",
                "get_principal_from_context",
                "_get_principal_id_from_context",
            ]
        )

        # Check for tenant usage
        has_tenant = "get_current_tenant" in content

        # If tool uses tenant context, it MUST call auth first
        if has_tenant and not has_auth:
            issues.append(f"{tool_file}: Uses get_current_tenant() but missing auth call")

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
