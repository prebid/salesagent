"""Integration tests for device_type filtering in get_products.

Covers: salesagent-1rc7 (v3.6 device_type/device_platform targeting on Product)

Products declare supported device types via targeting_template.device_targets.
When a buyer requests products filtered by device_types, only products whose
device_targets intersect with the requested set should be returned. Products with
no device_targets (null) match any device_types filter (unrestricted).

These tests verify:
1. Device type filter returns only matching products
2. Products with no device_targets match any filter
3. Products with partial overlap match (ANY intersection)
4. Products with no overlap are excluded
5. No device_types filter returns all products (no filtering)
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestDeviceTypeFilter:
    """Products filtered by device_types in get_products filters."""

    @pytest.fixture
    def env(self, integration_db):
        """ProductEnv with products having different device_targets."""
        with ProductEnv(tenant_id="device-filter", principal_id="device-principal") as env:
            tenant = TenantFactory(tenant_id="device-filter", subdomain="device-filter")
            PrincipalFactory(tenant=tenant, principal_id="device-principal")

            # Mobile-only product
            p_mobile = ProductFactory(
                tenant=tenant,
                product_id="mobile_only",
                name="Mobile Only Product",
                targeting_template={"device_targets": ["mobile"]},
            )
            PricingOptionFactory(product=p_mobile, pricing_model="cpm", rate=Decimal("10.0"), is_fixed=True)

            # Desktop + tablet product
            p_dt = ProductFactory(
                tenant=tenant,
                product_id="desktop_tablet",
                name="Desktop and Tablet Product",
                targeting_template={"device_targets": ["desktop", "tablet"]},
            )
            PricingOptionFactory(product=p_dt, pricing_model="cpm", rate=Decimal("12.0"), is_fixed=True)

            # CTV-only product
            p_ctv = ProductFactory(
                tenant=tenant,
                product_id="ctv_only",
                name="CTV Only Product",
                targeting_template={"device_targets": ["ctv"]},
            )
            PricingOptionFactory(product=p_ctv, pricing_model="cpm", rate=Decimal("20.0"), is_fixed=True)

            # All-device product (no device restriction)
            p_all = ProductFactory(
                tenant=tenant,
                product_id="all_devices",
                name="All Devices Product",
                targeting_template={"geo": ["US"]},  # no device_targets
            )
            PricingOptionFactory(product=p_all, pricing_model="cpm", rate=Decimal("8.0"), is_fixed=True)

            yield env

    @pytest.mark.asyncio
    async def test_filter_mobile_returns_mobile_and_unrestricted(self, env):
        """Filtering by device_types=['mobile'] returns mobile products and unrestricted ones.

        Covers: salesagent-1rc7
        """
        response = await env.call_impl(
            brief="mobile ads",
            filters={"device_types": ["mobile"]},
        )

        product_ids = {p.product_id for p in response.products}
        assert "mobile_only" in product_ids, "Mobile product should match mobile filter"
        assert "all_devices" in product_ids, "Unrestricted product should match any filter"
        assert "ctv_only" not in product_ids, "CTV product should not match mobile filter"
        assert "desktop_tablet" not in product_ids, "Desktop+tablet product should not match mobile filter"

    @pytest.mark.asyncio
    async def test_filter_desktop_returns_desktop_and_unrestricted(self, env):
        """Filtering by device_types=['desktop'] returns desktop products and unrestricted.

        Covers: salesagent-1rc7
        """
        response = await env.call_impl(
            brief="desktop ads",
            filters={"device_types": ["desktop"]},
        )

        product_ids = {p.product_id for p in response.products}
        assert "desktop_tablet" in product_ids, "Desktop+tablet product should match desktop filter"
        assert "all_devices" in product_ids, "Unrestricted product should match any filter"
        assert "mobile_only" not in product_ids, "Mobile product should not match desktop filter"
        assert "ctv_only" not in product_ids, "CTV product should not match desktop filter"

    @pytest.mark.asyncio
    async def test_filter_multiple_types_returns_any_overlap(self, env):
        """Filtering by device_types=['mobile', 'tablet'] returns products with ANY overlap.

        Covers: salesagent-1rc7
        """
        response = await env.call_impl(
            brief="mobile and tablet ads",
            filters={"device_types": ["mobile", "tablet"]},
        )

        product_ids = {p.product_id for p in response.products}
        assert "mobile_only" in product_ids, "Mobile product overlaps with ['mobile', 'tablet']"
        assert "desktop_tablet" in product_ids, "Desktop+tablet product overlaps via 'tablet'"
        assert "all_devices" in product_ids, "Unrestricted product matches any filter"
        assert "ctv_only" not in product_ids, "CTV product has no overlap with mobile+tablet"

    @pytest.mark.asyncio
    async def test_no_filter_returns_all_products(self, env):
        """No device_types filter returns all products.

        Covers: salesagent-1rc7
        """
        response = await env.call_impl(brief="all ads")

        product_ids = {p.product_id for p in response.products}
        assert len(product_ids) == 4, "All 4 products should be returned when no device filter"
        assert "mobile_only" in product_ids
        assert "desktop_tablet" in product_ids
        assert "ctv_only" in product_ids
        assert "all_devices" in product_ids

    @pytest.mark.asyncio
    async def test_filter_ctv_returns_ctv_and_unrestricted(self, env):
        """Filtering by device_types=['ctv'] returns CTV products and unrestricted.

        Covers: salesagent-1rc7
        """
        response = await env.call_impl(
            brief="ctv ads",
            filters={"device_types": ["ctv"]},
        )

        product_ids = {p.product_id for p in response.products}
        assert "ctv_only" in product_ids, "CTV product should match ctv filter"
        assert "all_devices" in product_ids, "Unrestricted product should match any filter"
        assert "mobile_only" not in product_ids, "Mobile product should not match ctv filter"
        assert "desktop_tablet" not in product_ids, "Desktop+tablet product should not match ctv filter"
