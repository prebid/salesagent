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

from flask import Blueprint, Flask, Response, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.admin.blueprints.products import _format_labels, _parse_authored_format_options
from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.admin.utils.operator_errors import safe_error_message
from src.core.database.database_session import get_db_session
from src.core.database.integrity import resolve_or_write
from src.core.database.models import (
    InventoryProfile,
    Product,
    PropertyTag,
    Tenant,
)
from src.core.database.repositories.authorized_property import AuthorizedPropertyRepository
from src.core.format_resolver import format_contract
from src.core.format_satisfaction import refuse_outside_common_intersection
from src.core.helpers.publisher_property_helpers import (
    STALE_SELECTION_MESSAGE,
    RenderedSelection,
    id_selection,
    judge_selection_save,
    profile_form_prefill,
    selection_digest,
    selector_selection,
    tag_selection,
)
from src.core.publisher_format_catalog import PublisherCatalog

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


#: The property modes whose form field holds the selectors as JSON. ``all`` and ``specific``
#: are what the unified inventory page sends; ``full`` is the profile pages' legacy mode.
_JSON_MODES = frozenset({"full", "all", "specific"})


def _selectors_from_form(
    session: Session, tenant_id: str, property_mode: str, stored: list | None = None
) -> tuple[list[dict], str | None]:
    """The form's property selection as selectors, one per publisher, in any of its modes.

    Each selector names the publisher whose verified authorized properties it matches,
    never the tenant's own host: a tenant is a seller representing many publishers (#1845),
    and AdCP 3.2.7 ``core/publisher-property-selector.json`` makes property IDs
    publisher-scoped. A pending property is not offered, and is refused if submitted anyway,
    because a product would not sell it (``AuthorizedPropertyRepository.list_refs``). The
    product forms validate through the same ``tag_selection`` / ``id_selection``. The JSON
    modes are held to the rule ``get_products`` sells stored selectors by
    (``selector_selection``), so every mode offers what it accepts; a selector the profile
    already *stored* is carried as it is.

    Returns ``(selectors, None)``, or ``([], message)`` when the selection is unusable.
    """
    authorized = AuthorizedPropertyRepository(session, tenant_id).list_refs()
    if property_mode == "tags":
        tags = [tag for tag in (t.strip().lower() for t in request.form.get("property_tags", "").split(",")) if tag]
        return tag_selection(tags, authorized)
    if property_mode in _JSON_MODES:
        raw = request.form.get("publisher_properties", "").strip()
        try:
            return selector_selection(json.loads(raw) if raw else [], authorized, stored or [])
        except json.JSONDecodeError as e:
            return [], f"Invalid publisher properties JSON: {e}"
    return id_selection(request.form.getlist("selected_property_ids"), authorized)


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


def _profile_format_labels(profile: InventoryProfile) -> list[str]:
    """One label per format the profile declares, read off its format contract."""
    return _format_labels(format_contract(format_options=profile.format_options, format_ids=profile.format_ids))


def _get_format_summary(profile: InventoryProfile) -> str:
    """The profile's formats as a short human-readable list, at most five named."""
    labels = _profile_format_labels(profile)
    if not labels:
        return "No formats"
    summary = ", ".join(labels[:5])
    if len(labels) > 5:
        summary += f" (+{len(labels) - 5} more)"
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


@inventory_profiles_bp.after_request
def _answer_a_fetch_refusal_as_json(response: Response) -> Response:
    """A POST refused with a flash and a redirect is a 400 with the reason, to a fetch that asks.

    ``fetch`` follows the redirect to a 200 and reads it as success, and the redirected GET
    consumes the flash, so the inventory page's modal reported a profile it had not created.
    A browser form still gets the flash and the redirect.
    """
    refused = response.status_code in (301, 302, 303) and request.method == "POST"
    if not refused or request.accept_mimetypes.best_match(["text/html", "application/json"]) != "application/json":
        return response
    errors = [message for category, message in session.get("_flashes", []) if category == "error"]
    if not errors:
        return response
    session.pop("_flashes")
    refusal = jsonify({"error": errors[0]})
    refusal.status_code = 400
    return refusal


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
                    "format_summary": _get_format_summary(profile),
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

            # Parse publisher properties based on property_mode
            # NOTE: New unified inventory page sends either:
            # - property_mode = "all" or "specific" with full publisher_properties JSON
            # - or legacy modes: "tags", "property_ids", "full"
            publisher_properties: list[dict] = []
            property_mode = form_data.get("property_mode", "tags")

            with get_db_session() as prop_session:
                tenant = prop_session.get(Tenant, tenant_id)
                if not tenant:
                    flash("Tenant not found", "error")
                    return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                # The profile's canonical format declarations: what every linked product is
                # announced from. format_ids is not authored here (core/product.json: "Do not
                # author a new product from format_ids alone"); a legacy projection comes from
                # the declarations' v1_format_ref.
                try:
                    format_options = _parse_authored_format_options(
                        form_data.get("format_options"), PublisherCatalog.of(tenant)
                    )
                except ValueError as e:
                    flash(str(e), "error")
                    return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))
                if not format_options:
                    flash("At least one canonical format declaration (format_options) is required", "error")
                    return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

                if property_mode in {"tags", "property_ids"} | _JSON_MODES:
                    publisher_properties, error = _selectors_from_form(prop_session, tenant_id, property_mode)
                    if error:
                        flash(error, "error")
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
                # Check for duplicate profile_id. uq_inventory_profile is the
                # authority, so this same answer serves the loser of a concurrent
                # create rather than a flash carrying the driver's message.
                existing_profile_stmt = select(InventoryProfile).where(
                    InventoryProfile.tenant_id == tenant_id, InventoryProfile.profile_id == profile_id
                )

                def already_exists():
                    existing = session.scalar(existing_profile_stmt)
                    if existing:
                        flash(f"Inventory profile with ID '{profile_id}' already exists", "error")
                        return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))
                    return None

                profile = InventoryProfile(
                    tenant_id=tenant_id,
                    profile_id=profile_id,
                    name=name,
                    description=description if description else None,
                    inventory_config=inventory_config,
                    format_options=format_options,
                    publisher_properties=publisher_properties,
                    targeting_template=targeting_template,
                )

                conflict = resolve_or_write(
                    session,
                    conflict=already_exists,
                    write=lambda: session.add(profile),
                    constraint="uq_inventory_profile",
                )
                if conflict is not None:
                    return conflict
                session.commit()

                flash(f"Inventory profile '{name}' created successfully!", "success")
                return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

        except Exception as e:
            logger.error(f"Error creating inventory profile: {e}", exc_info=True)
            flash(f"Error creating inventory profile: {safe_error_message(e)}", "error")
            return redirect(url_for("inventory_profiles.add_inventory_profile", tenant_id=tenant_id))

    # GET: Show form
    with get_db_session() as session:
        tenant = session.get(Tenant, tenant_id)

        # The verified properties: a pending one is not selectable
        authorized_properties = AuthorizedPropertyRepository(session, tenant_id).list_refs()

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
                # The save is judged against the selection the page rendered, not one recomputed now.
                selection_save = judge_selection_save(
                    request.form.get("rendered_selection"),
                    request.form.getlist,
                    selection_digest(profile.publisher_properties),
                )
                if selection_save == "stale":
                    flash(STALE_SELECTION_MESSAGE.format(subject="profile"), "error")
                    return redirect(
                        url_for("inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id)
                    )

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

                tenant_obj = session.get(Tenant, tenant_id)
                if not tenant_obj:
                    flash("Tenant not found", "error")
                    return redirect(
                        url_for("inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id)
                    )

                # The canonical arm. An empty editor leaves the stored arms alone, so a
                # legacy-only profile stays editable; authoring declarations retires its
                # stored format_ids, which its linked products no longer read once the
                # profile carries a canonical arm (``Product.format_contract``).
                authored_format_options = _parse_authored_format_options(
                    form_data.get("format_options"), PublisherCatalog.of(tenant_obj)
                )
                if authored_format_options:
                    profile.format_options = authored_format_options
                    profile.format_ids = None

                # Update publisher properties based on property_mode
                property_mode = form_data.get("property_mode", "tags")

                # A save posting what the page rendered leaves the stored selection as it is.
                if property_mode in {"tags", "property_ids"} | _JSON_MODES and selection_save == "changed":
                    publisher_properties, error = _selectors_from_form(
                        session, tenant_id, property_mode, profile.publisher_properties
                    )
                    if error:
                        flash(error, "error")
                        return redirect(
                            url_for(
                                "inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id
                            )
                        )
                    profile.publisher_properties = publisher_properties

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

                # The profile supplies its linked products' contract and publisher properties,
                # so each of them is held to the common-intersection rule as its own save is.
                # Refused like the product form refuses it: flash and return to the form
                # without committing, so nothing renders from a rolled-back row.
                try:
                    for linked in profile.products:
                        refuse_outside_common_intersection(linked)
                except ValueError as e:
                    flash(str(e), "error")
                    return redirect(
                        url_for("inventory_profiles.edit_inventory_profile", tenant_id=tenant_id, profile_id=profile_id)
                    )

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
                flash(f"Error updating inventory profile: {safe_error_message(e)}", "error")
                session.rollback()

        # GET: Show form with existing data
        tenant = session.get(Tenant, tenant_id)

        authorized_properties = AuthorizedPropertyRepository(session, tenant_id).list_refs()

        property_tags_list: Sequence[PropertyTag] = session.scalars(
            select(PropertyTag).where(PropertyTag.tenant_id == tenant_id).order_by(PropertyTag.tag_id)
        ).all()

        prefill = profile_form_prefill(profile.publisher_properties, authorized_properties)
        rendered_selection = RenderedSelection(prefill.fields(), selection_digest(profile.publisher_properties))

        # Rendered inside the session: a POST that failed in the handler above rolled back,
        # which expires ``profile``, and the form reads it back from the stored row.
        return render_template(
            "edit_inventory_profile.html",
            tenant_id=tenant_id,
            tenant=tenant,
            profile=profile,
            prefill=prefill,
            rendered_selection=rendered_selection,
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
                "format_options": profile.format_options,
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
                "format_count": len(_profile_format_labels(profile)),
                "format_summary": _get_format_summary(profile),
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
                    "format_summary": _get_format_summary(profile),
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
