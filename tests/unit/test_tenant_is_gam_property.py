#!/usr/bin/env python3
"""Unit tests for Tenant.is_gam_tenant property.

Tests the centralized GAM tenant detection property that consolidates
checks for both ad_server and adapter_config.adapter_type.

Note: These are simple unit tests that test the property logic via ad_server field.
Integration tests in test_gam_tenant_setup.py test the full adapter_config path.
"""

import pytest

from src.core.database.models import Tenant


def test_is_gam_tenant_via_ad_server():
    """Test is_gam_tenant returns True when ad_server is google_ad_manager."""
    tenant = Tenant(
        tenant_id="test_tenant",
        name="Test Tenant",
        subdomain="test",
        ad_server="google_ad_manager",
    )
    # No adapter_config set
    tenant.adapter_config = None

    assert tenant.is_gam_tenant is True


def test_is_not_gam_tenant_mock_adapter():
    """Test is_gam_tenant returns False for mock adapter."""
    tenant = Tenant(
        tenant_id="test_tenant",
        name="Test Tenant",
        subdomain="test",
        ad_server="mock",
    )
    tenant.adapter_config = None

    assert tenant.is_gam_tenant is False


def test_is_not_gam_tenant_kevel_adapter():
    """Test is_gam_tenant returns False for Kevel adapter."""
    tenant = Tenant(
        tenant_id="test_tenant",
        name="Test Tenant",
        subdomain="test",
        ad_server="kevel",
    )
    tenant.adapter_config = None

    assert tenant.is_gam_tenant is False


def test_is_not_gam_tenant_no_adapter_configured():
    """Test is_gam_tenant returns False when no adapter is configured."""
    tenant = Tenant(
        tenant_id="test_tenant",
        name="Test Tenant",
        subdomain="test",
        ad_server=None,
    )
    tenant.adapter_config = None

    assert tenant.is_gam_tenant is False


def test_is_not_gam_tenant_empty_ad_server():
    """Test is_gam_tenant returns False when ad_server is empty string."""
    tenant = Tenant(
        tenant_id="test_tenant",
        name="Test Tenant",
        subdomain="test",
        ad_server="",
    )
    tenant.adapter_config = None

    assert tenant.is_gam_tenant is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
