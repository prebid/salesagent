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
_IDEMPOTENCY_KEY = "sync-creatives-test-key"


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
                    parameters={
                        "creatives": [],
                        "account": account_param,
                        "idempotency_key": _IDEMPOTENCY_KEY,
                    },
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


class TestSyncCreativesAssetsDefault:
    """The A2A handler must default the (required) ``assets`` field to {} like the impl (#1546).

    ``CreativeAsset.assets`` is a required field, but a static creative may legitimately
    omit it — the shared impl (_sync.py) defaults it via ``setdefault("assets", {})``.
    The A2A handler constructed ``CreativeAsset(**c)`` directly, so an assets-less
    creative that the impl would accept was rejected at the A2A boundary. This pins the
    handler mirroring the impl's default.
    """

    def _call_handler_with_creative(self, creative_dict):
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
        captured = {}

        def _fake_core(creatives, **kwargs):
            captured["creatives"] = creatives
            result = MagicMock()
            result.model_dump.return_value = {}
            return result

        with patch("src.a2a_server.adcp_a2a_server.core_sync_creatives_tool", side_effect=_fake_core):
            import asyncio

            asyncio.run(
                handler._handle_sync_creatives_skill(
                    parameters={
                        "creatives": [creative_dict],
                        "idempotency_key": _IDEMPOTENCY_KEY,
                    },
                    identity=_MOCK_IDENTITY,
                )
            )
        return captured["creatives"]

    def test_assets_less_creative_defaults_to_empty_dict(self):
        """A creative dict without ``assets`` is accepted; the handler builds a CreativeAsset
        with assets defaulted to {} instead of raising 'assets: Field required'."""
        from adcp.types import CreativeAsset

        creative = {
            "creative_id": "c_no_assets",
            "name": "Static creative without assets",
            "format_id": {"id": "display_300x250_image", "agent_url": "https://creative.example.com/"},
            "media_url": "https://example.com/banner.png",
        }
        creatives = self._call_handler_with_creative(creative)
        assert len(creatives) == 1
        built = creatives[0]
        assert isinstance(built, CreativeAsset)
        assert built.creative_id == "c_no_assets"
        assert built.assets == {}

    def test_provided_assets_are_preserved(self):
        """An explicit assets value is not overwritten by the default."""
        creative = {
            "creative_id": "c_with_assets",
            "name": "Static creative with assets",
            "format_id": {"id": "display_300x250_image", "agent_url": "https://creative.example.com/"},
            "assets": {
                "banner": {
                    "asset_type": "image",
                    "url": "https://example.com/banner.png",
                    "width": 300,
                    "height": 250,
                }
            },
        }
        creatives = self._call_handler_with_creative(creative)
        assert "banner" in creatives[0].assets
