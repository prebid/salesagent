"""Products management blueprint for admin UI."""

import csv
import io
import json
import logging
import uuid

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from src.admin.utils import require_tenant_access
from src.core.database.database_session import get_db_session
from src.core.database.models import CreativeFormat, Product, Tenant
from src.core.validation import sanitize_form_data
from src.services.ai_product_service import AIProductConfigurationService
from src.services.default_products import get_default_products, get_industry_specific_products

logger = logging.getLogger(__name__)

# Create Blueprint
products_bp = Blueprint("products", __name__)


def get_creative_formats():
    """Get all available creative formats for the product form."""
    try:
        with get_db_session() as db_session:
            # Get both foundational formats (tenant_id is None) and tenant-specific formats
            formats = (
                db_session.query(CreativeFormat)
                .filter(
                    CreativeFormat.tenant_id.is_(None)  # Only get foundational/standard formats for product creation
                )
                .order_by(CreativeFormat.type, CreativeFormat.name)
                .all()
            )

            # Convert to dict format for template
            formats_list = []
            for fmt in formats:
                format_dict = {
                    "format_id": fmt.format_id,
                    "name": fmt.name,
                    "type": fmt.type,
                    "description": fmt.description,
                    "dimensions": None,
                    "duration": None,
                }

                # Add dimensions for display formats
                if fmt.width and fmt.height:
                    format_dict["dimensions"] = f"{fmt.width}x{fmt.height}"

                # Add duration for video/audio formats
                if fmt.duration_seconds:
                    format_dict["duration"] = f"{fmt.duration_seconds}s"

                formats_list.append(format_dict)

            return formats_list
    except Exception as e:
        logger.error(f"Error loading creative formats: {e}", exc_info=True)
        # Return default formats as fallback
        return [
            {
                "format_id": "display_300x250",
                "name": "Medium Rectangle",
                "type": "display",
                "description": "Standard display banner",
                "dimensions": "300x250",
                "duration": None,
            },
            {
                "format_id": "display_728x90",
                "name": "Leaderboard",
                "type": "display",
                "description": "Top of page banner",
                "dimensions": "728x90",
                "duration": None,
            },
            {
                "format_id": "video_30s",
                "name": "30 Second Video",
                "type": "video",
                "description": "Standard video advertisement",
                "dimensions": None,
                "duration": "30s",
            },
        ]


@products_bp.route("/")
@require_tenant_access()
def list_products(tenant_id):
    """List all products for a tenant."""
    try:
        with get_db_session() as db_session:
            tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            products = db_session.query(Product).filter_by(tenant_id=tenant_id).order_by(Product.name).all()

            # Convert products to dict format for template
            products_list = []
            for product in products:
                product_dict = {
                    "product_id": product.product_id,
                    "name": product.name,
                    "description": product.description,
                    "delivery_type": product.delivery_type,
                    "is_fixed_price": product.is_fixed_price,
                    "cpm": product.cpm,
                    "price_guidance": product.price_guidance,
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
    """Add a new product."""
    if request.method == "POST":
        try:
            # Sanitize form data
            form_data = sanitize_form_data(request.form.to_dict())

            # Validate required fields
            if not form_data.get("name"):
                flash("Product name is required", "error")
                return redirect(url_for("products.add_product", tenant_id=tenant_id))

            with get_db_session() as db_session:
                # Parse formats - expecting multiple checkbox values
                formats = request.form.getlist("formats")
                if not formats:
                    formats = []

                # Parse countries - from multi-select
                countries = request.form.getlist("countries")
                if not countries or "ALL" in countries:
                    countries = None  # None means all countries

                # Get pricing based on delivery type
                delivery_type = form_data.get("delivery_type", "guaranteed")
                cpm = None
                price_guidance = None

                if delivery_type == "guaranteed":
                    cpm = float(form_data.get("cpm", 0)) if form_data.get("cpm") else None
                else:
                    # Non-guaranteed - use price guidance
                    price_min = (
                        float(form_data.get("price_guidance_min", 0)) if form_data.get("price_guidance_min") else None
                    )
                    price_max = (
                        float(form_data.get("price_guidance_max", 0)) if form_data.get("price_guidance_max") else None
                    )
                    if price_min and price_max:
                        price_guidance = {"min": price_min, "max": price_max}

                # Create product with correct fields matching the Product model
                product = Product(
                    product_id=form_data.get("product_id") or f"prod_{uuid.uuid4().hex[:8]}",
                    tenant_id=tenant_id,
                    name=form_data["name"],
                    description=form_data.get("description", ""),
                    formats=formats,  # List, not JSON string
                    countries=countries,  # List or None
                    delivery_type=delivery_type,
                    is_fixed_price=(delivery_type == "guaranteed"),
                    cpm=cpm,
                    price_guidance=price_guidance,
                    targeting_template={},  # Empty targeting template
                    implementation_config=None,
                )
                db_session.add(product)
                db_session.commit()

                flash(f"Product '{product.name}' created successfully", "success")
                return redirect(url_for("products.list_products", tenant_id=tenant_id))

        except Exception as e:
            logger.error(f"Error creating product: {e}", exc_info=True)
            flash("Error creating product", "error")
            return redirect(url_for("products.add_product", tenant_id=tenant_id))

    # GET request - show form
    formats = get_creative_formats()
    return render_template("add_product.html", tenant_id=tenant_id, formats=formats)


@products_bp.route("/<product_id>/edit", methods=["GET", "POST"])
@require_tenant_access()
def edit_product(tenant_id, product_id):
    """Edit an existing product."""
    try:
        with get_db_session() as db_session:
            product = db_session.query(Product).filter_by(tenant_id=tenant_id, product_id=product_id).first()
            if not product:
                flash("Product not found", "error")
                return redirect(url_for("products.list_products", tenant_id=tenant_id))

            if request.method == "POST":
                # Sanitize form data
                form_data = sanitize_form_data(request.form.to_dict())

                # Update product
                product.name = form_data.get("name", product.name)
                product.description = form_data.get("description", product.description)
                product.delivery_type = form_data.get("delivery_type", product.delivery_type)
                product.is_fixed_price = form_data.get("is_fixed_price", "true").lower() == "true"

                # Update pricing based on delivery type
                if product.is_fixed_price:
                    product.cpm = float(form_data.get("cpm")) if form_data.get("cpm") else product.cpm
                    product.price_guidance = None
                else:
                    product.cpm = None
                    price_min = (
                        float(form_data.get("price_guidance_min")) if form_data.get("price_guidance_min") else None
                    )
                    price_max = (
                        float(form_data.get("price_guidance_max")) if form_data.get("price_guidance_max") else None
                    )
                    if price_min and price_max:
                        product.price_guidance = {"min": price_min, "max": price_max}

                # Update formats and countries
                if "formats" in form_data:
                    formats = [f.strip() for f in form_data["formats"].split(",") if f.strip()]
                    product.formats = formats

                if "countries" in form_data:
                    countries = [c.strip().upper() for c in form_data["countries"].split(",") if c.strip()]
                    product.countries = countries
                db_session.commit()

                flash(f"Product '{product.name}' updated successfully", "success")
                return redirect(url_for("products.list_products", tenant_id=tenant_id))

            # GET request - show form
            product_dict = {
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "delivery_type": product.delivery_type,
                "is_fixed_price": product.is_fixed_price,
                "cpm": product.cpm,
                "price_guidance": product.price_guidance,
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
            }

            return render_template(
                "edit_product.html",
                tenant_id=tenant_id,
                product=product_dict,
            )

    except Exception as e:
        logger.error(f"Error editing product: {e}", exc_info=True)
        flash("Error editing product", "error")
        return redirect(url_for("products.list_products", tenant_id=tenant_id))


@products_bp.route("/add/ai", methods=["GET"])
@require_tenant_access()
def add_product_ai_form(tenant_id):
    """Show AI-powered product creation form."""
    return render_template("add_product_ai.html", tenant_id=tenant_id)


@products_bp.route("/analyze_ai", methods=["POST"])
@require_tenant_access()
def analyze_product_ai(tenant_id):
    """Analyze product description with AI."""
    try:
        data = request.get_json()
        description = data.get("description", "").strip()

        if not description:
            return jsonify({"error": "Description is required"}), 400

        # Use AI service to analyze
        ai_service = AIProductConfigurationService()
        result = ai_service.analyze_product_description(description)

        if result:
            return jsonify(result)
        else:
            return jsonify({"error": "Failed to analyze description"}), 500

    except Exception as e:
        logger.error(f"Error analyzing product with AI: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@products_bp.route("/bulk", methods=["GET"])
@require_tenant_access()
def bulk_upload_form(tenant_id):
    """Show bulk product upload form."""
    return render_template("bulk_product_upload.html", tenant_id=tenant_id)


@products_bp.route("/bulk/upload", methods=["POST"])
@require_tenant_access()
def bulk_upload(tenant_id):
    """Handle bulk product upload."""
    try:
        if "file" not in request.files:
            flash("No file uploaded", "error")
            return redirect(url_for("products.bulk_upload_form", tenant_id=tenant_id))

        file = request.files["file"]
        if file.filename == "":
            flash("No file selected", "error")
            return redirect(url_for("products.bulk_upload_form", tenant_id=tenant_id))

        # Check file extension
        if not file.filename.lower().endswith((".csv", ".json")):
            flash("Only CSV and JSON files are supported", "error")
            return redirect(url_for("products.bulk_upload_form", tenant_id=tenant_id))

        # Process file
        created_count = 0

        with get_db_session() as db_session:
            if file.filename.lower().endswith(".csv"):
                # Process CSV
                stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
                csv_reader = csv.DictReader(stream)

                for row in csv_reader:
                    try:
                        # Parse formats if it's a JSON string
                        formats = row.get("formats", "[]")
                        if isinstance(formats, str):
                            try:
                                formats = json.loads(formats)
                            except:
                                formats = []

                        # Parse targeting_template if it's a JSON string
                        targeting = row.get("targeting_template", "{}")
                        if isinstance(targeting, str):
                            try:
                                targeting = json.loads(targeting)
                            except:
                                targeting = {}

                        product = Product(
                            product_id=row.get("product_id") or f"prod_{uuid.uuid4().hex[:8]}",
                            tenant_id=tenant_id,
                            name=row.get("name", ""),
                            description=row.get("description", ""),
                            formats=formats,
                            targeting_template=targeting,
                            delivery_type=row.get("delivery_type", "standard"),
                            is_fixed_price=(
                                row.get("is_fixed_price", True)
                                if isinstance(row.get("is_fixed_price"), bool)
                                else str(row.get("is_fixed_price", "true")).lower() == "true"
                            ),
                            cpm=float(row.get("cpm", 0)) if row.get("cpm") else None,
                            price_guidance=None,
                            countries=None,
                            implementation_config=None,
                        )
                        db_session.add(product)
                        created_count += 1
                    except Exception as e:
                        logger.error(f"Error processing row: {e}")
                        continue

            else:
                # Process JSON
                data = json.loads(file.stream.read())
                products_data = data if isinstance(data, list) else [data]

                for item in products_data:
                    try:
                        # Parse formats from JSON
                        formats = item.get("formats", [])
                        if isinstance(formats, str):
                            try:
                                formats = json.loads(formats)
                            except:
                                formats = []

                        # Parse targeting_template from JSON
                        targeting = item.get("targeting_template", {})
                        if isinstance(targeting, str):
                            try:
                                targeting = json.loads(targeting)
                            except:
                                targeting = {}

                        # Parse countries
                        countries = item.get("countries")
                        if isinstance(countries, str):
                            try:
                                countries = json.loads(countries)
                            except:
                                countries = None

                        product = Product(
                            product_id=item.get("product_id") or f"prod_{uuid.uuid4().hex[:8]}",
                            tenant_id=tenant_id,
                            name=item.get("name", ""),
                            description=item.get("description", ""),
                            formats=formats,
                            targeting_template=targeting,
                            delivery_type=item.get("delivery_type", "standard"),
                            is_fixed_price=item.get("is_fixed_price", True),
                            cpm=float(item.get("cpm", 0)) if item.get("cpm") else None,
                            price_guidance=item.get("price_guidance"),
                            countries=countries,
                            implementation_config=item.get("implementation_config"),
                        )
                        db_session.add(product)
                        created_count += 1
                    except Exception as e:
                        logger.error(f"Error processing item: {e}")
                        continue

            db_session.commit()
            flash(f"Successfully created {created_count} products", "success")

    except Exception as e:
        logger.error(f"Error in bulk upload: {e}", exc_info=True)
        flash("Error processing file", "error")

    return redirect(url_for("products.list_products", tenant_id=tenant_id))


@products_bp.route("/templates", methods=["GET"])
@require_tenant_access()
def get_templates(tenant_id):
    """Get product templates."""
    try:
        # Get industry filter
        industry = request.args.get("industry", "all")

        # Get templates
        if industry and industry != "all":
            products = get_industry_specific_products(industry)
        else:
            products = get_default_products()

        # Convert to template format
        templates = {}
        for product in products:
            templates[product.get("product_id", product["name"].lower().replace(" ", "_"))] = product

        return jsonify({"templates": templates})

    except Exception as e:
        logger.error(f"Error getting templates: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@products_bp.route("/templates/browse", methods=["GET"])
@require_tenant_access()
def browse_templates(tenant_id):
    """Browse and use product templates."""

    # Get all available templates
    standard_templates = get_default_products()

    # Get industry templates for different industries
    industry_templates = {
        "news": get_industry_specific_products("news"),
        "sports": get_industry_specific_products("sports"),
        "entertainment": get_industry_specific_products("entertainment"),
        "ecommerce": get_industry_specific_products("ecommerce"),
    }

    # Filter out standard templates from industry lists
    standard_ids = {t["product_id"] for t in standard_templates}
    for industry in industry_templates:
        industry_templates[industry] = [t for t in industry_templates[industry] if t["product_id"] not in standard_ids]

    # Get creative formats for display
    formats = get_creative_formats()

    return render_template(
        "product_templates.html",
        tenant_id=tenant_id,
        standard_templates=standard_templates,
        industry_templates=industry_templates,
        formats=formats,
    )


@products_bp.route("/templates/create", methods=["POST"])
@require_tenant_access()
def create_from_template(tenant_id):
    """Create a product from a template."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request"}), 400

        template_id = data.get("template_id")
        if not template_id:
            return jsonify({"error": "Template ID required"}), 400

        # Get all available templates
        all_templates = get_default_products()
        # Add industry templates
        for industry in ["news", "sports", "entertainment", "ecommerce"]:
            all_templates.extend(get_industry_specific_products(industry))

        # Find the template
        template = None
        for t in all_templates:
            if t.get("product_id") == template_id:
                template = t
                break

        if not template:
            return jsonify({"error": "Template not found"}), 404

        # Create product from template
        with get_db_session() as db_session:
            product_id = f"prod_{uuid.uuid4().hex[:8]}"

            # Convert template to product
            product = Product(
                tenant_id=tenant_id,
                product_id=product_id,
                name=template.get("name"),
                description=template.get("description"),
                formats=template.get("formats", []),
                countries=template.get("countries"),
                targeting_template=template.get("targeting_template", {}),
                delivery_type=template.get("delivery_type", "standard"),
                is_fixed_price=template.get("pricing", {}).get("model", "CPM") == "CPM",
                cpm=(
                    template.get("pricing", {}).get("base_price")
                    if template.get("pricing", {}).get("model", "CPM") == "CPM"
                    else None
                ),
                price_guidance=(
                    {
                        "min": template.get("pricing", {}).get("min_spend", 0),
                        "max": template.get("pricing", {}).get("base_price", 0),
                    }
                    if template.get("pricing", {}).get("model", "CPM") != "CPM"
                    else None
                ),
                implementation_config=None,
            )

            db_session.add(product)
            db_session.commit()

            return jsonify(
                {
                    "success": True,
                    "product_id": product_id,
                    "message": f"Product '{template.get('name')}' created successfully",
                }
            )

    except Exception as e:
        logger.error(f"Error creating product from template: {e}", exc_info=True)
        return jsonify({"error": "Failed to create product"}), 500


@products_bp.route("/setup-wizard")
@require_tenant_access()
def setup_wizard(tenant_id):
    """Show product setup wizard for new tenants."""
    with get_db_session() as db_session:
        tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("core.index"))

        # Check if tenant already has products
        product_count = db_session.query(Product).filter_by(tenant_id=tenant_id).count()

        # Get industry from tenant config
        from src.admin.utils import get_tenant_config_from_db

        config = get_tenant_config_from_db(tenant_id)
        tenant_industry = config.get("industry", "general")

        # Get AI service
        ai_service = AIProductConfigurationService()

        # Get suggestions based on industry
        suggestions = ai_service.get_product_suggestions(
            industry=tenant_industry, include_standard=True, include_industry=True
        )

        # Get creative formats for display
        formats = get_creative_formats()

        return render_template(
            "product_setup_wizard.html",
            tenant_id=tenant_id,
            tenant_name=tenant.name,
            tenant_industry=tenant_industry,
            has_existing_products=product_count > 0,
            suggestions=suggestions,
            formats=formats,
        )


@products_bp.route("/<product_id>/delete", methods=["DELETE"])
@require_tenant_access()
def delete_product(tenant_id, product_id):
    """Delete a product."""
    try:
        with get_db_session() as db_session:
            # Find the product
            product = db_session.query(Product).filter_by(tenant_id=tenant_id, product_id=product_id).first()

            if not product:
                return jsonify({"error": "Product not found"}), 404

            # Store product name for response
            product_name = product.name

            # Check if product is used in any active media buys
            # Import here to avoid circular imports
            from src.core.database.models import MediaBuy

            active_buys = (
                db_session.query(MediaBuy)
                .filter_by(tenant_id=tenant_id)
                .filter(MediaBuy.status.in_(["pending", "active", "paused"]))
                .all()
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


@products_bp.route("/create-bulk", methods=["POST"])
@require_tenant_access()
def create_bulk(tenant_id):
    """Create multiple products from wizard suggestions."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request"}), 400

        product_ids = data.get("product_ids", [])
        if not product_ids:
            return jsonify({"error": "No products selected"}), 400

        # Get all available templates
        all_templates = get_default_products()
        # Add industry templates
        for industry in ["news", "sports", "entertainment", "ecommerce"]:
            all_templates.extend(get_industry_specific_products(industry))

        created_products = []
        errors = []

        with get_db_session() as db_session:
            for template_id in product_ids:
                # Find the template
                template = None
                for t in all_templates:
                    if t.get("product_id") == template_id:
                        template = t
                        break

                if not template:
                    errors.append(f"Template '{template_id}' not found")
                    continue

                try:
                    # Create unique product ID
                    product_id = f"prod_{uuid.uuid4().hex[:8]}"

                    # Convert template to product
                    product = Product(
                        tenant_id=tenant_id,
                        product_id=product_id,
                        name=template.get("name"),
                        description=template.get("description"),
                        formats=template.get("formats", []),
                        countries=template.get("countries"),
                        targeting_template=template.get("targeting_template", {}),
                        delivery_type=template.get("delivery_type", "standard"),
                        is_fixed_price=template.get("pricing", {}).get("model", "CPM") == "CPM",
                        cpm=(
                            template.get("pricing", {}).get("base_price")
                            if template.get("pricing", {}).get("model", "CPM") == "CPM"
                            else None
                        ),
                        price_guidance=(
                            {
                                "min": template.get("pricing", {}).get("min_spend", 0),
                                "max": template.get("pricing", {}).get("base_price", 0),
                            }
                            if template.get("pricing", {}).get("model", "CPM") != "CPM"
                            else None
                        ),
                        implementation_config=None,
                    )

                    db_session.add(product)
                    created_products.append({"product_id": product_id, "name": template.get("name")})

                except Exception as e:
                    logger.error(f"Error creating product from template {template_id}: {e}")
                    errors.append(f"Failed to create '{template.get('name', template_id)}': {str(e)}")

            db_session.commit()

        return jsonify(
            {
                "success": len(created_products) > 0,
                "created": created_products,
                "errors": errors,
                "message": f"Created {len(created_products)} products successfully",
            }
        )

    except Exception as e:
        logger.error(f"Error creating bulk products: {e}", exc_info=True)
        return jsonify({"error": "Failed to create products"}), 500
