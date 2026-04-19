"""Admin blueprint for managing inventory profiles.

Inventory profiles are reusable configurations of:
- Ad units and placements (inventory)
- Creative formats
- Publisher properties
- Optional targeting defaults

Products can reference inventory profiles to avoid duplicating configuration.
"""

import json
import logging
import re
from collections.abc import Sequence

from flask import Blueprint, Flask, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AuthorizedProperty,
    InventoryProfile,
    Product,
    PropertyTag,
    Tenant,
)

logger = logging.getLogger(__name__)

inventory_profiles_bp = Blueprint("inventory_profiles", __name__)


def _generate_profile_id(name: str) -> str:
    """Generate a profile_id from name.

    Args:
        name: Human-readable profile name

    Returns:
        URL-safe profile ID (lowercase with underscores)
    """
    # Convert to lowercase, replace spaces/special chars with underscores
    profile_id = re.sub(r"[^a-z0-9_]+", "_", name.lower())
    # Remove leading/trailing underscores
    profile_id = profile_id.strip("_")
    # Collapse multiple underscores
    profile_id = re.sub(r"_+", "_", profile_id)
    return profile_id


def _get_inventory_summary(inventory_config: dict) -> str:
    """Generate human-readable inventory summary.

    Args:
        inventory_config: Inventory configuration dict

    Returns:
        Summary string (e.g., "12 ad units, 3 placements")
    """
    ad_units = len(inventory_config.get("ad_units", []))
    placements = len(inventory_config.get("placements", []))

    parts = []
    if ad_units:
        parts.append(f"{ad_units} ad unit{'s' if ad_units != 1 else ''}")
    if placements:
        parts.append(f"{placements} placement{'s' if placements != 1 else ''}")

    return ", ".join(parts) if parts else "No inventory"


def _get_format_summary(formats: list[dict], tenant_id: str) -> str:
    """Generate human-readable format summary.

    Args:
        formats: List of format ID objects
        tenant_id: Tenant ID for format lookup

    Returns:
        Summary string (e.g., "Display (300x250, 728x90), Video")
    """
    if not formats:
        return "No formats"

    # Group by type for simpler display
    format_names = []
    for fmt in formats[:5]:  # Limit to first 5
        format_names.append(fmt.get("id", "Unknown"))

    summary = ", ".join(format_names)
    if len(formats) > 5:
        summary += f" (+{len(formats) - 5} more)"

    return summary


def _get_property_summary(publisher_properties: list[dict]) -> str:
    """Generate human-readable property summary.

    Args:
        publisher_properties: List of publisher property objects

    Returns:
        Summary string (e.g., "2 domains, 5 properties")
    """
    if not publisher_properties:
        return "No properties"

    domains = {p["publisher_domain"] for p in publisher_properties if "publisher_domain" in p}

    # Count property IDs and tags
    property_count = 0
    tag_count = 0
    for prop in publisher_properties:
        property_count += len(prop.get("property_ids", []))
        tag_count += len(prop.get("property_tags", []))

    parts = [f"{len(domains)} domain{'s' if len(domains) != 1 else ''}"]
    if property_count:
        parts.append(f"{property_count} propert{'ies' if property_count != 1 else 'y'}")
    if tag_count:
        parts.append(f"{tag_count} tag{'s' if tag_count != 1 else ''}")

    return ", ".join(parts)


@inventory_profiles_bp.route("/")
@require_tenant_access()
def list_inventory_profiles(tenant_id: str):
    """List all inventory profiles for this tenant."""
    with get_db_session() as session:
        # Get tenant
        tenant = session.get(Tenant, tenant_id)

        # Get all profiles with product counts in a single query (prevents N+1)
        stmt = (
            select(InventoryProfile, func.count(Product.product_id).label("product_count"))
            .outerjoin(Product, InventoryProfile.id == Product.inventory_profile_id)
            .where(InventoryProfile.tenant_id == tenant_id)
            .group_by(InventoryProfile.id)
            .order_by(InventoryProfile.name)
        )

        results = session.execute(stmt).all()

        # Build profile data with summaries
        profiles_data = []
        for profile, product_count in results:
            profiles_data.append(
                {
                    "id": profile.id,
                    "profile_id": profile.profile_id,
                    "name": profile.name,
                    "description": profile.description,
                    "inventory_summary": _get_inventory_summary(profile.inventory_config),
                    "format_summary": _get_format_summary(profile.format_ids, tenant_id),
                    "property_summary": _get_property_summary(profile.publisher_properties),
                    "product_count": product_count,
                    "created_at": profile.created_at,
                    "updated_at": profile.updated_at,
                }
            )

    return render_template(
        "inventory_unified.html",
        tenant_id=tenant_id,
        tenant=tenant,
        profiles=profiles_data,
        active_tab="inventory_profiles",
    )


@inventory_profiles_bp.route("/add", methods=["GET", "POST"])
@require_tenant_access()
@log_admin_action("create_inventory_profile")
def add_inventory_profile(tenant_id: str):
    """Create new inventory profile."""
    if request.method == "POST":
        try:
            # Parse form data
            form_data = request.form.to_dict()

            # Basic fields
            name = form_data.get("name", "").strip()
            if not name:
                flash("Profile name is required", "error")
                return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

            profile_id = form_data.get("profile_id", "").strip() or _generate_profile_id(name)
            description = form_data.get("description", "").strip()

            # Parse inventory config
            inventory_config = {
                "ad_units": json.loads(form_data.get("targeted_ad_unit_ids", "[]")),
                "placements": json.loads(form_data.get("targeted_placement_ids", "[]")),
                "include_descendants": form_data.get("include_descendants") == "on",
            }

            # Parse formats
            formats = json.loads(form_data.get("formats", "[]"))
            if not formats:
                flash("At least one creative format is required", "error")
                return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

            # Parse publisher properties based on property_mode
            # NOTE: New unified inventory page sends either:
            # - property_mode = "all" or "specific" with full publisher_properties JSON
            # - or legacy modes: "tags", "property_ids", "full"
            publisher_properties: list[dict] = []
            property_mode = form_data.get("property_mode", "tags")

            with get_db_session() as prop_session:
                tenant = prop_session.get(Tenant, tenant_id)
                if not tenant or not tenant.primary_domain:
                    flash("Tenant primary_domain not configured", "error")
                    return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                publisher_domain = tenant.primary_domain

                if property_mode == "tags":
                    # by_tag mode: Parse comma-separated tags
                    property_tags_str = form_data.get("property_tags", "").strip()
                    if not property_tags_str:
                        flash("Property tags are required", "error")
                        return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                    # Validate tag format per AdCP spec
                    import re

                    TAG_PATTERN = re.compile(r"^[a-z0-9_]{2,50}$")
                    property_tags = []
                    for tag in property_tags_str.split(","):
                        tag = tag.strip().lower()
                        if tag and TAG_PATTERN.match(tag):
                            property_tags.append(tag)
                        elif tag:
                            flash(
                                f"Invalid tag format: '{tag}'. Use lowercase letters, numbers, underscores (2-50 chars)",
                                "error",
                            )
                            return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                    if not property_tags:
                        flash("At least one valid property tag is required", "error")
                        return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                    publisher_properties = [
                        {
                            "publisher_domain": publisher_domain,
                            "property_tags": property_tags,
                            "selection_type": "by_tag",
                        }
                    ]

                elif property_mode == "property_ids":
                    # by_id mode: Parse selected_property_ids checkboxes
                    selected_property_ids = request.form.getlist("selected_property_ids")
                    if not selected_property_ids:
                        flash("At least one property must be selected", "error")
                        return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                    # Verify property_ids exist in authorized properties
                    stmt = select(AuthorizedProperty.property_id).filter(
                        AuthorizedProperty.tenant_id == tenant_id,
                        AuthorizedProperty.property_id.in_(selected_property_ids),
                    )
                    existing_ids = set(prop_session.scalars(stmt).all())
                    invalid_ids = set(selected_property_ids) - existing_ids
                    if invalid_ids:
                        flash(f"Invalid property IDs: {', '.join(invalid_ids)}", "error")
                        return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                    publisher_properties = [
                        {
                            "publisher_domain": publisher_domain,
                            "property_ids": selected_property_ids,
                            "selection_type": "by_id",
                        }
                    ]

                elif property_mode in {"full", "all", "specific"}:
                    # "full" is legacy textarea JSON mode
                    # "all"/"specific" are used by the unified inventory page and
                    # send complete publisher_properties JSON in the form field.
                    publisher_properties_json = form_data.get("publisher_properties", "").strip()
                    if publisher_properties_json:
                        try:
                            publisher_properties = json.loads(publisher_properties_json)
                        except json.JSONDecodeError as e:
                            flash(f"Invalid publisher properties JSON: {e}", "error")
                            return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                    if not publisher_properties:
                        flash("Publisher properties are required", "error")
                        return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

            # Parse targeting template (optional)
            targeting_template = None
            targeting_json = form_data.get("targeting_template", "").strip()
            if targeting_json:
                try:
                    targeting_template = json.loads(targeting_json)
                except json.JSONDecodeError as e:
                    flash(f"Invalid targeting template JSON: {e}", "error")
                    return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

            # Create inventory profile
            with get_db_session() as session:
                # Check for duplicate profile_id
                existing = session.scalar(
                    select(InventoryProfile).where(
                        InventoryProfile.tenant_id == tenant_id, InventoryProfile.profile_id == profile_id
                    )
                )
                if existing:
                    flash(f"Inventory profile with ID '{profile_id}' already exists", "error")
                    return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                profile = InventoryProfile(
                    tenant_id=tenant_id,
                    profile_id=profile_id,
                    name=name,
                    description=description if description else None,
                    inventory_config=inventory_config,
                    format_ids=formats,
                    publisher_properties=publisher_properties,
                    targeting_template=targeting_template,
                )

                session.add(profile)
                session.commit()

                flash(f"Inventory profile '{name}' created successfully!", "success")
                return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

        except Exception as e:
            logger.error(f"Error creating inventory profile: {e}", exc_info=True)
            flash(f"Error creating inventory profile: {str(e)}", "error")
            return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

    # GET: Show form
    with get_db_session() as session:
        # Get tenant and check primary_domain upfront
        tenant = session.get(Tenant, tenant_id)
        if not tenant or not tenant.primary_domain:
            flash("Tenant primary_domain must be configured before creating inventory profiles", "warning")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))

        # Get authorized properties for property selection
        authorized_properties = session.scalars(
            select(AuthorizedProperty).where(AuthorizedProperty.tenant_id == tenant_id)
        ).all()

        # Get property tags (as PropertyTag objects, not strings)
        property_tags_list: Sequence[PropertyTag] = session.scalars(
            select(PropertyTag).where(PropertyTag.tenant_id == tenant_id).order_by(PropertyTag.tag_id)
        ).all()

    return render_template(
        "add_inventory_profile.html",
        tenant_id=tenant_id,
        tenant=tenant,
        authorized_properties=authorized_properties,
        property_tags=property_tags_list,
        active_tab="inventory_profiles",
    )


@inventory_profiles_bp.route("/<int:profile_id>/edit", methods=["GET", "POST"])
@require_tenant_access()
@log_admin_action("update_inventory_profile")
def edit_inventory_profile(tenant_id: str, profile_id: int):
    """Edit existing inventory profile."""
    with get_db_session() as session:
        profile = session.get(InventoryProfile, profile_id)

        if not profile or profile.tenant_id != tenant_id:
            flash("Inventory profile not found", "error")
            return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

        if request.method == "POST":
            try:
                # Parse form data (same as add)
                form_data = request.form.to_dict()

                # Update fields
                name = form_data.get("name", "").strip()
                if name:
                    profile.name = name

                description = form_data.get("description", "").strip()
                profile.description = description if description else None

                # Update inventory config
                inventory_config = {
                    "ad_units": json.loads(form_data.get("targeted_ad_unit_ids", "[]")),
                    "placements": json.loads(form_data.get("targeted_placement_ids", "[]")),
                    "include_descendants": form_data.get("include_descendants") == "on",
                }
                profile.inventory_config = inventory_config

                # Update formats
                formats = json.loads(form_data.get("formats", "[]"))
                if formats:
                    profile.format_ids = formats

                # Update publisher properties based on property_mode
                property_mode = form_data.get("property_mode", "tags")

                tenant_obj = session.get(Tenant, tenant_id)
                if not tenant_obj or not tenant_obj.primary_domain:
                    flash("Tenant primary_domain not configured", "error")
                    return redirect(
                        url_for("inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id)
                    )

                publisher_domain = tenant_obj.primary_domain

                if property_mode == "tags":
                    # by_tag mode: Parse comma-separated tags
                    property_tags_str = form_data.get("property_tags", "").strip()
                    if not property_tags_str:
                        flash("Property tags are required", "error")
                        return redirect(
                            url_for(
                                "inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id
                            )
                        )

                    # Validate tag format per AdCP spec
                    import re

                    TAG_PATTERN = re.compile(r"^[a-z0-9_]{2,50}$")
                    property_tags = []
                    for tag in property_tags_str.split(","):
                        tag = tag.strip().lower()
                        if tag and TAG_PATTERN.match(tag):
                            property_tags.append(tag)
                        elif tag:
                            flash(
                                f"Invalid tag format: '{tag}'. Use lowercase letters, numbers, underscores (2-50 chars)",
                                "error",
                            )
                            return redirect(
                                url_for(
                                    "inventory_profiles.edit_inventory_profile",
                                    tenant_id=tenant_id,
                                    profile_id=profile_id,
                                )
                            )

                    if not property_tags:
                        flash("At least one valid property tag is required", "error")
                        return redirect(
                            url_for(
                                "inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id
                            )
                        )

                    profile.publisher_properties = [
                        {
                            "publisher_domain": publisher_domain,
                            "property_tags": property_tags,
                            "selection_type": "by_tag",
                        }
                    ]

                elif property_mode == "property_ids":
                    # by_id mode: Parse selected_property_ids checkboxes
                    selected_property_ids = request.form.getlist("selected_property_ids")
                    if not selected_property_ids:
                        flash("At least one property must be selected", "error")
                        return redirect(
                            url_for(
                                "inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id
                            )
                        )

                    # Verify property_ids exist in authorized properties
                    stmt = select(AuthorizedProperty.property_id).filter(
                        AuthorizedProperty.tenant_id == tenant_id,
                        AuthorizedProperty.property_id.in_(selected_property_ids),
                    )
                    existing_ids = set(session.scalars(stmt).all())
                    invalid_ids = set(selected_property_ids) - existing_ids
                    if invalid_ids:
                        flash(f"Invalid property IDs: {', '.join(invalid_ids)}", "error")
                        return redirect(
                            url_for(
                                "inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id
                            )
                        )

                    profile.publisher_properties = [
                        {
                            "publisher_domain": publisher_domain,
                            "property_ids": selected_property_ids,
                            "selection_type": "by_id",
                        }
                    ]

                elif property_mode == "full":
                    # Legacy full mode: Parse JSON from textarea
                    publisher_properties_json = form_data.get("publisher_properties", "").strip()
                    if publisher_properties_json:
                        try:
                            publisher_properties = json.loads(publisher_properties_json)
                            profile.publisher_properties = publisher_properties
                        except json.JSONDecodeError as e:
                            flash(f"Invalid publisher properties JSON: {e}", "error")
                            return redirect(
                                url_for(
                                    "inventory_profiles.edit_inventory_profile",
                                    tenant_id=tenant_id,
                                    profile_id=profile_id,
                                )
                            )

                # Update targeting template
                targeting_json = form_data.get("targeting_template", "").strip()
                if targeting_json:
                    try:
                        targeting_template = json.loads(targeting_json)
                        profile.targeting_template = targeting_template
                    except json.JSONDecodeError as e:
                        flash(f"Invalid targeting template JSON: {e}", "error")
                        return redirect(
                            url_for(
                                "inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id
                            )
                        )
                else:
                    profile.targeting_template = None

                # Count products using this profile before commit
                product_count = (
                    session.scalar(
                        select(func.count()).select_from(Product).where(Product.inventory_profile_id == profile_id)
                    )
                    or 0
                )

                session.commit()

                # Success message with warning about future updates
                flash(f"Inventory profile '{profile.name}' updated successfully!", "success")
                if product_count > 0:
                    flash(
                        f"Note: This profile is used by {product_count} product(s). "
                        "Changes will affect future media buys using these products. "
                        "Existing campaigns in GAM will not be modified.",
                        "info",
                    )
                return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

            except Exception as e:
                logger.error(f"Error updating inventory profile: {e}", exc_info=True)
                flash(f"Error updating inventory profile: {str(e)}", "error")
                session.rollback()

        # GET: Show form with existing data
        # Get tenant and check primary_domain upfront
        tenant = session.get(Tenant, tenant_id)
        if not tenant or not tenant.primary_domain:
            flash("Tenant primary_domain must be configured before editing inventory profiles", "warning")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))

        authorized_properties = session.scalars(
            select(AuthorizedProperty).where(AuthorizedProperty.tenant_id == tenant_id)
        ).all()

        property_tags_list: Sequence[PropertyTag] = session.scalars(
            select(PropertyTag).where(PropertyTag.tenant_id == tenant_id).order_by(PropertyTag.tag_id)
        ).all()

    return render_template(
        "edit_inventory_profile.html",
        tenant_id=tenant_id,
        tenant=tenant,
        profile=profile,
        authorized_properties=authorized_properties,
        property_tags=property_tags_list,
        active_tab="inventory_profiles",
    )


@inventory_profiles_bp.route("/<int:profile_id>/delete", methods=["DELETE", "POST"])
@require_tenant_access()
@log_admin_action("delete_inventory_profile")
def delete_inventory_profile(tenant_id: str, profile_id: int):
    """Delete inventory profile."""
    with get_db_session() as session:
        profile = session.get(InventoryProfile, profile_id)

        if not profile or profile.tenant_id != tenant_id:
            if request.method == "DELETE":
                return jsonify({"error": "Inventory profile not found"}), 404
            flash("Inventory profile not found", "error")
            return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

        # Check if any products use this profile
        product_count = (
            session.scalar(select(func.count()).select_from(Product).where(Product.inventory_profile_id == profile_id))
            or 0
        )

        if product_count > 0:
            error_msg = f"Cannot delete inventory profile - used by {product_count} product(s)"
            if request.method == "DELETE":
                return jsonify({"error": error_msg}), 400
            flash(error_msg, "error")
            return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

        profile_name = profile.name
        session.delete(profile)
        session.commit()

        if request.method == "DELETE":
            return jsonify({"success": True, "message": f"Inventory profile '{profile_name}' deleted successfully"})

        flash(f"Inventory profile '{profile_name}' deleted successfully", "success")
        return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))


@inventory_profiles_bp.route("/<int:profile_id>/api")
@require_tenant_access(api_mode=True)
def get_inventory_profile_api(tenant_id: str, profile_id: int):
    """Get full inventory profile data for editing (API endpoint)."""
    with get_db_session() as session:
        profile = session.get(InventoryProfile, profile_id)

        if not profile or profile.tenant_id != tenant_id:
            return jsonify({"error": "Inventory profile not found"}), 404

        return jsonify(
            {
                "id": profile.id,
                "profile_id": profile.profile_id,
                "name": profile.name,
                "description": profile.description,
                "inventory_config": profile.inventory_config,
                "targeted_ad_unit_ids": ",".join(profile.inventory_config.get("ad_units", [])),
                "targeted_placement_ids": ",".join(profile.inventory_config.get("placements", [])),
                "include_descendants": profile.inventory_config.get("include_descendants", True),
                "formats": profile.format_ids,
                "publisher_properties": profile.publisher_properties,
                "property_mode": "all",  # Default to "all" mode for now (no DB column yet)
                "targeting_template": profile.targeting_template,
            }
        )


@inventory_profiles_bp.route("/<int:profile_id>/preview")
@require_tenant_access(api_mode=True)
def preview_inventory_profile(tenant_id: str, profile_id: int):
    """Get inventory profile preview for product form (API endpoint)."""
    with get_db_session() as session:
        profile = session.get(InventoryProfile, profile_id)

        if not profile or profile.tenant_id != tenant_id:
            return jsonify({"error": "Inventory profile not found"}), 404

        return jsonify(
            {
                "id": profile.id,
                "profile_id": profile.profile_id,
                "name": profile.name,
                "description": profile.description,
                "ad_unit_count": len(profile.inventory_config.get("ad_units", [])),
                "placement_count": len(profile.inventory_config.get("placements", [])),
                "format_count": len(profile.format_ids),
                "format_summary": _get_format_summary(profile.format_ids, tenant_id),
                "property_summary": _get_property_summary(profile.publisher_properties),
            }
        )


@inventory_profiles_bp.route("/api/list")
@require_tenant_access(api_mode=True)
def list_inventory_profiles_api(tenant_id: str):
    """Get all inventory profiles for this tenant as JSON (API endpoint for unified page)."""
    with get_db_session() as session:
        # Get all profiles with product counts in a single query (prevents N+1)
        stmt = (
            select(InventoryProfile, func.count(Product.product_id).label("product_count"))
            .outerjoin(Product, InventoryProfile.id == Product.inventory_profile_id)
            .where(InventoryProfile.tenant_id == tenant_id)
            .group_by(InventoryProfile.id)
            .order_by(InventoryProfile.name)
        )

        results = session.execute(stmt).all()

        # Build profile data with summaries
        profiles_data = []
        for profile, product_count in results:
            profiles_data.append(
                {
                    "id": profile.id,
                    "profile_id": profile.profile_id,
                    "name": profile.name,
                    "description": profile.description,
                    "inventory_summary": _get_inventory_summary(profile.inventory_config),
                    "format_summary": _get_format_summary(profile.format_ids, tenant_id),
                    "property_summary": _get_property_summary(profile.publisher_properties),
                    "product_count": product_count,
                    "created_at": profile.created_at.isoformat() if profile.created_at else None,
                    "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
                }
            )

        return jsonify({"profiles": profiles_data, "total": len(profiles_data)})


def register_blueprint(app: Flask):
    """Register inventory profiles blueprint."""
    app.register_blueprint(inventory_profiles_bp, url_prefix="/admin/tenant/<tenant_id>/inventory-profiles")
