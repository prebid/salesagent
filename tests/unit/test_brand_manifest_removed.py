"""Tests that naming utilities correctly use brand (BrandReference) instead of brand_manifest.

After adcp 3.6.0, CreateMediaBuyRequest no longer has brand_manifest.
It was replaced by brand (BrandReference with .domain and optional .brand_id).

These tests demonstrate that the naming code in src/core/utils/naming.py,
src/adapters/gam/utils/naming.py, and src/adapters/xandr.py fail to extract
brand information from real adcp 3.6.0 request objects because they still
look for the removed brand_manifest attribute.
"""

from datetime import UTC, datetime

from adcp.types import CreateMediaBuyRequest as LibraryCreateMediaBuyRequest
from adcp.types import PackageRequest as LibraryPackageRequest

from src.core.utils.naming import (
    _extract_brand_name,
    build_order_name_context,
)

gam_build_order_name_context = build_order_name_context


def _make_request(domain: str = "nike.com", brand_id: str | None = None):
    """Create a real adcp 3.6.0 CreateMediaBuyRequest with brand field."""
    brand = {"domain": domain}
    if brand_id:
        brand["brand_id"] = brand_id
    return LibraryCreateMediaBuyRequest(
        buyer_ref="TEST-001",
        brand=brand,
        packages=[
            LibraryPackageRequest(
                product_id="prod_1",
                budget=5000,
                pricing_option_id="cpm-fixed",
                buyer_ref="pkg-1",
            )
        ],
        start_time="2025-06-01T00:00:00Z",
        end_time="2025-06-30T23:59:59Z",
    )


class TestExtractBrandNameFromAdcp360:
    """_extract_brand_name must read brand.domain from adcp 3.6.0 requests."""

    def test_extract_brand_name_from_real_request(self):
        """Brand name should be extracted from a real adcp 3.6.0 request.

        BUG: _extract_brand_name looks for request.brand_manifest which no
        longer exists in adcp 3.6.0. It should look at request.brand.domain.
        """
        request = _make_request(domain="nike.com")

        # Verify the request has brand but NOT brand_manifest
        assert hasattr(request, "brand"), "adcp 3.6.0 request must have brand field"
        assert not hasattr(request, "brand_manifest"), "adcp 3.6.0 request must NOT have brand_manifest"

        # _extract_brand_name should return the domain as brand name
        brand_name = _extract_brand_name(request)
        assert brand_name is not None, "Brand name must not be None when request has brand.domain='nike.com'"
        assert brand_name == "nike.com"


class TestBuildOrderNameContextFromAdcp360:
    """build_order_name_context must populate brand_name from brand.domain."""

    def test_brand_name_populated_in_context(self):
        """Order name context must have the brand name from brand.domain.

        BUG: build_order_name_context in src/core/utils/naming.py reads
        request.brand_manifest which no longer exists. brand_name ends up
        as 'N/A' even though brand.domain='nike.com' is present.
        """
        request = _make_request(domain="nike.com")
        start_time = datetime(2025, 6, 1, tzinfo=UTC)
        end_time = datetime(2025, 6, 30, tzinfo=UTC)

        context = build_order_name_context(
            request=request,
            packages=request.packages,
            start_time=start_time,
            end_time=end_time,
        )

        assert context["brand_name"] != "N/A", "brand_name should not be 'N/A' when request.brand.domain is set"
        assert context["brand_name"] == "nike.com"

    def test_campaign_name_uses_brand_domain(self):
        """Campaign name should incorporate brand domain, not generic fallback.

        BUG: Without brand extraction, campaign_name falls back to
        'Campaign TEST-001' instead of using the brand domain.
        """
        request = _make_request(domain="adidas.com")
        start_time = datetime(2025, 6, 1, tzinfo=UTC)
        end_time = datetime(2025, 6, 30, tzinfo=UTC)

        context = build_order_name_context(
            request=request,
            packages=request.packages,
            start_time=start_time,
            end_time=end_time,
        )

        assert context["campaign_name"] == "adidas.com", "campaign_name should be 'adidas.com', not a generic fallback"


class TestGamBuildOrderNameContextFromAdcp360:
    """GAM naming must also extract brand from brand.domain."""

    def test_gam_brand_name_populated(self):
        """GAM order name context must have brand name from brand.domain.

        BUG: GAM build_order_name_context reads request.brand_manifest
        which no longer exists in adcp 3.6.0. brand_name is None and
        campaign_name falls back to 'Campaign TEST-001'.
        """
        request = _make_request(domain="nike.com")
        start_time = datetime(2025, 6, 1, tzinfo=UTC)
        end_time = datetime(2025, 6, 30, tzinfo=UTC)

        context = build_order_name_context(
            request=request,
            packages=request.packages,
            start_time=start_time,
            end_time=end_time,
        )

        assert context["brand_name"] != "N/A", "GAM brand_name should not be 'N/A' when request.brand.domain is set"
        assert context["brand_name"] == "nike.com"
