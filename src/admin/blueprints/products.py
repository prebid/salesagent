"""Products management blueprint for admin UI."""

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from adcp.exceptions import ADCPConnectionError, ADCPError, ADCPTimeoutError
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from pydantic import ValidationError
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import HTTPException

from src.admin.form_validation import sanitize_form_data
from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.integrity import resolve_or_write
from src.core.database.models import PersistedMediaBuyStatus, PricingOption, Product, ProductInventoryMapping, Tenant
from src.core.database.product_pricing import get_product_pricing_options
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.database.repositories.principal import PrincipalRepository
from src.core.format_resolver import linked_format_contract
from src.core.format_satisfaction import refuse_outside_common_intersection
from src.core.publisher_format_catalog import PublisherCatalog, format_declarations_type
from src.core.schemas import Format
from src.services.gam_product_config_service import GAMProductConfigService

logger = logging.getLogger(__name__)

# Create Blueprint
products_bp = Blueprint("products", __name__)


def _format_to_dict(fmt: Format) -> dict:
    """Convert a Format object to a frontend-compatible dict.

    Uses library's model_dump() for consistency, keeping nested format_id structure
    so frontend matches backend/library schema.

    Args:
        fmt: Format object (extends adcp library Format)

    Returns:
        Dict with library schema structure (nested format_id)
    """
    # Use library serialization with mode='json' for JSON compatibility
    data = fmt.model_dump(mode="json")

    # Keep format_id as nested object (frontend will access format_id.id)
    # This matches the library schema structure directly

    # Add convenience fields for frontend
    data["agent_url"] = str(fmt.format_id.agent_url)
    data["form_value"] = fmt.get_form_value()

    # Add 'id' field for template compatibility (templates expect format.id not format.format_id.id)
    data["id"] = str(fmt.format_id.id)

    # Add dimensions string for display formats
    dimensions = fmt.get_primary_dimensions()
    if dimensions:
        width, height = dimensions
        data["dimensions"] = f"{width}x{height}"
    elif data.get("dimensions") is None:
        # Try to parse from format_id as fallback
        import re

        format_id_str = str(fmt.format_id.id)
        match = re.search(r"_(\d+)x(\d+)_", format_id_str)
        if match:
            data["dimensions"] = f"{match.group(1)}x{match.group(2)}"

    return data


def _parse_format_entries(formats_parsed: list[dict]) -> list[dict]:
    """Parse format entries from form JSON without validation.

    Extracts agent_url, id, and optional dimension/duration parameters
    from each parsed format dict. Used when validation is skipped
    (creative agent unreachable or returned no formats).
    """
    entries = []
    for fmt in formats_parsed:
        if not fmt.get("agent_url"):
            continue
        format_id = fmt.get("id") or fmt.get("format_id")
        if not format_id:
            continue
        format_entry: dict = {"agent_url": fmt["agent_url"], "id": format_id}
        if fmt.get("width") is not None:
            format_entry["width"] = int(fmt["width"])
        if fmt.get("height") is not None:
            format_entry["height"] = int(fmt["height"])
        if fmt.get("duration_ms") is not None:
            format_entry["duration_ms"] = float(fmt["duration_ms"])
        entries.append(format_entry)
    return entries


def _parse_authored_format_options(raw: str | None, catalog: PublisherCatalog) -> list[dict] | None:
    """The canonical arm of core/product.json's root anyOf, as the operator authored it.

    Validated at AUTHOR time against the pinned discriminated union
    (``format_declarations_type``, read off the DTO that serves the column, so the form
    refuses exactly what ``convert_product_model_to_schema`` would refuse later). An invalid
    declaration does not fail on the row -- it fails in
    ``convert_product_model_to_schema``, which ``get_products`` runs in a loop that
    re-raises, so one mis-authored product answers INTERNAL_ERROR for the whole tenant's
    discovery. The validated models are discarded on purpose: the column stores the
    document and ``product_conversion`` hands that document through untouched, so
    validating here and storing the original is what keeps the two in agreement.

    Returns ``None`` when the operator authored nothing, so an absent arm is recorded as
    NULL rather than as ``[]``. The pin gives ``format_options`` ``minItems: 1``, and the
    deprecated arm is satisfied by its KEY alone -- an empty array asserts a declaration
    that is not there.

    A declaration naming a publisher option in this seller's OWN namespace must name one its
    publisher format catalog declares (*catalog*, read off the tenant by the caller): RFC S3,
    see ``src/core/publisher_format_catalog.py``.
    """
    if not raw or not raw.strip():
        return None
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"format_options is not valid JSON: {e}") from e
    if not document:
        return None
    try:
        format_declarations_type().validate_python(document)
    except ValidationError as e:
        first = e.errors()[0]
        location = "/".join(str(part) for part in first["loc"]) or "format_options"
        raise ValueError(
            f"format_options is not a valid canonical format declaration ({location}: {first['msg']})"
        ) from e
    catalog.refuse_unresolved(document)
    return document


def _refuse_a_product_with_no_format_arm(
    format_ids: Sequence[Any] | None, format_options: Sequence[Any] | None
) -> None:
    """core/product.json's root anyOf, checked once where both arms are visible.

    The pin's ``format_ids`` property says "Products MUST carry ``format_ids``,
    ``format_options``, or both during the 3.x migration window", so the refusable state
    is NEITHER arm -- never a missing legacy arm. It is refused at the authoring surface
    because that is the only place an operator can still fix it: stored, the row passes
    every column CHECK and then takes the tenant's whole product catalog down at
    discovery time.

    STILL TAKES BOTH ARMS, now that only one of them is authorable here. ``format_ids``
    is no longer written by this surface, so on CREATE it is always ``None`` and this
    reduces to "canonical declarations are required". On EDIT it is the arm ALREADY
    STORED, which is exactly what must keep an existing legacy product editable: such a
    product carries no ``format_options`` and authoring one must not be the price of
    changing its name. Narrowing this to ``format_options`` alone would refuse every
    legacy product's first edit.
    """
    if not format_ids and not format_options:
        raise ValueError(
            # No spec version in the sentence: 0d8d26d72 swept version literals out of
            # src/ ("read the spec and SDK versions from the SDK, never from a comment")
            # and `AdCP 3.2` now appears nowhere under src/. An operator does not need the
            # release number to act on this -- they need to know the field is not theirs to
            # fill -- so the whole clause goes rather than half of it.
            "A product must declare at least one creative format: author canonical "
            "format_options, or link an inventory profile that declares them. (format_ids is "
            "deprecated and is no longer authored here; a product that already has one keeps it.)"
        )


def _legacy_format_facts(product) -> dict[str, object]:
    """What the edit form shows, read-only, about the product's legacy named formats.

    Read off ``Product.format_contract``, the same contract ``get_products`` announces, so
    the form and the wire cannot disagree:

    * ``announced_legacy_refs`` -- the ``format_ids`` a legacy buyer receives.
    * ``legacy_refs_derived`` -- True when they are DERIVED from canonical declarations
      (their ``v1_format_ref``), False when they are an authored legacy-only arm.
    * ``inventory_profile_name`` -- the linked profile, when there is one, because a
      profile supplies the contract on the product's behalf and is SHARED: changing it
      changes every product linked to it (salesagent-ym571).
    """
    from src.core.format_resolver import CanonicalFormats, format_display, format_identity_or_none

    contract = product.format_contract
    identities = (format_identity_or_none(entry) for entry in contract.format_ids)
    profile = product.inventory_profile if product.inventory_profile_id else None
    return {
        "announced_legacy_refs": [format_display(identity) for identity in identities if identity is not None],
        "legacy_refs_derived": isinstance(contract, CanonicalFormats),
        "inventory_profile_name": profile.name if profile else None,
    }


def _format_labels(contract) -> list[str]:
    """One display label per format a contract declares, for the admin lists.

    A canonical declaration shows its ``display_name`` when the seller gave one, else its
    ``format_option_id``, else its ``format_kind``; it is not shown as a named format,
    because the projection runs v1 -> v2 only. A legacy-only contract shows its ids.
    """
    from src.core.format_resolver import format_ref_id

    if contract.format_options:
        return [
            str(
                declaration.get("display_name")
                or declaration.get("format_option_id")
                or declaration.get("format_kind")
                or "unknown"
            )
            for declaration in contract.format_options
        ]
    return [format_ref_id(ref) or "unknown" for ref in contract.format_ids]


def _format_id_to_display_name(format_id: str) -> str:
    """Convert a format_id to a friendly display name when format lookup fails.

    Examples:
        "leaderboard_728x90" → "Leaderboard (728x90)"
        "rectangle_300x250" → "Rectangle (300x250)"
        "display_300x250" → "Display (300x250)"
        "video_instream" → "Video Instream"
        "native_card" → "Native Card"
    """
    import re

    # Extract dimensions if present
    size_match = re.search(r"(\d+)x(\d+)", format_id)

    # Remove dimensions and underscores, convert to title case
    base_name = re.sub(r"_?\d+x\d+", "", format_id)
    base_name = base_name.replace("_", " ").title()

    # Add dimensions back if found
    if size_match:
        return f"{base_name} ({size_match.group(0)})"
    else:
        return base_name


def get_creative_formats(
    tenant_id: str | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
    min_width: int | None = None,
    min_height: int | None = None,
    is_responsive: bool | None = None,
    asset_types: list[str] | None = None,
    name_search: str | None = None,
):
    """Get all available creative formats for the product form.

    Returns formats from all registered creative agents (default + tenant-specific).
    Uses CreativeAgentRegistry for dynamic format discovery.

    Args:
        tenant_id: Optional tenant ID for tenant-specific agents
        max_width: Maximum width in pixels (inclusive)
        max_height: Maximum height in pixels (inclusive)
        min_width: Minimum width in pixels (inclusive)
        min_height: Minimum height in pixels (inclusive)
        is_responsive: Filter for responsive formats
        asset_types: Filter by asset types
        name_search: Search by name

    Returns:
        List of format dictionaries for frontend
    """
    from src.core.format_resolver import list_available_formats

    # Get formats from creative agent registry with optional filtering
    try:
        formats = list_available_formats(
            tenant_id=tenant_id,
            max_width=max_width,
            max_height=max_height,
            min_width=min_width,
            min_height=min_height,
            is_responsive=is_responsive,
            asset_types=asset_types,
            name_search=name_search,
        )
    except (asyncio.CancelledError, TimeoutError, ADCPTimeoutError) as e:
        logger.warning(f"Timeout fetching formats from creative agent registry: {e}")
        formats = []  # Return empty list if format fetching fails
    except (ADCPConnectionError, ADCPError) as e:
        logger.warning(f"Failed to connect to creative agent registry: {e}")
        formats = []  # Return empty list if format fetching fails
    except RuntimeError as e:
        logger.warning(f"Runtime error fetching formats (event loop issue): {e}")
        formats = []  # Return empty list if format fetching fails

    logger.info(f"get_creative_formats: Fetched {len(formats)} formats from registry for tenant {tenant_id}")

    formats_list = []
    for idx, fmt in enumerate(formats):
        # Use helper function for consistent serialization
        format_dict = _format_to_dict(fmt)

        # Debug: Log first few formats
        if idx < 5:
            logger.info(
                f"[DEBUG] Format {idx}: {fmt.name} - "
                f"format_id={format_dict['format_id']}, "
                f"dimensions={format_dict.get('dimensions')}"
            )

        # Add duration for video/audio formats from internal requirements field
        if fmt.requirements and "duration" in fmt.requirements:
            format_dict["duration"] = f"{fmt.requirements['duration']}s"
        elif fmt.requirements and "duration_max" in fmt.requirements:
            format_dict["duration"] = f"{fmt.requirements['duration_max']}s"

        formats_list.append(format_dict)

    # Sort by name (type field was removed from Format in the pinned SDK)
    formats_list.sort(key=lambda x: x["name"])

    logger.info(f"get_creative_formats: Returning {len(formats_list)} formatted formats")

    return formats_list


def parse_pricing_options_from_form(form_data: dict) -> list[PricingOption]:
    """Parse pricing options from form data into unpersisted ``PricingOption`` rows.

    Form data uses indexed fields: pricing_model_0, pricing_model_1, etc.
    Indices may be non-contiguous if user removed and re-added pricing options.

    Returns rows, not dicts. A dict of the same seven fields is a second shape for a
    thing the ORM model already types, and it cost three copies of the ``Decimal``
    coercion plus a mapping step at every write site. ``tenant_id`` and ``product_id``
    are left unset: on the create form the product does not exist yet when the form is
    parsed, so the caller stamps them just before ``session.add``.
    """
    pricing_options: list[PricingOption] = []

    # Find all pricing option indices by scanning form keys
    # This handles non-contiguous indices (e.g., 0 removed, only 1 exists)
    indices = set()
    for key in form_data.keys():
        if key.startswith("pricing_model_"):
            try:
                idx = int(key.replace("pricing_model_", ""))
                indices.add(idx)
            except ValueError:
                pass

    # Process each found index in order
    for index in sorted(indices):
        pricing_model_raw = form_data.get(f"pricing_model_{index}")
        if not pricing_model_raw:
            continue

        # Parse pricing model and is_fixed from combined value
        # Guaranteed (fixed): cpm_fixed, flat_rate
        # Non-guaranteed (auction): cpm_auction, vcpm, cpc
        if pricing_model_raw == "cpm_fixed":
            pricing_model = "cpm"
            is_fixed = True
        elif pricing_model_raw == "cpm_auction":
            pricing_model = "cpm"
            is_fixed = False
        elif pricing_model_raw == "flat_rate":
            pricing_model = "flat_rate"
            is_fixed = True
        elif pricing_model_raw == "vcpm":
            pricing_model = "vcpm"
            is_fixed = False  # vCPM is always auction-based
        elif pricing_model_raw == "cpc":
            pricing_model = "cpc"
            is_fixed = False  # CPC is always auction-based
        else:
            # Fallback for any other models (shouldn't happen with current UI)
            pricing_model = pricing_model_raw
            is_fixed = True

        # Parse basic fields
        currency = form_data.get(f"currency_{index}", "USD")

        # Parse rate (for fixed pricing)
        rate = None
        rate_str = form_data.get(f"rate_{index}", "").strip()
        if rate_str:
            try:
                rate = float(rate_str)
            except ValueError:
                raise ValueError(f"Invalid rate value for pricing option {index}")

        # Validate rate is required for fixed pricing
        if is_fixed and rate is None:
            raise ValueError(f"Rate is required for fixed pricing (pricing option {index})")

        # Parse price_guidance (for auction pricing)
        price_guidance = None
        if not is_fixed:
            # Floor price is required for auction
            floor_str = form_data.get(f"floor_{index}", "").strip()
            if not floor_str:
                raise ValueError(f"Floor price is required for auction pricing (pricing option {index})")
            try:
                floor = float(floor_str)
                price_guidance = {"floor": floor}

                # Optional percentiles
                for percentile in ["p25", "p50", "p75", "p90"]:
                    value_str = form_data.get(f"{percentile}_{index}", "").strip()
                    if value_str:
                        try:
                            price_guidance[percentile] = float(value_str)
                        except ValueError:
                            pass
            except ValueError:
                raise ValueError(f"Invalid floor price value for pricing option {index}")

        # Parse min_spend_per_package
        min_spend = None
        min_spend_str = form_data.get(f"min_spend_{index}", "").strip()
        if min_spend_str:
            try:
                min_spend = float(min_spend_str)
            except ValueError:
                pass

        # Parse model-specific parameters
        parameters = None
        if pricing_model == "cpp":
            # CPP parameters
            demographic = form_data.get(f"demographic_{index}", "").strip()
            min_points_str = form_data.get(f"min_points_{index}", "").strip()
            if demographic or min_points_str:
                parameters = {}
                if demographic:
                    parameters["demographic"] = demographic
                if min_points_str:
                    try:
                        parameters["min_points"] = float(min_points_str)
                    except ValueError:
                        pass

        elif pricing_model == "cpv":
            # CPV parameters
            view_threshold_str = form_data.get(f"view_threshold_{index}", "").strip()
            if view_threshold_str:
                try:
                    view_threshold = float(view_threshold_str)
                    if 0 <= view_threshold <= 1:
                        parameters = {"view_threshold": view_threshold}
                except ValueError:
                    pass

        pricing_options.append(
            PricingOption.create(
                pricing_model=pricing_model,
                currency=currency,
                is_fixed=is_fixed,
                rate=Decimal(str(rate)) if rate is not None else None,
                price_guidance=price_guidance,
                parameters=parameters,
                min_spend_per_package=Decimal(str(min_spend)) if min_spend is not None else None,
            )
        )
        index += 1

    return pricing_options


#: Which columns are IDENTITY rather than terms — the one judgement a program cannot
#: derive. ``tenant_id``/``product_id`` say which product the row belongs to, and
#: ``pricing_option_id`` is what a buyer's ``PackageRequest`` names, so re-pricing an
#: option must not rename the entity that buyer selected.
_IDENTITY_COLUMNS = frozenset({"id", "tenant_id", "product_id", "pricing_option_id"})

#: Everything else the model declares. Read off the mapper so a column added to
#: ``PricingOption`` is carried by the edit form without anyone remembering to list it
#: here — a hand-written list of the same names silently stops covering the model.
_FORM_OWNED_COLUMNS = tuple(
    attr.key for attr in inspect(PricingOption).mapper.column_attrs if attr.key not in _IDENTITY_COLUMNS
)


def create_custom_key_inventory_mappings(db_session, tenant_id: str, product_id: str, custom_keys: dict) -> int:
    """Create ProductInventoryMapping entries for custom targeting keys.

    Handles three formats:
    - Groups format: {'groups': [{'criteria': [{'keyId': '123', 'values': ['v1'], 'exclude': False}]}]}
    - Enhanced format: {'include': {'keyId': ['v1']}, 'exclude': {'keyId': ['v2']}, 'operator': 'AND'}
    - Legacy format: {'keyId': 'value'}

    Args:
        db_session: Database session
        tenant_id: Tenant ID
        product_id: Product ID
        custom_keys: Custom targeting configuration in any of the three formats

    Returns:
        Number of mappings created
    """
    mapping_count = 0

    # Groups format (GAM-style nested groups)
    if isinstance(custom_keys, dict) and "groups" in custom_keys:
        for group in custom_keys.get("groups", []):
            for criterion in group.get("criteria", []):
                key_id = criterion.get("keyId")
                is_exclude = criterion.get("exclude", False)
                for value_id in criterion.get("values", []):
                    prefix = "NOT_" if is_exclude else ""
                    mapping = ProductInventoryMapping(
                        tenant_id=tenant_id,
                        product_id=product_id,
                        inventory_type="custom_key",
                        inventory_id=f"{prefix}{key_id}={value_id}",
                        is_primary=False,
                    )
                    db_session.add(mapping)
                    mapping_count += 1

    # Enhanced format (include/exclude)
    elif isinstance(custom_keys, dict) and ("include" in custom_keys or "exclude" in custom_keys):
        for key_id, value_ids in custom_keys.get("include", {}).items():
            for value_id in value_ids:
                mapping = ProductInventoryMapping(
                    tenant_id=tenant_id,
                    product_id=product_id,
                    inventory_type="custom_key",
                    inventory_id=f"{key_id}={value_id}",
                    is_primary=False,
                )
                db_session.add(mapping)
                mapping_count += 1
        for key_id, value_ids in custom_keys.get("exclude", {}).items():
            for value_id in value_ids:
                mapping = ProductInventoryMapping(
                    tenant_id=tenant_id,
                    product_id=product_id,
                    inventory_type="custom_key",
                    inventory_id=f"NOT_{key_id}={value_id}",
                    is_primary=False,
                )
                db_session.add(mapping)
                mapping_count += 1

    # Legacy format ({keyId: value})
    else:
        for key, value in custom_keys.items():
            custom_key_id = f"{key}={value}"
            mapping = ProductInventoryMapping(
                tenant_id=tenant_id,
                product_id=product_id,
                inventory_type="custom_key",
                inventory_id=custom_key_id,
                is_primary=False,
            )
            db_session.add(mapping)
            mapping_count += 1

    return mapping_count


@products_bp.route("/")
@require_tenant_access()
def list_products(tenant_id):
    """List all products for a tenant."""
    try:
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            products = (
                db_session.scalars(
                    select(Product)
                    .options(joinedload(Product.pricing_options))
                    .options(joinedload(Product.inventory_profile))
                    .filter_by(tenant_id=tenant_id)
                    .order_by(Product.name)
                )
                .unique()
                .all()
            )

            # Get inventory details for all products (breakdown by type)
            inventory_details = {}
            for product in products:
                # Get all mappings for this product
                mappings = db_session.scalars(
                    select(ProductInventoryMapping).where(
                        ProductInventoryMapping.tenant_id == tenant_id,
                        ProductInventoryMapping.product_id == product.product_id,
                    )
                ).all()

                # Count by inventory type
                ad_unit_count = sum(1 for m in mappings if m.inventory_type == "ad_unit")
                placement_count = sum(1 for m in mappings if m.inventory_type == "placement")
                custom_key_count = sum(1 for m in mappings if m.inventory_type == "custom_key")

                inventory_details[product.product_id] = {
                    "total": len(mappings),
                    "ad_units": ad_unit_count,
                    "placements": placement_count,
                    "custom_keys": custom_key_count,
                }

            # Convert products to dict format for template
            products_list = []
            for product in products:
                # Use helper function to get pricing options (handles legacy fallback)
                pricing_options_list = get_product_pricing_options(product)

                # What the product is announced with: its canonical declarations, or the
                # legacy arm of a product that has none (salesagent-gi2du).
                resolved_formats = [
                    {"format_id": label, "name": label} for label in _format_labels(product.format_contract)
                ]

                # Get inventory profile info if product uses one
                inventory_profile_dict = None
                if product.inventory_profile:
                    # Generate inventory summary from profile
                    inventory_config = product.inventory_profile.inventory_config or {}
                    ad_units = inventory_config.get("ad_units", [])
                    placements = inventory_config.get("placements", [])

                    summary_parts = []
                    if ad_units:
                        summary_parts.append(f"{len(ad_units)} ad unit{'s' if len(ad_units) != 1 else ''}")
                    if placements:
                        summary_parts.append(f"{len(placements)} placement{'s' if len(placements) != 1 else ''}")

                    inventory_summary = ", ".join(summary_parts) if summary_parts else "No inventory"

                    inventory_profile_dict = {
                        "id": product.inventory_profile.id,
                        "profile_id": product.inventory_profile.profile_id,
                        "name": product.inventory_profile.name,
                        "description": product.inventory_profile.description,
                        "inventory_summary": inventory_summary,
                    }

                product_dict = {
                    "product_id": product.product_id,
                    "name": product.name,
                    "description": product.description,
                    "pricing_options": pricing_options_list,
                    "formats": resolved_formats,
                    "countries": (
                        product.countries
                        if isinstance(product.countries, list)
                        else json.loads(product.countries)
                        if product.countries
                        else []
                    ),
                    "implementation_config": (
                        product.implementation_config
                        if isinstance(product.implementation_config, dict)
                        else json.loads(product.implementation_config)
                        if product.implementation_config
                        else {}
                    ),
                    "created_at": getattr(product, "created_at", None),
                    "inventory_details": inventory_details.get(
                        product.product_id,
                        {
                            "total": 0,
                            "ad_units": 0,
                            "placements": 0,
                            "custom_keys": 0,
                        },
                    ),
                    "inventory_profile": inventory_profile_dict,
                    # Dynamic product fields
                    "is_dynamic": getattr(product, "is_dynamic", False),
                    "is_dynamic_variant": getattr(product, "is_dynamic_variant", False),
                    "activation_key": getattr(product, "activation_key", None),
                    "product_card": getattr(product, "product_card", None),
                }
                products_list.append(product_dict)
            return render_template(
                "products.html",
                tenant=tenant,
                tenant_id=tenant_id,
                products=products_list,
            )

    except Exception as e:
        logger.error(f"Error loading products: {e}", exc_info=True)
        flash("Error loading products", "error")
        return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))


def _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data=None):
    """Helper to render add product form with optional preserved form data.

    Args:
        tenant_id: Tenant ID
        tenant: Tenant object
        adapter_type: Adapter type (e.g., "google_ad_manager")
        currencies: List of currency codes
        form_data: Optional dict of form data to preserve on validation errors

    Returns:
        Rendered template response
    """
    from src.core.database.models import (
        AuthorizedProperty,
        GAMInventory,
        InventoryProfile,
        PropertyTag,
        SignalsAgent,
    )

    with get_db_session() as db_session:
        # Load authorized properties (all statuses - verification can take time)
        authorized_properties_query = db_session.scalars(
            select(AuthorizedProperty).filter_by(tenant_id=tenant_id).order_by(AuthorizedProperty.name)
        ).all()
        properties_list = [
            {
                "property_id": p.property_id,
                "name": p.name,
                "property_type": p.property_type,
                "tags": p.tags or [],
                "verification_status": p.verification_status,
                "publisher_domain": p.publisher_domain,
            }
            for p in authorized_properties_query
        ]

        # Load property tags
        property_tags = db_session.scalars(
            select(PropertyTag).filter_by(tenant_id=tenant_id).order_by(PropertyTag.name)
        ).all()

        # Load principals for access control dropdown
        principals = PrincipalRepository(db_session, tenant_id).list_all()
        principals_list = [{"principal_id": p.principal_id, "name": p.name} for p in principals]

        if adapter_type == "google_ad_manager":
            # Check if inventory has been synced
            inventory_count = db_session.scalar(
                select(func.count()).select_from(GAMInventory).filter_by(tenant_id=tenant_id)
            )
            inventory_synced = inventory_count > 0

            # Get signals agents
            signals_agents = db_session.scalars(select(SignalsAgent).filter_by(tenant_id=tenant_id, enabled=True)).all()

            # Get inventory profiles
            inventory_profiles = db_session.scalars(
                select(InventoryProfile).filter_by(tenant_id=tenant_id).order_by(InventoryProfile.name)
            ).all()

            return render_template(
                "add_product_gam.html",
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                tenant=tenant,
                inventory_synced=inventory_synced,
                formats=get_creative_formats(tenant_id=tenant_id),
                authorized_properties=properties_list,
                property_tags=property_tags,
                currencies=currencies,
                signals_agents=signals_agents,
                inventory_profiles=inventory_profiles,
                principals=principals_list,
                form_data=form_data,  # Preserve form data on error
            )
        else:
            # For Mock and other adapters - use unified template
            formats = get_creative_formats(tenant_id=tenant_id)
            return render_template(
                "add_product.html",
                tenant_id=tenant_id,
                tenant=tenant,
                adapter_type=adapter_type,
                formats=formats,
                authorized_properties=properties_list,
                property_tags=property_tags,
                currencies=currencies,
                principals=principals_list,
                form_data=form_data,  # Preserve form data on error
            )


@products_bp.route("/add", methods=["GET", "POST"])
@log_admin_action("add_product")
@require_tenant_access()
def add_product(tenant_id):
    """Add a new product - adapter-specific form."""
    # Get tenant's adapter type and currencies
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("products.list_products", tenant_id=tenant_id))

        adapter_type = tenant.ad_server or "mock"

        # Get tenant's supported currencies from currency_limits
        from src.core.database.models import CurrencyLimit

        currency_limits = db_session.scalars(select(CurrencyLimit).filter_by(tenant_id=tenant_id)).all()
        currencies = [limit.currency_code for limit in currency_limits]
        # Default to USD if no currencies configured
        if not currencies:
            currencies = ["USD"]

    if request.method == "POST":
        try:
            # Sanitize form data
            form_data = sanitize_form_data(request.form.to_dict())

            # Validate required fields
            if not form_data.get("name"):
                flash("Product name is required", "error")
                return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)

            with get_db_session() as db_session:
                # format_ids IS NOT AUTHORED HERE. The pin deprecates it in AdCP 3.2,
                # removes it in 4.0, and says outright "Do not author a new product from
                # format_ids alone"; this epic's charter keeps format_id an INPUT only. So
                # a product created here carries NO legacy arm and must author the
                # canonical one, which `_refuse_a_product_with_no_format_arm` now enforces
                # by reducing to exactly that. The column still EXISTS and existing rows
                # keep their value -- not authored is not the same as dropped, and
                # dropping it would be a migration this stage does not imply.

                # The inventory profile, resolved FIRST because it decides the product's
                # format contract: a linked profile supplies the declarations
                # (``Product.format_contract``), so whether this product still has to author
                # its own cannot be answered without it.
                profile = None
                inventory_profile_id = form_data.get("inventory_profile_id", "").strip()
                if inventory_profile_id:
                    try:
                        profile_id = int(inventory_profile_id)
                    except (ValueError, TypeError):
                        flash("Invalid inventory profile ID", "error")
                        return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)
                    # SECURITY: Verify profile belongs to this tenant
                    from src.core.database.models import InventoryProfile

                    profile = db_session.scalars(select(InventoryProfile).filter_by(id=profile_id)).first()
                    if not profile or profile.tenant_id != tenant_id:
                        flash(
                            "Invalid inventory profile - profile not found or does not belong to this tenant",
                            "error",
                        )
                        return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)

                # Parse the CANONICAL arm the pin tells new products to author, and refuse
                # only the state the anyOf actually refuses: neither arm -- read off the
                # contract the product will be SERVED, so a product linked to a profile
                # that declares formats is not made to author declarations its profile
                # then overrides.
                try:
                    format_options = _parse_authored_format_options(
                        form_data.get("format_options"), PublisherCatalog.of(tenant)
                    )
                    # ``None`` for the legacy arm, literally: a product created here HAS no
                    # legacy arm, and its canonical declarations come from the product or
                    # its profile, never from a legacy-only profile's ``format_ids``.
                    contract = linked_format_contract(profile=profile, format_options=format_options, format_ids=None)
                    _refuse_a_product_with_no_format_arm(None, contract.format_options)
                except ValueError as e:
                    flash(str(e), "error")
                    return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)

                # Parse countries - from multi-select
                countries_list = request.form.getlist("countries")
                # Only set countries if some were selected; None means all countries
                countries = countries_list if countries_list and "ALL" not in countries_list else None

                # Parse and create pricing options (AdCP PR #88)
                try:
                    pricing_options_data = parse_pricing_options_from_form(form_data)
                except ValueError as e:
                    flash(str(e), "error")
                    return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)

                # CRITICAL: Products MUST have at least one pricing option
                if not pricing_options_data or len(pricing_options_data) == 0:
                    flash("Product must have at least one pricing option", "error")
                    return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)

                # Derive delivery_type from first pricing option for implementation_config
                delivery_type = "guaranteed"  # Default

                if pricing_options_data and len(pricing_options_data) > 0:
                    first_option = pricing_options_data[0]
                    # Determine delivery_type based on is_fixed
                    if first_option.is_fixed:
                        delivery_type = "guaranteed"
                    else:
                        delivery_type = "non_guaranteed"

                # Build implementation config based on adapter type
                implementation_config = {}
                if adapter_type == "google_ad_manager":
                    # Parse GAM-specific fields from unified form
                    gam_config_service = GAMProductConfigService()
                    # ``None`` for the legacy arm: a product created here has none. The
                    # seeder takes ``format_options`` so it can derive its placeholder from
                    # the canonical declarations (cd0d9921f) the product will be served --
                    # its profile's when it links one.
                    base_config = gam_config_service.generate_default_config(
                        delivery_type, None, format_options=contract.format_options
                    )

                    # Add ad unit/placement targeting if provided
                    ad_unit_ids = form_data.get("targeted_ad_unit_ids", "").strip()
                    validated_ad_unit_ids = []
                    if ad_unit_ids:
                        # Parse comma-separated IDs
                        id_list = [id.strip() for id in ad_unit_ids.split(",") if id.strip()]

                        # Validate that all IDs are numeric (GAM requires numeric IDs)
                        invalid_ids = [id for id in id_list if not id.isdigit()]
                        if invalid_ids:
                            flash(
                                f"Invalid ad unit IDs: {', '.join(invalid_ids)}. "
                                f"Ad unit IDs must be numeric (e.g., '23312403859'). "
                                f"Use 'Browse Ad Units' to select valid ad units.",
                                "error",
                            )
                            # Redirect to form instead of re-rendering to avoid missing context
                            return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)

                        # Validate ad unit IDs exist in inventory
                        from src.core.database.models import GAMInventory

                        existing_ad_units = db_session.scalars(
                            select(GAMInventory).filter(
                                GAMInventory.tenant_id == tenant_id,
                                GAMInventory.inventory_type == "ad_unit",
                                GAMInventory.inventory_id.in_(id_list),
                            )
                        ).all()

                        existing_ids = {unit.inventory_id for unit in existing_ad_units}
                        missing_ids = set(id_list) - existing_ids

                        if missing_ids:
                            flash(
                                f"Ad unit IDs not found in synced inventory: {', '.join(missing_ids)}. "
                                f"Please sync inventory first or use existing ad unit IDs.",
                                "warning",
                            )
                            # Continue anyway - they might be valid in GAM but not synced yet

                        validated_ad_unit_ids = id_list
                        base_config["targeted_ad_unit_ids"] = id_list

                    placement_ids = form_data.get("targeted_placement_ids", "").strip()
                    validated_placement_ids = []
                    if placement_ids:
                        id_list = [id.strip() for id in placement_ids.split(",") if id.strip()]

                        # Validate placement IDs exist in inventory
                        from src.core.database.models import GAMInventory

                        existing_placements = db_session.scalars(
                            select(GAMInventory).filter(
                                GAMInventory.tenant_id == tenant_id,
                                GAMInventory.inventory_type == "placement",
                                GAMInventory.inventory_id.in_(id_list),
                            )
                        ).all()

                        existing_ids = {p.inventory_id for p in existing_placements}
                        missing_ids = set(id_list) - existing_ids

                        if missing_ids:
                            flash(
                                f"Placement IDs not found in synced inventory: {', '.join(missing_ids)}. "
                                f"Please sync inventory first or use existing placement IDs.",
                                "warning",
                            )
                            # Continue anyway - they might be valid in GAM but not synced yet

                        validated_placement_ids = id_list
                        base_config["targeted_placement_ids"] = id_list

                    base_config["include_descendants"] = form_data.get("include_descendants") == "on"

                    # Add GAM-specific settings
                    if form_data.get("line_item_type"):
                        base_config["line_item_type"] = form_data["line_item_type"]
                    if form_data.get("priority"):
                        base_config["priority"] = int(form_data["priority"])

                    implementation_config = base_config
                else:
                    # For other adapters, use simple config
                    gam_config_service = GAMProductConfigService()
                    # ``None`` for the legacy arm, because a product created here has no
                    # legacy arm to hand over. ``generate_default_config`` declares
                    # ``formats`` optional and takes ``format_options`` precisely so the
                    # seeder can derive its sizes from the canonical declarations
                    # (cd0d9921f) the product will be served -- its profile's when it links one.
                    implementation_config = gam_config_service.generate_default_config(
                        delivery_type, None, format_options=contract.format_options
                    )

                # Parse targeting template from form (includes custom targeting key-value pairs)
                targeting_template_json = form_data.get("targeting_template", "{}")
                try:
                    targeting_template = json.loads(targeting_template_json) if targeting_template_json else {}
                except json.JSONDecodeError:
                    targeting_template = {}

                # If targeting template has key_value_pairs, copy to implementation_config for GAM
                if targeting_template.get("key_value_pairs"):
                    if "custom_targeting_keys" not in implementation_config:
                        implementation_config["custom_targeting_keys"] = {}
                    # Handle different targeting formats
                    kv_pairs = targeting_template["key_value_pairs"]
                    if isinstance(kv_pairs, dict) and "groups" in kv_pairs:
                        # Groups format (GAM-style nested) - pass through directly
                        implementation_config["custom_targeting_keys"] = kv_pairs
                    elif isinstance(kv_pairs, dict) and ("include" in kv_pairs or "exclude" in kv_pairs):
                        # Enhanced format - pass through directly
                        implementation_config["custom_targeting_keys"] = kv_pairs
                    else:
                        # Legacy format - merge as before
                        implementation_config["custom_targeting_keys"].update(kv_pairs)

                # Build product kwargs, excluding None values for JSON fields that have database constraints
                product_kwargs = {
                    "product_id": form_data.get("product_id") or f"prod_{uuid.uuid4().hex[:8]}",
                    "tenant_id": tenant_id,
                    "name": form_data["name"],
                    "description": form_data.get("description", ""),
                    # The two arms of core/product.json's root anyOf, each recorded as
                    # authored. NULL rather than [] for an arm the operator did not
                    # author: the column is nullable precisely so an unauthored arm is
                    # absent rather than present-and-empty.
                    "format_options": format_options,
                    "delivery_type": delivery_type,
                    "targeting_template": targeting_template,
                    "implementation_config": implementation_config,
                }

                # Inventory profile association (optional), resolved and checked above. The
                # relationship too, so ``Product.format_contract`` reads the profile before the
                # row is flushed.
                if profile is not None:
                    product_kwargs["inventory_profile_id"] = profile.id
                    product_kwargs["inventory_profile"] = profile

                # Only add countries if explicitly set
                if countries is not None:
                    product_kwargs["countries"] = countries

                # Handle channels field (advertising channels)
                channels = request.form.getlist("channels")
                if channels:
                    product_kwargs["channels"] = channels

                # Handle principal access control (allowed_principal_ids)
                # Empty list or no selection means visible to all (default)
                allowed_principals = request.form.getlist("allowed_principal_ids")
                if allowed_principals:
                    product_kwargs["allowed_principal_ids"] = allowed_principals

                # Handle product detail fields (AdCP compliance)
                # Delivery measurement (REQUIRED per AdCP spec)
                delivery_measurement_provider = form_data.get("delivery_measurement_provider", "").strip()
                delivery_measurement_notes = form_data.get("delivery_measurement_notes", "").strip()
                if delivery_measurement_provider:
                    product_kwargs["delivery_measurement"] = {
                        "provider": delivery_measurement_provider,
                    }
                    if delivery_measurement_notes:
                        product_kwargs["delivery_measurement"]["notes"] = delivery_measurement_notes

                # Product image for card generation (optional)
                product_image_url = form_data.get("product_image_url", "").strip()
                if product_image_url:
                    # Auto-generate product card from image and product data
                    product_kwargs["product_card"] = {
                        "format_id": {
                            "agent_url": "https://creative.adcontextprotocol.org/",
                            "id": "product_card_standard",
                        },
                        "manifest": {
                            "product_image": product_image_url,
                            "product_name": form_data["name"],
                            "product_description": form_data.get("description", ""),
                            "delivery_type": delivery_type,
                        },
                    }
                    # Add pricing info to manifest if available
                    if pricing_options_data and len(pricing_options_data) > 0:
                        first_option = pricing_options_data[0]
                        product_kwargs["product_card"]["manifest"]["pricing_model"] = first_option.pricing_model
                        # ``fixed_price`` is what this branch used to read, and the form
                        # parser has never produced that key — so a created product's card
                        # never carried an amount. The column is ``rate``, as the edit path
                        # below already read.
                        if first_option.is_fixed and first_option.rate:
                            product_kwargs["product_card"]["manifest"]["pricing_amount"] = str(first_option.rate)
                            product_kwargs["product_card"]["manifest"]["pricing_currency"] = first_option.currency

                # Handle property authorization (AdCP requirement)
                # Default to empty property_tags if not specified (satisfies DB constraint)
                property_mode = form_data.get("property_mode", "tags")
                if property_mode == "tags":
                    # Get selected property tags (format: "domain:tag")
                    selected_tags = request.form.getlist("selected_property_tags")

                    if not selected_tags:
                        flash("Please select at least one property tag", "error")
                        return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)

                    # Parse domain:tag pairs and group by publisher_domain
                    import re
                    from collections import defaultdict

                    tags_by_domain: dict[str, list[str]] = defaultdict(list)
                    tag_pattern = re.compile(r"^[a-z0-9_]+$")

                    for selection in selected_tags:
                        if ":" not in selection:
                            flash(f"Invalid tag selection format: {selection}", "error")
                            return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)

                        domain, tag = selection.split(":", 1)

                        # Validate tag format
                        if not tag_pattern.match(tag):
                            flash(f"Invalid tag '{tag}': use only lowercase letters, numbers, and underscores", "error")
                            return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)

                        if tag not in tags_by_domain[domain]:
                            tags_by_domain[domain].append(tag)

                    # Validate that tags exist for properties from these publishers
                    from src.core.database.models import AuthorizedProperty

                    for domain, tags in tags_by_domain.items():
                        # Check that properties with these tags exist for this publisher
                        props_with_tags = db_session.scalars(
                            select(AuthorizedProperty).filter(
                                AuthorizedProperty.tenant_id == tenant_id,
                                AuthorizedProperty.publisher_domain == domain,
                            )
                        ).all()

                        available_tags = set()
                        for prop in props_with_tags:
                            if prop.tags:
                                available_tags.update(prop.tags)

                        missing_tags = set(tags) - available_tags
                        if missing_tags:
                            flash(
                                f"Tags not found for publisher {domain}: {', '.join(missing_tags)}",
                                "error",
                            )
                            return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)

                    # Build discriminated union format
                    publisher_properties = []
                    for domain, tags in tags_by_domain.items():
                        publisher_properties.append(
                            {
                                "publisher_domain": domain,
                                "property_tags": tags,
                                "selection_type": "by_tag",
                            }
                        )

                    # Store in the properties field (supports full publisher_properties structure)
                    product_kwargs["properties"] = publisher_properties
                elif property_mode == "property_ids":
                    # Get selected property IDs and store in AdCP discriminated union format
                    # grouped by publisher_domain
                    property_ids_list = request.form.getlist("selected_property_ids")

                    if not property_ids_list:
                        flash("Please select at least one property", "error")
                        return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)

                    from src.core.database.models import AuthorizedProperty

                    # Query by property_id (string), not integer id
                    properties = db_session.scalars(
                        select(AuthorizedProperty).filter(
                            AuthorizedProperty.property_id.in_(property_ids_list),
                            AuthorizedProperty.tenant_id == tenant_id,
                        )
                    ).all()

                    # Verify all requested IDs were found (prevent TOCTOU)
                    if len(properties) != len(property_ids_list):
                        flash("One or more selected properties not found or not authorized", "error")
                        return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)

                    # Group property_ids by publisher_domain for correct AdCP format
                    from collections import defaultdict

                    properties_by_domain: dict[str, list[str]] = defaultdict(list)
                    for prop in properties:
                        properties_by_domain[prop.publisher_domain].append(prop.property_id)

                    # Build discriminated union format
                    publisher_properties = []
                    for domain, prop_ids in properties_by_domain.items():
                        publisher_properties.append(
                            {
                                "publisher_domain": domain,
                                "property_ids": prop_ids,
                                "selection_type": "by_id",
                            }
                        )

                    # Store in the properties field (supports full publisher_properties structure)
                    product_kwargs["properties"] = publisher_properties

                elif property_mode == "full":
                    # Get selected property IDs and load full property objects (legacy mode)
                    property_ids_list = request.form.getlist("full_property_ids")

                    if not property_ids_list:
                        # No properties selected, default to empty property_tags to satisfy DB constraint
                        product_kwargs["property_tags"] = []
                    else:
                        from src.core.database.models import AuthorizedProperty

                        # Query by property_id (string), not integer id
                        properties = db_session.scalars(
                            select(AuthorizedProperty).filter(
                                AuthorizedProperty.property_id.in_(property_ids_list),
                                AuthorizedProperty.tenant_id == tenant_id,
                            )
                        ).all()

                        # Verify all requested IDs were found (prevent TOCTOU)
                        if len(properties) != len(property_ids_list):
                            flash("One or more selected properties not found or not authorized", "error")
                            return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)

                        if properties:
                            # Convert to dict format for JSONB storage (legacy format)
                            properties_data = []
                            for prop in properties:
                                prop_dict = {
                                    "property_type": prop.property_type,
                                    "name": prop.name,
                                    "identifiers": prop.identifiers or [],
                                    "tags": prop.tags or [],
                                    "publisher_domain": prop.publisher_domain,
                                }
                                properties_data.append(prop_dict)
                            product_kwargs["properties"] = properties_data
                        else:
                            # No properties found, default to empty property_tags to satisfy DB constraint
                            product_kwargs["property_tags"] = []

                # Ensure either properties or property_tags is set (DB constraint requirement)
                if "properties" not in product_kwargs and "property_tags" not in product_kwargs:
                    # Default to empty property_tags list if neither was set
                    product_kwargs["property_tags"] = []

                # Handle dynamic product fields
                is_dynamic = form_data.get("is_dynamic") in [True, "true", "on", 1, "1"]
                if is_dynamic:
                    product_kwargs["is_dynamic"] = True

                    # Handle signals agent selection (radio buttons: "all" vs "specific")
                    signals_agent_selection = form_data.get("signals_agent_selection", "all")
                    if signals_agent_selection == "specific":
                        # Get specific signals agent IDs (multi-select)
                        signals_agent_ids = request.form.getlist("signals_agent_ids")
                        if signals_agent_ids:
                            product_kwargs["signals_agent_ids"] = signals_agent_ids
                        else:
                            # User selected "specific" but didn't choose any agents - error
                            flash("Please select at least one signals agent", "error")
                            return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)
                    else:
                        # "all" selected - set signals_agent_ids to None or empty list to indicate "use all"
                        product_kwargs["signals_agent_ids"] = None

                    # Handle variant naming pattern (radio buttons)
                    variant_name_pattern = form_data.get("variant_name_pattern", "default")
                    if variant_name_pattern == "signal_only":
                        product_kwargs["variant_name_template"] = "{{signal.name}}"
                    elif variant_name_pattern == "custom":
                        variant_name_template = form_data.get("variant_name_template", "").strip()
                        if variant_name_template:
                            product_kwargs["variant_name_template"] = variant_name_template
                    # else: default pattern (None) - uses "Product Name - Signal Name"

                    # Handle append signal description checkbox
                    append_signal_description = form_data.get("append_signal_description") == "on"
                    if not append_signal_description:
                        # User unchecked - don't append signal description (empty template means no auto-append)
                        product_kwargs["variant_description_template"] = ""
                    # else: None (default behavior - auto-append signal description)

                    # Get max signals
                    max_signals = form_data.get("max_signals", "5")
                    try:
                        product_kwargs["max_signals"] = int(max_signals)
                    except ValueError:
                        product_kwargs["max_signals"] = 5

                    # Get variant TTL days (optional)
                    variant_ttl_days = form_data.get("variant_ttl_days", "").strip()
                    if variant_ttl_days:
                        try:
                            product_kwargs["variant_ttl_days"] = int(variant_ttl_days)
                        except ValueError:
                            flash(f"Invalid variant TTL days: '{variant_ttl_days}' is not a number", "error")
                            return _render_add_product_form(tenant_id, form_data=form_data)

                # Create product with correct fields matching the Product model
                product = Product(**product_kwargs)
                db_session.add(product)
                # The contract the product is SERVED, whichever arm supplies it: a linked
                # profile announces its declarations on the product's behalf, and those were
                # checked against the catalog when the profile was authored, not since.
                try:
                    PublisherCatalog.of(tenant).refuse_unresolved(product.format_contract.format_options)
                    refuse_outside_common_intersection(product)
                except ValueError as e:
                    db_session.rollback()
                    flash(str(e), "error")
                    return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data)
                db_session.flush()  # Flush to get product ID before creating pricing options

                # Create pricing options (already parsed above)
                if pricing_options_data:
                    logger.info(
                        f"Creating {len(pricing_options_data)} pricing options for product {product.product_id}"
                    )
                    for pricing_option in pricing_options_data:
                        pricing_option.tenant_id = tenant_id
                        pricing_option.product_id = product.product_id
                        db_session.add(pricing_option)

                # Create inventory mappings for GAM ad units and placements
                if adapter_type == "google_ad_manager":
                    # Save ad unit mappings
                    if validated_ad_unit_ids:
                        logger.info(
                            f"Creating {len(validated_ad_unit_ids)} ad unit mappings for product {product.product_id}"
                        )
                        for idx, ad_unit_id in enumerate(validated_ad_unit_ids):
                            mapping = ProductInventoryMapping(
                                tenant_id=tenant_id,
                                product_id=product.product_id,
                                inventory_type="ad_unit",
                                inventory_id=ad_unit_id,
                                is_primary=(idx == 0),  # First ad unit is primary
                            )
                            db_session.add(mapping)

                    # Save placement mappings
                    if validated_placement_ids:
                        logger.info(
                            f"Creating {len(validated_placement_ids)} placement mappings for product {product.product_id}"
                        )
                        for idx, placement_id in enumerate(validated_placement_ids):
                            mapping = ProductInventoryMapping(
                                tenant_id=tenant_id,
                                product_id=product.product_id,
                                inventory_type="placement",
                                inventory_id=placement_id,
                                is_primary=(idx == 0),  # First placement is primary
                            )
                            db_session.add(mapping)

                    # Save custom targeting key mappings
                    if implementation_config.get("custom_targeting_keys"):
                        custom_keys = implementation_config["custom_targeting_keys"]
                        mapping_count = create_custom_key_inventory_mappings(
                            db_session, tenant_id, product.product_id, custom_keys
                        )
                        logger.info(
                            f"Created {mapping_count} custom targeting key mappings for product {product.product_id}"
                        )

                db_session.commit()

                flash(f"Product '{product.name}' created successfully!", "success")
                # Redirect to products list
                return redirect(url_for("products.list_products", tenant_id=tenant_id))

        except HTTPException:
            # Werkzeug's own control flow, not a failure to report -- see the twin handler at
            # the end of ``edit_product`` for why this is re-raised rather than rendered.
            raise
        except Exception:
            # Says nothing to the operator about their input, for the same reason as
            # ``edit_product``'s handler: every failure this function CAN act on is caught by
            # one of its eight typed handlers, which re-render the form with a message. An
            # exception arriving here is a defect in this code or an outage beneath it, and
            # flashing ``str(e)`` asserted that the operator caused it.
            logger.error(f"Unhandled error creating product for tenant {tenant_id}", exc_info=True)
            abort(500)

    # GET request - show adapter-specific form (use helper with no form_data)
    return _render_add_product_form(tenant_id, tenant, adapter_type, currencies, form_data=None)


@products_bp.route("/<product_id>/edit", methods=["GET", "POST"])
@log_admin_action("edit_product")
@require_tenant_access()
def edit_product(tenant_id, product_id):
    """Edit an existing product."""
    from sqlalchemy import select

    # Get tenant's adapter type and currencies
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("products.list_products", tenant_id=tenant_id))

        adapter_type = tenant.ad_server or "mock"

        # Get tenant's supported currencies from currency_limits
        from src.core.database.models import CurrencyLimit

        currency_limits = db_session.scalars(select(CurrencyLimit).filter_by(tenant_id=tenant_id)).all()
        currencies = [limit.currency_code for limit in currency_limits]
        # Default to USD if no currencies configured
        if not currencies:
            currencies = ["USD"]

    # The canonical arm, parsed in the same pre-session window and for the same reason.
    # Presence of the KEY is the operator's intent: the editor is always submitted, so a
    # blank one clears the arm, while a POST that omits the field entirely leaves the
    # stored declaration alone.
    format_options_submitted = "format_options" in request.form
    authored_format_options = None
    if request.method == "POST":
        if format_options_submitted:
            try:
                authored_format_options = _parse_authored_format_options(
                    request.form.get("format_options"), PublisherCatalog.of(tenant)
                )
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("products.edit_product", tenant_id=tenant_id, product_id=product_id))

        # The "No valid formats selected" refusal that used to stand here was a per-arm
        # check: an empty legacy arm is only an error when the canonical arm is empty too,
        # which is what _refuse_a_product_with_no_format_arm says. It cannot be decided
        # here -- an edit that submits one arm leaves the other at its stored value -- so
        # the anyOf is checked below, once both arms carry their post-edit values.

    try:
        with get_db_session() as db_session:
            product = db_session.scalars(select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)).first()
            if not product:
                flash("Product not found", "error")
                return redirect(url_for("products.list_products", tenant_id=tenant_id))

            if request.method == "POST":
                # Sanitize form data
                form_data = sanitize_form_data(request.form.to_dict())

                # Update basic fields
                product.name = form_data.get("name", product.name)
                product.description = form_data.get("description", product.description)

                # Handle inventory profile association (optional)
                # Check for inventory_profile_id directly (no need for inventory_mode radio button)
                inventory_profile_id = form_data.get("inventory_profile_id", "").strip()
                if inventory_profile_id:
                    try:
                        profile_id = int(inventory_profile_id)
                        # SECURITY: Verify profile belongs to this tenant
                        from src.core.database.models import InventoryProfile

                        profile_stmt = select(InventoryProfile).filter_by(id=profile_id)
                        profile = db_session.scalars(profile_stmt).first()
                        if not profile or profile.tenant_id != tenant_id:
                            flash(
                                "Invalid inventory profile - profile not found or does not belong to this tenant",
                                "error",
                            )
                            return redirect(
                                url_for("products.edit_product", tenant_id=tenant_id, product_id=product_id)
                            )
                        # The relationship too, so ``Product.format_contract`` reads the new link
                        # before the row is flushed.
                        product.inventory_profile_id = profile_id
                        product.inventory_profile = profile
                    except (ValueError, TypeError):
                        flash("Invalid inventory profile ID", "error")
                        return redirect(url_for("products.edit_product", tenant_id=tenant_id, product_id=product_id))
                else:
                    # No profile selected - clear inventory profile association
                    product.inventory_profile_id = None
                    product.inventory_profile = None

                # Apply the canonical arm (validated above, outside the session).
                #
                # format_ids is not authored here. A legacy-only product keeps it as stored, so
                # an edit cannot drop its only format declaration; once the product authors
                # canonical declarations the stored column is retired, because its legacy arm
                # is then derived from them (`Product.format_contract`) and a column kept
                # beside them would be the "second authored catalog" canonical-formats.mdx's
                # producer rules forbid.
                if format_options_submitted:
                    product.format_options = authored_format_options
                    if authored_format_options:
                        product.format_ids = None
                    from sqlalchemy.orm import attributes

                    attributes.flag_modified(product, "format_options")

                # The anyOf, and the served contract's own-namespace options, now that both arms
                # and the profile link carry their post-edit values: the contract the product
                # will be served, so a linked profile's declarations count.
                try:
                    contract = product.format_contract
                    _refuse_a_product_with_no_format_arm(list(contract.format_ids), contract.format_options)
                    PublisherCatalog.of(tenant).refuse_unresolved(contract.format_options)
                except ValueError as e:
                    flash(str(e), "error")
                    return redirect(url_for("products.edit_product", tenant_id=tenant_id, product_id=product_id))

                # Parse countries - from multi-select
                countries_list = request.form.getlist("countries")
                if countries_list and "ALL" not in countries_list:
                    product.countries = countries_list
                    from sqlalchemy.orm import attributes

                    attributes.flag_modified(product, "countries")
                else:
                    product.countries = None
                    from sqlalchemy.orm import attributes

                    attributes.flag_modified(product, "countries")

                # Handle channels field (advertising channels)
                from sqlalchemy.orm import attributes

                channels = request.form.getlist("channels")
                if channels:
                    product.channels = channels
                else:
                    product.channels = None
                attributes.flag_modified(product, "channels")

                # Handle principal access control (allowed_principal_ids)
                # Empty list or no selection means visible to all (default)
                from sqlalchemy.orm import attributes

                allowed_principals = request.form.getlist("allowed_principal_ids")
                if allowed_principals:
                    product.allowed_principal_ids = allowed_principals
                else:
                    product.allowed_principal_ids = None
                attributes.flag_modified(product, "allowed_principal_ids")

                # Handle publisher properties (AdCP requirement)
                property_mode = form_data.get("property_mode", "tags")
                if property_mode == "tags":
                    # Get selected property tags (format: "domain:tag")
                    selected_tags = request.form.getlist("selected_property_tags")
                    if selected_tags:
                        import re
                        from collections import defaultdict

                        tags_by_domain: dict[str, list[str]] = defaultdict(list)
                        tag_pattern = re.compile(r"^[a-z0-9_]+$")

                        for selection in selected_tags:
                            if ":" in selection:
                                domain, tag = selection.split(":", 1)
                                if tag_pattern.match(tag) and tag not in tags_by_domain[domain]:
                                    tags_by_domain[domain].append(tag)

                        # Build AdCP discriminated union format
                        publisher_properties = []
                        for domain, tags in tags_by_domain.items():
                            publisher_properties.append(
                                {
                                    "publisher_domain": domain,
                                    "property_tags": tags,
                                    "selection_type": "by_tag",
                                }
                            )

                        if publisher_properties:
                            product.properties = publisher_properties
                            product.property_tags = None
                            product.property_ids = None
                            attributes.flag_modified(product, "properties")

                elif property_mode == "property_ids":
                    # Get selected property IDs
                    property_ids_list = request.form.getlist("selected_property_ids")
                    if property_ids_list:
                        from collections import defaultdict

                        from src.core.database.models import AuthorizedProperty

                        # Query properties to get their publisher_domain
                        properties = db_session.scalars(
                            select(AuthorizedProperty).filter(
                                AuthorizedProperty.property_id.in_(property_ids_list),
                                AuthorizedProperty.tenant_id == tenant_id,
                            )
                        ).all()

                        # Group by publisher_domain
                        properties_by_domain: dict[str, list[str]] = defaultdict(list)
                        for prop in properties:
                            properties_by_domain[prop.publisher_domain].append(prop.property_id)

                        # Build AdCP discriminated union format
                        publisher_properties = []
                        for domain, prop_ids in properties_by_domain.items():
                            publisher_properties.append(
                                {
                                    "publisher_domain": domain,
                                    "property_ids": prop_ids,
                                    "selection_type": "by_id",
                                }
                            )

                        if publisher_properties:
                            product.properties = publisher_properties
                            product.property_tags = None
                            product.property_ids = None
                            attributes.flag_modified(product, "properties")

                elif property_mode == "full":
                    # Get selected full property IDs (legacy mode)
                    property_ids_list = request.form.getlist("full_property_ids")
                    if property_ids_list:
                        from src.core.database.models import AuthorizedProperty

                        properties = db_session.scalars(
                            select(AuthorizedProperty).filter(
                                AuthorizedProperty.property_id.in_(property_ids_list),
                                AuthorizedProperty.tenant_id == tenant_id,
                            )
                        ).all()

                        if properties:
                            properties_data = []
                            for prop in properties:
                                prop_dict = {
                                    "property_type": prop.property_type,
                                    "name": prop.name,
                                    "identifiers": prop.identifiers or [],
                                    "tags": prop.tags or [],
                                    "publisher_domain": prop.publisher_domain,
                                }
                                properties_data.append(prop_dict)

                            product.properties = properties_data
                            product.property_tags = None
                            product.property_ids = None
                            attributes.flag_modified(product, "properties")

                # No ``line_item_type`` is read here. The GAM form does not submit one -- no
                # template in this repository declares a field by that name -- so the branches
                # that used to hang off it could not execute, and one of them had carried a
                # NameError for its whole life behind that unsatisfiable guard.
                #
                # Two gaps the deletion makes visible are tracked rather than papered over, and
                # both predate it: this path never recomputes ``product.delivery_type`` from the
                # pricing options it replaces below, and an edit that changes the product's
                # format declarations leaves the seeded ``creative_placeholders`` stale. A
                # reseed must key on the declarations having changed, never on a GAM form field.
                if adapter_type == "google_ad_manager":
                    # Start with existing config to preserve fields not in the form
                    base_config = product.implementation_config.copy() if product.implementation_config else {}

                    # Add ad unit/placement targeting if provided
                    ad_unit_ids = form_data.get("targeted_ad_unit_ids", "").strip()
                    if ad_unit_ids:
                        # Parse comma-separated IDs
                        id_list = [id.strip() for id in ad_unit_ids.split(",") if id.strip()]

                        # Validate that all IDs are numeric (GAM requires numeric IDs)
                        invalid_ids = [id for id in id_list if not id.isdigit()]
                        if invalid_ids:
                            flash(
                                f"Invalid ad unit IDs: {', '.join(invalid_ids)}. "
                                f"Ad unit IDs must be numeric (e.g., '23312403859'). "
                                f"Use 'Browse Ad Units' to select valid ad units.",
                                "error",
                            )
                            return redirect(
                                url_for("products.edit_product", tenant_id=tenant_id, product_id=product_id)
                            )

                        base_config["targeted_ad_unit_ids"] = id_list

                    placement_ids = form_data.get("targeted_placement_ids", "").strip()
                    if placement_ids:
                        base_config["targeted_placement_ids"] = [
                            id.strip() for id in placement_ids.split(",") if id.strip()
                        ]

                    base_config["include_descendants"] = form_data.get("include_descendants") == "on"

                    # ``line_item_type`` and ``priority`` were written here from the form. They
                    # were the only fields this handler read that no template submits, so
                    # neither could ever be set. No other surface sets them either: the form
                    # parser that reads both, ``GAMProductConfigService.parse_form_config``, has
                    # no callers at all. An operator who needs to change them edits the stored
                    # config directly.

                    # Parse targeting template from form (includes custom targeting key-value pairs)
                    targeting_template_json = form_data.get("targeting_template", "{}")
                    try:
                        targeting_template = json.loads(targeting_template_json) if targeting_template_json else {}
                    except json.JSONDecodeError:
                        targeting_template = {}

                    # If targeting template has key_value_pairs, copy to implementation_config for GAM
                    if targeting_template.get("key_value_pairs"):
                        if "custom_targeting_keys" not in base_config:
                            base_config["custom_targeting_keys"] = {}
                        # Handle different targeting formats
                        kv_pairs = targeting_template["key_value_pairs"]
                        if isinstance(kv_pairs, dict) and "groups" in kv_pairs:
                            # Groups format (GAM-style nested) - pass through directly
                            base_config["custom_targeting_keys"] = kv_pairs
                        elif isinstance(kv_pairs, dict) and ("include" in kv_pairs or "exclude" in kv_pairs):
                            # Enhanced format - pass through directly
                            base_config["custom_targeting_keys"] = kv_pairs
                        else:
                            # Legacy format - merge as before
                            base_config["custom_targeting_keys"].update(kv_pairs)

                    # Store targeting_template in product
                    product.targeting_template = targeting_template

                    product.implementation_config = base_config
                    from sqlalchemy.orm import attributes

                    attributes.flag_modified(product, "implementation_config")
                    attributes.flag_modified(product, "targeting_template")

                    # Sync inventory mappings based on implementation_config
                    # Delete all existing mappings first
                    from src.core.database.models import ProductInventoryMapping

                    existing_mappings = db_session.scalars(
                        select(ProductInventoryMapping).filter_by(tenant_id=tenant_id, product_id=product_id)
                    ).all()
                    for mapping in existing_mappings:
                        db_session.delete(mapping)

                    # Flush deletes to avoid unique constraint violations
                    db_session.flush()

                    # Recreate mappings from implementation_config — ad units and
                    # placements are the same mapping, only the inventory type differs
                    for config_key, inventory_type in (
                        ("targeted_ad_unit_ids", "ad_unit"),
                        ("targeted_placement_ids", "placement"),
                    ):
                        for idx, inventory_id in enumerate(base_config.get(config_key) or []):
                            mapping_stmt = select(ProductInventoryMapping).filter_by(
                                tenant_id=tenant_id,
                                product_id=product_id,
                                inventory_type=inventory_type,
                                inventory_id=inventory_id,
                            )
                            mapping = ProductInventoryMapping(
                                tenant_id=tenant_id,
                                product_id=product_id,
                                inventory_type=inventory_type,
                                inventory_id=inventory_id,
                                is_primary=(idx == 0),
                            )

                            # A mapping's identity IS uq_product_inventory, so a lost race
                            # means the row is already there; the caller's is_primary still
                            # applies. Both callables run before this iteration ends, so the
                            # late binding B023 warns about cannot happen.
                            already_mapped = resolve_or_write(
                                db_session,
                                conflict=lambda: db_session.scalars(mapping_stmt).first(),  # noqa: B023
                                write=lambda: db_session.add(mapping),  # noqa: B023
                                constraint="uq_product_inventory",
                            )
                            if already_mapped is not None:
                                already_mapped.is_primary = idx == 0

                    # Custom targeting keys
                    if base_config.get("custom_targeting_keys"):
                        custom_keys = base_config["custom_targeting_keys"]
                        create_custom_key_inventory_mappings(db_session, tenant_id, product_id, custom_keys)

                # Update pricing options (AdCP PR #88)
                # Note: min_spend is now stored in pricing_options[].min_spend_per_package
                # Parse pricing options from form FIRST
                try:
                    pricing_options_data = parse_pricing_options_from_form(form_data)
                except ValueError as e:
                    flash(str(e), "error")
                    return redirect(url_for("products.edit_product", tenant_id=tenant_id, product_id=product_id))

                logger.info(f"Parsed {len(pricing_options_data) if pricing_options_data else 0} pricing options")

                # CRITICAL: Products MUST have at least one pricing option
                if not pricing_options_data or len(pricing_options_data) == 0:
                    flash("Product must have at least one pricing option", "error")
                    return redirect(url_for("products.edit_product", tenant_id=tenant_id, product_id=product_id))

                # Fetch existing pricing options
                existing_options = list(
                    db_session.scalars(
                        select(PricingOption).filter_by(tenant_id=tenant_id, product_id=product_id)
                    ).all()
                )

                logger.info(
                    f"Updating pricing options for product {product.product_id}: "
                    f"{len(existing_options)} existing, {len(pricing_options_data)} new"
                )

                # Update existing options or create new ones
                for idx, parsed in enumerate(pricing_options_data):
                    if idx < len(existing_options):
                        # Copy the TERMS onto the existing row and keep its identity.
                        po = existing_options[idx]
                        for column in _FORM_OWNED_COLUMNS:
                            setattr(po, column, getattr(parsed, column))
                    else:
                        parsed.tenant_id = tenant_id
                        parsed.product_id = product.product_id
                        db_session.add(parsed)

                # Delete excess existing options (if new list is shorter)
                if len(existing_options) > len(pricing_options_data):
                    for po in existing_options[len(pricing_options_data) :]:
                        db_session.delete(po)

                # Handle product detail fields (AdCP compliance)
                # Delivery measurement (REQUIRED per AdCP spec)
                delivery_measurement_provider = form_data.get("delivery_measurement_provider", "").strip()
                delivery_measurement_notes = form_data.get("delivery_measurement_notes", "").strip()
                if delivery_measurement_provider:
                    product.delivery_measurement = {
                        "provider": delivery_measurement_provider,
                    }
                    if delivery_measurement_notes:
                        product.delivery_measurement["notes"] = delivery_measurement_notes
                    from sqlalchemy.orm import attributes

                    attributes.flag_modified(product, "delivery_measurement")
                elif product.delivery_measurement:
                    # Clear if provider was removed
                    product.delivery_measurement = None
                    from sqlalchemy.orm import attributes

                    attributes.flag_modified(product, "delivery_measurement")

                # Product image for card generation (optional)
                product_image_url = form_data.get("product_image_url", "").strip()
                if product_image_url:
                    # Update or create product card
                    if not product.product_card:
                        product.product_card = {
                            "format_id": {
                                "agent_url": "https://creative.adcontextprotocol.org/",
                                "id": "product_card_standard",
                            },
                            "manifest": {},
                        }

                    # Update manifest
                    product.product_card["manifest"]["product_image"] = product_image_url
                    product.product_card["manifest"]["product_name"] = product.name
                    product.product_card["manifest"]["product_description"] = product.description or ""
                    product.product_card["manifest"]["delivery_type"] = product.delivery_type

                    # Add pricing info to manifest if available
                    if pricing_options_data and len(pricing_options_data) > 0:
                        first_option = pricing_options_data[0]
                        product.product_card["manifest"]["pricing_model"] = first_option.pricing_model
                        if first_option.is_fixed and first_option.rate:
                            product.product_card["manifest"]["pricing_amount"] = str(first_option.rate)
                            product.product_card["manifest"]["pricing_currency"] = first_option.currency

                    from sqlalchemy.orm import attributes

                    attributes.flag_modified(product, "product_card")
                elif product.product_card and product.product_card.get("manifest", {}).get("product_image"):
                    # If image URL was removed, clear the card
                    product.product_card = None
                    from sqlalchemy.orm import attributes

                    attributes.flag_modified(product, "product_card")

                # Debug: Log final state before commit
                from sqlalchemy import inspect as sa_inspect

                logger.info(f"[DEBUG] About to commit product {product_id}")
                logger.info(f"[DEBUG] product.format_ids = {product.format_ids}")
                logger.info(f"[DEBUG] product.format_ids type = {type(product.format_ids)}")
                logger.info(f"[DEBUG] SQLAlchemy dirty objects: {db_session.dirty}")

                # Check if product is in dirty set and formats was modified
                if product in db_session.dirty:
                    insp = sa_inspect(product)
                    if insp.attrs.format_ids.history.has_changes():
                        logger.info("[DEBUG] format_ids attribute was modified")
                    else:
                        logger.info("[DEBUG] format_ids attribute NOT modified (flag_modified may be needed)")

                # Asked here, not with the anyOf above: the publisher properties it reads are
                # written after that check.
                try:
                    refuse_outside_common_intersection(product)
                except ValueError as e:
                    flash(str(e), "error")
                    return redirect(url_for("products.edit_product", tenant_id=tenant_id, product_id=product_id))

                db_session.commit()

                # Debug: Verify formats after commit by re-querying
                db_session.refresh(product)
                logger.info(f"[DEBUG] After commit - product.format_ids from DB: {product.format_ids}")

                flash(f"Product '{product.name}' updated successfully", "success")
                return redirect(url_for("products.list_products", tenant_id=tenant_id))

            # GET request - show form
            # Load existing pricing options (AdCP PR #88)
            pricing_options = db_session.scalars(
                select(PricingOption).filter_by(tenant_id=tenant_id, product_id=product_id)
            ).all()

            pricing_options_list = []
            for po in pricing_options:
                pricing_options_list.append(
                    {
                        "pricing_model": po.pricing_model,
                        "rate": float(po.rate) if po.rate else None,
                        "currency": po.currency,
                        "is_fixed": po.is_fixed,
                        "price_guidance": po.price_guidance,
                        "parameters": po.parameters,
                        "min_spend_per_package": float(po.min_spend_per_package) if po.min_spend_per_package else None,
                    }
                )

            # Derive display values from pricing_options
            delivery_type = product.delivery_type
            cpm = None
            price_guidance = None

            if pricing_options_list:
                first_pricing = pricing_options_list[0]
                delivery_type = "guaranteed" if first_pricing["is_fixed"] else "non_guaranteed"
                cpm = first_pricing["rate"]
                price_guidance = first_pricing["price_guidance"]

            # Parse implementation_config
            implementation_config = (
                product.implementation_config
                if isinstance(product.implementation_config, dict)
                else json.loads(product.implementation_config)
                if product.implementation_config
                else {}
            )

            # Parse targeting_template - build from implementation_config if not set
            targeting_template = (
                product.targeting_template
                if isinstance(product.targeting_template, dict)
                else json.loads(product.targeting_template)
                if product.targeting_template
                else {}
            )

            # If targeting_template doesn't have key_value_pairs but implementation_config has custom_targeting_keys,
            # populate targeting_template from implementation_config for backwards compatibility
            if not targeting_template.get("key_value_pairs") and implementation_config.get("custom_targeting_keys"):
                targeting_template["key_value_pairs"] = implementation_config["custom_targeting_keys"]

            product_dict = {
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "inventory_profile_id": product.inventory_profile_id,
                "delivery_type": delivery_type,
                "cpm": cpm,
                "price_guidance": price_guidance,
                "formats": (
                    product.format_ids
                    if isinstance(product.format_ids, list)
                    else json.loads(product.format_ids)
                    if product.format_ids
                    else []
                ),
                # The canonical arm, read back as the stored document so the editor shows
                # what the row actually declares rather than a rendering of it.
                "format_options": product.format_options,
                "countries": (
                    product.countries
                    if isinstance(product.countries, list)
                    else json.loads(product.countries)
                    if product.countries
                    else []
                ),
                "implementation_config": implementation_config,
                "targeting_template": targeting_template,
                # Product detail fields (AdCP compliance)
                "delivery_measurement": product.delivery_measurement,
                "product_card": product.product_card,
                "product_card_detailed": product.product_card_detailed,
                "placements": product.placements,
                "reporting_capabilities": product.reporting_capabilities,
                # Advertising channels
                "channels": product.channels or [],
                # Principal access control
                "allowed_principal_ids": product.allowed_principal_ids or [],
            }

            product_dict["pricing_options"] = pricing_options_list

            # Get all principals for this tenant (for access control dropdown)
            principals = PrincipalRepository(db_session, tenant_id).list_all()
            principals_list = [{"principal_id": p.principal_id, "name": p.name} for p in principals]

            # Get authorized properties for publisher properties selector
            from src.core.database.models import AuthorizedProperty

            authorized_properties_query = db_session.scalars(
                select(AuthorizedProperty).filter_by(tenant_id=tenant_id).order_by(AuthorizedProperty.name)
            ).all()
            authorized_properties_list = [
                {
                    "property_id": p.property_id,
                    "name": p.name,
                    "property_type": p.property_type,
                    "tags": p.tags or [],
                    "verification_status": p.verification_status,
                    "publisher_domain": p.publisher_domain,
                }
                for p in authorized_properties_query
            ]

            # Get current publisher properties from product (for pre-selecting in edit form)
            selected_publisher_properties = product.effective_properties

            # Show adapter-specific form
            if adapter_type == "google_ad_manager":
                from src.core.database.models import GAMInventory

                inventory_count = db_session.scalar(
                    select(func.count()).select_from(GAMInventory).filter_by(tenant_id=tenant_id)
                )
                inventory_synced = inventory_count > 0

                # Fetch assigned inventory for this product
                from src.core.database.models import ProductInventoryMapping

                assigned_inventory_query = (
                    select(ProductInventoryMapping, GAMInventory)
                    .join(
                        GAMInventory,
                        (ProductInventoryMapping.tenant_id == GAMInventory.tenant_id)
                        & (ProductInventoryMapping.inventory_type == GAMInventory.inventory_type)
                        & (ProductInventoryMapping.inventory_id == GAMInventory.inventory_id),
                    )
                    .where(
                        ProductInventoryMapping.tenant_id == tenant_id,
                        ProductInventoryMapping.product_id == product_id,
                    )
                )
                assigned_inventory_results = db_session.execute(assigned_inventory_query).all()
                assigned_inventory = [
                    {
                        "mapping_id": mapping.id,
                        "inventory_id": inventory.inventory_id,
                        "inventory_type": inventory.inventory_type,
                        "name": inventory.name,
                        "path": inventory.path,
                        "is_primary": mapping.is_primary,
                    }
                    for mapping, inventory in assigned_inventory_results
                ]

                # Get inventory profiles for this tenant
                from src.core.database.models import InventoryProfile

                stmt_profiles = select(InventoryProfile).filter_by(tenant_id=tenant_id).order_by(InventoryProfile.name)
                inventory_profiles = db_session.scalars(stmt_profiles).all()

                return render_template(
                    "add_product_gam.html",
                    tenant_id=tenant_id,
                    product=product_dict,
                    inventory_synced=inventory_synced,
                    formats=get_creative_formats(tenant_id=tenant_id),
                    currencies=currencies,
                    assigned_inventory=assigned_inventory,
                    inventory_profiles=inventory_profiles,
                    principals=principals_list,
                    authorized_properties=authorized_properties_list,
                    selected_publisher_properties=selected_publisher_properties,
                )
            else:
                # For non-GAM adapters - use unified edit template
                # Reload tenant for template context (measurement_providers, etc.)
                tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
                return render_template(
                    "edit_product.html",
                    tenant_id=tenant_id,
                    tenant=tenant,
                    adapter_type=adapter_type,
                    product=product_dict,
                    currencies=currencies,
                    principals=principals_list,
                    authorized_properties=authorized_properties_list,
                    selected_publisher_properties=selected_publisher_properties,
                    **_legacy_format_facts(product),
                )

    except HTTPException:
        # Werkzeug's own control flow, not a failure to report. An ``abort()`` reached from
        # inside this handler used to be caught below and rendered as
        # "Error editing product: 404 Not Found" behind a 302, losing both its status and its
        # meaning. ``src/admin/utils/helpers.py`` guards the same way for 403/404.
        raise
    except Exception:
        # NOTHING HERE IS SAID TO THE OPERATOR, and that is the point. This handler reaches
        # here only for a failure it did not anticipate -- every failure it CAN act on is
        # caught by one of the seven typed handlers above, which flash the operator a message
        # about their input and redirect. So an exception arriving here is a defect in this
        # code or an outage beneath it, and the previous answer -- flashing ``str(e)`` and
        # redirecting -- made it look like neither: it asserted that the operator's input
        # caused a NameError, discarded their edit, and returned a 302 that monitoring counts
        # as a success. That is how the NameError behind the deleted ``line_item_type`` guard
        # survived unnoticed for the life of that branch.
        #
        # The traceback goes to the log, where a diagnostic belongs, and the response is a 500,
        # which is both honest to the operator and visible to whatever watches status codes.
        logger.error(
            f"Unhandled error editing product {product_id} for tenant {tenant_id}",
            exc_info=True,
        )
        abort(500)


@products_bp.route("/<product_id>/delete", methods=["DELETE"])
@require_tenant_access()
def delete_product(tenant_id, product_id):
    """Delete a product."""
    try:
        with get_db_session() as db_session:
            # Find the product
            product = db_session.scalars(select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)).first()

            if not product:
                return jsonify({"error": "Product not found"}), 404

            # Store product name for response
            product_name = product.name

            # Check if product is used in any active media buys
            mb_repo = MediaBuyRepository(db_session, tenant_id)
            # SCHEDULED is here because a buy whose flight window has not opened yet is
            # still a live commitment: the seller has agreed to run it, and deleting the
            # product underneath it breaks a buy that has not had its chance to deliver.
            # It became reachable when the admin approval routes started writing the
            # resolved flight-window status -- a pre-window buy used to land `active`
            # (and so was protected by that member) and now lands `scheduled`.
            #
            # Listed explicitly rather than reusing models._SELLER_COMMITTED_STATUSES,
            # although that set is a near-match and the resemblance is a trap: it also
            # contains COMPLETED and CANCELED. A finished or cancelled buy must NOT
            # block product deletion -- it has no future delivery to protect -- so
            # adopting the set would silently make products undeletable forever.
            # The correct membership rule for THIS guard is "could still deliver".
            active_buys = mb_repo.list_by_statuses(
                [
                    PersistedMediaBuyStatus.PENDING,
                    PersistedMediaBuyStatus.SCHEDULED,
                    PersistedMediaBuyStatus.ACTIVE,
                    PersistedMediaBuyStatus.PAUSED,
                ]
            )

            # Check if any active media buys reference this product
            for buy in active_buys:
                # Check both config (legacy) and raw_request (current) fields for backward compatibility
                config_product_ids = []
                try:
                    # Legacy field: may not exist on older MediaBuy records
                    config_data = getattr(buy, "config", None)
                    if config_data:
                        config_product_ids = config_data.get("product_ids", [])
                except (AttributeError, TypeError):
                    pass

                # Current field: should always exist
                raw_request_product_ids = (buy.raw_request or {}).get("product_ids", [])
                all_product_ids = config_product_ids + raw_request_product_ids

                if product_id in all_product_ids:
                    return (
                        jsonify(
                            {
                                "error": f"Cannot delete product '{product_name}' - it is used in active media buy '{buy.media_buy_id}'"
                            }
                        ),
                        400,
                    )

            # Delete the product and related pricing options
            # Foreign key CASCADE automatically handles pricing_options deletion
            db_session.delete(product)
            db_session.commit()

            logger.info(f"Product {product_id} ({product_name}) deleted by tenant {tenant_id}")

            return jsonify({"success": True, "message": f"Product '{product_name}' deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting product {product_id}: {e}", exc_info=True)

        # Rollback on any error
        try:
            db_session.rollback()
        except Exception:
            logger.debug("Rollback failed during error handling", exc_info=True)

        # More specific error handling
        error_message = str(e)

        # Check for common error types
        if "ForeignKeyViolation" in error_message or "foreign key constraint" in error_message.lower():
            logger.error(f"Foreign key constraint violation when deleting product {product_id}")
            return jsonify({"error": "Cannot delete product - it is referenced by other records"}), 400

        if "ValidationError" in error_message or "pattern" in error_message.lower():
            logger.warning(f"Product validation error for {product_id}: {error_message}")
            return jsonify({"error": "Product data validation failed"}), 400

        # Generic error
        logger.error(f"Product deletion failed for {product_id}: {error_message}")
        return jsonify({"error": f"Failed to delete product: {error_message}"}), 500


@products_bp.route("/<product_id>/inventory", methods=["POST"])
@log_admin_action("assign_inventory_to_product")
@require_tenant_access(api_mode=True)
def assign_inventory_to_product(tenant_id, product_id):
    """Assign inventory items to a product.

    Request body:
    {
        "inventory_id": "123",
        "inventory_type": "ad_unit",  # or "placement"
        "is_primary": false  # optional, default false
    }
    """
    try:
        from src.core.database.models import GAMInventory, ProductInventoryMapping

        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body required"}), 400

        inventory_id = data.get("inventory_id")
        inventory_type = data.get("inventory_type")
        is_primary = data.get("is_primary", False)

        if not inventory_id or not inventory_type:
            return jsonify({"error": "inventory_id and inventory_type are required"}), 400

        with get_db_session() as db_session:
            # Verify product exists
            product = db_session.scalars(select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)).first()

            if not product:
                return jsonify({"error": "Product not found"}), 404

            # Verify inventory exists
            inventory = db_session.scalars(
                select(GAMInventory).filter_by(
                    tenant_id=tenant_id, inventory_id=inventory_id, inventory_type=inventory_type
                )
            ).first()

            if not inventory:
                return jsonify({"error": "Inventory item not found"}), 404

            # Check if mapping already exists
            mapping_stmt = select(ProductInventoryMapping).filter_by(
                tenant_id=tenant_id, product_id=product_id, inventory_id=inventory_id, inventory_type=inventory_type
            )
            mapping = ProductInventoryMapping(
                tenant_id=tenant_id,
                product_id=product_id,
                inventory_id=inventory_id,
                inventory_type=inventory_type,
                is_primary=is_primary,
            )
            existing = resolve_or_write(
                db_session,
                conflict=lambda: db_session.scalars(mapping_stmt).first(),
                write=lambda: db_session.add(mapping),
                constraint="uq_product_inventory",
            )
            if existing is not None:
                # Update existing mapping — also where an insert that lost the race lands.
                existing.is_primary = is_primary
            db_session.commit()

            # CRITICAL: Update product's implementation_config with inventory targeting
            # GAM adapter requires this to create line items
            from sqlalchemy.orm import attributes

            if not product.implementation_config:
                product.implementation_config = {}

            # Get all inventory mappings for this product
            all_mappings = db_session.scalars(
                select(ProductInventoryMapping).filter_by(tenant_id=tenant_id, product_id=product_id)
            ).all()

            # Build targeted_ad_unit_ids and targeted_placement_ids from mappings
            ad_unit_ids = []
            placement_ids = []

            for m in all_mappings:
                inv = db_session.scalars(
                    select(GAMInventory).filter_by(
                        tenant_id=tenant_id, inventory_id=m.inventory_id, inventory_type=m.inventory_type
                    )
                ).first()

                if inv:
                    if m.inventory_type == "ad_unit":
                        ad_unit_ids.append(inv.inventory_id)
                    elif m.inventory_type == "placement":
                        placement_ids.append(inv.inventory_id)

            # Update implementation_config
            if ad_unit_ids:
                product.implementation_config["targeted_ad_unit_ids"] = ad_unit_ids
            if placement_ids:
                product.implementation_config["targeted_placement_ids"] = placement_ids

            # Mark as modified for SQLAlchemy to detect JSONB change
            attributes.flag_modified(product, "implementation_config")
            db_session.commit()

            if existing:
                return jsonify(
                    {
                        "message": "Inventory assignment updated",
                        "mapping_id": existing.id,
                        "inventory_name": inventory.name,
                    }
                )
            else:
                return (
                    jsonify(
                        {
                            "message": "Inventory assigned to product successfully",
                            "mapping_id": mapping.id,
                            "inventory_name": inventory.name,
                        }
                    ),
                    201,
                )

    except Exception as e:
        logger.error(f"Error assigning inventory to product: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@products_bp.route("/<product_id>/inventory", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_product_inventory(tenant_id, product_id):
    """Get all inventory items assigned to a product."""
    try:
        from src.core.database.models import GAMInventory, ProductInventoryMapping

        with get_db_session() as db_session:
            # Verify product exists
            product = db_session.scalars(select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)).first()

            if not product:
                return jsonify({"error": "Product not found"}), 404

            # Get all mappings for this product
            mappings = db_session.scalars(
                select(ProductInventoryMapping).filter_by(tenant_id=tenant_id, product_id=product_id)
            ).all()

            # Fetch inventory details for each mapping
            result = []
            for mapping in mappings:
                inventory = db_session.scalars(
                    select(GAMInventory).filter_by(
                        tenant_id=tenant_id, inventory_id=mapping.inventory_id, inventory_type=mapping.inventory_type
                    )
                ).first()

                if inventory:
                    result.append(
                        {
                            "mapping_id": mapping.id,
                            "inventory_id": inventory.inventory_id,
                            "inventory_name": inventory.name,
                            "inventory_type": mapping.inventory_type,
                            "is_primary": mapping.is_primary,
                            "status": inventory.status,
                            "path": inventory.path,
                        }
                    )

            return jsonify({"inventory": result, "count": len(result)})

    except Exception as e:
        logger.error(f"Error fetching product inventory: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@products_bp.route("/<product_id>/inventory/<int:mapping_id>", methods=["DELETE"])
@log_admin_action("unassign_inventory_from_product")
@require_tenant_access(api_mode=True)
def unassign_inventory_from_product(tenant_id, product_id, mapping_id):
    """Remove an inventory assignment from a product (API endpoint)."""
    try:
        from src.core.database.models import ProductInventoryMapping

        with get_db_session() as db_session:
            # Find the mapping
            stmt = select(ProductInventoryMapping).filter_by(id=mapping_id, tenant_id=tenant_id, product_id=product_id)
            mapping = db_session.scalars(stmt).first()

            if not mapping:
                return jsonify({"success": False, "message": "Inventory assignment not found"}), 404

            # Store details for logging
            inventory_name = f"{mapping.inventory_type}:{mapping.inventory_id}"
            inventory_id_to_remove = mapping.inventory_id
            inventory_type_to_remove = mapping.inventory_type

            # Delete the mapping
            db_session.delete(mapping)
            db_session.commit()

            # Update product's implementation_config to remove the inventory ID
            from sqlalchemy.orm import attributes

            from src.core.database.models import GAMInventory

            product = db_session.scalars(select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)).first()

            if product and product.implementation_config:
                # Get remaining inventory mappings
                remaining_mappings = db_session.scalars(
                    select(ProductInventoryMapping).filter_by(tenant_id=tenant_id, product_id=product_id)
                ).all()

                # Rebuild targeted IDs from remaining mappings
                ad_unit_ids = []
                placement_ids = []

                for m in remaining_mappings:
                    inv = db_session.scalars(
                        select(GAMInventory).filter_by(
                            tenant_id=tenant_id, inventory_id=m.inventory_id, inventory_type=m.inventory_type
                        )
                    ).first()

                    if inv:
                        if m.inventory_type == "ad_unit":
                            ad_unit_ids.append(inv.inventory_id)
                        elif m.inventory_type == "placement":
                            placement_ids.append(inv.inventory_id)

                # Update implementation_config
                if ad_unit_ids:
                    product.implementation_config["targeted_ad_unit_ids"] = ad_unit_ids
                else:
                    # Remove key if no ad units remain
                    product.implementation_config.pop("targeted_ad_unit_ids", None)

                if placement_ids:
                    product.implementation_config["targeted_placement_ids"] = placement_ids
                else:
                    # Remove key if no placements remain
                    product.implementation_config.pop("targeted_placement_ids", None)

                # Mark as modified for SQLAlchemy
                attributes.flag_modified(product, "implementation_config")
                db_session.commit()

            logger.info(
                f"Removed inventory assignment: product={product_id}, inventory={inventory_name}, mapping_id={mapping_id}"
            )

            return jsonify({"success": True, "message": "Inventory assignment removed successfully"})

    except Exception as e:
        logger.error(f"Error removing inventory assignment: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
