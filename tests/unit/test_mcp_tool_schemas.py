"""Tests for MCP tool schema exposure.

Verifies that MCP tools use proper AdCP library types in their signatures,
which ensures tools/list exposes full JSON schemas with $defs for nested types.
"""

import inspect


class TestMCPToolTypedSchemas:
    """Verify MCP tools expose typed schemas instead of untyped dicts."""

    def test_get_products_uses_typed_parameters(self):
        """get_products should use BrandReference (adcp 3.6.0), ProductFilters, ContextObject types."""
        from src.core.tools.products import get_products

        sig = inspect.signature(get_products)
        params = sig.parameters

        # adcp 3.6.0: brand_manifest replaced by brand (BrandReference)
        assert "brand" in params, "get_products should have 'brand' parameter (adcp 3.6.0)"
        assert "brand_manifest" not in params, "brand_manifest was removed in adcp 3.6.0"
        assert "BrandReference" in str(params["brand"].annotation), (
            f"brand should use BrandReference type, got {params['brand'].annotation}"
        )

        # Check filters uses ProductFilters type
        assert "ProductFilters" in str(params["filters"].annotation), (
            f"filters should use ProductFilters type, got {params['filters'].annotation}"
        )

        # Check context uses ContextObject type
        assert "ContextObject" in str(params["context"].annotation), (
            f"context should use ContextObject type, got {params['context'].annotation}"
        )

    def test_sync_creatives_uses_typed_parameters(self):
        """sync_creatives should use CreativeAsset, ValidationMode, etc."""
        from src.core.tools.creatives import sync_creatives

        sig = inspect.signature(sync_creatives)
        params = sig.parameters

        # Check creatives uses CreativeAsset type
        assert "CreativeAsset" in str(params["creatives"].annotation), (
            f"creatives should use CreativeAsset type, got {params['creatives'].annotation}"
        )

        # Check validation_mode uses ValidationMode type
        assert "ValidationMode" in str(params["validation_mode"].annotation), (
            f"validation_mode should use ValidationMode type, got {params['validation_mode'].annotation}"
        )

        # Check context uses ContextObject type
        assert "ContextObject" in str(params["context"].annotation), (
            f"context should use ContextObject type, got {params['context'].annotation}"
        )

    def test_list_creatives_uses_typed_parameters(self):
        """list_creatives should use CreativeFilters, Sort, Pagination types."""
        from src.core.tools.creatives import list_creatives

        sig = inspect.signature(list_creatives)
        params = sig.parameters

        # Check filters uses CreativeFilters type
        assert "CreativeFilters" in str(params["filters"].annotation), (
            f"filters should use CreativeFilters type, got {params['filters'].annotation}"
        )

        # Check sort uses Sort type
        assert "Sort" in str(params["sort"].annotation), f"sort should use Sort type, got {params['sort'].annotation}"

        # Check pagination uses Pagination type
        assert "Pagination" in str(params["pagination"].annotation), (
            f"pagination should use Pagination type, got {params['pagination'].annotation}"
        )

    def test_create_media_buy_uses_typed_parameters(self):
        """create_media_buy should use BrandReference (brand), PackageRequest, etc.

        adcp 3.6.0: brand_manifest renamed to brand (BrandReference with domain field).
        """
        from src.core.tools.media_buy_create import create_media_buy

        sig = inspect.signature(create_media_buy)
        params = sig.parameters

        # adcp 3.6.0: brand_manifest → brand (BrandReference)
        assert "brand" in params, (
            f"create_media_buy should have 'brand' parameter (adcp 3.6.0). Got parameters: {list(params.keys())}"
        )
        # brand_manifest is no longer in the signature
        assert "brand_manifest" not in params, "brand_manifest was removed in adcp 3.6.0, use 'brand' instead"

        # Check packages uses PackageRequest type
        assert "PackageRequest" in str(params["packages"].annotation), (
            f"packages should use PackageRequest type, got {params['packages'].annotation}"
        )

        # Check targeting_overlay uses TargetingOverlay type
        assert "TargetingOverlay" in str(params["targeting_overlay"].annotation), (
            f"targeting_overlay should use TargetingOverlay type, got {params['targeting_overlay'].annotation}"
        )

    def test_update_media_buy_uses_typed_parameters(self):
        """update_media_buy should use TargetingOverlay, PackageUpdate types.

        V3 Migration: Packages renamed to PackageUpdate in adcp library.
        """
        from src.core.tools.media_buy_update import update_media_buy

        sig = inspect.signature(update_media_buy)
        params = sig.parameters

        # Check targeting_overlay uses TargetingOverlay type
        assert "TargetingOverlay" in str(params["targeting_overlay"].annotation), (
            f"targeting_overlay should use TargetingOverlay type, got {params['targeting_overlay'].annotation}"
        )

        # Check packages uses PackageUpdate type (V3: was Packages)
        assert "PackageUpdate" in str(params["packages"].annotation), (
            f"packages should use PackageUpdate type (V3), got {params['packages'].annotation}"
        )

    def test_list_creative_formats_uses_typed_parameters(self):
        """list_creative_formats should use FormatCategory, FormatId, etc."""
        from src.core.tools.creative_formats import list_creative_formats

        sig = inspect.signature(list_creative_formats)
        params = sig.parameters

        # Check type uses FormatCategory enum
        assert "FormatCategory" in str(params["type"].annotation), (
            f"type should use FormatCategory type, got {params['type'].annotation}"
        )

        # Check format_ids uses FormatId type
        assert "FormatId" in str(params["format_ids"].annotation), (
            f"format_ids should use FormatId type, got {params['format_ids'].annotation}"
        )

        # Check asset_types uses AssetContentType type
        assert "AssetContentType" in str(params["asset_types"].annotation), (
            f"asset_types should use AssetContentType type, got {params['asset_types'].annotation}"
        )

    def test_get_media_buy_delivery_uses_typed_parameters(self):
        """get_media_buy_delivery should use ContextObject type."""
        from src.core.tools.media_buy_delivery import get_media_buy_delivery

        sig = inspect.signature(get_media_buy_delivery)
        params = sig.parameters

        # Check context uses ContextObject type
        assert "ContextObject" in str(params["context"].annotation), (
            f"context should use ContextObject type, got {params['context'].annotation}"
        )

    def test_update_performance_index_uses_typed_parameters(self):
        """update_performance_index should use ContextObject type."""
        from src.core.tools.performance import update_performance_index

        sig = inspect.signature(update_performance_index)
        params = sig.parameters

        # Check context uses ContextObject type
        assert "ContextObject" in str(params["context"].annotation), (
            f"context should use ContextObject type, got {params['context'].annotation}"
        )

    def test_list_authorized_properties_uses_typed_parameters(self):
        """list_authorized_properties should use ContextObject type."""
        from src.core.tools.properties import list_authorized_properties

        sig = inspect.signature(list_authorized_properties)
        params = sig.parameters

        # Check context uses ContextObject type
        assert "ContextObject" in str(params["context"].annotation), (
            f"context should use ContextObject type, got {params['context'].annotation}"
        )


class TestMCPToolSchemaNotUntyped:
    """Ensure MCP tools don't use untyped dict parameters for complex types."""

    def test_brand_manifest_removed_from_get_products(self):
        """brand_manifest parameter must not exist in get_products (removed in adcp 3.6.0)."""
        from src.core.tools.products import get_products

        sig = inspect.signature(get_products)
        assert "brand_manifest" not in sig.parameters, (
            "brand_manifest was removed in adcp 3.6.0 — get_products must use 'brand' instead"
        )

    def test_no_untyped_dict_for_filters(self):
        """filters should NOT be dict."""
        from src.core.tools.products import get_products

        sig = inspect.signature(get_products)
        annotation = str(sig.parameters["filters"].annotation)

        # Should not be plain dict
        assert annotation != "dict | None", "filters should use typed model, not plain dict"

    def test_no_untyped_dict_for_packages(self):
        """packages should NOT be list[dict]."""
        from src.core.tools.media_buy_create import create_media_buy

        sig = inspect.signature(create_media_buy)
        annotation = str(sig.parameters["packages"].annotation)

        # Should not contain plain dict
        assert "dict[str, Any]" not in annotation, "packages should use typed model, not list[dict[str, Any]]"
