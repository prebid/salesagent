"""Products management blueprint for admin UI."""

import json
import logging
import uuid

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from src.admin.utils import require_tenant_access
from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption, Product, Tenant
from src.core.database.product_pricing import get_product_pricing_options
from src.core.validation import sanitize_form_data
from src.services.gam_product_config_service import GAMProductConfigService

logger = logging.getLogger(__name__)

# Create Blueprint
products_bp = Blueprint("products", __name__)


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
    for fmt in formats:
        format_dict = {
            "format_id": fmt.format_id,
            "agent_url": fmt.agent_url,
            "name": fmt.name,
            "type": fmt.type,
            "category": fmt.category,
            "description": fmt.description or f"{fmt.name} - {fmt.iab_specification or 'Standard format'}",
            "dimensions": None,
            "duration": None,
        }

        # Add dimensions for display/video formats
        if fmt.requirements and "width" in fmt.requirements and "height" in fmt.requirements:
            format_dict["dimensions"] = f"{fmt.requirements['width']}x{fmt.requirements['height']}"
        elif "_" in fmt.format_id:
            # Fallback: Parse dimensions from format_id (e.g., "display_300x250_image" → "300x250")
            # This handles creative agents that don't populate requirements field
            import re

            match = re.search(r"_(\d+)x(\d+)_", fmt.format_id)
            if match:
                format_dict["dimensions"] = f"{match.group(1)}x{match.group(2)}"

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

        # Parse pricing model and is_fixed from combined value (AdCP PR #88 UI update)
        # Values are: cpm_fixed, cpm_auction, cpcv, cpp, cpc, cpv, flat_rate
        if pricing_model_raw == "cpm_fixed":
            pricing_model = "cpm"
            is_fixed = True
        elif pricing_model_raw == "cpm_auction":
            pricing_model = "cpm"
            is_fixed = False
        else:
            # All other models are fixed-rate only (cpcv, cpp, cpc, cpv, flat_rate)
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
            if floor_str:
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
                    pass

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

            products = db_session.scalars(
                select(Product)
                .options(joinedload(Product.pricing_options))
                .filter_by(tenant_id=tenant_id)
                .order_by(Product.name)
            ).all()

            # Convert products to dict format for template
            products_list = []
            for product in products:
                # Use helper function to get pricing options (handles legacy fallback)
                pricing_options_list = get_product_pricing_options(product)

                product_dict = {
                    "product_id": product.product_id,
                    "name": product.name,
                    "description": product.description,
                    "pricing_options": pricing_options_list,
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
                    "implementation_config": (
                        product.implementation_config
                        if isinstance(product.implementation_config, dict)
                        else json.loads(product.implementation_config) if product.implementation_config else {}
                    ),
                    "created_at": product.created_at if hasattr(product, "created_at") else None,
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
                # Parse formats - expecting JSON string with FormatReference objects
                formats_json = form_data.get("formats", "[]")
                try:
                    formats = json.loads(formats_json) if formats_json else []
                    # Validate format structure
                    if not isinstance(formats, list):
                        formats = []
                except json.JSONDecodeError:
                    # Fallback to legacy format (list of strings)
                    formats = request.form.getlist("formats")
                    if not formats:
                        formats = []

                # Parse countries - from multi-select
                countries_list = request.form.getlist("countries")
                # Only set countries if some were selected; None means all countries
                countries = countries_list if countries_list and "ALL" not in countries_list else None

                # Parse and create pricing options (AdCP PR #88)
                pricing_options_data = parse_pricing_options_from_form(form_data)

                # Derive legacy fields (delivery_type, cpm, price_guidance) from first pricing option for backwards compatibility
                # These legacy fields are used by implementation_config but will eventually be deprecated
                delivery_type = "guaranteed"  # Default
                cpm = None
                price_guidance = None

                if pricing_options_data and len(pricing_options_data) > 0:
                    first_option = pricing_options_data[0]
                    # Determine delivery_type based on is_fixed
                    if first_option.get("is_fixed", True):
                        delivery_type = "guaranteed"
                        cpm = first_option.get("rate")
                    else:
                        delivery_type = "non-guaranteed"
                        pg = first_option.get("price_guidance")
                        if pg and "floor" in pg:
                            price_guidance = {"min": pg["floor"], "max": pg.get("p90", pg["floor"])}

                # Build implementation config based on adapter type
                implementation_config = {}
                if adapter_type == "google_ad_manager":
                    # Parse GAM-specific fields from unified form
                    gam_config_service = GAMProductConfigService()
                    base_config = gam_config_service.generate_default_config(delivery_type, formats)

                    # Add ad unit/placement targeting if provided
                    ad_unit_ids = form_data.get("targeted_ad_unit_ids", "").strip()
                    if ad_unit_ids:
                        base_config["targeted_ad_unit_ids"] = [
                            id.strip() for id in ad_unit_ids.split(",") if id.strip()
                        ]

                    placement_ids = form_data.get("targeted_placement_ids", "").strip()
                    if placement_ids:
                        base_config["targeted_placement_ids"] = [
                            id.strip() for id in placement_ids.split(",") if id.strip()
                        ]

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

                # Build product kwargs, excluding None values for JSON fields that have database constraints
                product_kwargs = {
                    "product_id": form_data.get("product_id") or f"prod_{uuid.uuid4().hex[:8]}",
                    "tenant_id": tenant_id,
                    "name": form_data["name"],
                    "description": form_data.get("description", ""),
                    "formats": formats,
                    "delivery_type": delivery_type,
                    "is_fixed_price": (delivery_type == "guaranteed"),
                    "cpm": cpm,
                    "price_guidance": price_guidance,
                    "targeting_template": {},
                    "implementation_config": implementation_config,
                }

                # Only add countries if explicitly set
                if countries is not None:
                    product_kwargs["countries"] = countries

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

                db_session.commit()

                flash(f"Product '{product.name}' created successfully!", "success")
                # Redirect to products list
                return redirect(url_for("products.list_products", tenant_id=tenant_id))

        except Exception as e:
            logger.error(f"Error creating product: {e}", exc_info=True)
            flash("Error creating product", "error")
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
                    "id": prop.id,
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

        return render_template(
            "add_product_gam.html",
            tenant_id=tenant_id,
            inventory_synced=inventory_synced,
            formats=get_creative_formats(tenant_id=tenant_id),
            authorized_properties=properties_list,
            property_tags=property_tags,
            currencies=currencies,
        )
    else:
        # For Mock and other adapters: simple form
        formats = get_creative_formats(tenant_id=tenant_id)
        return render_template(
            "add_product.html",
            tenant_id=tenant_id,
            formats=formats,
            authorized_properties=properties_list,
            property_tags=property_tags,
            currencies=currencies,
        )


@products_bp.route("/<product_id>/edit", methods=["GET", "POST"])
@require_tenant_access()
def edit_product(tenant_id, product_id):
    """Edit an existing product."""
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

                # Parse formats - expecting multiple checkbox values
                formats = request.form.getlist("formats")
                if formats:
                    product.formats = formats

                # Parse countries - from multi-select
                countries_list = request.form.getlist("countries")
                if countries_list and "ALL" not in countries_list:
                    product.countries = countries_list
                else:
                    product.countries = None

                # Get pricing based on line item type (GAM form) or delivery type (other adapters)
                line_item_type = form_data.get("line_item_type")

                if line_item_type:
                    # GAM form: map line item type to delivery type and pricing
                    if line_item_type in ["STANDARD", "SPONSORSHIP"]:
                        product.delivery_type = "guaranteed"
                        product.is_fixed_price = True
                        product.cpm = float(form_data.get("cpm", 0)) if form_data.get("cpm") else None
                        product.price_guidance = None
                    elif line_item_type == "PRICE_PRIORITY":
                        product.delivery_type = "non-guaranteed"
                        product.is_fixed_price = False
                        product.cpm = None
                        floor_cpm = float(form_data.get("floor_cpm", 0)) if form_data.get("floor_cpm") else None
                        if floor_cpm:
                            product.price_guidance = {"min": floor_cpm, "max": floor_cpm}
                    elif line_item_type == "HOUSE":
                        product.delivery_type = "non-guaranteed"
                        product.is_fixed_price = False
                        product.cpm = None
                        product.price_guidance = None

                    # Update implementation_config with GAM-specific fields
                    if adapter_type == "google_ad_manager":
                        from src.services.gam_product_config_service import GAMProductConfigService

                        gam_config_service = GAMProductConfigService()
                        base_config = gam_config_service.generate_default_config(product.delivery_type, formats)

                        # Add ad unit/placement targeting if provided
                        ad_unit_ids = form_data.get("targeted_ad_unit_ids", "").strip()
                        if ad_unit_ids:
                            base_config["targeted_ad_unit_ids"] = [
                                id.strip() for id in ad_unit_ids.split(",") if id.strip()
                            ]

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

                        product.implementation_config = base_config
                        from sqlalchemy.orm import attributes

                        attributes.flag_modified(product, "implementation_config")

                # Update minimum spend override
                from decimal import Decimal, InvalidOperation

                min_spend_str = form_data.get("min_spend", "").strip()
                if min_spend_str:
                    try:
                        product.min_spend = Decimal(min_spend_str)
                    except (ValueError, InvalidOperation):
                        flash("Invalid minimum spend value", "error")
                        return redirect(url_for("products.edit_product", tenant_id=tenant_id, product_id=product_id))
                else:
                    product.min_spend = None

                # Update pricing options (AdCP PR #88)
                # Delete existing pricing options and recreate from form
                db_session.query(PricingOption).filter_by(tenant_id=tenant_id, product_id=product_id).delete()

                pricing_options_data = parse_pricing_options_from_form(form_data)
                if pricing_options_data:
                    logger.info(
                        f"Updating {len(pricing_options_data)} pricing options for product {product.product_id}"
                    )
                    for option_data in pricing_options_data:
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

                db_session.commit()

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

            # Derive legacy fields from pricing_options (preferred) or product model (fallback)
            # This ensures form displays pricing_options data, not potentially stale legacy fields
            if pricing_options_list:
                first_pricing = pricing_options_list[0]
                delivery_type = "guaranteed" if first_pricing["is_fixed"] else "non_guaranteed"
                is_fixed_price = first_pricing["is_fixed"]
                cpm = first_pricing["rate"]
                price_guidance = first_pricing["price_guidance"]
            else:
                # Fallback to legacy fields if no pricing_options exist
                delivery_type = product.delivery_type
                is_fixed_price = product.is_fixed_price
                cpm = product.cpm
                price_guidance = product.price_guidance

            product_dict = {
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "delivery_type": delivery_type,
                "is_fixed_price": is_fixed_price,
                "cpm": cpm,
                "min_spend": product.min_spend,
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
                "implementation_config": (
                    product.implementation_config
                    if isinstance(product.implementation_config, dict)
                    else json.loads(product.implementation_config) if product.implementation_config else {}
                ),
            }

            product_dict["pricing_options"] = pricing_options_list

            # Show adapter-specific form
            if adapter_type == "google_ad_manager":
                from src.core.database.models import GAMInventory

                inventory_count = db_session.scalar(
                    select(func.count()).select_from(GAMInventory).filter_by(tenant_id=tenant_id)
                )
                inventory_synced = inventory_count > 0

                return render_template(
                    "add_product_gam.html",
                    tenant_id=tenant_id,
                    product=product_dict,
                    inventory_synced=inventory_synced,
                    formats=get_creative_formats(),
                    currencies=currencies,
                )
            else:
                return render_template(
                    "edit_product.html",
                    tenant_id=tenant_id,
                    product=product_dict,
                    tenant_adapter=adapter_type,
                    currencies=currencies,
                )

    except Exception as e:
        logger.error(f"Error editing product: {e}", exc_info=True)
        flash("Error editing product", "error")
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

            # Delete the product
            db_session.delete(product)
            db_session.commit()

            logger.info(f"Product {product_id} ({product_name}) deleted by tenant {tenant_id}")

            return jsonify({"success": True, "message": f"Product '{product_name}' deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting product {product_id}: {e}", exc_info=True)
        # Sanitize error messages to prevent information leakage
        error_message = str(e)
        if "ValidationError" in error_message or "pattern" in error_message.lower():
            logger.warning(f"Product validation error for {product_id}: {error_message}")
            return jsonify({"error": "Product data validation failed"}), 400

        logger.error(f"Product deletion failed for {product_id}: {error_message}")
        return jsonify({"error": "Failed to delete product. Please contact support."}), 500
