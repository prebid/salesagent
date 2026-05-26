"""``get_adcp_capabilities`` request-scoped capability handling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from adcp.decisioning import DecisioningCapabilities
from adcp.decisioning.capabilities import MediaBuy


def test_sdk_capabilities_response_emits_status_natively() -> None:
    """Beta 4's SDK response builder owns the envelope status field."""
    from adcp.server.responses import capabilities_response

    assert capabilities_response(["media_buy"])["status"] == "completed"


@pytest.mark.asyncio
async def test_webhook_shim_is_installed_on_platform_handler() -> None:
    """Importing the shim module installs the remaining webhook patch."""
    from adcp.decisioning.handler import PlatformHandler

    from core.platforms import _capabilities_envelope

    assert PlatformHandler.get_adcp_capabilities is _capabilities_envelope._get_adcp_capabilities_patched


@pytest.mark.asyncio
async def test_webhook_signing_capability_appended() -> None:
    """Webhook signing capability is populated from the tenant-specific helper."""
    import core.platforms._capabilities_envelope as mod
    from core.platforms._capabilities_envelope import (
        _ORIGINAL,
        _get_adcp_capabilities_patched,
    )

    async def _original(self, params, context):  # noqa: ANN001
        return {"status": "completed", "adcp": {}, "supported_protocols": ["media_buy"]}

    capability = {
        "supported": True,
        "profile": "adcp/webhook-signing/v1",
        "algorithms": ["ed25519"],
        "legacy_hmac_fallback": True,
    }

    mod._ORIGINAL = _original
    try:
        with patch(
            "core.platforms._capabilities_envelope._webhook_signing_for_tenant_id",
            return_value=capability,
        ) as webhook_mock:
            result = await _get_adcp_capabilities_patched(object(), context=SimpleNamespace(tenant_id="tenant_1"))
        assert result["webhook_signing"] == capability
        webhook_mock.assert_called_once_with("tenant_1")
    finally:
        mod._ORIGINAL = _ORIGINAL


@pytest.mark.asyncio
async def test_webhook_signing_capability_does_not_clobber_sdk_output() -> None:
    """If the SDK/projected capabilities already include webhook_signing, keep it."""
    import core.platforms._capabilities_envelope as mod
    from core.platforms._capabilities_envelope import (
        _ORIGINAL,
        _get_adcp_capabilities_patched,
    )

    existing = {"supported": False}

    async def _original(self, params, context):  # noqa: ANN001
        return {"status": "completed", "webhook_signing": existing}

    mod._ORIGINAL = _original
    try:
        with patch("core.platforms._capabilities_envelope._webhook_signing_for_tenant_id") as webhook_mock:
            result = await _get_adcp_capabilities_patched(object())
        assert result["webhook_signing"] == existing
        webhook_mock.assert_not_called()
    finally:
        mod._ORIGINAL = _ORIGINAL


def test_request_scoped_capabilities_adds_portfolio_domains() -> None:
    """Tenant publisher domains flow through the SDK's typed capabilities hook."""
    from core.platforms._capabilities_envelope import capabilities_for_request

    base = DecisioningCapabilities(media_buy=MediaBuy(supported_pricing_models=["cpm"]))
    context = SimpleNamespace(tenant_id="tenant_1")

    with patch(
        "core.platforms._capabilities_envelope._publisher_domains_for_tenant_id",
        return_value=["alpha.com", "mike.com", "zeta.com"],
    ):
        scoped = capabilities_for_request(base, context=context)

    assert scoped is not None
    assert scoped.media_buy is not None
    assert scoped.media_buy.portfolio is not None
    assert [domain.root for domain in scoped.media_buy.portfolio.publisher_domains] == [
        "alpha.com",
        "mike.com",
        "zeta.com",
    ]


def test_request_scoped_capabilities_omits_empty_portfolio_domains() -> None:
    """Empty publisher-domain sets return None so the base capabilities project."""
    from core.platforms._capabilities_envelope import capabilities_for_request

    base = DecisioningCapabilities(media_buy=MediaBuy(supported_pricing_models=["cpm"]))

    with patch("core.platforms._capabilities_envelope._publisher_domains_for_tenant_id", return_value=[]):
        scoped = capabilities_for_request(base, context=SimpleNamespace(tenant_id="tenant_1"))

    assert scoped is None


def test_webhook_signing_unsupported_without_current_tenant() -> None:
    """Discovery stays valid even when no tenant context is present."""
    from core.platforms._capabilities_envelope import _webhook_signing_for_current_tenant

    with patch("core.platforms._capabilities_envelope.current_tenant", return_value=None):
        assert _webhook_signing_for_current_tenant() == {"supported": False, "legacy_hmac_fallback": True}


def test_webhook_signing_supported_for_active_local_credential() -> None:
    """A usable local signing credential advertises the AdCP signing profile."""
    from core.platforms._capabilities_envelope import _webhook_signing_for_current_tenant

    with (
        patch("core.platforms._capabilities_envelope.current_tenant", return_value=SimpleNamespace(id="tenant_1")),
        patch(
            "src.services.webhook_signing.load_active_signing_credential", return_value=SimpleNamespace(alg="ed25519")
        ) as load_mock,
    ):
        assert _webhook_signing_for_current_tenant() == {
            "supported": True,
            "profile": "adcp/webhook-signing/v1",
            "algorithms": ["ed25519"],
            "legacy_hmac_fallback": True,
        }
    load_mock.assert_called_once_with(tenant_id="tenant_1", signing_mode="rfc9421")


def test_webhook_signing_unsupported_when_credential_load_fails() -> None:
    """Missing rows, KMS backends, unreadable PEMs, and bad JWKs stay unsupported."""
    from core.platforms._capabilities_envelope import _webhook_signing_for_current_tenant
    from src.services.webhook_signing import SigningConfigurationError

    with (
        patch("core.platforms._capabilities_envelope.current_tenant", return_value=SimpleNamespace(id="tenant_1")),
        patch(
            "src.services.webhook_signing.load_active_signing_credential",
            side_effect=SigningConfigurationError("failed to read PEM"),
        ) as load_mock,
    ):
        assert _webhook_signing_for_current_tenant() == {"supported": False, "legacy_hmac_fallback": True}
    load_mock.assert_called_once_with(tenant_id="tenant_1", signing_mode="rfc9421")
