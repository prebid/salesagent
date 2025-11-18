"""Product conversion utilities.

This module provides functions to convert between database Product models
and AdCP Product schema objects, including proper handling of pricing options,
publisher properties, and all required fields.
"""

from adcp import (
    CpcPricingOption,
    CpcvPricingOption,
    CpmAuctionPricingOption,
    CpmFixedRatePricingOption,
    CppPricingOption,
    CpvPricingOption,
    FlatRatePricingOption,
    VcpmAuctionPricingOption,
    VcpmFixedRatePricingOption,
)

# Import our extended Product (includes implementation_config)
# Not the library Product - we need the internal fields
from src.core.schemas import Product


def convert_pricing_option_to_adcp(
    pricing_option,
) -> (
    CpmFixedRatePricingOption
    | CpmAuctionPricingOption
    | VcpmFixedRatePricingOption
    | VcpmAuctionPricingOption
    | CpcPricingOption
    | CpcvPricingOption
    | CpvPricingOption
    | CppPricingOption
    | FlatRatePricingOption
):
    """Convert database PricingOption to AdCP pricing option discriminated union.

    Args:
        pricing_option: Database PricingOption model

    Returns:
        Typed AdCP pricing option instance (CpmFixedRatePricingOption, etc.)

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
    is_fixed = get_attr(pricing_option, "is_fixed")
    currency = get_attr(pricing_option, "currency")

    pricing_option_id = f"{pricing_model}_{currency.lower()}_{'fixed' if is_fixed else 'auction'}"

    # Build common fields shared across all pricing options
    common_fields = {
        "pricing_model": pricing_model,
        "currency": currency,
        "pricing_option_id": pricing_option_id,
        "is_fixed": is_fixed,  # Required by adcp 2.5.0 pricing option classes
    }

    # Add min_spend_per_package if present
    min_spend = get_attr(pricing_option, "min_spend_per_package")
    if min_spend:
        common_fields["min_spend_per_package"] = float(min_spend)

    rate = get_attr(pricing_option, "rate")
    price_guidance = get_attr(pricing_option, "price_guidance")
    parameters = get_attr(pricing_option, "parameters")

    # Discriminate by pricing_model and is_fixed to return typed instances
    if pricing_model == "cpm":
        if is_fixed:
            if not rate:
                raise ValueError(f"Fixed CPM pricing option {pricing_option_id} requires rate")
            return CpmFixedRatePricingOption(
                **common_fields,
                rate=float(rate),
            )
        else:
            if not price_guidance:
                raise ValueError(f"Auction CPM pricing option {pricing_option_id} requires price_guidance")
            return CpmAuctionPricingOption(
                **common_fields,
                price_guidance=price_guidance,
            )

    elif pricing_model == "vcpm":
        if is_fixed:
            if not rate:
                raise ValueError(f"Fixed VCPM pricing option {pricing_option_id} requires rate")
            return VcpmFixedRatePricingOption(
                **common_fields,
                rate=float(rate),
            )
        else:
            if not price_guidance:
                raise ValueError(f"Auction VCPM pricing option {pricing_option_id} requires price_guidance")
            return VcpmAuctionPricingOption(
                **common_fields,
                price_guidance=price_guidance,
            )

    elif pricing_model == "cpc":
        # CPC: adcp library v2.5.0 only supports fixed-rate CPC
        # CpcPricingOption has is_fixed=const(true) - auction CPC not supported yet
        if not is_fixed:
            raise ValueError(
                f"Auction CPC pricing option {pricing_option_id} is not supported by adcp library v2.5.0. "
                f"The CpcPricingOption class only supports fixed-rate pricing (is_fixed=true). "
                f"Please use fixed-rate CPC (with rate parameter) or contact AdCP maintainers to add CpcAuctionPricingOption support."
            )
        if not rate:
            raise ValueError(f"Fixed CPC pricing option {pricing_option_id} requires rate")
        return CpcPricingOption(
            **common_fields,
            rate=float(rate),
        )

    elif pricing_model == "cpcv":
        # CPCV (Cost Per Completed View) - typically fixed rate
        if not rate:
            raise ValueError(f"CPCV pricing option {pricing_option_id} requires rate")
        result_fields = {**common_fields, "rate": float(rate)}
        # CPCV may have optional parameters for view completion threshold
        if parameters:
            result_fields["parameters"] = parameters
        return CpcvPricingOption(**result_fields)

    elif pricing_model == "cpv":
        # CPV (Cost Per View) - typically auction-based
        if not rate:
            raise ValueError(f"CPV pricing option {pricing_option_id} requires rate")
        result_fields = {**common_fields, "rate": float(rate)}
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
            rate=float(rate),
            parameters=parameters,
        )

    elif pricing_model == "flat_rate":
        # Flat rate pricing - fixed cost regardless of delivery
        if not rate:
            raise ValueError(f"Flat rate pricing option {pricing_option_id} requires rate")
        result_fields = {
            **common_fields,
            "rate": float(rate),
            "is_fixed": True,  # Flat rate is always fixed per AdCP spec
        }
        # Flat rate may have optional parameters (DOOH venue packages, SOV, etc.)
        if parameters:
            result_fields["parameters"] = parameters
        return FlatRatePricingOption(**result_fields)

    else:
        raise ValueError(
            f"Unsupported pricing_model '{pricing_model}'. "
            f"Supported models: cpm, vcpm, cpc, cpcv, cpv, cpp, flat_rate"
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
    product_data["format_ids"] = product_model.effective_format_ids or []

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

    # pricing_options: Convert database PricingOption models to AdCP discriminated unions
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
    # Note: countries is NOT in AdCP Product schema - omit it
    # if product_model.countries:
    #     product_data["countries"] = product_model.countries
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

    return Product(**product_data)
