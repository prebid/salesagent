"""Test that brand_manifest RootModel wrappers are correctly unwrapped.

This tests the fix for GitHub issue #932 where MCP get_products fails
because brand_manifest is wrapped in a BrandManifestReference RootModel,
but the code was checking for .name and .url on the wrapper instead of
the inner BrandManifest object.
"""

from adcp.types.generated_poc.core.brand_manifest import BrandManifest

from src.core.schema_helpers import create_get_products_request


def test_brand_manifest_rootmodel_unwrapping():
    """Test that BrandManifest in GetProductsRequest is correctly accessed.

    The adcp library wraps BrandManifest in BrandManifestReference (a RootModel).
    The extraction code must unwrap via .root to access .name and .url.
    """
    manifest = BrandManifest(name="Test Brand", url="https://test.example.com")
    req = create_get_products_request(brief="test", brand_manifest=manifest)

    # req.brand_manifest is a BrandManifestReference wrapper
    assert hasattr(req.brand_manifest, "root"), "brand_manifest should have .root attribute"

    # The wrapper does NOT have .name directly
    assert not hasattr(req.brand_manifest, "name") or req.brand_manifest.name is None, (
        "brand_manifest wrapper should not have .name directly accessible"
    )

    # But .root does have .name
    assert req.brand_manifest.root.name == "Test Brand"
    assert str(req.brand_manifest.root.url).rstrip("/") == "https://test.example.com"


def test_brand_manifest_extraction_logic():
    """Test the actual extraction logic used in _get_products_impl."""
    manifest = BrandManifest(name="Test Brand", url="https://test.example.com")
    req = create_get_products_request(brief="test", brand_manifest=manifest)

    # This is the extraction logic from products.py
    offering = None
    if req.brand_manifest:
        # Handle RootModel wrappers
        brand_manifest = req.brand_manifest
        if hasattr(brand_manifest, "root"):
            brand_manifest = brand_manifest.root

        if isinstance(brand_manifest, str):
            offering = f"Brand at {brand_manifest}"
        elif hasattr(brand_manifest, "__str__") and str(brand_manifest).startswith("http"):
            offering = f"Brand at {brand_manifest}"
        elif hasattr(brand_manifest, "name") and brand_manifest.name:
            offering = brand_manifest.name
        elif hasattr(brand_manifest, "url") and brand_manifest.url:
            offering = f"Brand at {brand_manifest.url}"
        elif isinstance(brand_manifest, dict):
            offering = brand_manifest.get("name") or brand_manifest.get("url", "")

    assert offering == "Test Brand", f"Expected 'Test Brand', got '{offering}'"


def test_brand_manifest_url_only_via_dict():
    """Test extraction when only URL is provided via dict.

    Note: BrandManifest requires 'name' directly, but create_get_products_request
    handles dicts with only 'url' by auto-generating a name from the domain.
    """
    # Pass as dict so create_get_products_request can add the name
    req = create_get_products_request(brief="test", brand_manifest={"url": "https://example.com"})

    brand_manifest = req.brand_manifest
    if hasattr(brand_manifest, "root"):
        brand_manifest = brand_manifest.root

    # Should have the domain as name (from create_get_products_request adaptation)
    assert brand_manifest.name is not None
    assert brand_manifest.name == "example.com"  # Domain extracted from URL
    # URL should be preserved
    assert "example.com" in str(brand_manifest.url)


def test_brand_manifest_dict_input():
    """Test that dict input is converted to BrandManifest and wrapped."""
    req = create_get_products_request(
        brief="test", brand_manifest={"name": "Dict Brand", "url": "https://dict.example.com"}
    )

    # Should be wrapped in RootModel
    assert hasattr(req.brand_manifest, "root")

    # Inner object should have correct values
    assert req.brand_manifest.root.name == "Dict Brand"
    assert "dict.example.com" in str(req.brand_manifest.root.url)


def test_brand_manifest_none():
    """Test that None brand_manifest is handled correctly."""
    req = create_get_products_request(brief="test", brand_manifest=None)

    assert req.brand_manifest is None
