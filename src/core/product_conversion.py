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

from adcp import EventType, TimeUnit
from adcp.types import ReportingCapabilities as LibraryReportingCapabilities
from adcp.types._generated import MediaChannel
from adcp.types.domains.core.reporting_capabilities import DateRangeSupport
from adcp.types.domains.enums.available_metric import AvailableMetric
from adcp.types.domains.enums.reporting_frequency import ReportingFrequency
from adcp.types.domains.pricing_options.time_option import Parameters as TimeParameters

# Import our extended Product (includes implementation_config) and the local
# pricing member subclasses (our extra policy + internal adapter annotations)
# — not the raw SDK types (Pattern #1 / local-schema-imports guard).
from src.core.schemas import (
    CpaPricingOption,
    CpcPricingOption,
    CpcvPricingOption,
    CpmPricingOption,
    CppPricingOption,
    CpvPricingOption,
    FlatRatePricingOption,
    Product,
    TimeBasedPricingOption,
    VcpmPricingOption,
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
    | CpaPricingOption
    | TimeBasedPricingOption
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

    # The stored identifier, never a recomputed one. ``pricing_option_id`` is REQUIRED by
    # every ``pricing-options/*.json`` member and is what a buyer's ``PackageRequest``
    # names back, so a row that has none names nothing a buyer can select.
    pricing_option_id = get_attr(pricing_option, "pricing_option_id")
    if not pricing_option_id:
        raise ValueError(
            f"{pricing_model} pricing option for {get_attr(pricing_option, 'product_id')} has no "
            "pricing_option_id — build rows through PricingOption.create()"
        )

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
        # pricing-options/cpcv-option.json declares NO parameters
        # property (completion is definitional for CPCV). Stored parameters
        # cannot be expressed on the wire — fail loud rather than silently
        # dropping seller-configured data (no-quiet-failures). They previously
        # leaked through the SDK members' extra="allow" as a non-spec emission.
        if parameters:
            raise ValueError(
                f"CPCV pricing option {pricing_option_id} has parameters {parameters!r}, "
                f"but cpcv-option.json defines no parameters property. "
                f"Remove them from the pricing option record."
            )
        return CpcvPricingOption(
            **common_fields,
            fixed_price=float(rate),
        )

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
        # the pinned SDK: Parameters requires type="dooh" discriminator
        if parameters:
            if isinstance(parameters, dict) and "type" not in parameters:
                parameters = {**parameters, "type": "dooh"}
            result_fields["parameters"] = parameters
        return FlatRatePricingOption(**result_fields)

    elif pricing_model == "cpa":
        # CPA (Cost Per Acquisition) - pricing model for affiliate/conversion pricing.
        # event_type is required per cpa-option.json; no default — an unknown value is a mispricing.
        if not rate:
            raise ValueError(f"CPA pricing option {pricing_option_id} requires rate")
        raw_event = parameters.get("event_type") if isinstance(parameters, dict) else None
        if not raw_event:
            raise ValueError(f"CPA pricing option {pricing_option_id} requires parameters.event_type")
        try:
            event_type_val = EventType(raw_event)
        except ValueError:
            raise ValueError(
                f"CPA pricing option {pricing_option_id} has unknown event_type '{raw_event}'. "
                f"Supported values: {[e.value for e in EventType]}"
            )
        if event_type_val == EventType.custom:
            custom_event_name = parameters.get("custom_event_name") if isinstance(parameters, dict) else None
            if not custom_event_name:
                raise ValueError(
                    f"CPA pricing option {pricing_option_id} with event_type 'custom' requires parameters.custom_event_name"
                )
        else:
            custom_event_name = None
        event_source_id = parameters.get("event_source_id") if isinstance(parameters, dict) else None
        return CpaPricingOption(
            **common_fields,
            event_type=event_type_val,
            custom_event_name=custom_event_name,
            event_source_id=event_source_id,
            fixed_price=float(rate),
        )

    elif pricing_model == "time":
        # Time-Based pricing - model where rate scales with campaign duration.
        # fixed_price is the cost per time_unit; parameters.time_unit is required.
        # Supports both fixed (fixed_price) and auction (floor_price) variants.
        if not parameters or not isinstance(parameters, dict) or "time_unit" not in parameters:
            raise ValueError(
                f"Time-based pricing option {pricing_option_id} requires parameters.time_unit "
                f"(one of: hour, day, week, month)"
            )
        raw_time_unit = parameters["time_unit"]
        try:
            time_unit_val = TimeUnit(raw_time_unit)
        except ValueError:
            raise ValueError(
                f"Time-based pricing option {pricing_option_id} has unknown time_unit '{raw_time_unit}'. "
                f"Supported values: {[u.value for u in TimeUnit]}"
            )
        time_params = TimeParameters(
            time_unit=time_unit_val,
            min_duration=parameters.get("min_duration"),
            max_duration=parameters.get("max_duration"),
        )
        if (
            time_params.min_duration is not None
            and time_params.max_duration is not None
            and time_params.max_duration < time_params.min_duration
        ):
            raise ValueError(
                f"Time-based pricing option {pricing_option_id} has max_duration "
                f"({time_params.max_duration}) < min_duration ({time_params.min_duration})"
            )
        time_fields: dict = {**common_fields, "parameters": time_params}
        if is_fixed:
            if rate is None:
                raise ValueError(f"Fixed time-based pricing option {pricing_option_id} requires rate")
            time_fields["fixed_price"] = float(rate)
        else:
            if floor_price is not None:
                time_fields["floor_price"] = float(floor_price)
            if price_guidance:
                time_fields["price_guidance"] = price_guidance
        return TimeBasedPricingOption(**time_fields)

    else:
        raise ValueError(
            f"Unsupported pricing_model '{pricing_model}'. "
            f"Supported models: cpm, vcpm, cpc, cpcv, cpv, cpp, flat_rate, cpa, time"
        )


def default_reporting_capabilities() -> LibraryReportingCapabilities:
    """The reporting_capabilities a row that stores NULL is served with.

    core/product.json requires the field unconditionally, and ``Product`` inherits it as
    required. The default therefore lives at the edges that build a Product from something
    that may lack one -- the row-to-model read below and the adapters that assemble
    products by hand -- never on the wire model. A fresh instance per call, so no lists
    are shared between products. Retired by salesagent-3cs7o.21 (NOT NULL with a backfill).
    """
    # THE PINNED VOCABULARIES, by member rather than by string. Each of these three fields
    # is typed with a closed enum, so pydantic coerced the literals and they worked -- which
    # is exactly why they survived: a string that happens to be a member looks identical to
    # one that is not until the value changes. ``AvailableMetric`` alone has 49 members, and
    # this seller referenced it nowhere while naming one of them by hand.
    return LibraryReportingCapabilities(
        available_reporting_frequencies=[ReportingFrequency.daily],
        expected_delay_minutes=1440,
        timezone="UTC",
        supports_webhooks=False,
        available_metrics=[AvailableMetric.impressions],
        date_range_support=DateRangeSupport.date_range,
    )


def convert_product_model_to_schema(
    product_model,
    adapter_type: str | None = None,
) -> Product:
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
    product_data["description"] = product_model.description
    product_data["delivery_type"] = product_model.delivery_type

    # The two format arms of core/product.json's root anyOf, from the ONE contract the
    # product carries (`Product.format_contract`). A canonical product's `format_ids` is the
    # projection its declarations assert through `v1_format_ref`, derived there and never
    # read from a stored column -- canonical-formats.mdx, Producer rules: "Compatibility
    # format_ids MUST be derived from them rather than maintained as a second authored
    # catalog." A legacy-only product serves the arm it authored.
    #
    # Each key is omitted rather than emitted empty. An empty `format_ids` would be worse
    # than absence: the deprecated branch requires only the KEY, so `format_ids: []`
    # satisfies it by presence while declaring no format at all. `format_options` is handed
    # through as the stored documents; the DTO validates each into its `format_kind` arm.
    contract = product_model.format_contract
    if contract.format_ids:
        product_data["format_ids"] = list(contract.format_ids)
    if contract.format_options:
        product_data["format_options"] = list(contract.format_options)

    # The anyOf itself, refused once rather than per-arm. "Products MUST carry `format_ids`,
    # `format_options`, or both during the 3.x migration window" -- so the error case is
    # NEITHER arm, not a missing legacy arm. This check used to fire on an absent
    # `format_ids` alone, which made a canonically-authored product unservable: the very
    # shape the 3.2 spec tells new products to use was rejected for lacking the field it
    # tells them to stop using.
    if "format_ids" not in product_data and "format_options" not in product_data:
        raise ValueError(
            f"Product {product_model.product_id} declares no creative format. "
            f"core/product.json requires format_ids, format_options, or both; configure "
            f"canonical format_options on the product or its inventory profile."
        )

    # publisher_properties: Use effective_properties which returns discriminated union format
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

    # Optional fields
    if product_model.measurement:
        product_data["measurement"] = product_model.measurement
    if product_model.creative_policy:
        product_data["creative_policy"] = product_model.creative_policy
    # Note: price_guidance is database metadata, not in AdCP Product schema - omit it
    # Pricing information should be in pricing_options per AdCP spec

    # Filter-related internal fields
    if hasattr(product_model, "countries") and product_model.countries:
        product_data["countries"] = product_model.countries
    # channels: DB stores strings, schema uses MediaChannel enum
    if hasattr(product_model, "channels") and product_model.channels:
        converted_channels = []
        for ch in product_model.channels:
            try:
                converted_channels.append(MediaChannel(ch))
            except ValueError:
                logger.warning("Unknown channel value '%s' in product %s, skipping", ch, product_model.product_id)
        if converted_channels:
            product_data["channels"] = converted_channels

    if product_model.product_card:
        product_data["product_card"] = product_model.product_card
    if product_model.product_card_detailed:
        product_data["product_card_detailed"] = product_model.product_card_detailed
    if product_model.placements:
        # Served as stored: ck_products_placements_explicit makes every stored placement state
        # its kind and mode, so nothing is filled in here.
        product_data["placements"] = product_model.placements
    # core/product.json requires reporting_capabilities; the column is still nullable
    # (salesagent-3cs7o.21 makes it NOT NULL with a backfill). The default is supplied HERE,
    # at the row-to-model edge, never by the wire model: Product inherits the field as
    # required, so a NULL row is completed where the row is read and nowhere else.
    product_data["reporting_capabilities"] = product_model.reporting_capabilities or default_reporting_capabilities()

    # Default is_custom to False if not set
    product_data["is_custom"] = product_model.is_custom if product_model.is_custom else False

    # fields — direct attribute access on typed Mapped[] columns
    if product_model.property_targeting_allowed is not None:
        product_data["property_targeting_allowed"] = product_model.property_targeting_allowed
    if product_model.signal_targeting_allowed is not None:
        product_data["signal_targeting_allowed"] = product_model.signal_targeting_allowed
    if product_model.catalog_match is not None:
        product_data["catalog_match"] = product_model.catalog_match
    if product_model.catalog_types is not None:
        product_data["catalog_types"] = product_model.catalog_types
    if product_model.conversion_tracking is not None:
        product_data["conversion_tracking"] = product_model.conversion_tracking
    if product_model.data_provider_signals is not None:
        product_data["data_provider_signals"] = product_model.data_provider_signals
    if product_model.forecast is not None:
        product_data["forecast"] = product_model.forecast
    # expires_at is a PINNED field (core/product.json /properties/expires_at, 3.1.1) and is
    # emitted on get_products. The strip that hid it is gone; this copy is what puts a
    # stored value on the wire. A NULL column stays absent (exclude_none).
    if product_model.expires_at is not None:
        product_data["expires_at"] = product_model.expires_at

    # Internal fields (not in AdCP spec, but in our extended Product schema)
    # ``effective_implementation_config`` resolves from the inventory profile when one is
    # set and falls back to the product's own column when it is not, so it is the whole
    # answer. It is an unconditional ``@property`` on the ORM model
    # (``database/models.py``:594), and all three callers pass an ORM row, so the two
    # ``hasattr`` fallbacks this replaced could never run: a raw-column branch and a
    # ``None`` branch, both unreachable. Asking a model we declare whether it has an
    # attribute we declared is a data-model smell — and worse than dead here, because
    # ``hasattr`` swallows an ``AttributeError`` raised INSIDE the getter, so a genuine
    # fault in the property would have been masked by silently reading the raw column.
    product_data["implementation_config"] = product_model.effective_implementation_config

    # Principal access control (internal field)
    product_data["allowed_principal_ids"] = getattr(product_model, "allowed_principal_ids", None)

    return Product(**product_data)
