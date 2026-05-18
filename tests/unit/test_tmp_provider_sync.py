"""Unit tests for TMP Provider package sync service.

Covers:
- _build_package_payload: field mapping from MediaPackage to TMP Provider format
- sync_packages_for_media_buy: fan-out logic, error isolation, logging
- _resolve_seller_agent_url: env override, tenant virtual_host, fallback

beads: salesagent-tmp-sync
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.services.tmp_provider_sync import (
    _build_package_payload,
    _resolve_seller_agent_url,
    sync_packages_for_media_buy,
)


# ---------------------------------------------------------------------------
# _build_package_payload tests
# ---------------------------------------------------------------------------


class TestBuildPackagePayload:
    """_build_package_payload maps MediaPackage DB rows to TMP Provider sync format."""

    def test_maps_all_fields_from_package_config(self):
        """All expected fields are extracted from package_config."""
        pkg = MagicMock()
        pkg.package_id = "pkg-001"
        pkg.package_config = {
            "product_id": "prod-42",
            "brand": "Acme Corp",
            "keywords": ["shoes", "running"],
            "topics": ["sports"],
            "content_policies": ["brand_safe"],
            "summary": "Running shoes campaign",
            "creative_manifest": {"format": "banner"},
            "price": {"amount": 5.0, "currency": "USD"},
            "macros": {"CLICK_URL": "https://example.com"},
            "is_active": True,
            "expires_at": "2026-12-31T23:59:59Z",
        }

        result = _build_package_payload("mb-100", pkg, "http://agent.example.com/mcp")

        assert result["package_id"] == "pkg-001"
        assert result["media_buy_id"] == "mb-100"
        assert result["offering_id"] == "prod-42"
        assert result["brand"] == "Acme Corp"
        assert result["keywords"] == ["shoes", "running"]
        assert result["topics"] == ["sports"]
        assert result["content_policies"] == ["brand_safe"]
        assert result["summary"] == "Running shoes campaign"
        assert result["creative_manifest"] == {"format": "banner"}
        assert result["price"] == {"amount": 5.0, "currency": "USD"}
        assert result["macros"] == {"CLICK_URL": "https://example.com"}
        assert result["si_agent_endpoint"] == "http://agent.example.com/mcp"
        assert result["is_active"] is True
        assert result["expires_at"] == "2026-12-31T23:59:59Z"

    def test_uses_offering_id_fallback(self):
        """Falls back to offering_id when product_id is absent."""
        pkg = MagicMock()
        pkg.package_id = "pkg-002"
        pkg.package_config = {"offering_id": "offer-99"}

        result = _build_package_payload("mb-200", pkg, "http://agent/mcp")

        assert result["offering_id"] == "offer-99"

    def test_defaults_for_missing_config_fields(self):
        """Missing config fields get sensible defaults (empty lists, None, etc.)."""
        pkg = MagicMock()
        pkg.package_id = "pkg-003"
        pkg.package_config = {}

        result = _build_package_payload("mb-300", pkg, "http://agent/mcp")

        assert result["offering_id"] == ""
        assert result["brand"] is None
        assert result["keywords"] == []
        assert result["topics"] == []
        assert result["content_policies"] == []
        assert result["summary"] == ""
        assert result["creative_manifest"] is None
        assert result["price"] is None
        assert result["macros"] == {}
        assert result["is_active"] is True
        assert result["expires_at"] is None

    def test_handles_none_package_config(self):
        """package_config=None doesn't crash — treated as empty dict."""
        pkg = MagicMock()
        pkg.package_id = "pkg-004"
        pkg.package_config = None

        result = _build_package_payload("mb-400", pkg, "http://agent/mcp")

        assert result["package_id"] == "pkg-004"
        assert result["media_buy_id"] == "mb-400"
        assert result["keywords"] == []

    def test_required_policies_fallback_for_content_policies(self):
        """Falls back to required_policies when content_policies is absent."""
        pkg = MagicMock()
        pkg.package_id = "pkg-005"
        pkg.package_config = {"required_policies": ["no_alcohol"]}

        result = _build_package_payload("mb-500", pkg, "http://agent/mcp")

        assert result["content_policies"] == ["no_alcohol"]

    def test_bid_price_fallback_for_price(self):
        """Falls back to bid_price when price is absent."""
        pkg = MagicMock()
        pkg.package_id = "pkg-006"
        pkg.package_config = {"bid_price": {"amount": 3.5}}

        result = _build_package_payload("mb-600", pkg, "http://agent/mcp")

        assert result["price"] == {"amount": 3.5}

    def test_name_fallback_for_summary(self):
        """Falls back to name when summary is absent."""
        pkg = MagicMock()
        pkg.package_id = "pkg-007"
        pkg.package_config = {"name": "Campaign Alpha"}

        result = _build_package_payload("mb-700", pkg, "http://agent/mcp")

        assert result["summary"] == "Campaign Alpha"


# ---------------------------------------------------------------------------
# sync_packages_for_media_buy fan-out tests
# ---------------------------------------------------------------------------


class TestSyncPackagesFanOut:
    """sync_packages_for_media_buy loads packages and fans out to providers."""

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp")
    @patch("src.services.tmp_provider_sync.TMPProviderUoW")
    @patch("src.services.tmp_provider_sync.MediaBuyUoW")
    def test_fans_out_to_all_providers(self, mock_mb_uow_cls, mock_tp_uow_cls,
                                        mock_resolve, mock_post):
        """Packages are POSTed to every syncable provider."""
        # Setup media buy UoW
        pkg = MagicMock()
        pkg.package_id = "pkg-1"
        pkg.package_config = {"product_id": "prod-1", "name": "Test"}
        mock_mb_uow = MagicMock()
        mock_mb_uow.media_buys.get_packages.return_value = [pkg]
        mock_mb_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_mb_uow)
        mock_mb_uow_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Setup TMP provider UoW
        provider1 = MagicMock()
        provider1.name = "Provider A"
        provider1.endpoint = "http://provider-a:3000"
        provider2 = MagicMock()
        provider2.name = "Provider B"
        provider2.endpoint = "http://provider-b:3000"
        mock_tp_uow = MagicMock()
        mock_tp_uow.tmp_providers.list_syncable.return_value = [provider1, provider2]
        mock_tp_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_tp_uow)
        mock_tp_uow_cls.return_value.__exit__ = MagicMock(return_value=False)

        sync_packages_for_media_buy("tenant-1", "mb-1")

        assert mock_post.call_count == 2
        mock_post.assert_any_call("http://provider-a:3000", [_build_package_payload("mb-1", pkg, "http://agent/mcp")])
        mock_post.assert_any_call("http://provider-b:3000", [_build_package_payload("mb-1", pkg, "http://agent/mcp")])

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp")
    @patch("src.services.tmp_provider_sync.TMPProviderUoW")
    @patch("src.services.tmp_provider_sync.MediaBuyUoW")
    def test_skips_when_no_packages(self, mock_mb_uow_cls, mock_tp_uow_cls,
                                     mock_resolve, mock_post):
        """No HTTP calls when media buy has no packages."""
        mock_mb_uow = MagicMock()
        mock_mb_uow.media_buys.get_packages.return_value = []
        mock_mb_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_mb_uow)
        mock_mb_uow_cls.return_value.__exit__ = MagicMock(return_value=False)

        sync_packages_for_media_buy("tenant-1", "mb-1")

        mock_post.assert_not_called()

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp")
    @patch("src.services.tmp_provider_sync.TMPProviderUoW")
    @patch("src.services.tmp_provider_sync.MediaBuyUoW")
    def test_skips_when_no_providers(self, mock_mb_uow_cls, mock_tp_uow_cls,
                                      mock_resolve, mock_post):
        """No HTTP calls when tenant has no syncable providers."""
        pkg = MagicMock()
        pkg.package_id = "pkg-1"
        pkg.package_config = {}
        mock_mb_uow = MagicMock()
        mock_mb_uow.media_buys.get_packages.return_value = [pkg]
        mock_mb_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_mb_uow)
        mock_mb_uow_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_tp_uow = MagicMock()
        mock_tp_uow.tmp_providers.list_syncable.return_value = []
        mock_tp_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_tp_uow)
        mock_tp_uow_cls.return_value.__exit__ = MagicMock(return_value=False)

        sync_packages_for_media_buy("tenant-1", "mb-1")

        mock_post.assert_not_called()

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp")
    @patch("src.services.tmp_provider_sync.TMPProviderUoW")
    @patch("src.services.tmp_provider_sync.MediaBuyUoW")
    def test_one_provider_failure_does_not_block_others(self, mock_mb_uow_cls, mock_tp_uow_cls,
                                                         mock_resolve, mock_post):
        """If one provider fails, the others still get called."""
        pkg = MagicMock()
        pkg.package_id = "pkg-1"
        pkg.package_config = {}
        mock_mb_uow = MagicMock()
        mock_mb_uow.media_buys.get_packages.return_value = [pkg]
        mock_mb_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_mb_uow)
        mock_mb_uow_cls.return_value.__exit__ = MagicMock(return_value=False)

        provider1 = MagicMock()
        provider1.name = "Failing Provider"
        provider1.endpoint = "http://fail:3000"
        provider2 = MagicMock()
        provider2.name = "Working Provider"
        provider2.endpoint = "http://ok:3000"
        mock_tp_uow = MagicMock()
        mock_tp_uow.tmp_providers.list_syncable.return_value = [provider1, provider2]
        mock_tp_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_tp_uow)
        mock_tp_uow_cls.return_value.__exit__ = MagicMock(return_value=False)

        # First call raises, second succeeds
        mock_post.side_effect = [httpx.ConnectError("refused"), None]

        # Should not raise — errors are logged and swallowed
        sync_packages_for_media_buy("tenant-1", "mb-1")

        assert mock_post.call_count == 2

    @patch("src.services.tmp_provider_sync._post_packages_sync")
    @patch("src.services.tmp_provider_sync._resolve_seller_agent_url", return_value="http://agent/mcp")
    @patch("src.services.tmp_provider_sync.TMPProviderUoW")
    @patch("src.services.tmp_provider_sync.MediaBuyUoW")
    def test_package_load_failure_returns_early(self, mock_mb_uow_cls, mock_tp_uow_cls,
                                                 mock_resolve, mock_post):
        """If loading packages fails, no HTTP calls are made."""
        mock_mb_uow_cls.return_value.__enter__ = MagicMock(
            side_effect=RuntimeError("DB connection failed")
        )
        mock_mb_uow_cls.return_value.__exit__ = MagicMock(return_value=False)

        sync_packages_for_media_buy("tenant-1", "mb-1")

        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# _resolve_seller_agent_url tests
# ---------------------------------------------------------------------------


class TestResolveSellAgentUrl:
    """_resolve_seller_agent_url resolves the seller agent URL for package payloads."""

    def test_env_override_takes_precedence(self):
        """ADCP_AGENT_URL env var overrides tenant-based resolution."""
        with patch.dict("os.environ", {"ADCP_AGENT_URL": "https://custom.agent.com/mcp/"}):
            result = _resolve_seller_agent_url("any-tenant")

        assert result == "https://custom.agent.com/mcp"

    @patch("src.services.tmp_provider_sync.TenantConfigUoW")
    def test_uses_tenant_virtual_host(self, mock_uow_cls):
        """Uses tenant.virtual_host when ADCP_AGENT_URL is not set."""
        tenant = MagicMock()
        tenant.virtual_host = "tenant.salesagent.example.com"
        tenant.subdomain = "tenant"
        mock_uow = MagicMock()
        mock_uow.tenant_config = MagicMock()
        mock_uow.tenant_config.get_tenant.return_value = tenant
        mock_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow_cls.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict("os.environ", {}, clear=False):
            # Ensure ADCP_AGENT_URL is not set
            import os
            os.environ.pop("ADCP_AGENT_URL", None)
            result = _resolve_seller_agent_url("test-tenant")

        assert result == "https://tenant.salesagent.example.com/mcp"

    @patch("src.services.tmp_provider_sync.TenantConfigUoW")
    def test_falls_back_to_default(self, mock_uow_cls):
        """Falls back to default URL when tenant has no virtual_host or subdomain."""
        tenant = MagicMock()
        tenant.virtual_host = None
        tenant.subdomain = None
        mock_uow = MagicMock()
        mock_uow.tenant_config = MagicMock()
        mock_uow.tenant_config.get_tenant.return_value = tenant
        mock_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow_cls.return_value.__exit__ = MagicMock(return_value=False)

        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("ADCP_AGENT_URL", None)
            result = _resolve_seller_agent_url("test-tenant")

        assert result == "http://salesagent:8000/mcp"
