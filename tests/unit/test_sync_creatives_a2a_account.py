"""Regression tests for issue #1237: sync_creatives account raw-dict crash.

_handle_sync_creatives_skill passed `account` as a raw dict to core, but
resolve_account calls .root on it expecting an AccountReference RootModel.
Verifies the A2A handler wraps the dict in AccountReference before forwarding.
"""

from unittest.mock import MagicMock, patch

from adcp.types import AccountReference as LibraryAccountReference

from src.core.resolved_identity import ResolvedIdentity

_MOCK_IDENTITY = ResolvedIdentity(
    principal_id="principal_123",
    tenant_id="tenant_123",
    tenant={"tenant_id": "tenant_123"},
    protocol="a2a",
)


class TestSyncCreativesAccountCoercion:
    """A2A handler must coerce raw account dict to AccountReference before calling core."""

    def _call_handler_with_account(self, account_param):
        """Invoke _handle_sync_creatives_skill with a given account parameter value."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)

        captured = {}

        def _fake_core(creatives, **kwargs):
            captured["account"] = kwargs.get("account")
            result = MagicMock()
            result.model_dump.return_value = {}
            return result

        with patch("src.a2a_server.adcp_a2a_server.core_sync_creatives_tool", side_effect=_fake_core):
            import asyncio

            asyncio.run(
                handler._handle_sync_creatives_skill(
                    parameters={"creatives": [], "account": account_param},
                    identity=_MOCK_IDENTITY,
                )
            )

        return captured.get("account")

    def test_dict_account_is_wrapped_in_account_reference(self):
        """A raw dict account is coerced to AccountReference with field values preserved."""
        account_dict = {"brand": {"domain": "example.com"}, "operator": "op-1", "sandbox": False}
        result = self._call_handler_with_account(account_dict)
        assert isinstance(result, LibraryAccountReference)
        assert result.root.brand.domain == "example.com"
        assert result.root.operator == "op-1"
        assert result.root.sandbox is False

    def test_none_account_passes_through_as_none(self):
        """None account is passed through unchanged."""
        result = self._call_handler_with_account(None)
        assert result is None

    def test_already_typed_account_passes_through(self):
        """An already-validated AccountReference is forwarded by identity, not re-validated."""
        typed = LibraryAccountReference.model_validate(
            {"brand": {"domain": "example.com"}, "operator": "op-1", "sandbox": False}
        )
        result = self._call_handler_with_account(typed)
        assert result is typed
