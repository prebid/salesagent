"""Regression tests for issue #1237: sync_creatives account raw-dict crash.

_handle_sync_creatives_skill passed `account` as a raw dict to core, but
resolve_account calls .root on it expecting an AccountReference RootModel.
Verifies the A2A handler wraps the dict in AccountReference before forwarding.
"""

from unittest.mock import MagicMock, patch

import pytest
from adcp.types import AccountReference as LibraryAccountReference

from src.core.exceptions import AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schema_helpers import to_account_reference

_MOCK_IDENTITY = ResolvedIdentity(
    principal_id="principal_123",
    tenant_id="tenant_123",
    tenant={"tenant_id": "tenant_123"},
    protocol="a2a",
)


def test_to_account_reference_handles_supported_inputs():
    """The shared helper validates dicts and preserves typed/empty values."""
    account_dict = {"brand": {"domain": "example.com"}, "operator": "op-1", "sandbox": False}
    result = to_account_reference(account_dict)
    assert isinstance(result, LibraryAccountReference)
    assert result.root.brand.domain == "example.com"
    assert result.root.operator == "op-1"
    assert result.root.sandbox is False
    assert to_account_reference(result) is result
    assert to_account_reference(None) is None


def test_to_account_reference_rejects_invalid_account_payload():
    """Malformed oneOf account payloads fail as a TYPED error at the shared helper.

    Updated for #1417: the to_* coercions carry an internal
    ``adcp_validation_boundary`` (the coerce_creative_filters pattern), so the
    rejection is an ``AdCPValidationError`` with a top-level suggestion — the
    previous raw ``pydantic.ValidationError`` leak WAS the disease this test
    now guards against.
    """
    with pytest.raises(AdCPValidationError) as excinfo:
        to_account_reference({})
    assert excinfo.value.suggestion, "typed rejection must carry a top-level suggestion"


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
