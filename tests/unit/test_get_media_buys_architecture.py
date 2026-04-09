"""Architecture violation tests for _get_media_buys_impl.

Verifies Critical Pattern #5 (Transport Boundary) compliance:
1. _get_media_buys_impl accepts identity: ResolvedIdentity, not ctx: Context
2. _get_media_buys_impl raises AdCPError, not ToolError
3. _get_media_buys_impl signature has no transport-specific types
"""

import inspect

import pytest

from src.core.exceptions import AdCPAuthenticationError, AdCPError, AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import GetMediaBuysRequest


class TestGetMediaBuysImplAcceptsResolvedIdentity:
    """Violation 1: _get_media_buys_impl must accept identity: ResolvedIdentity, not ctx: Context."""

    def test_impl_has_identity_parameter(self):
        """_get_media_buys_impl should have an 'identity' parameter."""
        from src.core.tools.media_buy_list import _get_media_buys_impl

        sig = inspect.signature(_get_media_buys_impl)
        assert "identity" in sig.parameters, (
            "_get_media_buys_impl must have 'identity' parameter accepting ResolvedIdentity, "
            f"but found parameters: {list(sig.parameters.keys())}"
        )

    def test_impl_does_not_have_ctx_parameter(self):
        """_get_media_buys_impl should NOT have a 'ctx' parameter (transport-specific)."""
        from src.core.tools.media_buy_list import _get_media_buys_impl

        sig = inspect.signature(_get_media_buys_impl)
        assert "ctx" not in sig.parameters, (
            "_get_media_buys_impl still has 'ctx' parameter accepting Context/ToolContext, "
            "which violates Critical Pattern #5 (transport-agnostic _impl)"
        )


class TestGetMediaBuysImplRaisesAdCPError:
    """Violation 2: _get_media_buys_impl must raise AdCPError, not ToolError."""

    def test_none_identity_raises_adcp_error(self):
        """Passing identity=None should raise AdCPAuthenticationError (not ToolError)."""
        from src.core.tools.media_buy_list import _get_media_buys_impl

        req = GetMediaBuysRequest()
        with pytest.raises(AdCPAuthenticationError):
            _get_media_buys_impl(req, identity=None)

    def test_unsupported_account_id_raises_adcp_error(self):
        """Passing account_id should raise AdCPValidationError (not ToolError)."""
        from src.core.tools.media_buy_list import _get_media_buys_impl

        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant"},
        )
        req = GetMediaBuysRequest(account_id="some_account")
        with pytest.raises(AdCPValidationError):
            _get_media_buys_impl(req, identity=identity)

    def test_unsupported_account_raises_adcp_error(self):
        """Passing account (nested, AdCP 3.x) should raise AdCPValidationError."""
        from src.core.tools.media_buy_list import _get_media_buys_impl

        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant"},
        )
        req = GetMediaBuysRequest(account={"account_id": "some_account"})
        with pytest.raises(AdCPValidationError):
            _get_media_buys_impl(req, identity=identity)

    def test_error_types_are_adcp_error_subclasses(self):
        """All errors raised by _impl are AdCPError subclasses, not ToolError."""
        assert issubclass(AdCPAuthenticationError, AdCPError)
        assert issubclass(AdCPValidationError, AdCPError)


class TestGetMediaBuysImplNoTransportImports:
    """Violation 3: _get_media_buys_impl signature has no transport-specific types."""

    def test_impl_signature_has_no_transport_context(self):
        """_get_media_buys_impl signature must not reference Context or ToolContext.

        Note: The MCP wrapper (get_media_buys) still uses Context -- that's correct.
        Only the _impl function must be transport-agnostic.
        """
        from src.core.tools.media_buy_list import _get_media_buys_impl

        sig = inspect.signature(_get_media_buys_impl)
        for param_name, param in sig.parameters.items():
            annotation = str(param.annotation)
            if "Context" in annotation and "ContextObject" not in annotation:
                pytest.fail(
                    f"_get_media_buys_impl has transport-specific type in parameter "
                    f"'{param_name}: {annotation}'. _impl functions must be transport-agnostic."
                )
