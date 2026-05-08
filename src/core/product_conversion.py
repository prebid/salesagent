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
from adcp.types import Product as LibraryProduct
from adcp.types._generated import MediaChannel

# Import our extended Product (includes implementation_config)
# Not the library Product - we need the internal fields
from src.core.resolved_product import ResolvedProduct
from src.core.schemas import Product

# ``Product`` schema declares the four internal-only fields with
# ``exclude=True`` so they are stripped by ``model_dump()``. The conversion
# below relies on that contract — if any of these gets the marker dropped
# upstream, the field would leak into ``LibraryProduct.model_validate`` and
# either crash dev (extra='forbid') or silently drop in prod (extra='ignore').
# Guard the contract at conversion time.
_INTERNAL_FIELD_NAMES: frozenset[str] = frozenset(
    {"implementation_config", "countries", "device_types", "allowed_principal_ids"}
)

logger = logging.getLogger(__name__)


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
        if is_fixed:
            if not rate:
                raise ValueError(f"Fixed CPC pricing option {pricing_option_id} requires rate")
            return CpcPricingOption(
                **common_fields,
                fixed_price=float(rate),
            )
        else:
            if not price_guidance:
                raise ValueError(f"Auction CPC pricing option {pricing_option_id} requires price_guidance")
            cpc_result = CpcPricingOption(
                **common_fields,
                price_guidance=price_guidance,
            )
            if floor_price is not None:
                cpc_result = CpcPricingOption(
                    **common_fields,
                    floor_price=float(floor_price),
                    price_guidance=price_guidance,
                )
            return cpc_result

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
        # adcp 3.10: Parameters requires type="dooh" discriminator
        if parameters:
            if isinstance(parameters, dict) and "type" not in parameters:
                parameters = {**parameters, "type": "dooh"}
            result_fields["parameters"] = parameters
        return FlatRatePricingOption(**result_fields)

    else:
        raise ValueError(
            f"Unsupported pricing_model '{pricing_model}'. Supported models: cpm, vcpm, cpc, cpcv, cpv, cpp, flat_rate"
        )


def convert_product_model_to_schema(product_model, adapter_type: str | None = None) -> Product:
    """Convert database Product model to Product schema.

    Args:
        product_model: Product database model
        adapter_type: Adapter type for the tenant (e.g., "google_ad_manager", "mock").
            Used to determine the default delivery_measurement when the product
            does not have one configured. If None, falls back to generic "publisher".

    Returns:
        Product schema object

    Raises:
        ValueError: In non-production environments, if delivery_measurement is missing
            and no adapter_type is provided to determine the default.
    """
    # Map fields from model to schema
    product_data = {}

    # Required fields per AdCP spec
    product_data["product_id"] = product_model.product_id
    product_data["name"] = product_model.name
    # AdCP Product.description is a required non-null string. The ORM column
    # is nullable for legacy rows, so coalesce to empty string to keep the
    # wire shape valid (mirrors the reporting_capabilities default for #71).
    product_data["description"] = product_model.description or ""
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

    # delivery_measurement: REQUIRED per AdCP spec.
    # Use the product's configured value, or fall back to adapter-specific default.
    if product_model.delivery_measurement:
        product_data["delivery_measurement"] = product_model.delivery_measurement
    else:
        from src.adapters import get_adapter_default_delivery_measurement
        from src.core.config import is_production

        default_dm = get_adapter_default_delivery_measurement(adapter_type or "")
        if is_production():
            logger.info(
                "Product %s missing delivery_measurement, using adapter default: %s",
                product_model.product_id,
                default_dm["provider"],
            )
        else:
            logger.warning(
                "Product %s missing delivery_measurement (REQUIRED per AdCP spec). "
                "Using adapter default '%s'. Configure delivery_measurement on the product to silence this warning.",
                product_model.product_id,
                default_dm["provider"],
            )
        product_data["delivery_measurement"] = default_dm

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

    # AdCP 4.4 made reporting_capabilities required. The ORM column is NOT NULL
    # with a server_default (migration c8404b483cf3), so this is always populated.
    product_data["reporting_capabilities"] = product_model.reporting_capabilities

    # is_custom: column is Mapped[bool] non-null with default False on the ORM.
    product_data["is_custom"] = product_model.is_custom

    # Optional fields — emit only when set, so Pydantic field defaults apply
    # for the rest. ``price_guidance`` is DB-only metadata; pricing lives on
    # ``pricing_options`` per AdCP spec.
    _OPTIONAL_PASSTHROUGH = (
        "measurement",
        "creative_policy",
        "product_card",
        "product_card_detailed",
        "placements",
        "property_targeting_allowed",
        "signal_targeting_allowed",
        "catalog_match",
        "catalog_types",
        "conversion_tracking",
        "data_provider_signals",
        "forecast",
    )
    for field_name in _OPTIONAL_PASSTHROUGH:
        value = getattr(product_model, field_name, None)
        if value is not None:
            product_data[field_name] = value

    # channels: DB stores strings, schema uses MediaChannel enum.
    if product_model.channels:
        converted_channels = []
        for ch in product_model.channels:
            try:
                converted_channels.append(MediaChannel(ch))
            except ValueError:
                logger.warning("Unknown channel value '%s' in product %s, skipping", ch, product_model.product_id)
        if converted_channels:
            product_data["channels"] = converted_channels

    # countries: filter-related, kept on the schema with ``exclude=True`` so it
    # doesn't reach the wire.
    if product_model.countries:
        product_data["countries"] = product_model.countries

    # implementation_config: prefer the inventory-profile-resolved value
    # (``effective_implementation_config`` is defined on the ORM model and
    # falls back to the row's own column when no profile is attached).
    product_data["implementation_config"] = product_model.effective_implementation_config

    # Principal access control (internal field, ``exclude=True`` on schema).
    product_data["allowed_principal_ids"] = product_model.allowed_principal_ids

    # Device type targeting (from targeting_template.device_targets).
    targeting_template = product_model.targeting_template
    if isinstance(targeting_template, dict):
        device_targets = targeting_template.get("device_targets")
        if isinstance(device_targets, list):
            product_data["device_types"] = device_targets

    return Product(**product_data)


def convert_product_model_to_resolved(product_model, adapter_type: str | None = None) -> ResolvedProduct:
    """Convert ORM Product → :class:`ResolvedProduct`.

    Sibling of :func:`convert_product_model_to_schema`. Returns a
    ``ResolvedProduct`` (wire-shape ``LibraryProduct`` + internal fields)
    instead of the local ``Product`` schema extension. The filter pipeline
    is being migrated from the latter to the former; both functions exist
    side-by-side until the migration completes.

    The wire-shape projection reuses the existing converter — same field
    population, same defaults, same validation. Internal fields are
    pulled directly off the ORM model.
    """
    schema_product = convert_product_model_to_schema(product_model, adapter_type=adapter_type)

    # Project to library Product (drops the four internal fields, which
    # have ``exclude=True`` on the local Product extension; the resulting
    # dict is spec-clean).
    wire_dict = schema_product.model_dump(mode="python")

    # Defense-in-depth: catch the day someone drops ``exclude=True`` on an
    # internal field. Without this assert, a leaked field would either
    # crash dev (LibraryProduct extra='forbid') or silently drop in prod
    # (extra='ignore'). Fail loud, fail early.
    leaked = _INTERNAL_FIELD_NAMES & wire_dict.keys()
    assert not leaked, (
        f"Internal Product fields leaked into wire dict: {leaked}. "
        "Check ``exclude=True`` is set on these fields in src/core/schemas/product.py."
    )

    wire = LibraryProduct.model_validate(wire_dict)

    # Internal fields — pull directly from ORM where source-of-truth lives.
    countries = product_model.countries if product_model.countries else None
    allowed_principal_ids = product_model.allowed_principal_ids
    implementation_config = product_model.effective_implementation_config

    # device_types is derived from targeting_template.device_targets.
    device_types: list[str] | None = None
    targeting_template = product_model.targeting_template
    if isinstance(targeting_template, dict):
        device_targets = targeting_template.get("device_targets")
        if isinstance(device_targets, list):
            device_types = device_targets

    return ResolvedProduct(
        wire=wire,
        implementation_config=implementation_config,
        countries=countries,
        device_types=device_types,
        allowed_principal_ids=allowed_principal_ids,
    )
