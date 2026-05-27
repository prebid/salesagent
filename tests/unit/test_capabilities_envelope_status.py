"""``get_adcp_capabilities`` request-scoped capability handling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from adcp.decisioning import DecisioningCapabilities
from adcp.decisioning.capabilities import MediaBuy, WebhookSigning


def test_sdk_capabilities_response_emits_status_natively() -> None:
    """Beta 4's SDK response builder owns the envelope status field."""
    from adcp.server.responses import capabilities_response

    assert capabilities_response(["media_buy"])["status"] == "completed"


def test_webhook_signing_uses_sdk_native_projection() -> None:
    """Importing the helper module no longer monkey-patches the SDK handler."""
    from adcp.decisioning.handler import PlatformHandler

    from core.platforms import _capabilities_envelope

    assert not hasattr(_capabilities_envelope, "_get_adcp_capabilities_patched")
    assert PlatformHandler.get_adcp_capabilities.__module__ == "adcp.decisioning.handler"


def test_request_scoped_capabilities_adds_webhook_signing() -> None:
    """Tenant webhook signing support flows through the typed SDK hook."""
    from core.platforms._capabilities_envelope import capabilities_for_request

    base = DecisioningCapabilities(
        webhook_signing_managed_externally=True,
        webhook_signing=WebhookSigning(supported=False, legacy_hmac_fallback=True),
    )
    context = SimpleNamespace(tenant_id="tenant_1")

    with (
        patch("core.platforms._capabilities_envelope._publisher_domains_for_tenant_id", return_value=[]),
        patch(
            "src.services.webhook_signing.load_active_signing_credential",
            return_value=SimpleNamespace(alg="ed25519"),
        ) as load_mock,
    ):
        scoped = capabilities_for_request(base, context=context)

    assert scoped is not None
    assert scoped.webhook_signing_managed_externally is True
    assert scoped.webhook_signing is not None
    assert scoped.webhook_signing.model_dump(mode="json", exclude_none=True) == {
        "supported": True,
        "profile": "adcp/webhook-signing/v1",
        "algorithms": ["ed25519"],
        "legacy_hmac_fallback": True,
    }
    load_mock.assert_called_once_with(tenant_id="tenant_1", signing_mode="rfc9421")


def test_request_scoped_capabilities_adds_portfolio_domains() -> None:
    """Tenant publisher domains flow through the SDK's typed capabilities hook."""
    from core.platforms._capabilities_envelope import capabilities_for_request

    base = DecisioningCapabilities(
        media_buy=MediaBuy(supported_pricing_models=["cpm"], supports_proposals=True),
        webhook_signing=WebhookSigning(supported=False, legacy_hmac_fallback=True),
    )
    context = SimpleNamespace(tenant_id="tenant_1")

    with (
        patch(
            "core.platforms._capabilities_envelope._publisher_domains_for_tenant_id",
            return_value=["alpha.com", "mike.com", "zeta.com"],
        ),
        patch("src.services.webhook_signing.load_active_signing_credential", return_value=None),
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


def test_request_scoped_capabilities_advertises_compose_products_disabled() -> None:
    """Storefront-owned product composition is surfaced via supports_proposals."""
    from core.platforms._capabilities_envelope import capabilities_for_request

    base = DecisioningCapabilities(
        media_buy=MediaBuy(supported_pricing_models=["cpm"], supports_proposals=True),
        webhook_signing=WebhookSigning(supported=False, legacy_hmac_fallback=True),
    )

    with (
        patch("core.platforms._capabilities_envelope.publisher_owns_compose_products", return_value=False),
        patch("core.platforms._capabilities_envelope._publisher_domains_for_tenant_id", return_value=[]),
        patch("src.services.webhook_signing.load_active_signing_credential", return_value=None),
    ):
        scoped = capabilities_for_request(base, context=SimpleNamespace(tenant_id="tenant_1"))

    assert scoped is not None
    assert scoped.media_buy is not None
    assert scoped.media_buy.supports_proposals is False


def test_request_scoped_capabilities_omits_empty_portfolio_domains() -> None:
    """Empty publisher-domain sets return None so the base capabilities project."""
    from core.platforms._capabilities_envelope import capabilities_for_request

    base = DecisioningCapabilities(
        media_buy=MediaBuy(supported_pricing_models=["cpm"], supports_proposals=True),
        webhook_signing=WebhookSigning(supported=False, legacy_hmac_fallback=True),
    )

    with (
        patch("core.platforms._capabilities_envelope._publisher_domains_for_tenant_id", return_value=[]),
        patch("src.services.webhook_signing.load_active_signing_credential", return_value=None),
    ):
        scoped = capabilities_for_request(base, context=SimpleNamespace(tenant_id="tenant_1"))

    assert scoped is None


def test_webhook_signing_unsupported_without_current_tenant() -> None:
    """Discovery stays valid even when no tenant context is present."""
    from core.platforms._capabilities_envelope import _webhook_signing_for_current_tenant

    with patch("core.platforms._capabilities_envelope.current_tenant", return_value=None):
        assert _webhook_signing_for_current_tenant().model_dump(mode="json", exclude_none=True) == {
            "supported": False,
            "legacy_hmac_fallback": True,
        }


def test_webhook_signing_supported_for_active_local_credential() -> None:
    """A usable local signing credential advertises the AdCP signing profile."""
    from core.platforms._capabilities_envelope import _webhook_signing_for_current_tenant

    with (
        patch("core.platforms._capabilities_envelope.current_tenant", return_value=SimpleNamespace(id="tenant_1")),
        patch(
            "src.services.webhook_signing.load_active_signing_credential", return_value=SimpleNamespace(alg="ed25519")
        ) as load_mock,
    ):
        assert _webhook_signing_for_current_tenant().model_dump(mode="json", exclude_none=True) == {
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
        assert _webhook_signing_for_current_tenant().model_dump(mode="json", exclude_none=True) == {
            "supported": False,
            "legacy_hmac_fallback": True,
        }
    load_mock.assert_called_once_with(tenant_id="tenant_1", signing_mode="rfc9421")
