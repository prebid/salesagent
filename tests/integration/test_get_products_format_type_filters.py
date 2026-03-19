"""Integration tests for format_types, format_ids, and standard_formats_only filters.

Tests lines 585-660 of src/core/tools/products.py — the three untested
UC-001 filter code paths:
  1. format_types: filters products by format category (video, display, audio)
  2. format_ids: filters products by specific format IDs
  3. standard_formats_only: excludes products with only non-standard formats

These tests exercise the REAL filtering logic with a real database.
The format_types filter calls get_format_by_id() which needs the creative
agent registry, so we patch that single function to return proper Format
objects with the correct .type field.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest

from src.core.schemas import Format, FormatId, FormatTypeEnum
from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import ProductEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Map format ID prefixes to FormatCategory enum values for the mock
_FORMAT_TYPE_MAP = {
    "display_": FormatTypeEnum.display,
    "video_": FormatTypeEnum.video,
    "audio_": FormatTypeEnum.audio,
    "native_": FormatTypeEnum.native,
}


def _mock_get_format_by_id(format_id: str, tenant_id: str | None = None) -> Format | None:
    """Mock that returns a Format with the correct .type based on format_id prefix."""
    for prefix, fmt_type in _FORMAT_TYPE_MAP.items():
        if format_id.startswith(prefix):
            return Format(
                format_id=FormatId(
                    agent_url="https://creative.adcontextprotocol.org",
                    id=format_id,
                ),
                name=format_id.replace("_", " ").title(),
                type=fmt_type,
            )
    return None


@pytest.mark.requires_db
class TestFormatTypesFilter:
    """Test format_types filter (lines 591-609): filters by format category."""

    @pytest.fixture
    def env(self, integration_db):
        with ProductEnv(tenant_id="fmt-type-test", principal_id="fmt-type-principal") as env:
            tenant = TenantFactory(tenant_id="fmt-type-test", subdomain="fmt-type-test")
            PrincipalFactory(tenant=tenant, principal_id="fmt-type-principal")

            p1 = ProductFactory(
                tenant=tenant,
                product_id="display_only",
                name="Display Only",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
                ],
            )
            PricingOptionFactory(product=p1, pricing_model="cpm", rate=Decimal("10.0"), is_fixed=True)

            p2 = ProductFactory(
                tenant=tenant,
                product_id="video_only",
                name="Video Only",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_30s"},
                ],
            )
            PricingOptionFactory(product=p2, pricing_model="cpm", rate=Decimal("15.0"), is_fixed=True)

            p3 = ProductFactory(
                tenant=tenant,
                product_id="audio_only",
                name="Audio Only",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "audio_30s"},
                ],
            )
            PricingOptionFactory(product=p3, pricing_model="cpm", rate=Decimal("20.0"), is_fixed=True)

            p4 = ProductFactory(
                tenant=tenant,
                product_id="display_video_mix",
                name="Display + Video Mix",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"},
                ],
            )
            PricingOptionFactory(product=p4, pricing_model="cpm", rate=Decimal("12.0"), is_fixed=True)

            yield env

    @pytest.mark.asyncio
    @patch("src.core.schemas.get_format_by_id", side_effect=_mock_get_format_by_id)
    async def test_filter_by_format_type_video(self, _mock, env):
        """format_types=["video"] returns only products with video formats."""
        result = await env.call_impl(brief="", filters={"format_types": ["video"]})

        product_ids = {p.product_id for p in result.products}
        assert "video_only" in product_ids
        assert "display_video_mix" in product_ids
        assert "display_only" not in product_ids
        assert "audio_only" not in product_ids

    @pytest.mark.asyncio
    @patch("src.core.schemas.get_format_by_id", side_effect=_mock_get_format_by_id)
    async def test_filter_by_format_type_display(self, _mock, env):
        """format_types=["display"] returns only products with display formats."""
        result = await env.call_impl(brief="", filters={"format_types": ["display"]})

        product_ids = {p.product_id for p in result.products}
        assert "display_only" in product_ids
        assert "display_video_mix" in product_ids
        assert "video_only" not in product_ids
        assert "audio_only" not in product_ids

    @pytest.mark.asyncio
    @patch("src.core.schemas.get_format_by_id", side_effect=_mock_get_format_by_id)
    async def test_filter_by_format_type_audio(self, _mock, env):
        """format_types=["audio"] returns only the audio product."""
        result = await env.call_impl(brief="", filters={"format_types": ["audio"]})

        product_ids = {p.product_id for p in result.products}
        assert product_ids == {"audio_only"}

    @pytest.mark.asyncio
    @patch("src.core.schemas.get_format_by_id", side_effect=_mock_get_format_by_id)
    async def test_filter_by_multiple_format_types(self, _mock, env):
        """format_types=["video", "audio"] returns products with either type."""
        result = await env.call_impl(brief="", filters={"format_types": ["video", "audio"]})

        product_ids = {p.product_id for p in result.products}
        assert "video_only" in product_ids
        assert "audio_only" in product_ids
        assert "display_video_mix" in product_ids
        assert "display_only" not in product_ids

    @pytest.mark.asyncio
    @patch("src.core.schemas.get_format_by_id", side_effect=_mock_get_format_by_id)
    async def test_filter_by_format_type_no_matches(self, _mock, env):
        """format_types=["native"] returns empty when no products match."""
        result = await env.call_impl(brief="", filters={"format_types": ["native"]})

        assert len(result.products) == 0


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


@pytest.mark.requires_db
class TestCombinedFormatFilters:
    """Test combining format_types, format_ids, and standard_formats_only (AND logic)."""

    @pytest.fixture
    def env(self, integration_db):
        with ProductEnv(tenant_id="combo-fmt-test", principal_id="combo-fmt-principal") as env:
            tenant = TenantFactory(tenant_id="combo-fmt-test", subdomain="combo-fmt-test")
            PrincipalFactory(tenant=tenant, principal_id="combo-fmt-principal")

            # Standard display product
            p1 = ProductFactory(
                tenant=tenant,
                product_id="std_display",
                name="Standard Display",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                ],
            )
            PricingOptionFactory(product=p1, pricing_model="cpm", rate=Decimal("10.0"), is_fixed=True)

            # Standard video product
            p2 = ProductFactory(
                tenant=tenant,
                product_id="std_video",
                name="Standard Video",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"},
                ],
            )
            PricingOptionFactory(product=p2, pricing_model="cpm", rate=Decimal("15.0"), is_fixed=True)

            # Custom-only product
            p3 = ProductFactory(
                tenant=tenant,
                product_id="custom_product",
                name="Custom Product",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "custom_takeover"},
                ],
            )
            PricingOptionFactory(product=p3, pricing_model="cpm", rate=Decimal("30.0"), is_fixed=True)

            yield env

    @pytest.mark.asyncio
    @patch("src.core.schemas.get_format_by_id", side_effect=_mock_get_format_by_id)
    async def test_format_types_and_standard_formats_combined(self, _mock, env):
        """format_types + standard_formats_only filters are ANDed together."""
        result = await env.call_impl(
            brief="",
            filters={"format_types": ["display"], "standard_formats_only": True},
        )

        product_ids = {p.product_id for p in result.products}
        assert "std_display" in product_ids
        assert "std_video" not in product_ids
        assert "custom_product" not in product_ids

    @pytest.mark.asyncio
    @patch("src.core.schemas.get_format_by_id", side_effect=_mock_get_format_by_id)
    async def test_format_types_and_format_ids_combined(self, _mock, env):
        """format_types + format_ids filters are ANDed: product must pass both."""
        result = await env.call_impl(
            brief="",
            filters={
                "format_types": ["display"],
                "format_ids": [
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                ],
            },
        )

        product_ids = {p.product_id for p in result.products}
        assert "std_display" in product_ids
        assert "std_video" not in product_ids
        assert "custom_product" not in product_ids

    @pytest.mark.asyncio
    @patch("src.core.schemas.get_format_by_id", side_effect=_mock_get_format_by_id)
    async def test_all_three_filters_combined(self, _mock, env):
        """format_types + format_ids + standard_formats_only all applied as AND."""
        result = await env.call_impl(
            brief="",
            filters={
                "format_types": ["display"],
                "format_ids": [
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                ],
                "standard_formats_only": True,
            },
        )

        product_ids = {p.product_id for p in result.products}
        assert product_ids == {"std_display"}
