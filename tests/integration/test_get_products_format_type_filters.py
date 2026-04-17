"""Integration tests for format_ids and standard_formats_only filters.

Tests the format filtering code paths in src/core/tools/products.py:
  1. format_ids: filters products by specific format IDs
  2. standard_formats_only: excludes products with only non-standard formats

Note: format_types filter was removed in adcp 3.12.

These tests exercise the REAL filtering logic with a real database.
"""

from decimal import Decimal

import pytest

from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.requires_db
class TestStandardFormatsOnlyFilter:
    """Test standard_formats_only filter (lines 642-660): excludes non-standard formats."""

    @pytest.fixture
    def env(self, integration_db):
        with ProductEnv(tenant_id="std-fmt-test", principal_id="std-fmt-principal") as env:
            tenant = TenantFactory(tenant_id="std-fmt-test", subdomain="std-fmt-test")
            PrincipalFactory(tenant=tenant, principal_id="std-fmt-principal")

            # Standard formats only (display_, video_ prefixes)
            p1 = ProductFactory(
                tenant=tenant,
                product_id="standard_product",
                name="Standard Formats",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"},
                ],
            )
            PricingOptionFactory(product=p1, pricing_model="cpm", rate=Decimal("10.0"), is_fixed=True)

            # Custom formats only (no standard prefix)
            p2 = ProductFactory(
                tenant=tenant,
                product_id="custom_only_product",
                name="Custom Only",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "custom_takeover"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "sponsored_listing"},
                ],
            )
            PricingOptionFactory(product=p2, pricing_model="cpm", rate=Decimal("25.0"), is_fixed=True)

            # Mix of standard and custom
            p3 = ProductFactory(
                tenant=tenant,
                product_id="mixed_product",
                name="Mixed Formats",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "custom_interstitial"},
                ],
            )
            PricingOptionFactory(product=p3, pricing_model="cpm", rate=Decimal("18.0"), is_fixed=True)

            # Audio standard format
            p4 = ProductFactory(
                tenant=tenant,
                product_id="audio_standard",
                name="Audio Standard",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "audio_30s"},
                ],
            )
            PricingOptionFactory(product=p4, pricing_model="cpm", rate=Decimal("20.0"), is_fixed=True)

            yield env

    @pytest.mark.asyncio
    async def test_standard_formats_only_true_excludes_custom(self, env):
        """standard_formats_only=true excludes products with only custom formats."""
        result = await env.call_impl(brief="", filters={"standard_formats_only": True})

        product_ids = {p.product_id for p in result.products}
        # Products with ALL standard formats pass
        assert "standard_product" in product_ids
        assert "audio_standard" in product_ids
        # Mixed has custom_interstitial (non-standard) so it's excluded
        assert "mixed_product" not in product_ids
        # Custom-only is excluded
        assert "custom_only_product" not in product_ids

    @pytest.mark.asyncio
    async def test_standard_formats_only_false_includes_all(self, env):
        """standard_formats_only=false returns all products (no filter effect)."""
        result = await env.call_impl(brief="", filters={"standard_formats_only": False})

        product_ids = {p.product_id for p in result.products}
        assert "standard_product" in product_ids
        assert "custom_only_product" in product_ids
        assert "mixed_product" in product_ids
        assert "audio_standard" in product_ids

    @pytest.mark.asyncio
    async def test_no_standard_formats_filter_returns_all(self, env):
        """Without standard_formats_only, all products are returned."""
        result = await env.call_impl(brief="")

        assert len(result.products) == 4
