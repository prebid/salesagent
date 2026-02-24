"""Tests for transport boundary tenant resolution.

Core Invariant: Tenant context is resolved ONCE at the transport boundary
and passed through ResolvedIdentity — business logic (_impl functions)
never resolve, load, or validate tenant themselves.

These tests verify that resolve_identity_from_context() produces a
ResolvedIdentity with a TenantContext Pydantic model (loaded from DB),
not a raw dict or minimal stub.
"""

from unittest.mock import patch

import pytest

from src.core.resolved_identity import ResolvedIdentity
from src.core.tenant_context import TenantContext
from src.core.tool_context import ToolContext
from src.core.transport_helpers import resolve_identity_from_context


def _make_tool_context(**overrides):
    """Create a ToolContext with all required fields."""
    from datetime import UTC, datetime

    defaults = {
        "context_id": "test_ctx",
        "tenant_id": "test_tenant",
        "principal_id": "test_principal",
        "tool_name": "test_tool",
        "request_timestamp": datetime.now(UTC),
    }
    defaults.update(overrides)
    return ToolContext(**defaults)


FULL_TENANT_DICT = {
    "tenant_id": "test_tenant",
    "name": "Test Tenant",
    "subdomain": "test",
    "ad_server": "mock",
    "approval_mode": "require-human",
    "human_review_required": True,
    "enable_axe_signals": True,
    "brand_manifest_policy": "require_auth",
    "authorized_emails": [],
    "authorized_domains": [],
}


class TestToolContextProducesFullTenant:
    """ToolContext path must load full tenant from DB as TenantContext model."""

    def test_toolcontext_path_loads_full_tenant_from_db(self):
        """When resolve_identity_from_context receives a ToolContext,
        the resulting ResolvedIdentity.tenant must be a TenantContext model
        with all fields loaded from the database."""
        ctx = _make_tool_context()

        with patch(
            "src.core.config_loader.get_tenant_by_id",
            return_value=FULL_TENANT_DICT,
        ) as mock_get_tenant, patch("src.core.config_loader.set_current_tenant"):
            identity = resolve_identity_from_context(ctx)

        assert identity is not None
        assert identity.tenant is not None
        # Must be a TenantContext model, not a raw dict
        assert isinstance(identity.tenant, TenantContext)
        # Verify fields loaded from DB
        assert identity.tenant.tenant_id == "test_tenant"
        assert identity.tenant.approval_mode == "require-human"
        assert identity.tenant.human_review_required is True
        assert identity.tenant.name == "Test Tenant"
        # Dict-like access still works (backward compat)
        assert identity.tenant["tenant_id"] == "test_tenant"
        assert "approval_mode" in identity.tenant
        # DB was queried
        mock_get_tenant.assert_called_once_with("test_tenant")

    def test_toolcontext_path_sets_current_tenant_contextvar(self):
        """The transport boundary must call set_current_tenant() so that
        downstream code using get_current_tenant() gets the full dict."""
        ctx = _make_tool_context()

        with patch(
            "src.core.config_loader.get_tenant_by_id",
            return_value=FULL_TENANT_DICT,
        ), patch("src.core.config_loader.set_current_tenant") as mock_set:
            resolve_identity_from_context(ctx)

        mock_set.assert_called_once()
        tenant_arg = mock_set.call_args[0][0]
        assert tenant_arg["tenant_id"] == "test_tenant"
        assert "approval_mode" in tenant_arg

    def test_toolcontext_path_falls_back_to_minimal_when_db_unavailable(self):
        """When DB is unavailable, still create TenantContext with tenant_id."""
        ctx = _make_tool_context()

        with patch(
            "src.core.config_loader.get_tenant_by_id",
            side_effect=RuntimeError("DB not available"),
        ), patch("src.core.config_loader.set_current_tenant"):
            identity = resolve_identity_from_context(ctx)

        assert identity is not None
        assert identity.tenant_id == "test_tenant"
        # Fallback must still be a TenantContext, not a raw dict
        assert isinstance(identity.tenant, TenantContext)
        assert identity.tenant.tenant_id == "test_tenant"
        # Defaults should be applied
        assert identity.tenant.human_review_required is True
        assert identity.tenant.approval_mode == "require-human"

    def test_toolcontext_preserves_principal_and_testing_context(self):
        """ToolContext path must preserve principal_id and testing_context."""
        from src.core.testing_hooks import AdCPTestContext

        testing_ctx = AdCPTestContext(dry_run=True, test_session_id="sess_123")
        ctx = _make_tool_context(
            principal_id="test_advertiser",
            tool_name="sync_creatives",
            testing_context=testing_ctx,
        )

        with patch(
            "src.core.config_loader.get_tenant_by_id",
            return_value=FULL_TENANT_DICT,
        ), patch("src.core.config_loader.set_current_tenant"):
            identity = resolve_identity_from_context(ctx)

        assert identity.principal_id == "test_advertiser"
        assert identity.testing_context is not None
        assert identity.testing_context.dry_run is True
        assert identity.testing_context.test_session_id == "sess_123"


class TestImplFunctionsDoNotResolveTenant:
    """No _impl function should import or call ensure_tenant_context.

    This is a structural test to enforce the invariant: tenant resolution
    happens at the transport boundary, not in business logic.
    """

    IMPL_MODULES = [
        "src.core.tools.media_buy_create",
        "src.core.tools.media_buy_update",
        "src.core.tools.media_buy_delivery",
        "src.core.tools.creatives._sync",
        "src.core.tools.creatives.listing",
        "src.core.tools.signals",
        "src.core.tools.performance",
    ]

    @pytest.mark.parametrize("module_path", IMPL_MODULES)
    def test_impl_module_does_not_import_ensure_tenant_context(self, module_path):
        """No _impl module should reference ensure_tenant_context."""
        import importlib

        mod = importlib.import_module(module_path)
        source_file = mod.__file__
        with open(source_file) as f:
            source = f.read()

        assert "ensure_tenant_context" not in source, (
            f"{module_path} still references ensure_tenant_context. "
            f"Tenant resolution should happen at the transport boundary, not in _impl."
        )
