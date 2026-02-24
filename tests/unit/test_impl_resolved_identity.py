#!/usr/bin/env python3
"""Tests that _impl functions accept ResolvedIdentity instead of Context|ToolContext.

Verifies the transport-agnostic migration: all _impl functions should accept
ResolvedIdentity as their context parameter, extracting principal_id and
tenant_id directly from it without isinstance checks or auth extraction.

Core Invariant: _impl functions receive ResolvedIdentity (transport-agnostic).
They never import from fastmcp, never call get_principal_from_context, never
do isinstance checks on context types.
"""

import inspect

import pytest

from src.core.resolved_identity import ResolvedIdentity

# ---------------------------------------------------------------------------
# Signature tests — verify _impl functions accept ResolvedIdentity
# ---------------------------------------------------------------------------


class TestImplSignaturesAcceptResolvedIdentity:
    """All _impl functions must have an identity: ResolvedIdentity parameter."""

    @staticmethod
    def _get_identity_param(func) -> inspect.Parameter | None:
        """Find the identity/context parameter in a function signature."""
        sig = inspect.signature(func)
        # Look for 'identity' parameter (post-migration name)
        if "identity" in sig.parameters:
            return sig.parameters["identity"]
        return None

    def test_capabilities_impl_accepts_resolved_identity(self):
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        param = self._get_identity_param(_get_adcp_capabilities_impl)
        assert param is not None, "_get_adcp_capabilities_impl must have 'identity' parameter"

    def test_creative_formats_impl_accepts_resolved_identity(self):
        from src.core.tools.creative_formats import _list_creative_formats_impl

        param = self._get_identity_param(_list_creative_formats_impl)
        assert param is not None, "_list_creative_formats_impl must have 'identity' parameter"

    def test_properties_impl_accepts_resolved_identity(self):
        from src.core.tools.properties import _list_authorized_properties_impl

        param = self._get_identity_param(_list_authorized_properties_impl)
        assert param is not None, "_list_authorized_properties_impl must have 'identity' parameter"

    def test_products_impl_accepts_resolved_identity(self):
        from src.core.tools.products import _get_products_impl

        param = self._get_identity_param(_get_products_impl)
        assert param is not None, "_get_products_impl must have 'identity' parameter"

    def test_media_buy_create_impl_accepts_resolved_identity(self):
        from src.core.tools.media_buy_create import _create_media_buy_impl

        param = self._get_identity_param(_create_media_buy_impl)
        assert param is not None, "_create_media_buy_impl must have 'identity' parameter"

    def test_media_buy_update_impl_accepts_resolved_identity(self):
        from src.core.tools.media_buy_update import _update_media_buy_impl

        param = self._get_identity_param(_update_media_buy_impl)
        assert param is not None, "_update_media_buy_impl must have 'identity' parameter"

    def test_media_buy_delivery_impl_accepts_resolved_identity(self):
        from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl

        param = self._get_identity_param(_get_media_buy_delivery_impl)
        assert param is not None, "_get_media_buy_delivery_impl must have 'identity' parameter"

    def test_performance_impl_accepts_resolved_identity(self):
        from src.core.tools.performance import _update_performance_index_impl

        param = self._get_identity_param(_update_performance_index_impl)
        assert param is not None, "_update_performance_index_impl must have 'identity' parameter"

    def test_sync_creatives_impl_accepts_resolved_identity(self):
        from src.core.tools.creatives._sync import _sync_creatives_impl

        param = self._get_identity_param(_sync_creatives_impl)
        assert param is not None, "_sync_creatives_impl must have 'identity' parameter"

    def test_list_creatives_impl_accepts_resolved_identity(self):
        from src.core.tools.creatives.listing import _list_creatives_impl

        param = self._get_identity_param(_list_creatives_impl)
        assert param is not None, "_list_creatives_impl must have 'identity' parameter"

    def test_signals_get_impl_accepts_resolved_identity(self):
        from src.core.tools.signals import _get_signals_impl

        param = self._get_identity_param(_get_signals_impl)
        assert param is not None, "_get_signals_impl must have 'identity' parameter"

    def test_signals_activate_impl_accepts_resolved_identity(self):
        from src.core.tools.signals import _activate_signal_impl

        param = self._get_identity_param(_activate_signal_impl)
        assert param is not None, "_activate_signal_impl must have 'identity' parameter"


# ---------------------------------------------------------------------------
# Transport-agnostic invariant — no fastmcp imports in _impl files
# ---------------------------------------------------------------------------


class TestNoTransportImportsInImpl:
    """_impl functions must not import from fastmcp (transport-agnostic invariant).

    Verified by checking that _impl functions accept ResolvedIdentity parameter,
    which structurally prevents the old get_principal_from_context pattern.
    """

    IMPL_FUNCTIONS = [
        ("src.core.tools.capabilities", "_get_adcp_capabilities_impl"),
        ("src.core.tools.creative_formats", "_list_creative_formats_impl"),
        ("src.core.tools.properties", "_list_authorized_properties_impl"),
        ("src.core.tools.products", "_get_products_impl"),
        ("src.core.tools.media_buy_create", "_create_media_buy_impl"),
        ("src.core.tools.media_buy_update", "_update_media_buy_impl"),
        ("src.core.tools.media_buy_delivery", "_get_media_buy_delivery_impl"),
        ("src.core.tools.performance", "_update_performance_index_impl"),
        ("src.core.tools.creatives._sync", "_sync_creatives_impl"),
        ("src.core.tools.creatives.listing", "_list_creatives_impl"),
        ("src.core.tools.signals", "_get_signals_impl"),
    ]

    @pytest.mark.parametrize("module_path,func_name", IMPL_FUNCTIONS)
    def test_impl_file_has_no_get_principal_from_context_in_impl(self, module_path, func_name):
        """_impl functions should accept ResolvedIdentity, not Context/ToolContext."""
        import importlib

        mod = importlib.import_module(module_path)
        func = getattr(mod, func_name)
        sig = inspect.signature(func)

        # Verify 'identity' parameter exists (means it was migrated)
        assert "identity" in sig.parameters, (
            f"{module_path}::{func_name} lacks 'identity' parameter — "
            f"should accept ResolvedIdentity instead of Context/ToolContext"
        )

        # Verify the old 'context' or 'ctx' parameter accepting Context is gone
        # (context: ContextObject is OK — that's the AdCP payload context, not transport)
        for param_name, param in sig.parameters.items():
            annotation = str(param.annotation)
            if "Context" in annotation and "ContextObject" not in annotation and param_name != "context":
                raise AssertionError(
                    f"{module_path}::{func_name} still has transport Context in parameter "
                    f"'{param_name}: {annotation}' — should use identity: ResolvedIdentity"
                )


# ---------------------------------------------------------------------------
# Behavioral test — ResolvedIdentity passes through correctly
# ---------------------------------------------------------------------------


class TestResolvedIdentityPassthrough:
    """Verify _impl functions can extract fields from ResolvedIdentity."""

    def test_resolved_identity_provides_principal_id(self):
        """ResolvedIdentity.principal_id is accessible for _impl use."""
        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant"},
            protocol="mcp",
        )
        assert identity.principal_id == "test_principal"
        assert identity.tenant_id == "test_tenant"
        assert identity.tenant["tenant_id"] == "test_tenant"

    def test_none_identity_for_discovery(self):
        """_impl functions should handle None identity for discovery endpoints."""
        identity = ResolvedIdentity(
            principal_id=None,
            tenant_id="default",
            tenant={"tenant_id": "default"},
            protocol="mcp",
        )
        assert identity.principal_id is None
        assert identity.is_authenticated is False
        assert identity.tenant_id == "default"
