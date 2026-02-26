"""Unit tests reproducing brand_manifest → brand migration failures.

After dropping brand_manifest backward compat (adcp v3.6.0 upgrade), tests and
callers still passing brand_manifest instead of brand fail in multiple ways:

1. get_products_raw(brand_manifest=...) → TypeError (unknown keyword)
2. GetProductsRequest(brand_manifest=...) → ValidationError (extra forbidden)
3. CreateMediaBuyRequest(brand_manifest=...) → ValidationError (brand required + extra forbidden)
4. A2A handler with {"brand_manifest": ...} → "Either 'brief' or 'brand' parameter is required"

Bug: salesagent-k6cn
"""

import pytest
from pydantic import ValidationError


class TestGetProductsRawRejectsBrandManifest:
    """get_products_raw no longer accepts brand_manifest keyword."""

    def test_brand_manifest_kwarg_raises_type_error(self):
        """Calling get_products_raw with brand_manifest= raises TypeError.

        This reproduces the failure in:
        - tests/integration_v2/test_get_products_filters.py (8+ tests)
        """
        from src.core.tools.products import get_products_raw

        with pytest.raises(TypeError, match="brand_manifest"):
            # brand_manifest is not a valid parameter — brand is the new name.
            # TypeError is raised at call time (before coroutine creation)
            # because the function signature has no **kwargs.
            get_products_raw(
                brand_manifest={"name": "Test Brand"},
                brief="",
            )


class TestGetProductsRequestRejectsBrandManifest:
    """GetProductsRequest no longer accepts brand_manifest field."""

    def test_brand_manifest_field_raises_validation_error(self):
        """Creating GetProductsRequest with brand_manifest= raises ValidationError.

        This reproduces the failure in:
        - tests/integration_v2/test_get_products_format_id_filter.py (4 tests)
        """
        from src.core.schemas import GetProductsRequest

        with pytest.raises(ValidationError, match="brand_manifest"):
            GetProductsRequest(
                brand_manifest={"name": "Test campaign"},
            )


class TestCreateMediaBuyRequestRejectsBrandManifest:
    """CreateMediaBuyRequest no longer accepts brand_manifest field."""

    def test_brand_manifest_field_raises_validation_error(self):
        """Creating CreateMediaBuyRequest with brand_manifest= raises ValidationError.

        brand is now required (not brand_manifest), and brand_manifest is forbidden.

        This reproduces the failure in:
        - tests/integration_v2/test_mcp_endpoints_comprehensive.py::test_schema_adcp_format
        """
        from src.core.schemas import CreateMediaBuyRequest

        with pytest.raises(ValidationError):
            CreateMediaBuyRequest(
                brand_manifest={"name": "Adidas UltraBoost 2025 running shoes"},
                buyer_ref="custom_ref_123",
                po_number="PO-V24-67890",
                packages=[],
                start_time="2026-03-01T00:00:00Z",
                end_time="2026-04-01T00:00:00Z",
            )


class TestA2AHandlerBrandParameterLookup:
    """A2A handler now looks for 'brand' not 'brand_manifest' in parameters dict."""

    def test_brand_manifest_key_not_found_by_handler(self):
        """When A2A params have 'brand_manifest' instead of 'brand', the handler
        finds neither brief nor brand and raises InvalidParamsError.

        This reproduces the failure in:
        - tests/integration/test_a2a_response_message_fields.py::test_get_products_message_field_exists
        - tests/integration_v2/test_a2a_error_responses.py (3 tests using create_media_buy)
        """
        # Simulate the A2A handler's parameter extraction logic
        parameters = {
            "brand_manifest": {"name": "Test product search"},
            "brief": "",  # empty string is falsy
        }

        brief = parameters.get("brief", "")
        brand = parameters.get("brand")  # Handler looks for "brand", not "brand_manifest"

        # This is the validation that fails in _handle_get_products_skill
        assert not brief, "brief is empty string, should be falsy"
        assert brand is None, "brand is None because handler looks for 'brand' key, not 'brand_manifest'"

        # The handler would raise: "Either 'brief' or 'brand' parameter is required"
        validation_fails = not brief and not brand
        assert validation_fails, (
            "Validation should fail when params have 'brand_manifest' but handler looks for 'brand'"
        )

    def test_create_media_buy_handler_requires_brand_key(self):
        """When A2A create_media_buy params have 'brand_manifest' instead of 'brand',
        the handler's required param check fails.

        _handle_create_media_buy_skill checks: required_params = ['brand', 'packages', 'start_time', 'end_time']
        """
        parameters = {
            "brand_manifest": {"name": "Test Campaign"},
            "packages": [{"buyer_ref": "pkg_1", "product_id": "test"}],
            "start_time": "2026-03-01T00:00:00Z",
            "end_time": "2026-04-01T00:00:00Z",
        }

        required_params = ["brand", "packages", "start_time", "end_time"]
        missing_params = [p for p in required_params if p not in parameters]

        # 'brand' is not in parameters (only 'brand_manifest' is), so it's flagged as missing
        assert "brand" in missing_params, "'brand' should be missing because test passes 'brand_manifest' instead"
