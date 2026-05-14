"""Tests for transport boundary tenant resolution.

Core Invariant: Tenant context is resolved ONCE at the transport boundary
and passed through ResolvedIdentity — business logic (_impl functions)
never resolve, load, or validate tenant themselves.

These tests verify that resolve_identity_from_context() produces a
ResolvedIdentity with a lazy-loading tenant context that defers DB
queries until non-tenant_id fields are accessed.
"""

from unittest.mock import patch

import pytest

from src.core.tenant_context import LazyTenantContext
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


class TestLazyTenantContext:
    """LazyTenantContext must defer DB load and resolve on demand."""

    def test_tenant_id_available_without_db(self):
        """tenant_id is available immediately — no DB query."""
        lazy = LazyTenantContext("test_tenant")

        assert lazy.tenant_id == "test_tenant"
        assert lazy["tenant_id"] == "test_tenant"
        assert lazy.get("tenant_id") == "test_tenant"
        assert not lazy.is_loaded

    def test_non_tenant_id_field_triggers_db_load(self):
        """Accessing a field other than tenant_id triggers the DB query."""
        lazy = LazyTenantContext("test_tenant")

        with (
            patch(
                "src.core.config_loader.get_tenant_by_id",
                return_value=FULL_TENANT_DICT,
            ) as mock_get,
            patch("src.core.config_loader.set_current_tenant"),
        ):
            name = lazy.name

        assert name == "Test Tenant"
        assert lazy.is_loaded
        mock_get.assert_called_once_with("test_tenant")

    def test_db_queried_only_once(self):
        """Multiple field accesses should only trigger one DB query."""
        lazy = LazyTenantContext("test_tenant")

        with (
            patch(
                "src.core.config_loader.get_tenant_by_id",
                return_value=FULL_TENANT_DICT,
            ) as mock_get,
            patch("src.core.config_loader.set_current_tenant"),
        ):
            _ = lazy.name
            _ = lazy.approval_mode
            _ = lazy["ad_server"]

        mock_get.assert_called_once()

    def test_dict_like_access_triggers_load(self):
        """Dict-like access to non-tenant_id fields triggers load."""
        lazy = LazyTenantContext("test_tenant")

        with (
            patch(
                "src.core.config_loader.get_tenant_by_id",
                return_value=FULL_TENANT_DICT,
            ),
            patch("src.core.config_loader.set_current_tenant"),
        ):
            assert lazy["name"] == "Test Tenant"
            assert lazy.get("approval_mode") == "require-human"

        assert lazy.is_loaded

    def test_contains_does_not_trigger_load(self):
        """'field in tenant' checks known fields without DB query."""
        lazy = LazyTenantContext("test_tenant")

        assert "tenant_id" in lazy
        assert "approval_mode" in lazy
        assert "nonexistent" not in lazy
        assert not lazy.is_loaded

    def test_bool_always_true_without_load(self):
        """bool(lazy_tenant) is True without triggering DB load."""
        lazy = LazyTenantContext("test_tenant")

        assert bool(lazy)
        assert not lazy.is_loaded

    def test_fallback_when_db_unavailable(self):
        """Returns minimal TenantContext with defaults when DB fails."""
        lazy = LazyTenantContext("test_tenant")

        with (
            patch(
                "src.core.config_loader.get_tenant_by_id",
                side_effect=RuntimeError("DB not available"),
            ),
            patch("src.core.config_loader.set_current_tenant"),
        ):
            # Access a non-tenant_id field to trigger resolution
            mode = lazy.approval_mode

        assert lazy.is_loaded
        assert lazy.tenant_id == "test_tenant"
        assert mode == "require-human"  # default

    def test_resolve_does_not_set_current_tenant(self):
        """Property access (_resolve) must NOT mutate the ContextVar.

        ContextVar mutation only happens via explicit ensure_resolved() at boundaries.
        """
        lazy = LazyTenantContext("test_tenant")

        with (
            patch(
                "src.core.config_loader.get_tenant_by_id",
                return_value=FULL_TENANT_DICT,
            ),
            patch("src.core.config_loader.set_current_tenant") as mock_set,
        ):
            _ = lazy.name  # trigger resolve via property access

        mock_set.assert_not_called()

    def test_ensure_resolved_sets_current_tenant(self):
        """ensure_resolved() is the boundary call that sets the ContextVar."""
        lazy = LazyTenantContext("test_tenant")

        with (
            patch(
                "src.core.config_loader.get_tenant_by_id",
                return_value=FULL_TENANT_DICT,
            ),
            patch("src.core.config_loader.set_current_tenant") as mock_set,
        ):
            lazy.ensure_resolved()

        mock_set.assert_called_once()
        assert mock_set.call_args[0][0]["tenant_id"] == "test_tenant"

    def test_immutable(self):
        """LazyTenantContext is immutable — setting attributes raises."""
        lazy = LazyTenantContext("test_tenant")

        with pytest.raises(AttributeError, match="immutable"):
            lazy.name = "changed"


class TestToolContextProducesLazyTenant:
    """ToolContext path must produce a lazy tenant that defers DB load."""

    def test_toolcontext_path_creates_lazy_tenant(self):
        """resolve_identity_from_context creates a LazyTenantContext,
        not an eagerly-loaded TenantContext."""
        ctx = _make_tool_context()

        identity = resolve_identity_from_context(ctx)

        assert identity is not None
        assert identity.tenant is not None
        assert isinstance(identity.tenant, LazyTenantContext)
        # tenant_id available immediately
        assert identity.tenant.tenant_id == "test_tenant"

    def test_toolcontext_lazy_tenant_no_db_for_tenant_id_only(self):
        """If _impl only accesses tenant_id, no DB query is made."""
        ctx = _make_tool_context()

        with patch(
            "src.core.config_loader.get_tenant_by_id",
        ) as mock_get:
            identity = resolve_identity_from_context(ctx)
            # Only access tenant_id
            _ = identity.tenant["tenant_id"]
            _ = identity.tenant.tenant_id

        mock_get.assert_not_called()

    def test_toolcontext_lazy_tenant_loads_on_field_access(self):
        """Accessing a real tenant field triggers DB load."""
        ctx = _make_tool_context()

        identity = resolve_identity_from_context(ctx)

        with (
            patch(
                "src.core.config_loader.get_tenant_by_id",
                return_value=FULL_TENANT_DICT,
            ) as mock_get,
            patch("src.core.config_loader.set_current_tenant"),
        ):
            name = identity.tenant.name

        assert name == "Test Tenant"
        mock_get.assert_called_once_with("test_tenant")

    def test_toolcontext_preserves_principal_and_testing_context(self):
        """ToolContext path must preserve principal_id and testing_context."""
        from src.core.testing_hooks import AdCPTestContext

        testing_ctx = AdCPTestContext(dry_run=True, test_session_id="sess_123")
        ctx = _make_tool_context(
            principal_id="test_advertiser",
            tool_name="sync_creatives",
            testing_context=testing_ctx,
        )

        identity = resolve_identity_from_context(ctx)

        assert identity.principal_id == "test_advertiser"
        assert identity.testing_context is not None
        assert identity.testing_context.dry_run is True
        assert identity.testing_context.test_session_id == "sess_123"


class TestImplFunctionsDoNotResolveTenant:
    """No _impl function should read tenant from ContextVars.

    This is a structural test to enforce the invariant: tenant data flows
    through identity.tenant — business logic never reads from ContextVars.
    """

    IMPL_MODULES = [
        "src.core.tools.media_buy_create",
        "src.core.tools.media_buy_update",
        "src.core.tools.media_buy_delivery",
        "src.core.tools.creatives._sync",
        "src.core.tools.creatives.listing",
        "src.core.tools.signals",
        "src.core.tools.performance",
        "src.core.tools.products",
        "src.core.tools.properties",
        "src.core.tools.creative_formats",
        "src.core.tools.capabilities",
        "src.core.tools.task_management",
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

    # Modules where ContextVar usage is ONLY in _impl functions (strict check)
    STRICT_MODULES = [
        "src.core.tools.media_buy_update",
        "src.core.tools.media_buy_delivery",
        "src.core.tools.creatives._sync",
        "src.core.tools.creatives.listing",
        "src.core.tools.signals",
        "src.core.tools.performance",
        "src.core.tools.properties",
        "src.core.tools.creative_formats",
        "src.core.tools.capabilities",
        "src.core.tools.task_management",
    ]

    @pytest.mark.parametrize("module_path", STRICT_MODULES)
    def test_strict_module_no_contextvar(self, module_path):
        """These modules must not reference get_current_tenant or set_current_tenant at all."""
        import importlib

        mod = importlib.import_module(module_path)
        with open(mod.__file__) as f:
            source = f.read()

        assert "get_current_tenant" not in source, (
            f"{module_path} still calls get_current_tenant(). Use identity.tenant instead of reading from ContextVar."
        )
        assert "set_current_tenant" not in source, (
            f"{module_path} still calls set_current_tenant(). "
            f"Tenant ContextVar is managed by the transport boundary, not _impl."
        )

    def test_create_media_buy_impl_no_contextvar(self):
        """_create_media_buy_impl must not use ContextVar for tenant.

        Note: execute_approved_media_buy() in the same module is a transport-adjacent
        async handler that legitimately uses set_current_tenant (no identity in scope).
        """
        import importlib
        import re

        mod = importlib.import_module("src.core.tools.media_buy_create")
        with open(mod.__file__) as f:
            source = f.read()

        # Extract _create_media_buy_impl body
        match = re.search(r"(async def _create_media_buy_impl\(.*?)(?=\nasync def |\ndef |\Z)", source, re.DOTALL)
        assert match, "_create_media_buy_impl not found"
        impl_body = match.group(1)

        assert "get_current_tenant" not in impl_body, (
            "_create_media_buy_impl still calls get_current_tenant(). Use identity.tenant."
        )
        assert "set_current_tenant" not in impl_body, (
            "_create_media_buy_impl still calls set_current_tenant(). "
            "Tenant ContextVar is managed by the transport boundary."
        )

    def test_get_products_impl_no_contextvar(self):
        """_get_products_impl must not use ContextVar for tenant.

        Note: get_product_catalog() in the same module is a standalone helper
        with a ContextVar fallback for callers without identity.
        """
        import importlib
        import re

        mod = importlib.import_module("src.core.tools.products")
        with open(mod.__file__) as f:
            source = f.read()

        # Extract _get_products_impl body
        match = re.search(r"(async def _get_products_impl\(.*?)(?=\nasync def |\ndef |\Z)", source, re.DOTALL)
        assert match, "_get_products_impl not found"
        impl_body = match.group(1)

        assert "get_current_tenant" not in impl_body, (
            "_get_products_impl still calls get_current_tenant(). Use identity.tenant."
        )
        assert "set_current_tenant" not in impl_body, (
            "_get_products_impl still calls set_current_tenant(). "
            "Tenant ContextVar is managed by the transport boundary."
        )
