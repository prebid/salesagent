"""Products management blueprint for admin UI."""

import json
import logging
import uuid

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from src.admin.utils import require_tenant_access  # type: ignore[attr-defined]
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption, Product, ProductInventoryMapping, Tenant
from src.core.database.product_pricing import get_product_pricing_options
from src.core.validation import sanitize_form_data
from src.services.gam_product_config_service import GAMProductConfigService

logger = logging.getLogger(__name__)

# Create Blueprint
products_bp = Blueprint("products", __name__)


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
    type_filter: str | None = None,
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
        type_filter: Filter by format type (display, video, audio)

    Returns:
        List of format dictionaries for frontend
    """
    from src.core.format_resolver import list_available_formats

    # Get formats from creative agent registry with optional filtering
    formats = list_available_formats(
        tenant_id=tenant_id,
        max_width=max_width,
        max_height=max_height,
        min_width=min_width,
        min_height=min_height,
        is_responsive=is_responsive,
        asset_types=asset_types,
        name_search=name_search,
        type_filter=type_filter,
    )

    logger.info(f"get_creative_formats: Fetched {len(formats)} formats from registry for tenant {tenant_id}")

    formats_list = []
    for idx, fmt in enumerate(formats):
        # Debug: Log first few formats to diagnose dimension issues
        if idx < 5:
            logger.info(
                f"[DEBUG] Format {idx}: {fmt.name} - "
                f"format_id={fmt.format_id}, "
                f"type={fmt.type}, "
                f"requirements={fmt.requirements}"
            )

        format_dict = {
            "id": (
                fmt.format_id.id
                if isinstance(fmt.format_id, object) and hasattr(fmt.format_id, "id")
                else str(fmt.format_id)
            ),  # Extract string ID from FormatId object
            "agent_url": fmt.agent_url,
            "name": fmt.name,
            "type": fmt.type,
            "category": fmt.category,
            "description": fmt.description or f"{fmt.name} - {fmt.iab_specification or 'Standard format'}",
            "preview_url": getattr(fmt, "preview_url", None),
            "dimensions": None,
            "duration": None,
        }

        # Add dimensions for display/video formats using the helper method
        dimensions = fmt.get_primary_dimensions()
        if dimensions:
            width, height = dimensions
            format_dict["dimensions"] = f"{width}x{height}"
            if idx < 5:
                logger.info(f"[DEBUG] Format {idx}: Got dimensions: {format_dict['dimensions']}")
        elif "_" in str(fmt.format_id):
            # Fallback: Parse dimensions from format_id (e.g., "display_300x250_image" → "300x250")
            # This handles creative agents that don't populate renders or requirements field
            import re

            format_id_str = str(fmt.format_id)
            match = re.search(r"_(\d+)x(\d+)_", format_id_str)
            if match:
                format_dict["dimensions"] = f"{match.group(1)}x{match.group(2)}"
                if idx < 5:
                    logger.info(f"[DEBUG] Format {idx}: Parsed dimensions from format_id: {format_dict['dimensions']}")
            elif idx < 5:
                logger.info(f"[DEBUG] Format {idx}: No dimensions found - format_id doesn't match pattern")

        # Add duration for video/audio formats
        if fmt.requirements and "duration" in fmt.requirements:
            format_dict["duration"] = f"{fmt.requirements['duration']}s"
        elif fmt.requirements and "duration_max" in fmt.requirements:
            format_dict["duration"] = f"{fmt.requirements['duration_max']}s"

        formats_list.append(format_dict)

    # Sort by type, then name
    formats_list.sort(key=lambda x: (x["type"], x["name"]))

    logger.info(f"get_creative_formats: Returning {len(formats_list)} formatted formats")

    return formats_list


def parse_pricing_options_from_form(form_data: dict) -> list[dict]:
    """Parse pricing options from form data (AdCP PR #88).

    Form data uses indexed fields: pricing_model_0, pricing_model_1, etc.

    Returns list of pricing option dicts ready for database insertion.
    """
    pricing_options = []
    index = 0

    # Find all pricing options by looking for pricing_model_{i} fields
    while f"pricing_model_{index}" in form_data:
        pricing_model_raw = form_data.get(f"pricing_model_{index}")
        if not pricing_model_raw:
            index += 1
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
                pass

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

        # Build pricing option dict
        pricing_option = {
            "pricing_model": pricing_model,
            "currency": currency,
            "is_fixed": is_fixed,
            "rate": rate,
            "price_guidance": price_guidance,
            "parameters": parameters,
            "min_spend_per_package": min_spend,
        }

        pricing_options.append(pricing_option)
        index += 1

    return pricing_options


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

                # Parse formats and resolve names from creative agents
                formats_data = (
                    product.formats
                    if isinstance(product.formats, list)
                    else json.loads(product.formats) if product.formats else []
                )

                # Debug: Log raw formats data
                logger.info(f"[DEBUG] Product {product.product_id} raw product.formats from DB: {product.formats}")
                logger.info(f"[DEBUG] Product {product.product_id} formats_data after parsing: {formats_data}")
                logger.info(
                    f"[DEBUG] Product {product.product_id} formats_data type: {type(formats_data)}, len: {len(formats_data)}"
                )

                # Resolve format names from creative agent registry
                resolved_formats = []
                from src.core.format_resolver import get_format

                for fmt in formats_data:
                    agent_url = None
                    format_id = None

                    if isinstance(fmt, dict):
                        # Database JSONB: uses "id" per AdCP spec
                        agent_url = fmt.get("agent_url")
                        format_id = fmt.get("id") or fmt.get("format_id")  # "id" is AdCP spec, "format_id" is legacy
                    elif hasattr(fmt, "agent_url") and (hasattr(fmt, "format_id") or hasattr(fmt, "id")):
                        # Pydantic object: uses "format_id" attribute (serializes to "id" in JSON)
                        agent_url = fmt.agent_url
                        format_id = getattr(fmt, "format_id", None) or getattr(fmt, "id", None)
                    elif isinstance(fmt, str):
                        # Legacy: plain string format ID (no agent_url) - should be deprecated
                        # This data needs to be migrated to proper FormatId structure
                        logger.error(
                            f"Product {product.product_id} has DEPRECATED string format: {fmt}. "
                            "Please edit and re-save this product to migrate to FormatId structure."
                        )
                        format_id = fmt
                        agent_url = None  # Will fail validation below
                    else:
                        logger.error(
                            f"Product {product.product_id} has INVALID format type {type(fmt)}: {fmt}. "
                            "This data is corrupted and needs manual repair."
                        )
                        continue

                    # Validate format_id (agent_url is optional for legacy formats)
                    if not format_id:
                        logger.error(
                            f"Product {product.product_id} format missing format_id. "
                            "This product needs to be edited and re-saved to fix the data."
                        )
                        continue

                    # Warn if agent_url is missing but continue processing
                    if not agent_url:
                        logger.warning(
                            f"Product {product.product_id} format {format_id} missing agent_url. "
                            "Using format_id as display name. Edit product to fix."
                        )

                    # Resolve format name from creative agent registry
                    try:
                        if agent_url:
                            format_obj = get_format(format_id, agent_url, tenant_id)
                            resolved_formats.append(
                                {"format_id": format_id, "agent_url": agent_url, "name": format_obj.name}
                            )
                        else:
                            # No agent_url - try to find format in registry by format_id
                            # This handles legacy formats that don't have agent_url stored
                            from src.core.format_resolver import list_available_formats

                            all_formats = list_available_formats(tenant_id=tenant_id)
                            matching_format = None
                            for fmt in all_formats:
                                if fmt.format_id == format_id:
                                    matching_format = fmt
                                    break

                            if matching_format:
                                resolved_formats.append(
                                    {
                                        "format_id": format_id,
                                        "agent_url": matching_format.agent_url,
                                        "name": matching_format.name,
                                    }
                                )
                            else:
                                # Format not found in registry - generate friendly name from format_id
                                resolved_formats.append(
                                    {
                                        "format_id": format_id,
                                        "agent_url": None,
                                        "name": _format_id_to_display_name(format_id),
                                    }
                                )
                    except Exception as e:
                        logger.warning(f"Could not resolve format {format_id} from {agent_url}: {e}")
                        # Use friendly name as fallback
                        resolved_formats.append(
                            {
                                "format_id": format_id,
                                "agent_url": agent_url,
                                "name": _format_id_to_display_name(format_id),
                            }
                        )

                logger.info(f"[DEBUG] Product {product.product_id} resolved {len(resolved_formats)} formats")
                if formats_data and not resolved_formats:
                    logger.error(
                        f"[DEBUG] Product {product.product_id} ERROR: Had {len(formats_data)} formats but resolved 0! "
                        f"This means format resolution failed."
                    )

                product_dict = {
                    "product_id": product.product_id,
                    "name": product.name,
                    "description": product.description,
                    "pricing_options": pricing_options_list,
                    "formats": resolved_formats,
                    "countries": (
                        product.countries
                        if isinstance(product.countries, list)
                        else json.loads(product.countries) if product.countries else []
                    ),
                    "implementation_config": (
                        product.implementation_config
                        if isinstance(product.implementation_config, dict)
                        else json.loads(product.implementation_config) if product.implementation_config else {}
                    ),
                    "created_at": product.created_at if hasattr(product, "created_at") else None,
                    "inventory_details": inventory_details.get(
                        product.product_id,
                        {
                            "total": 0,
                            "ad_units": 0,
                            "placements": 0,
                            "custom_keys": 0,
                        },
                    ),
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
                return redirect(url_for("products.add_product", tenant_id=tenant_id))

            with get_db_session() as db_session:
                # Parse formats - expecting JSON string with FormatReference objects or checkbox values
                formats_json = form_data.get("formats", "[]")
                try:
                    formats = json.loads(formats_json) if formats_json else []
                    # Validate format structure
                    if not isinstance(formats, list):
                        formats = []
                except json.JSONDecodeError:
                    # Fallback to checkbox format: "agent_url|format_id"
                    formats_raw = request.form.getlist("formats")

                    # Validate formats against available formats
                    import asyncio

                    from src.core.creative_agent_registry import get_creative_agent_registry

                    try:
                        registry = get_creative_agent_registry()
                        # Run async list_all_formats
                        try:
                            loop = asyncio.get_running_loop()
                            # Already in async context, run in thread pool
                            import concurrent.futures

                            with concurrent.futures.ThreadPoolExecutor() as executor:
                                future = executor.submit(
                                    lambda: asyncio.run(registry.list_all_formats(tenant_id=tenant_id))
                                )
                                available_formats = future.result()
                        except RuntimeError:
                            # No running loop, safe to create one
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            try:
                                available_formats = loop.run_until_complete(
                                    registry.list_all_formats(tenant_id=tenant_id)
                                )
                            finally:
                                loop.close()

                        # Build set of valid format IDs for quick lookup
                        # Format objects have format_id (FormatId object with agent_url and id)
                        valid_format_ids = {
                            f"{fmt.format_id.agent_url}|{fmt.format_id.id}" for fmt in available_formats
                        }
                        logger.info(f"[DEBUG] Found {len(valid_format_ids)} valid formats for tenant {tenant_id}")
                        sample_ids = list(valid_format_ids)[:5]
                        logger.info(f"[DEBUG] Sample valid format IDs: {sample_ids}")
                        logger.info(f"[DEBUG] Form submitted formats_raw: {formats_raw}")
                        # Log the first submitted format to see exact structure
                        if formats_raw:
                            logger.info(f"[DEBUG] First submitted format: '{formats_raw[0]}'")
                    except Exception as e:
                        logger.error(f"Failed to fetch available formats: {e}")
                        flash("Unable to validate formats. Please try again.", "error")
                        return redirect(url_for("products.add_product", tenant_id=tenant_id))

                    formats = []
                    invalid_formats = []
                    for fmt_str in formats_raw:
                        if "|" not in fmt_str:
                            # Missing agent_url - data format error
                            logger.error(f"Invalid format value (missing agent_url): {fmt_str}")
                            flash(f"Invalid format data: {fmt_str}. Please contact support.", "error")
                            continue

                        agent_url, format_id = fmt_str.split("|", 1)

                        # Normalize agent_url by ensuring it has a trailing slash
                        # Form submits without trailing slash, but Format objects have trailing slash
                        if not agent_url.endswith("/"):
                            agent_url_normalized = agent_url + "/"
                        else:
                            agent_url_normalized = agent_url

                        normalized_fmt_str = f"{agent_url_normalized}|{format_id}"

                        # Validate format exists (using normalized URL)
                        if normalized_fmt_str not in valid_format_ids:
                            invalid_formats.append(format_id)
                            logger.warning(
                                f"Invalid format ID selected: {format_id} from {agent_url} (normalized: {agent_url_normalized})"
                            )
                            logger.warning(f"Looking for: {normalized_fmt_str} in valid_format_ids")
                            continue

                        # Store with original agent_url (without forcing trailing slash)
                        formats.append({"agent_url": agent_url, "id": format_id})

                    if invalid_formats:
                        flash(
                            f"Invalid format IDs: {', '.join(invalid_formats)}. "
                            f"These formats are not available for this tenant. Please select valid formats.",
                            "error",
                        )
                        return redirect(url_for("products.add_product", tenant_id=tenant_id))

                # Parse countries - from multi-select
                countries_list = request.form.getlist("countries")
                # Only set countries if some were selected; None means all countries
                countries = countries_list if countries_list and "ALL" not in countries_list else None

                # Parse and create pricing options (AdCP PR #88)
                pricing_options_data = parse_pricing_options_from_form(form_data)

                # CRITICAL: Products MUST have at least one pricing option
                if not pricing_options_data or len(pricing_options_data) == 0:
                    flash("Product must have at least one pricing option", "error")
                    return redirect(url_for("products.add_product", tenant_id=tenant_id))

                # Derive delivery_type from first pricing option for implementation_config
                delivery_type = "guaranteed"  # Default

                if pricing_options_data and len(pricing_options_data) > 0:
                    first_option = pricing_options_data[0]
                    # Determine delivery_type based on is_fixed
                    if first_option.get("is_fixed", True):
                        delivery_type = "guaranteed"
                    else:
                        delivery_type = "non_guaranteed"

                # Build implementation config based on adapter type
                implementation_config = {}
                if adapter_type == "google_ad_manager":
                    # Parse GAM-specific fields from unified form
                    gam_config_service = GAMProductConfigService()
                    base_config = gam_config_service.generate_default_config(delivery_type, formats)

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
                            return redirect(url_for("products.add_product", tenant_id=tenant_id))

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
                    implementation_config = gam_config_service.generate_default_config(delivery_type, formats)

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
                    # Merge key-value pairs into implementation_config for GAM adapter
                    implementation_config["custom_targeting_keys"].update(targeting_template["key_value_pairs"])

                # Build product kwargs, excluding None values for JSON fields that have database constraints
                product_kwargs = {
                    "product_id": form_data.get("product_id") or f"prod_{uuid.uuid4().hex[:8]}",
                    "tenant_id": tenant_id,
                    "name": form_data["name"],
                    "description": form_data.get("description", ""),
                    "formats": formats,
                    "delivery_type": delivery_type,
                    "targeting_template": targeting_template,
                    "implementation_config": implementation_config,
                }

                # Only add countries if explicitly set
                if countries is not None:
                    product_kwargs["countries"] = countries

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
                        product_kwargs["product_card"]["manifest"]["pricing_model"] = first_option.get(
                            "pricing_model", "CPM"
                        )
                        if first_option.get("is_fixed") and first_option.get("fixed_price"):
                            product_kwargs["product_card"]["manifest"]["pricing_amount"] = str(
                                first_option["fixed_price"]
                            )
                            product_kwargs["product_card"]["manifest"]["pricing_currency"] = first_option.get(
                                "currency_code", "USD"
                            )

                # Handle property authorization (AdCP requirement)
                # Default to empty property_tags if not specified (satisfies DB constraint)
                property_mode = form_data.get("property_mode", "tags")
                if property_mode == "tags":
                    # Parse property tags from comma-separated string
                    property_tags_str = form_data.get("property_tags", "").strip()
                    if property_tags_str:
                        property_tags = [
                            tag.strip().lower().replace("-", "_") for tag in property_tags_str.split(",") if tag.strip()
                        ]

                        # Server-side validation (defense in depth - client-side validation exists)
                        import re

                        for tag in property_tags:
                            # Length validation
                            if len(tag) < 2 or len(tag) > 50:
                                flash("Property tags must be 2-50 characters", "error")
                                return redirect(url_for("products.add_product", tenant_id=tenant_id))

                            # Character whitelist validation (AdCP spec: ^[a-z0-9_]+$)
                            if not re.match(r"^[a-z0-9_]+$", tag):
                                flash(
                                    f"Invalid tag '{tag}': use only lowercase letters, numbers, and underscores",
                                    "error",
                                )
                                return redirect(url_for("products.add_product", tenant_id=tenant_id))

                        # Check for duplicates
                        if len(property_tags) != len(set(property_tags)):
                            flash("Duplicate property tags detected", "error")
                            return redirect(url_for("products.add_product", tenant_id=tenant_id))

                        # Validate that all property tags exist in the database
                        if property_tags:
                            from src.core.database.models import PropertyTag

                            existing_tags = db_session.scalars(
                                select(PropertyTag).filter(
                                    PropertyTag.tenant_id == tenant_id, PropertyTag.tag_id.in_(property_tags)
                                )
                            ).all()

                            existing_tag_ids = {tag.tag_id for tag in existing_tags}
                            missing_tags = set(property_tags) - existing_tag_ids

                            if missing_tags:
                                flash(
                                    f"Property tags do not exist: {', '.join(missing_tags)}. "
                                    f"Please create them in Settings → Authorized Properties first.",
                                    "error",
                                )
                                return redirect(url_for("products.add_product", tenant_id=tenant_id))

                            product_kwargs["property_tags"] = property_tags
                    else:
                        # No tags provided, default to empty list to satisfy DB constraint
                        product_kwargs["property_tags"] = []
                elif property_mode == "full":
                    # Get selected property IDs and load full property objects
                    property_ids_str = request.form.getlist("property_ids")

                    # Validate property IDs are integers (security)
                    try:
                        property_ids = [int(pid) for pid in property_ids_str]
                    except (ValueError, TypeError):
                        flash("Invalid property IDs provided", "error")
                        return redirect(url_for("products.add_product", tenant_id=tenant_id))

                    if property_ids:
                        from src.core.database.models import AuthorizedProperty

                        properties = db_session.scalars(
                            select(AuthorizedProperty).filter(
                                AuthorizedProperty.id.in_(property_ids), AuthorizedProperty.tenant_id == tenant_id
                            )
                        ).all()

                        # Verify all requested IDs were found (prevent TOCTOU)
                        if len(properties) != len(property_ids):
                            flash("One or more selected properties not found or not authorized", "error")
                            return redirect(url_for("products.add_product", tenant_id=tenant_id))

                        if properties:
                            # Convert to dict format for JSONB storage
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
                    else:
                        # No properties selected, default to empty property_tags to satisfy DB constraint
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
                            return redirect(url_for("products.add_product", tenant_id=tenant_id))
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
                            pass  # Leave as None if invalid

                # Create product with correct fields matching the Product model
                product = Product(**product_kwargs)
                db_session.add(product)
                db_session.flush()  # Flush to get product ID before creating pricing options

                # Create pricing options (already parsed above)
                if pricing_options_data:
                    logger.info(
                        f"Creating {len(pricing_options_data)} pricing options for product {product.product_id}"
                    )
                    for option_data in pricing_options_data:
                        from decimal import Decimal

                        pricing_option = PricingOption(
                            tenant_id=tenant_id,
                            product_id=product.product_id,
                            pricing_model=option_data["pricing_model"],
                            rate=Decimal(str(option_data["rate"])) if option_data["rate"] is not None else None,
                            currency=option_data["currency"],
                            is_fixed=option_data["is_fixed"],
                            price_guidance=option_data["price_guidance"],
                            parameters=option_data["parameters"],
                            min_spend_per_package=(
                                Decimal(str(option_data["min_spend_per_package"]))
                                if option_data["min_spend_per_package"] is not None
                                else None
                            ),
                        )
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
                        logger.info(
                            f"Creating {len(custom_keys)} custom targeting key mappings for product {product.product_id}"
                        )
                        for _idx, (key, value) in enumerate(custom_keys.items()):
                            # Store as "key=value" format in inventory_id
                            custom_key_id = f"{key}={value}"
                            mapping = ProductInventoryMapping(
                                tenant_id=tenant_id,
                                product_id=product.product_id,
                                inventory_type="custom_key",
                                inventory_id=custom_key_id,
                                is_primary=False,
                            )
                            db_session.add(mapping)

                db_session.commit()

                flash(f"Product '{product.name}' created successfully!", "success")
                # Redirect to products list
                return redirect(url_for("products.list_products", tenant_id=tenant_id))

        except Exception as e:
            logger.error(f"Error creating product: {e}", exc_info=True)
            flash(f"Error creating product: {str(e)}", "error")
            return redirect(url_for("products.add_product", tenant_id=tenant_id))

    # GET request - show adapter-specific form
    # Load authorized properties and property tags for property selection
    with get_db_session() as db_session:
        from src.core.database.models import AuthorizedProperty, PropertyTag

        authorized_properties = db_session.scalars(
            select(AuthorizedProperty).filter_by(tenant_id=tenant_id, verification_status="verified")
        ).all()

        # Convert to dict for template
        properties_list = []
        for prop in authorized_properties:
            properties_list.append(
                {
                    "id": prop.property_id,
                    "name": prop.name,
                    "property_type": prop.property_type,
                    "tags": prop.tags or [],
                }
            )

        # Load all property tags for dropdown
        property_tags = db_session.scalars(
            select(PropertyTag).filter_by(tenant_id=tenant_id).order_by(PropertyTag.name)
        ).all()

    if adapter_type == "google_ad_manager":
        # For GAM: unified form with inventory selection
        # Check if inventory has been synced
        from src.core.database.models import GAMInventory

        with get_db_session() as db_session:
            inventory_count = db_session.scalar(
                select(func.count()).select_from(GAMInventory).filter_by(tenant_id=tenant_id)
            )
            inventory_synced = inventory_count > 0

            # Get signals agents for dynamic products
            from src.core.database.models import SignalsAgent

            stmt = select(SignalsAgent).filter_by(tenant_id=tenant_id, enabled=True)
            signals_agents = db_session.scalars(stmt).all()

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
        )
    else:
        # For Mock and other adapters: simple form
        formats = get_creative_formats(tenant_id=tenant_id)
        return render_template(
            "add_product_mock.html",
            tenant_id=tenant_id,
            formats=formats,
            authorized_properties=properties_list,
            property_tags=property_tags,
            currencies=currencies,
        )


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

    # Pre-validate formats BEFORE opening database session to avoid session conflicts
    validated_formats = None
    if request.method == "POST":
        formats_raw = request.form.getlist("formats")
        if formats_raw:
            import asyncio

            from src.core.creative_agent_registry import get_creative_agent_registry

            try:
                registry = get_creative_agent_registry()
                # Run async list_all_formats
                try:
                    loop = asyncio.get_running_loop()
                    # Already in async context, run in thread pool
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(lambda: asyncio.run(registry.list_all_formats(tenant_id=tenant_id)))
                        available_formats = future.result()
                except RuntimeError:
                    # No running loop, safe to create one
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        available_formats = loop.run_until_complete(registry.list_all_formats(tenant_id=tenant_id))
                    finally:
                        loop.close()

                # Build set of valid format IDs for quick lookup
                valid_format_ids = {f"{fmt.format_id.agent_url}|{fmt.format_id.id}" for fmt in available_formats}
                logger.info(f"[DEBUG] Found {len(valid_format_ids)} valid formats for tenant {tenant_id}")

                # Validate and convert formats
                validated_formats = []
                invalid_formats = []
                for fmt_str in formats_raw:
                    if "|" not in fmt_str:
                        logger.error(f"Invalid format value (missing agent_url): {fmt_str}")
                        continue

                    agent_url, format_id = fmt_str.split("|", 1)

                    # Normalize agent_url by ensuring it has a trailing slash
                    if not agent_url.endswith("/"):
                        agent_url_normalized = agent_url + "/"
                    else:
                        agent_url_normalized = agent_url

                    normalized_fmt_str = f"{agent_url_normalized}|{format_id}"

                    # Validate format exists (using normalized URL)
                    if normalized_fmt_str not in valid_format_ids:
                        invalid_formats.append(format_id)
                        logger.warning(f"Invalid format ID: {format_id} from {agent_url}")
                        continue

                    validated_formats.append({"agent_url": agent_url, "id": format_id})

                if invalid_formats:
                    flash(
                        f"Invalid format IDs: {', '.join(invalid_formats)}. "
                        f"These formats are not available for this tenant.",
                        "error",
                    )
                    return redirect(url_for("products.edit_product", tenant_id=tenant_id, product_id=product_id))

                if not validated_formats:
                    flash("No valid formats selected", "error")
                    return redirect(url_for("products.edit_product", tenant_id=tenant_id, product_id=product_id))

            except Exception as e:
                logger.error(f"Failed to fetch available formats: {e}")
                flash("Unable to validate formats. Please try again.", "error")
                return redirect(url_for("products.edit_product", tenant_id=tenant_id, product_id=product_id))

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

                # Apply validated formats (already validated above, outside session)
                if validated_formats is not None:
                    product.formats = validated_formats
                    logger.info(f"[DEBUG] Updated product.formats to: {validated_formats}")
                    # Flag JSONB column as modified so SQLAlchemy generates UPDATE
                    from sqlalchemy.orm import attributes

                    attributes.flag_modified(product, "formats")

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

                # Get pricing based on line item type (GAM form) or delivery type (other adapters)
                line_item_type = form_data.get("line_item_type")

                if line_item_type:
                    # GAM form: map line item type to delivery type
                    if line_item_type in ["STANDARD", "SPONSORSHIP"]:
                        product.delivery_type = "guaranteed"
                    elif line_item_type in ["PRICE_PRIORITY", "HOUSE"]:
                        product.delivery_type = "non_guaranteed"

                # Update implementation_config with GAM-specific fields
                # Note: This must run even if line_item_type is not present (automatic mode)
                if adapter_type == "google_ad_manager":
                    from src.services.gam_product_config_service import GAMProductConfigService

                    # Start with existing config to preserve fields not in the form
                    base_config = product.implementation_config.copy() if product.implementation_config else {}

                    # Only regenerate default config if we have line_item_type (explicit mode)
                    # Otherwise, preserve existing config structure
                    if line_item_type:
                        gam_config_service = GAMProductConfigService()
                        default_config = gam_config_service.generate_default_config(product.delivery_type, formats)
                        # Merge default config into base_config (preserving other fields)
                        base_config.update(default_config)

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

                    # Add GAM settings
                    if form_data.get("line_item_type"):
                        base_config["line_item_type"] = form_data["line_item_type"]
                    if form_data.get("priority"):
                        base_config["priority"] = int(form_data["priority"])

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
                        # Merge key-value pairs into implementation_config for GAM adapter
                        base_config["custom_targeting_keys"].update(targeting_template["key_value_pairs"])

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

                    # Recreate mappings from implementation_config
                    # Ad units
                    if base_config.get("targeted_ad_unit_ids"):
                        for idx, ad_unit_id in enumerate(base_config["targeted_ad_unit_ids"]):
                            mapping = ProductInventoryMapping(
                                tenant_id=tenant_id,
                                product_id=product_id,
                                inventory_type="ad_unit",
                                inventory_id=ad_unit_id,
                                is_primary=(idx == 0),
                            )
                            db_session.add(mapping)

                    # Placements
                    if base_config.get("targeted_placement_ids"):
                        for idx, placement_id in enumerate(base_config["targeted_placement_ids"]):
                            mapping = ProductInventoryMapping(
                                tenant_id=tenant_id,
                                product_id=product_id,
                                inventory_type="placement",
                                inventory_id=placement_id,
                                is_primary=(idx == 0),
                            )
                            db_session.add(mapping)

                    # Custom targeting keys
                    if base_config.get("custom_targeting_keys"):
                        for key, value in base_config["custom_targeting_keys"].items():
                            custom_key_id = f"{key}={value}"
                            mapping = ProductInventoryMapping(
                                tenant_id=tenant_id,
                                product_id=product_id,
                                inventory_type="custom_key",
                                inventory_id=custom_key_id,
                                is_primary=False,
                            )
                            db_session.add(mapping)

                # Update pricing options (AdCP PR #88)
                # Note: min_spend is now stored in pricing_options[].min_spend_per_package
                from decimal import Decimal

                # Parse pricing options from form FIRST
                pricing_options_data = parse_pricing_options_from_form(form_data)
                logger.info(
                    f"[DEBUG] Parsed {len(pricing_options_data) if pricing_options_data else 0} pricing options: {pricing_options_data}"
                )

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
                for idx, option_data in enumerate(pricing_options_data):
                    if idx < len(existing_options):
                        # Update existing pricing option
                        po = existing_options[idx]
                        po.pricing_model = option_data["pricing_model"]
                        po.rate = Decimal(str(option_data["rate"])) if option_data["rate"] is not None else None
                        po.currency = option_data["currency"]
                        po.is_fixed = option_data["is_fixed"]
                        po.price_guidance = option_data["price_guidance"]
                        po.parameters = option_data["parameters"]
                        po.min_spend_per_package = (
                            Decimal(str(option_data["min_spend_per_package"]))
                            if option_data["min_spend_per_package"] is not None
                            else None
                        )
                    else:
                        # Create new pricing option
                        pricing_option = PricingOption(
                            tenant_id=tenant_id,
                            product_id=product.product_id,
                            pricing_model=option_data["pricing_model"],
                            rate=Decimal(str(option_data["rate"])) if option_data["rate"] is not None else None,
                            currency=option_data["currency"],
                            is_fixed=option_data["is_fixed"],
                            price_guidance=option_data["price_guidance"],
                            parameters=option_data["parameters"],
                            min_spend_per_package=(
                                Decimal(str(option_data["min_spend_per_package"]))
                                if option_data["min_spend_per_package"] is not None
                                else None
                            ),
                        )
                        db_session.add(pricing_option)

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
                        product.product_card["manifest"]["pricing_model"] = first_option.get("pricing_model", "CPM")
                        if first_option.get("is_fixed") and first_option.get("rate"):
                            product.product_card["manifest"]["pricing_amount"] = str(first_option["rate"])
                            product.product_card["manifest"]["pricing_currency"] = first_option.get("currency", "USD")

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
                logger.info(f"[DEBUG] product.formats = {product.formats}")
                logger.info(f"[DEBUG] product.formats type = {type(product.formats)}")
                logger.info(f"[DEBUG] SQLAlchemy dirty objects: {db_session.dirty}")

                # Check if product is in dirty set and formats was modified
                if product in db_session.dirty:
                    insp = sa_inspect(product)
                    if insp.attrs.formats.history.has_changes():
                        logger.info("[DEBUG] formats attribute was modified")
                    else:
                        logger.info("[DEBUG] formats attribute NOT modified (flag_modified may be needed)")

                db_session.commit()

                # Debug: Verify formats after commit by re-querying
                db_session.refresh(product)
                logger.info(f"[DEBUG] After commit - product.formats from DB: {product.formats}")

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
            is_fixed_price = None
            cpm = None
            price_guidance = None

            if pricing_options_list:
                first_pricing = pricing_options_list[0]
                delivery_type = "guaranteed" if first_pricing["is_fixed"] else "non_guaranteed"
                is_fixed_price = first_pricing["is_fixed"]
                cpm = first_pricing["rate"]
                price_guidance = first_pricing["price_guidance"]

            # Parse implementation_config
            implementation_config = (
                product.implementation_config
                if isinstance(product.implementation_config, dict)
                else json.loads(product.implementation_config) if product.implementation_config else {}
            )

            # Parse targeting_template - build from implementation_config if not set
            targeting_template = (
                product.targeting_template
                if isinstance(product.targeting_template, dict)
                else json.loads(product.targeting_template) if product.targeting_template else {}
            )

            # If targeting_template doesn't have key_value_pairs but implementation_config has custom_targeting_keys,
            # populate targeting_template from implementation_config for backwards compatibility
            if not targeting_template.get("key_value_pairs") and implementation_config.get("custom_targeting_keys"):
                targeting_template["key_value_pairs"] = implementation_config["custom_targeting_keys"]

            product_dict = {
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "delivery_type": delivery_type,
                "is_fixed_price": is_fixed_price,
                "cpm": cpm,
                "price_guidance": price_guidance,
                "formats": (
                    product.formats
                    if isinstance(product.formats, list)
                    else json.loads(product.formats) if product.formats else []
                ),
                "countries": (
                    product.countries
                    if isinstance(product.countries, list)
                    else json.loads(product.countries) if product.countries else []
                ),
                "implementation_config": implementation_config,
                "targeting_template": targeting_template,
                # Product detail fields (AdCP compliance)
                "delivery_measurement": product.delivery_measurement,
                "product_card": product.product_card,
                "product_card_detailed": product.product_card_detailed,
                "placements": product.placements,
                "reporting_capabilities": product.reporting_capabilities,
            }

            product_dict["pricing_options"] = pricing_options_list

            # Show adapter-specific form
            if adapter_type == "google_ad_manager":
                from src.core.database.models import GAMInventory

                inventory_count = db_session.scalar(
                    select(func.count()).select_from(GAMInventory).filter_by(tenant_id=tenant_id)
                )
                inventory_synced = inventory_count > 0

                # Build set of selected format IDs for template checking
                # Use composite key (agent_url, format_id) tuples per AdCP spec (same as main.py)
                selected_format_ids = set()
                logger.info(
                    f"[DEBUG] Building selected_format_ids from product_dict['formats']: {product_dict['formats']}"
                )
                for fmt in product_dict["formats"]:
                    agent_url = None
                    format_id = None

                    if isinstance(fmt, dict):
                        # Database JSONB: uses "id" per AdCP spec
                        agent_url = fmt.get("agent_url")
                        format_id = fmt.get("id") or fmt.get("format_id")  # "id" is AdCP spec, "format_id" is legacy
                        logger.info(f"[DEBUG] Dict format: agent_url={agent_url}, format_id={format_id}")
                    elif hasattr(fmt, "agent_url") and (hasattr(fmt, "format_id") or hasattr(fmt, "id")):
                        # Pydantic object: uses "format_id" attribute (serializes to "id" in JSON)
                        agent_url = fmt.agent_url
                        format_id = getattr(fmt, "format_id", None) or getattr(fmt, "id", None)
                        logger.info(f"[DEBUG] Pydantic format: agent_url={agent_url}, format_id={format_id}")
                    elif isinstance(fmt, str):
                        # Legacy: plain string format ID (no agent_url) - should be deprecated
                        format_id = fmt
                        logger.warning(f"Product {product_dict['product_id']} has legacy string format: {fmt}")

                    if format_id:
                        selected_format_ids.add((agent_url, format_id))

                logger.info(f"[DEBUG] Final selected_format_ids set: {selected_format_ids}")

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

                return render_template(
                    "add_product_gam.html",
                    tenant_id=tenant_id,
                    product=product_dict,
                    selected_format_ids=selected_format_ids,
                    inventory_synced=inventory_synced,
                    formats=get_creative_formats(tenant_id=tenant_id),
                    currencies=currencies,
                    assigned_inventory=assigned_inventory,
                )
            else:
                return render_template(
                    "edit_product_mock.html",
                    tenant_id=tenant_id,
                    product=product_dict,
                    tenant_adapter=adapter_type,
                    currencies=currencies,
                )

    except Exception as e:
        logger.error(f"Error editing product: {e}", exc_info=True)
        flash(f"Error editing product: {str(e)}", "error")
        return redirect(url_for("products.list_products", tenant_id=tenant_id))


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
            # Import here to avoid circular imports
            from src.core.database.models import MediaBuy

            stmt = (
                select(MediaBuy)
                .filter_by(tenant_id=tenant_id)
                .filter(MediaBuy.status.in_(["pending", "active", "paused"]))
            )
            active_buys = db_session.scalars(stmt).all()

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
        except:
            pass

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
            existing = db_session.scalars(
                select(ProductInventoryMapping).filter_by(
                    tenant_id=tenant_id, product_id=product_id, inventory_id=inventory_id, inventory_type=inventory_type
                )
            ).first()

            if existing:
                # Update existing mapping
                existing.is_primary = is_primary
                db_session.commit()
            else:
                # Create new mapping
                mapping = ProductInventoryMapping(
                    tenant_id=tenant_id,
                    product_id=product_id,
                    inventory_id=inventory_id,
                    inventory_type=inventory_type,
                    is_primary=is_primary,
                )
                db_session.add(mapping)
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
