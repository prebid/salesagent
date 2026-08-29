"""Tests for MCP tool schema exposure.

Verifies that MCP tools use proper AdCP library types in their signatures,
which ensures tools/list exposes full JSON schemas with $defs for nested types.
"""

import inspect

from adcp import BuyingMode
from fastmcp.tools.tool import Tool


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
        adcp 4.3 (commit 3c604130): targeting_overlay and creatives moved to PackageRequest.
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

        # adcp 4.3: targeting_overlay and creatives are package-level, not request-level
        assert "targeting_overlay" not in params, (
            "targeting_overlay was moved to PackageRequest in adcp 4.3 (commit 3c604130); "
            "use packages[].targeting_overlay instead"
        )
        assert "creatives" not in params, (
            "creatives was moved to PackageRequest in adcp 4.3 (commit 3c604130); use packages[].creatives instead"
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
        """list_creative_formats should use FormatId, etc."""
        from src.core.tools.creative_formats import list_creative_formats

        sig = inspect.signature(list_creative_formats)
        params = sig.parameters

        # type parameter removed in adcp 3.12

        # Check format_ids uses FormatId type (alias for FormatReferenceStructuredObject in adcp 4.3)
        annotation_str = str(params["format_ids"].annotation)
        assert "FormatId" in annotation_str or "FormatReference" in annotation_str, (
            f"format_ids should use FormatId type, got {annotation_str}"
        )

        # Check asset_types uses AssetContentType type (if still present)
        if "asset_types" in params:
            assert "AssetContentType" in str(params["asset_types"].annotation) or "str" in str(
                params["asset_types"].annotation
            ), f"asset_types should use AssetContentType or str type, got {params['asset_types'].annotation}"

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


class TestMCPProductsSchemaDeclaration:
    """The MCP get_products tool must declare buying_mode, grounded on adcp.BuyingMode.

    buying_mode is the sole required entry of get-products-request@3.1.1, so a
    spec-valid client sends it on every transport. FastMCP advertises
    additionalProperties: false over the get_products params, so an *undeclared*
    buying_mode 400s (VALIDATION_ERROR, "Unexpected keyword argument") in dev/CI —
    the same rejection REST's extra="forbid" produces. This pins the MCP-side
    declaration in ``src.core.tools.products.get_products`` so that removing
    buying_mode from the signature reddens here instead of silently shipping a tool
    that rejects the required field.

    It also pins the enum to adcp.BuyingMode: the REST ``GetProductsBody.buying_mode``
    field and this MCP declaration share that one SDK enum rather than two
    hand-transcribed Literals, so an adcp bump that adds a mode cannot leave one
    boundary stale. The live 4-transport UC-001 acceptance that exercises the wire is
    deferred to #1730; this unit test pins the declaration.
    """

    @staticmethod
    def _buying_mode_enum(node: dict, root_schema: dict) -> set[str]:
        """Resolve buying_mode's enum whether FastMCP inlines it or emits a $ref."""
        defs = root_schema.get("$defs", {})
        for branch in node.get("anyOf", [node]):
            if "$ref" in branch:
                branch = defs.get(branch["$ref"].split("/")[-1], {})
            if "enum" in branch:
                return set(branch["enum"])
        return set()

    def test_mcp_tool_declares_buying_mode_enum(self):
        """MCP get_products declares buying_mode as the adcp.BuyingMode enum.

        Deletion oracle: drop buying_mode from the get_products signature and the
        presence assertion reddens; retype it to a hand-written Literal that drifts
        from adcp.BuyingMode and the enum-equality assertion reddens.
        """
        from src.core.tools.products import get_products

        schema = Tool.from_function(get_products).parameters
        props = schema["properties"]

        assert "buying_mode" in props, (
            "MCP get_products must declare buying_mode; FastMCP's additionalProperties: false "
            "otherwise 400s the sole required field of get-products-request@3.1.1"
        )
        assert self._buying_mode_enum(props["buying_mode"], schema) == {m.value for m in BuyingMode}, (
            "MCP buying_mode enum drifted from adcp.BuyingMode; both REST and MCP must ground on "
            "the SDK enum so an adcp bump updates them together"
        )
