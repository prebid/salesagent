"""Product conversion utilities.

This module provides functions to convert between database Product models
and AdCP Product schema objects, including proper handling of pricing options,
publisher properties, and all required fields.

V3 Migration Notes:
- Pricing types consolidated: CpmAuctionPricingOption/CpmFixedRatePricingOption → CpmPricingOption
- is_fixed removed: Use fixed_price presence to indicate fixed pricing
- rate → fixed_price for fixed pricing
- price_guidance.floor → floor_price (top-level)
"""

import logging

from adcp import (
    CpcPricingOption,
    CpcvPricingOption,
    CpmPricingOption,
    CppPricingOption,
    CpvPricingOption,
    FlatRatePricingOption,
    VcpmPricingOption,
)
from packaging.version import InvalidVersion, Version

# Import our extended Product (includes implementation_config)
# Not the library Product - we need the internal fields
from src.core.schemas import Product

logger = logging.getLogger(__name__)

V3_VERSION = Version("3.0.0")


def needs_v2_compat(adcp_version: str | None) -> bool:
    """Check if a client needs v2 backward-compat fields in responses.

    V2 compat fields (is_fixed, rate, price_guidance.floor) are only needed
    for pre-3.0 clients. V3+ clients get clean responses per AdCP v3 spec.

    Args:
        adcp_version: Client-declared AdCP version string, or None if unknown.

    Returns:
        True if v2 compat fields should be added (version is None, < 3.0, or unparseable).
    """
    if adcp_version is None:
        return True
    try:
        return Version(adcp_version) < V3_VERSION
    except InvalidVersion:
        logger.warning(f"Unparseable adcp_version '{adcp_version}', defaulting to v2 compat")
        return True


def convert_pricing_option_to_adcp(
    pricing_option,
) -> (
    CpmPricingOption
    | VcpmPricingOption
    | CpcPricingOption
    | CpcvPricingOption
    | CpvPricingOption
    | CppPricingOption
    | FlatRatePricingOption
):
    """Convert database PricingOption to AdCP V3 pricing option.

    V3 Changes:
    - Pricing types consolidated (CpmPricingOption instead of Cpm{Auction,Fixed}PricingOption)
    - is_fixed removed: fixed_price presence indicates fixed pricing
    - rate → fixed_price
    - price_guidance.floor → floor_price

    Args:
        pricing_option: Database PricingOption model

    Returns:
        Typed AdCP pricing option instance (CpmPricingOption, etc.)

    Raises:
        ValueError: If pricing_model is not supported
    """

    # Support both ORM objects and dicts
    def get_attr(obj, key):
        """Get attribute from either dict or object."""
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    pricing_model = get_attr(pricing_option, "pricing_model").lower()
    is_fixed = get_attr(pricing_option, "is_fixed")  # Internal flag, not sent to API
    currency = get_attr(pricing_option, "currency")

    pricing_option_id = f"{pricing_model}_{currency.lower()}_{'fixed' if is_fixed else 'auction'}"

    # Build common fields shared across all pricing options (V3 format)
    # Note: is_fixed and rate are added during serialization for v2.x compat
    common_fields = {
        "pricing_model": pricing_model,
        "currency": currency,
        "pricing_option_id": pricing_option_id,
    }

    # Add min_spend_per_package if present
    min_spend = get_attr(pricing_option, "min_spend_per_package")
    if min_spend:
        common_fields["min_spend_per_package"] = float(min_spend)

    rate = get_attr(pricing_option, "rate")
    price_guidance = get_attr(pricing_option, "price_guidance")
    parameters = get_attr(pricing_option, "parameters")

    # Extract floor from price_guidance if present (V3: moves to top-level floor_price)
    floor_price = None
    if price_guidance:
        if isinstance(price_guidance, dict):
            floor_price = price_guidance.get("floor")
        elif hasattr(price_guidance, "floor"):
            floor_price = price_guidance.floor

    # Discriminate by pricing_model to return typed instances
    if pricing_model == "cpm":
        if is_fixed:
            if not rate:
                raise ValueError(f"Fixed CPM pricing option {pricing_option_id} requires rate")
            return CpmPricingOption(
                **common_fields,
                fixed_price=float(rate),
            )
        else:
            if not price_guidance:
                raise ValueError(f"Auction CPM pricing option {pricing_option_id} requires price_guidance")
            # V3: floor moves to top-level, price_guidance only has percentiles
            result = CpmPricingOption(
                **common_fields,
                price_guidance=price_guidance,
            )
            if floor_price is not None:
                result = CpmPricingOption(
                    **common_fields,
                    floor_price=float(floor_price),
                    price_guidance=price_guidance,
                )
            return result

    elif pricing_model == "vcpm":
        if is_fixed:
            if not rate:
                raise ValueError(f"Fixed VCPM pricing option {pricing_option_id} requires rate")
            return VcpmPricingOption(
                **common_fields,
                fixed_price=float(rate),
            )
        else:
            if not price_guidance:
                raise ValueError(f"Auction VCPM pricing option {pricing_option_id} requires price_guidance")
            vcpm_result = VcpmPricingOption(
                **common_fields,
                price_guidance=price_guidance,
            )
            if floor_price is not None:
                vcpm_result = VcpmPricingOption(
                    **common_fields,
                    floor_price=float(floor_price),
                    price_guidance=price_guidance,
                )
            return vcpm_result

    elif pricing_model == "cpc":
        # CPC: V3 uses fixed_price for fixed-rate
        if not is_fixed:
            raise ValueError(
                f"Auction CPC pricing option {pricing_option_id} is not supported. "
                f"CPC pricing requires fixed_price. Use fixed-rate CPC (with rate parameter)."
            )
        if not rate:
            raise ValueError(f"Fixed CPC pricing option {pricing_option_id} requires rate")
        return CpcPricingOption(
            **common_fields,
            fixed_price=float(rate),
        )

    elif pricing_model == "cpcv":
        # CPCV (Cost Per Completed View) - typically fixed rate
        if not rate:
            raise ValueError(f"CPCV pricing option {pricing_option_id} requires rate")
        result_fields = {
            **common_fields,
            "fixed_price": float(rate),
        }
        # CPCV may have optional parameters for view completion threshold
        if parameters:
            result_fields["parameters"] = parameters
        return CpcvPricingOption(**result_fields)

    elif pricing_model == "cpv":
        # CPV (Cost Per View) - typically auction-based
        if not rate:
            raise ValueError(f"CPV pricing option {pricing_option_id} requires rate")
        result_fields = {**common_fields}
        if is_fixed:
            result_fields["fixed_price"] = float(rate)
        else:
            result_fields["floor_price"] = float(rate)
        # CPV may have optional parameters for view threshold
        if parameters:
            result_fields["parameters"] = parameters
        return CpvPricingOption(**result_fields)

    elif pricing_model == "cpp":
        # CPP (Cost Per Point) - requires demographic parameters
        if not rate:
            raise ValueError(f"CPP pricing option {pricing_option_id} requires rate")
        if not parameters:
            raise ValueError(f"CPP pricing option {pricing_option_id} requires parameters (demographic)")
        return CppPricingOption(
            **common_fields,
            fixed_price=float(rate),
            parameters=parameters,
        )

    elif pricing_model == "flat_rate":
        # Flat rate pricing - fixed cost regardless of delivery
        if not rate:
            raise ValueError(f"Flat rate pricing option {pricing_option_id} requires rate")
        result_fields = {
            **common_fields,
            "fixed_price": float(rate),
        }
        # Flat rate may have optional parameters (DOOH venue packages, SOV, etc.)
        if parameters:
            result_fields["parameters"] = parameters
        return FlatRatePricingOption(**result_fields)

    else:
        raise ValueError(
            f"Unsupported pricing_model '{pricing_model}'. Supported models: cpm, vcpm, cpc, cpcv, cpv, cpp, flat_rate"
        )


def convert_product_model_to_schema(product_model) -> Product:
    """Convert database Product model to Product schema.

    Args:
        product_model: Product database model

    Returns:
        Product schema object
    """
    # Map fields from model to schema
    product_data = {}

    # Required fields per AdCP spec
    product_data["product_id"] = product_model.product_id
    product_data["name"] = product_model.name
    product_data["description"] = product_model.description
    product_data["delivery_type"] = product_model.delivery_type

    # format_ids: Use effective_format_ids which auto-resolves from profile if set
    # Products must have at least one format_id to be valid for media buys
    effective_formats = product_model.effective_format_ids or []
    if not effective_formats:
        raise ValueError(
            f"Product {product_model.product_id} has no format_ids configured. "
            f"Products must specify supported creative formats to be available for purchase. "
            f"Configure format_ids on the product or its inventory profile."
        )
    product_data["format_ids"] = effective_formats

    # publisher_properties: Use effective_properties which returns AdCP 2.0.0 discriminated union format
    effective_props = product_model.effective_properties
    if not effective_props:
        raise ValueError(
            f"Product {product_model.product_id} has no publisher_properties. "
            "All products must have at least one property per AdCP spec."
        )
    product_data["publisher_properties"] = effective_props

    # delivery_measurement: Provide default if missing (required per AdCP spec)
    if product_model.delivery_measurement:
        product_data["delivery_measurement"] = product_model.delivery_measurement
    else:
        # Default measurement provider
        product_data["delivery_measurement"] = {
            "provider": "publisher",
            "notes": "Measurement methodology not specified",
        }

    # pricing_options: Convert database PricingOption models to AdCP V3 format
    # Per adcp library spec, pricing_options must have at least 1 item (min_length=1)
    if product_model.pricing_options:
        product_data["pricing_options"] = [convert_pricing_option_to_adcp(po) for po in product_model.pricing_options]
    else:
        # Products without pricing options cannot be converted to AdCP schema
        # This is a data integrity error - all products must have pricing
        raise ValueError(
            f"Product {product_model.product_id} has no pricing_options. "
            f"All products must have at least one pricing option per AdCP spec. "
            f"Create a PricingOption record for this product."
        )

    # Optional fields
    if product_model.measurement:
        product_data["measurement"] = product_model.measurement
    if product_model.creative_policy:
        product_data["creative_policy"] = product_model.creative_policy
    # Note: price_guidance is database metadata, not in AdCP Product schema - omit it
    # Pricing information should be in pricing_options per AdCP spec

    # Filter-related internal fields (not in AdCP spec, but needed for filtering)
    # These are marked as exclude=True in our extended Product schema
    if hasattr(product_model, "countries") and product_model.countries:
        product_data["countries"] = product_model.countries
    if hasattr(product_model, "channels") and product_model.channels:
        product_data["channels"] = product_model.channels

    if product_model.product_card:
        product_data["product_card"] = product_model.product_card
    if product_model.product_card_detailed:
        product_data["product_card_detailed"] = product_model.product_card_detailed
    if product_model.placements:
        product_data["placements"] = product_model.placements
    if product_model.reporting_capabilities:
        product_data["reporting_capabilities"] = product_model.reporting_capabilities

    # Default is_custom to False if not set
    product_data["is_custom"] = product_model.is_custom if product_model.is_custom else False

    # Internal fields (not in AdCP spec, but in our extended Product schema)
    # Use effective_implementation_config to auto-resolve from inventory profile if set
    if hasattr(product_model, "effective_implementation_config"):
        product_data["implementation_config"] = product_model.effective_implementation_config
    elif hasattr(product_model, "implementation_config"):
        product_data["implementation_config"] = product_model.implementation_config
    else:
        product_data["implementation_config"] = None

    # Principal access control (internal field)
    product_data["allowed_principal_ids"] = getattr(product_model, "allowed_principal_ids", None)

    return Product(**product_data)


def add_v2_compat_to_pricing_options(product_dict: dict) -> dict:
    """Add v2.x backward-compatible fields to pricing_options in a serialized product.

    V3.0 changes that need v2.x compat:
    - is_fixed removed: V2.x clients expect this discriminator field
    - rate renamed to fixed_price: V2.x clients expect rate field
    - floor moved from price_guidance.floor to top-level floor_price

    This function adds backward-compat fields during serialization:
    - is_fixed: True if fixed_price is present, False otherwise
    - rate: Copy of fixed_price when present (v2.x field name)
    - price_guidance.floor: Copy of floor_price when present

    Args:
        product_dict: Serialized product dictionary (from model_dump)

    Returns:
        Product dictionary with v2.x backward-compat fields added
    """
    if "pricing_options" not in product_dict:
        return product_dict

    for po in product_dict["pricing_options"]:
        # Add is_fixed discriminator (v2.x expected this field)
        fixed_price = po.get("fixed_price")
        po["is_fixed"] = fixed_price is not None

        # Add rate field (v2.x name for fixed_price)
        if fixed_price is not None:
            po["rate"] = fixed_price

        # If floor_price is set, add floor to price_guidance for v2.x compat
        floor_price = po.get("floor_price")
        if floor_price is not None:
            if "price_guidance" not in po:
                po["price_guidance"] = {}
            po["price_guidance"]["floor"] = floor_price

    return product_dict


def add_v2_compat_to_products(products: list[dict]) -> list[dict]:
    """Add v2.x backward-compatible fields to a list of serialized products.

    Args:
        products: List of serialized product dictionaries

    Returns:
        Products with v2.x backward-compat fields added
    """
    return [add_v2_compat_to_pricing_options(p) for p in products]
