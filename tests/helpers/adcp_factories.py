"""Test factories for creating AdCP-compliant objects.

This module provides factory functions for creating objects from the adcp library
that comply with the AdCP spec, including all required fields. Use these in tests
instead of manually constructing objects to avoid validation errors.

All factories use sensible defaults for required fields and accept overrides for customization.
"""

from typing import Any

from adcp import (
    BrandManifest,
    CreativeAsset,
    Format,
    FormatId,
    Package,
    Product,
    Property,
)


def create_test_product(
    product_id: str = "test_product",
    name: str = "Test Product",
    description: str = "Test product description",
    format_ids: list[str | dict | FormatId] | None = None,
    publisher_properties: list[dict[str, Any]] | None = None,
    delivery_type: str = "guaranteed",
    pricing_options: list[dict[str, Any]] | None = None,
    delivery_measurement: dict[str, Any] | None = None,
    **kwargs,
) -> Product:
    """Create a test Product with all required fields.

    Args:
        product_id: Product identifier
        name: Product name
        description: Product description
        format_ids: List of format IDs (as strings, dicts, or FormatId objects). Defaults to ["display_300x250"]
        publisher_properties: List of property dicts. Defaults to minimal test property
        delivery_type: "guaranteed" or "non_guaranteed"
        pricing_options: List of pricing option dicts. Defaults to minimal CPM option
        delivery_measurement: Delivery measurement dict. Defaults to test provider
        **kwargs: Additional optional fields (measurement, creative_policy, etc.)

    Returns:
        AdCP-compliant Product object

    Example:
        # Minimal product
        product = create_test_product()

        # Custom product
        product = create_test_product(
            product_id="video_premium",
            format_ids=["video_1920x1080"],
            pricing_options=[{"pricing_model": "cpm", "currency": "USD"}]
        )
    """
    # Default format_ids if not provided
    if format_ids is None:
        format_ids = ["display_300x250"]

    # Convert format_ids to FormatId objects
    format_id_objects = []
    for fmt in format_ids:
        if isinstance(fmt, str):
            # String format ID - convert to FormatId object
            format_id_objects.append(create_test_format_id(fmt))
        elif isinstance(fmt, dict):
            # Dict with agent_url and id
            format_id_objects.append(FormatId(**fmt))
        else:
            # Already a FormatId object
            format_id_objects.append(fmt)

    # Default publisher_properties if not provided
    if publisher_properties is None:
        publisher_properties = [create_test_property_dict()]

    # Default delivery_measurement if not provided
    if delivery_measurement is None:
        delivery_measurement = {
            "provider": "test_provider",
            "notes": "Test measurement methodology",
        }

    # Default pricing_options if not provided
    if pricing_options is None:
        pricing_options = [{"pricing_model": "cpm", "currency": "USD"}]

    return Product(
        product_id=product_id,
        name=name,
        description=description,
        publisher_properties=publisher_properties,
        format_ids=format_id_objects,
        delivery_type=delivery_type,
        pricing_options=pricing_options,
        delivery_measurement=delivery_measurement,
        **kwargs,
    )


def create_minimal_product(**overrides) -> Product:
    """Create a product with absolute minimal required fields.

    Args:
        **overrides: Override any default values

    Returns:
        Product with minimal required fields
    """
    defaults = {
        "product_id": "minimal",
        "name": "Minimal",
        "description": "Minimal test product",
        "publisher_properties": [create_test_property_dict()],
        "format_ids": [create_test_format_id("display_300x250")],
        "delivery_type": "guaranteed",
        "pricing_options": [{"pricing_model": "cpm", "currency": "USD"}],
        "delivery_measurement": {"provider": "test", "notes": "Test"},
    }
    defaults.update(overrides)
    return Product(**defaults)


def create_product_with_empty_pricing(**overrides) -> Product:
    """Create a product with empty pricing_options (anonymous user case).

    Args:
        **overrides: Override any default values

    Returns:
        Product with empty pricing_options list
    """
    return create_test_product(pricing_options=[], **overrides)


def create_test_format_id(
    format_id: str = "display_300x250", agent_url: str = "https://creative.adcontextprotocol.org"
) -> FormatId:
    """Create a test FormatId object.

    Args:
        format_id: Format identifier (e.g., "display_300x250", "video_1920x1080")
        agent_url: Agent URL defining the format namespace

    Returns:
        AdCP-compliant FormatId object

    Example:
        format_id = create_test_format_id("video_1920x1080")
    """
    return FormatId(agent_url=agent_url, id=format_id)


def create_test_format(
    format_id: str | FormatId | None = None,
    name: str = "Test Format",
    type: str = "display",
    is_standard: bool = True,
    **kwargs,
) -> Format:
    """Create a test Format object.

    Args:
        format_id: FormatId object or string. Defaults to "display_300x250"
        name: Human-readable format name
        type: Format type ("display", "video", "audio", etc.)
        is_standard: Whether this is a standard format
        **kwargs: Additional optional fields (requirements, iab_specification, etc.)

    Returns:
        AdCP-compliant Format object

    Example:
        format = create_test_format("video_1920x1080", name="Full HD Video", type="video")
    """
    if format_id is None:
        format_id = create_test_format_id("display_300x250")
    elif isinstance(format_id, str):
        format_id = create_test_format_id(format_id)

    return Format(format_id=format_id, name=name, type=type, is_standard=is_standard, **kwargs)


def create_test_property_dict(
    publisher_domain: str = "test.example.com",
    property_id: str = "test_property_1",
    property_name: str = "Test Property",
    property_type: str = "website",
    **kwargs,
) -> dict[str, Any]:
    """Create a test property dict for use in publisher_properties.

    Note: Returns a dict, not a Property object, because adcp.Product
    expects publisher_properties as a list of dicts.

    Args:
        publisher_domain: Domain of the publisher
        property_id: Property identifier
        property_name: Human-readable property name
        property_type: Type of property ("website", "app", etc.)
        **kwargs: Additional optional fields

    Returns:
        Property dict suitable for Product.publisher_properties

    Example:
        prop = create_test_property_dict(publisher_domain="news.example.com")
    """
    return {
        "publisher_domain": publisher_domain,
        "property_id": property_id,
        "property_name": property_name,
        "property_type": property_type,
        **kwargs,
    }


def create_test_property(
    property_type: str = "website",
    name: str = "Test Property",
    identifiers: list[dict[str, str]] | None = None,
    publisher_domain: str = "test.example.com",
    **kwargs,
) -> Property:
    """Create a test Property object (for full Property validation).

    Args:
        property_type: Type of property ("website", "app", etc.)
        name: Human-readable property name
        identifiers: List of identifier dicts. Defaults to domain identifier
        publisher_domain: Domain of the publisher
        **kwargs: Additional optional fields (tags, etc.)

    Returns:
        AdCP-compliant Property object

    Example:
        prop = create_test_property(
            property_type="app",
            identifiers=[{"type": "bundle_id", "value": "com.example.app"}]
        )
    """
    if identifiers is None:
        identifiers = [{"type": "domain", "value": publisher_domain}]

    return Property(
        property_type=property_type, name=name, identifiers=identifiers, publisher_domain=publisher_domain, **kwargs
    )


def create_test_package(
    package_id: str = "test_package",
    status: str = "active",
    products: list[str] | None = None,
    **kwargs,
) -> Package:
    """Create a test Package object.

    Args:
        package_id: Package identifier
        status: Package status ("active", "paused", etc.)
        products: List of product IDs. Defaults to ["test_product"]
        **kwargs: Additional optional fields (impressions, creative_assignments, etc.)

    Returns:
        AdCP-compliant Package object

    Example:
        package = create_test_package(
            package_id="pkg_001",
            products=["prod_1", "prod_2"],
            impressions=10000
        )
    """
    if products is None:
        products = ["test_product"]

    return Package(package_id=package_id, status=status, products=products, **kwargs)


def create_test_creative_asset(
    creative_id: str = "test_creative",
    name: str = "Test Creative",
    format_id: str | FormatId = "display_300x250",
    assets: dict[str, Any] | None = None,
    **kwargs,
) -> CreativeAsset:
    """Create a test CreativeAsset object.

    Args:
        creative_id: Creative identifier
        name: Human-readable creative name
        format_id: FormatId object or string
        assets: Assets dict keyed by asset_role. Defaults to {"primary": {"url": "https://example.com/creative.jpg"}}
        **kwargs: Additional optional fields (inputs, tags, approved, etc.)

    Returns:
        AdCP-compliant CreativeAsset object

    Example:
        creative = create_test_creative_asset(
            creative_id="creative_001",
            format_id="video_1920x1080",
            assets={"primary": {"url": "https://cdn.example.com/video.mp4", "mime_type": "video/mp4"}}
        )
    """
    if isinstance(format_id, str):
        format_id = create_test_format_id(format_id)

    if assets is None:
        assets = {"primary": {"url": "https://example.com/creative.jpg"}}

    return CreativeAsset(creative_id=creative_id, name=name, format_id=format_id, assets=assets, **kwargs)


def create_test_brand_manifest(
    name: str = "Test Brand",
    promoted_offering: str | None = None,
    **kwargs,
) -> BrandManifest:
    """Create a test BrandManifest object.

    Args:
        name: Brand name
        promoted_offering: What is being promoted. Defaults to name
        **kwargs: Additional optional fields (tagline, category, etc.)

    Returns:
        AdCP-compliant BrandManifest object

    Example:
        brand = create_test_brand_manifest(
            name="Acme Corp",
            promoted_offering="Premium Widget Pro",
            tagline="Best widgets in the world"
        )
    """
    if promoted_offering is None:
        promoted_offering = name

    return BrandManifest(name=name, promoted_offering=promoted_offering, **kwargs)


def create_test_pricing_option(pricing_model: str = "cpm", currency: str = "USD", **kwargs) -> dict[str, Any]:
    """Create a test pricing option dict.

    Note: Returns a dict because PricingOption in adcp is a discriminated union
    with complex internal structure. Tests should use dicts.

    Args:
        pricing_model: Pricing model ("cpm", "cpc", "vcpm", etc.)
        currency: Currency code (3-letter ISO)
        **kwargs: Additional optional fields (rate, floor, etc.)

    Returns:
        Pricing option dict suitable for Product.pricing_options

    Example:
        pricing = create_test_pricing_option("cpm", "USD", rate=10.0)
    """
    return {"pricing_model": pricing_model, "currency": currency, **kwargs}
