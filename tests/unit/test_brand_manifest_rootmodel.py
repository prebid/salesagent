"""Test that brand field is correctly handled in GetProductsRequest.

adcp 3.6.0: brand_manifest replaced by brand (BrandReference with domain field).
This tests the create_get_products_request helper and its backward compat handling.
"""

from adcp.types import BrandManifest

from src.core.schema_helpers import create_get_products_request


def test_brand_manifest_rootmodel_unwrapping():
    """Test that legacy brand_manifest input is converted to brand field.

    adcp 3.6.0: GetProductsRequest uses brand (BrandReference) not brand_manifest.
    The helper function converts legacy brand_manifest input to brand.domain.
    """
    manifest = BrandManifest(name="Test Brand", url="https://test.example.com")
    req = create_get_products_request(brief="test", brand_manifest=manifest)

    # adcp 3.6.0: brand_manifest is converted to brand.domain (BrandReference)
    assert req.brand is not None, "brand should be set from legacy brand_manifest"

    # The domain should be extracted from the URL
    domain = req.brand.domain if hasattr(req.brand, "domain") else req.brand.get("domain")
    assert domain == "test.example.com"


def test_brand_manifest_extraction_logic():
    """Test the extraction logic from legacy brand_manifest to brand.domain."""
    manifest = BrandManifest(name="Test Brand", url="https://test.example.com")
    req = create_get_products_request(brief="test", brand_manifest=manifest)

    # brand.domain should be the domain extracted from the manifest URL
    assert req.brand is not None
    domain = req.brand.domain if hasattr(req.brand, "domain") else req.brand.get("domain")
    assert "test.example.com" in domain


def test_brand_manifest_url_only_via_dict():
    """Test extraction when only URL is provided via dict.

    adcp 3.6.0: domain is extracted from URL and stored in brand.domain.
    """
    req = create_get_products_request(brief="test", brand_manifest={"url": "https://example.com"})

    # Should extract domain from URL
    assert req.brand is not None
    domain = req.brand.domain if hasattr(req.brand, "domain") else req.brand.get("domain")
    assert domain == "example.com"


def test_brand_manifest_dict_input():
    """Test that dict brand_manifest input is converted to brand.domain."""
    req = create_get_products_request(
        brief="test", brand_manifest={"name": "Dict Brand", "url": "https://dict.example.com"}
    )

    # Should extract domain from URL
    assert req.brand is not None
    domain = req.brand.domain if hasattr(req.brand, "domain") else req.brand.get("domain")
    assert "dict.example.com" in domain


def test_brand_manifest_none():
    """Test that None brand_manifest is handled correctly."""
    req = create_get_products_request(brief="test", brand_manifest=None)

    assert req.brand is None


def test_brand_takes_precedence_over_brand_manifest():
    """Test that brand parameter takes precedence over brand_manifest."""
    req = create_get_products_request(
        brief="test",
        brand={"domain": "brand-param.com"},
        brand_manifest={"name": "Dict Brand", "url": "https://dict.example.com"},
    )

    # brand parameter should take precedence
    assert req.brand is not None
    domain = req.brand.domain if hasattr(req.brand, "domain") else req.brand.get("domain")
    assert domain == "brand-param.com"
