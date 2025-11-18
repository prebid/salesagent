"""Test factories for creating AdCP-compliant objects.

This module provides factory functions for creating objects from the adcp library
that comply with the AdCP spec, including all required fields. Use these in tests
instead of manually constructing objects to avoid validation errors.

All factories use sensible defaults for required fields and accept overrides for customization.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

# Import types from adcp library - use public API when available
from adcp import BrandManifest, Format, Property
from adcp.types.generated_poc.creative_asset import CreativeAsset
from adcp.types.generated_poc.format_id import FormatId
from adcp.types.generated_poc.product import Product

# Import Package and PackageRequest from our schemas (they extend adcp library)
from src.core.schemas import Package, PackageRequest, url


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
    # Must be discriminated union format (by_id or by_tag variant)
    if publisher_properties is None:
        publisher_properties = [create_test_publisher_properties_by_tag()]

    # Default delivery_measurement if not provided
    if delivery_measurement is None:
        delivery_measurement = {
            "provider": "test_provider",
            "notes": "Test measurement methodology",
        }

    # Default pricing_options if not provided
    # Must be proper discriminated union (CpmFixedRatePricingOption, etc.)
    if pricing_options is None:
        pricing_options = [create_test_cpm_pricing_option()]

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
        "publisher_properties": [create_test_publisher_properties_by_tag()],
        "format_ids": [create_test_format_id("display_300x250")],
        "delivery_type": "guaranteed",
        "pricing_options": [create_test_cpm_pricing_option()],
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
    return FormatId(agent_url=url(agent_url), id=format_id)


def create_test_format(
    format_id: str | FormatId | None = None,
    name: str = "Test Format",
    type: str = "display",
    assets_required: list[dict[str, Any]] | None = None,
    **kwargs,
) -> Format:
    """Create a test Format object compatible with adcp 2.5.0.

    Args:
        format_id: FormatId object or string. Defaults to "display_300x250"
        name: Human-readable format name
        type: Format type ("display", "video", "audio", etc.)
        assets_required: List of asset requirements with discriminated union structure.
            Each asset must have 'item_type' discriminator:
            - 'individual': Single asset (requires asset_id, asset_type)
            - 'repeatable_group': Asset group (requires asset_group_id, assets, min_count, max_count)
            Defaults to a single image asset for display, video asset for video.
        **kwargs: Additional optional fields (requirements, iab_specification, etc.)

    Returns:
        AdCP-compliant Format object (adcp 2.5.0+)

    Example:
        # Simple display format
        format = create_test_format("display_300x250", name="Medium Rectangle")

        # Video format
        format = create_test_format("video_1920x1080", name="Full HD Video", type="video")

        # Custom assets
        format = create_test_format(
            "carousel_3x",
            assets_required=[
                {"item_type": "individual", "asset_id": "primary", "asset_type": "image"},
                {"item_type": "individual", "asset_id": "secondary", "asset_type": "image"},
            ]
        )
    """
    if format_id is None:
        format_id = create_test_format_id("display_300x250")
    elif isinstance(format_id, str):
        format_id = create_test_format_id(format_id)

    # Default assets_required based on type if not provided
    if assets_required is None:
        if "video" in type.lower():
            assets_required = [
                {
                    "item_type": "individual",
                    "asset_id": "primary",
                    "asset_type": "video",
                }
            ]
        elif "audio" in type.lower():
            assets_required = [
                {
                    "item_type": "individual",
                    "asset_id": "primary",
                    "asset_type": "audio",
                }
            ]
        else:  # display or other
            assets_required = [
                {
                    "item_type": "individual",
                    "asset_id": "primary",
                    "asset_type": "image",
                }
            ]

    return Format(format_id=format_id, name=name, type=type, assets_required=assets_required, **kwargs)


def create_test_publisher_properties_by_tag(
    publisher_domain: str = "test.example.com",
    property_tags: list[str] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Create test publisher_properties in by_tag variant (discriminated union).

    This is the AdCP 2.0.0+ discriminated union format for tag-based property selection.

    Args:
        publisher_domain: Domain of the publisher
        property_tags: List of property tags. Defaults to ["all_inventory"]
        **kwargs: Additional optional fields

    Returns:
        Publisher properties dict in by_tag variant format

    Example:
        props = create_test_publisher_properties_by_tag(
            publisher_domain="news.example.com",
            property_tags=["premium", "sports"]
        )
    """
    if property_tags is None:
        property_tags = ["all_inventory"]

    return {
        "publisher_domain": publisher_domain,
        "property_tags": property_tags,
        "selection_type": "by_tag",
        **kwargs,
    }


def create_test_publisher_properties_by_id(
    publisher_domain: str = "test.example.com",
    property_ids: list[str] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Create test publisher_properties in by_id variant (discriminated union).

    This is the AdCP 2.0.0+ discriminated union format for ID-based property selection.

    Args:
        publisher_domain: Domain of the publisher
        property_ids: List of property IDs. Defaults to ["test_property_1"]
        **kwargs: Additional optional fields

    Returns:
        Publisher properties dict in by_id variant format

    Example:
        props = create_test_publisher_properties_by_id(
            publisher_domain="news.example.com",
            property_ids=["prop_001", "prop_002"]
        )
    """
    if property_ids is None:
        property_ids = ["test_property_1"]

    return {
        "publisher_domain": publisher_domain,
        "property_ids": property_ids,
        "selection_type": "by_id",
        **kwargs,
    }


def create_test_property_dict(
    publisher_domain: str = "test.example.com",
    property_id: str = "test_property_1",
    property_name: str = "Test Property",
    property_type: str = "website",
    **kwargs,
) -> dict[str, Any]:
    """Create a test property dict for use in publisher_properties.

    DEPRECATED: Use create_test_publisher_properties_by_tag() or
    create_test_publisher_properties_by_id() instead. This function creates
    legacy format that is not compatible with adcp 2.1.0 Product schema.

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


def create_test_package_request(
    product_id: str = "test_product",
    buyer_ref: str | None = None,
    budget: float | None = None,
    pricing_option_id: str = "test_pricing_option",
    **kwargs,
) -> PackageRequest:
    """Create a test PackageRequest object (for CreateMediaBuyRequest).

    Args:
        product_id: Product ID for the package (REQUIRED per adcp PackageRequest)
        buyer_ref: Buyer reference for the package (REQUIRED per adcp PackageRequest)
        budget: Budget allocation (REQUIRED per adcp PackageRequest)
        pricing_option_id: Pricing option ID (REQUIRED per adcp PackageRequest)
        **kwargs: Additional optional fields (creative_ids, format_ids, targeting_overlay, etc.)

    Returns:
        AdCP-compliant PackageRequest object for use in CreateMediaBuyRequest

    Example:
        # Minimal package request
        pkg_request = create_test_package_request()

        # Custom package request
        pkg_request = create_test_package_request(
            product_id="prod_video",
            buyer_ref="buyer_pkg_001",
            budget=5000.0,
            creative_ids=["creative_1", "creative_2"]
        )
    """
    # Set defaults for required fields if not provided
    if buyer_ref is None:
        buyer_ref = f"buyer_pkg_{product_id}"
    if budget is None:
        budget = 1000.0

    return PackageRequest(
        product_id=product_id,
        buyer_ref=buyer_ref,
        budget=budget,
        pricing_option_id=pricing_option_id,
        **kwargs,
    )


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


def create_test_cpm_pricing_option(
    pricing_option_id: str = "cpm_option_1",
    currency: str = "USD",
    rate: float = 10.0,
    is_fixed: bool = True,
    **kwargs,
) -> dict[str, Any]:
    """Create a test CPM fixed rate pricing option (discriminated union).

    This creates a proper AdCP 2.4.0+ CpmFixedRatePricingOption discriminated union.
    As of adcp 2.4.0, is_fixed is a required field per AdCP spec.

    Args:
        pricing_option_id: Unique identifier for this pricing option
        currency: Currency code (3-letter ISO)
        rate: CPM rate in the specified currency
        is_fixed: Whether this is fixed rate (True) or auction (False). Defaults to True.
        **kwargs: Additional optional fields (min_spend_per_package, etc.)

    Returns:
        CPM pricing option dict suitable for Product.pricing_options

    Example:
        pricing = create_test_cpm_pricing_option(rate=15.0, currency="EUR")
    """
    return {
        "pricing_option_id": pricing_option_id,
        "pricing_model": "cpm",
        "currency": currency,
        "rate": rate,
        "is_fixed": is_fixed,
        **kwargs,
    }


def create_test_pricing_option(pricing_model: str = "cpm", currency: str = "USD", **kwargs) -> dict[str, Any]:
    """Create a test pricing option dict.

    DEPRECATED: Use create_test_cpm_pricing_option() or other specific pricing
    functions instead. This function creates incomplete pricing options that
    don't match adcp 2.1.0 discriminated union requirements.

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


def create_test_media_buy_request_dict(
    buyer_ref: str = "test_buyer_ref",
    product_ids: list[str] | None = None,
    total_budget: float = 10000.0,
    start_time: str | None = None,
    end_time: str | None = None,
    brand_manifest: dict[str, Any] | None = None,
    pricing_option_id: str = "cpm_option_1",
    **kwargs,
) -> dict[str, Any]:
    """Create a test media buy request dict (works with both internal and adcp CreateMediaBuyRequest).

    Note: Returns a dict instead of CreateMediaBuyRequest object because we have schema
    duplication issues (internal vs adcp library). Dicts work with both.

    Args:
        buyer_ref: Buyer reference identifier
        product_ids: List of product IDs to create packages from. Defaults to ["test_product"]
                     Note: Creates one package per product_id. Use packages kwarg for custom package structure.
        total_budget: Total budget for the campaign (divided equally among packages)
        start_time: Campaign start time (ISO string). Defaults to "asap"
        end_time: Campaign end time (ISO string). Defaults to 30 days from now
        brand_manifest: Brand info dict. Defaults to {"name": "Test Brand", "promoted_offering": "Test Product"}
        pricing_option_id: Pricing option ID for all packages. Defaults to "cpm_option_1"
        **kwargs: Additional optional fields (po_number, reporting_webhook, targeting_overlay, etc.)
                  targeting_overlay goes into packages, all others go to top level

    Returns:
        Media buy request dict suitable for create_media_buy tool

    Example:
        # Minimal request (one package with one product)
        request = create_test_media_buy_request_dict()

        # Custom request with multiple products (creates multiple packages)
        request = create_test_media_buy_request_dict(
            buyer_ref="buyer_001",
            product_ids=["prod_1", "prod_2"],
            total_budget=50000.0,
            start_time="2025-11-01T00:00:00Z",
            end_time="2025-11-30T23:59:59Z",
            brand_manifest={"name": "Nike", "promoted_offering": "Air Jordan 2025"}
        )
    """

    # Default start_time to "asap"
    if start_time is None:
        start_time = "asap"

    # Default end_time to 30 days from now
    if end_time is None:
        end_datetime = datetime.now(UTC) + timedelta(days=30)
        end_time = end_datetime.isoformat()

    # Default brand_manifest
    if brand_manifest is None:
        brand_manifest = {"name": "Test Brand", "promoted_offering": "Test Product"}

    # Default product_ids
    if product_ids is None:
        product_ids = ["test_product"]

    # Calculate per-package budget (divide total among packages)
    per_package_budget = total_budget / len(product_ids)

    # Build request dict with AdCP-compliant PackageRequest structure
    # One package per product_id (per AdCP spec, each package has one product_id)
    packages = []
    for idx, product_id in enumerate(product_ids, 1):
        package = {
            "buyer_ref": f"{buyer_ref}_pkg_{idx}",
            "product_id": product_id,
            "pricing_option_id": pricing_option_id,
            "budget": per_package_budget,
        }
        packages.append(package)

    request = {
        "buyer_ref": buyer_ref,
        "brand_manifest": brand_manifest,
        "packages": packages,
        "start_time": start_time,
        "end_time": end_time,
        "budget": total_budget,  # Top-level budget
    }

    # Handle targeting_overlay specially (goes in all packages, not top-level)
    targeting_overlay = kwargs.pop("targeting_overlay", None)
    if targeting_overlay is not None:
        for package in request["packages"]:
            package["targeting_overlay"] = targeting_overlay

    # Merge remaining kwargs to top level
    request.update(kwargs)

    return request


def create_test_media_buy_dict(
    media_buy_id: str = "test_media_buy_001",
    buyer_ref: str = "test_buyer_ref",
    status: str = "active",
    promoted_offering: str = "Test Product",
    total_budget: float = 10000.0,
    packages: list[dict[str, Any]] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Create a test MediaBuy dict (for response testing).

    Note: Returns a dict instead of MediaBuy object because of schema duplication.

    Args:
        media_buy_id: Media buy identifier
        buyer_ref: Buyer reference identifier
        status: Media buy status ("active", "paused", "completed", etc.)
        promoted_offering: What is being promoted
        total_budget: Total budget for the campaign
        packages: List of package dicts. Defaults to one test package
        **kwargs: Additional optional fields (creative_deadline, created_at, updated_at, etc.)

    Returns:
        MediaBuy dict

    Example:
        media_buy = create_test_media_buy_dict(
            media_buy_id="mb_001",
            status="active",
            promoted_offering="Nike Air Jordan 2025",
            total_budget=50000.0
        )
    """
    # Default packages if not provided
    if packages is None:
        packages = [
            {
                "package_id": "test_package",
                "buyer_ref": "test_package_ref",
                "status": "active",
                "products": ["test_product"],
                "budget": total_budget,
            }
        ]

    return {
        "media_buy_id": media_buy_id,
        "buyer_ref": buyer_ref,
        "status": status,
        "promoted_offering": promoted_offering,
        "total_budget": total_budget,
        "packages": packages,
        **kwargs,
    }


def create_test_package_request_dict(
    buyer_ref: str = "test_package_ref",
    product_id: str = "test_product",
    pricing_option_id: str = "cpm_option_1",
    budget: float = 10000.0,
    **kwargs,
) -> dict[str, Any]:
    """Create a test package request dict for use in media buy requests.

    Args:
        buyer_ref: Package reference identifier (REQUIRED per AdCP PackageRequest)
        product_id: Product ID for the package (REQUIRED per AdCP PackageRequest)
        pricing_option_id: Pricing option ID (REQUIRED per AdCP PackageRequest)
        budget: Package budget (REQUIRED per AdCP PackageRequest)
        **kwargs: Additional optional fields (targeting_overlay, creative_ids, etc.)

    Returns:
        Package request dict

    Example:
        pkg = create_test_package_request_dict(
            buyer_ref="pkg_001",
            product_id="prod_1",
            pricing_option_id="cpm_option_1",
            budget=25000.0,
            targeting_overlay={"geo": {"countries": ["US"]}}
        )
    """
    return {
        "buyer_ref": buyer_ref,
        "product_id": product_id,
        "pricing_option_id": pricing_option_id,
        "budget": budget,
        **kwargs,
    }


def create_test_db_product(
    tenant_id: str,
    product_id: str = "test_product",
    name: str = "Test Product",
    description: str = "Test product description",
    format_ids: list[dict[str, str]] | None = None,
    property_tags: list[str] | None = None,
    property_ids: list[str] | None = None,
    properties: list[dict] | None = None,
    delivery_type: str = "guaranteed",
    targeting_template: dict[str, Any] | None = None,
    inventory_profile_id: int | None = None,
    **kwargs,
):
    """Create a test Product database record with tenant_id.

    This factory creates database Product records (from src.core.database.models.Product)
    for tests that need to insert Products into the database. The database Product model
    uses legacy field names (property_tags, property_ids, properties) that get converted
    to publisher_properties when serializing to AdCP format.

    Use this factory for integration tests that create Product database records.
    Use create_test_product() for AdCP-compliant Product objects without tenant_id.

    Args:
        tenant_id: Tenant identifier (REQUIRED for database Product)
        product_id: Product identifier
        name: Product name
        description: Product description
        format_ids: List of format ID dicts with {agent_url: str, id: str}. Defaults to display_300x250
        property_tags: List of property tags (e.g., ["all_inventory", "premium"]). Default: ["all_inventory"]
        property_ids: List of property IDs (alternative to property_tags)
        properties: List of full Property objects (legacy, alternative to property_tags/property_ids)
        delivery_type: "guaranteed" or "non_guaranteed"
        targeting_template: Targeting template dict. Defaults to empty dict
        inventory_profile_id: Optional inventory profile ID to link
        **kwargs: Additional optional fields (measurement, creative_policy, implementation_config, etc.)

    Returns:
        Database Product model instance ready to be added to session

    Example:
        # Minimal database product
        from src.core.database.models import Product as DBProduct
        product = create_test_db_product(tenant_id="test_tenant")

        # Custom database product with inventory profile
        product = create_test_db_product(
            tenant_id="test_tenant",
            product_id="video_premium",
            format_ids=[{"agent_url": "https://creative.example.com", "id": "video_1920x1080"}],
            property_tags=["premium", "sports"],
            inventory_profile_id=123
        )

        # Add to database
        with get_db_session() as session:
            session.add(product)
            session.commit()
    """
    # Import database Product model
    from src.core.database.models import Product as DBProduct

    # Default format_ids if not provided
    if format_ids is None:
        format_ids = [
            {
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_300x250",
            }
        ]

    # Default property_tags if no property authorization provided
    if property_tags is None and property_ids is None and properties is None:
        property_tags = ["all_inventory"]

    # Default targeting_template
    if targeting_template is None:
        targeting_template = {}

    return DBProduct(
        tenant_id=tenant_id,
        product_id=product_id,
        name=name,
        description=description,
        format_ids=format_ids,
        property_tags=property_tags,
        property_ids=property_ids,
        properties=properties,
        delivery_type=delivery_type,
        targeting_template=targeting_template,
        inventory_profile_id=inventory_profile_id,
        **kwargs,
    )


def create_test_db_product_with_pricing(
    tenant_id: str,
    product_id: str = "test_product",
    pricing_model: str = "cpm",
    rate: float = 10.0,
    currency: str = "USD",
    **product_kwargs,
) -> tuple[Any, Any]:
    """Create a test Product with PricingOption - ready for AdCP schema conversion.

    This is a convenience helper that creates both a Product and its required PricingOption,
    ensuring the product can be successfully converted to AdCP schema format.

    Args:
        tenant_id: Tenant identifier (REQUIRED)
        product_id: Product identifier
        pricing_model: Pricing model (cpm, cpc, vcpm, etc.)
        rate: Fixed rate for the pricing model
        currency: Currency code (USD, EUR, etc.)
        **product_kwargs: Additional arguments passed to create_test_db_product()

    Returns:
        Tuple of (Product, PricingOption) ready to be added to session

    Example:
        from decimal import Decimal
        with get_db_session() as session:
            product, pricing = create_test_db_product_with_pricing(
                tenant_id="test_tenant",
                product_id="display_premium",
                rate=15.0
            )
            session.add(product)
            session.add(pricing)
            session.commit()

            # Product can now be converted to AdCP schema
            from src.core.product_conversion import convert_product_model_to_schema
            adcp_product = convert_product_model_to_schema(product)
    """
    from decimal import Decimal

    from src.core.database.models import PricingOption

    # Create product
    product = create_test_db_product(tenant_id=tenant_id, product_id=product_id, **product_kwargs)

    # Create pricing option
    pricing = PricingOption(
        tenant_id=tenant_id,
        product_id=product_id,
        pricing_model=pricing_model,
        rate=Decimal(str(rate)),
        currency=currency,
        is_fixed=True,
    )

    return product, pricing
