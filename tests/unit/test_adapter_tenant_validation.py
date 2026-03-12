"""Regression tests for tenant_id validation at system boundaries.

beads-yz1: Adapters using `tenant_id or ""` silently coerce None to empty
string, causing all tenant-scoped queries to return empty results instead of
raising an error.  The fix is to validate tenant_id at the adapter boundary
and raise ValueError for None or empty string.

beads-7zn: Same pattern in admin blueprint _call_webhook_for_creative_status —
`tenant_id or ""` coerces None to empty string, causing AdminCreativeUoW to
silently return empty results.
"""

from __future__ import annotations

import pytest

from src.core.schemas import Principal


def _make_principal() -> Principal:
    """Create a minimal Principal for adapter construction."""
    return Principal(
        principal_id="test_principal",
        name="Test Principal",
        platform_mappings={},
    )


class TestAdapterTenantIdValidation:
    """Adapter must reject None or empty tenant_id at construction time."""

    def test_gam_adapter_rejects_none_tenant_id(self):
        """GoogleAdManager with tenant_id=None must raise, not silently use ''."""
        from src.adapters.google_ad_manager import GoogleAdManager

        with pytest.raises(ValueError, match="tenant_id"):
            GoogleAdManager(
                config={"service_account_json": "{}"},
                principal=_make_principal(),
                network_code="12345",
                dry_run=True,
                tenant_id=None,
            )

    def test_gam_adapter_rejects_empty_tenant_id(self):
        """GoogleAdManager with tenant_id='' must raise, not proceed silently."""
        from src.adapters.google_ad_manager import GoogleAdManager

        with pytest.raises(ValueError, match="tenant_id"):
            GoogleAdManager(
                config={"service_account_json": "{}"},
                principal=_make_principal(),
                network_code="12345",
                dry_run=True,
                tenant_id="",
            )

    def test_mock_adapter_rejects_none_tenant_id(self):
        """MockAdServer with tenant_id=None must raise, not silently use ''."""
        from src.adapters.mock_ad_server import MockAdServer

        with pytest.raises(ValueError, match="tenant_id"):
            MockAdServer(
                config={},
                principal=_make_principal(),
                tenant_id=None,
            )

    def test_mock_adapter_rejects_empty_tenant_id(self):
        """MockAdServer with tenant_id='' must raise, not proceed silently."""
        from src.adapters.mock_ad_server import MockAdServer

        with pytest.raises(ValueError, match="tenant_id"):
            MockAdServer(
                config={},
                principal=_make_principal(),
                tenant_id="",
            )

    def test_gam_adapter_accepts_valid_tenant_id(self):
        """GoogleAdManager with valid tenant_id should initialize without error."""
        from src.adapters.google_ad_manager import GoogleAdManager

        adapter = GoogleAdManager(
            config={"service_account_json": "{}"},
            principal=_make_principal(),
            network_code="12345",
            dry_run=True,
            tenant_id="valid_tenant",
        )
        assert adapter.tenant_id == "valid_tenant"

    def test_mock_adapter_accepts_valid_tenant_id(self):
        """MockAdServer with valid tenant_id should initialize without error."""
        from src.adapters.mock_ad_server import MockAdServer

        adapter = MockAdServer(
            config={},
            principal=_make_principal(),
            tenant_id="valid_tenant",
        )
        assert adapter.tenant_id == "valid_tenant"


class TestBlueprintTenantIdValidation:
    """Admin blueprint functions must reject None/empty tenant_id explicitly."""

    @pytest.mark.asyncio
    async def test_call_webhook_rejects_none_tenant_id(self):
        """_call_webhook_for_creative_status with tenant_id=None must raise, not use ''."""
        from src.admin.blueprints.creatives import _call_webhook_for_creative_status

        with pytest.raises(ValueError, match="tenant_id"):
            await _call_webhook_for_creative_status(creative_id="cr_123", tenant_id=None)

    @pytest.mark.asyncio
    async def test_call_webhook_rejects_empty_tenant_id(self):
        """_call_webhook_for_creative_status with tenant_id='' must raise, not proceed."""
        from src.admin.blueprints.creatives import _call_webhook_for_creative_status

        with pytest.raises(ValueError, match="tenant_id"):
            await _call_webhook_for_creative_status(creative_id="cr_123", tenant_id="")
