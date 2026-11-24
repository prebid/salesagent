"""Admin blueprint for managing inventory profiles.

Inventory profiles are reusable configurations of:
- Ad units and placements (inventory)
- Creative formats
- Publisher properties
- Optional targeting defaults

Products can reference inventory profiles to avoid duplicating configuration.
"""

import logging
import re
from collections.abc import Sequence
from typing import Any

from flask import Blueprint, Flask, abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select

from src.admin.blueprints.products import (
    _format_labels,
    _parse_authored_format_options,
    _publisher_catalog,
    _refuse_unresolved_format_options,
)
from src.admin.form_validation import (
    format_options_refusal,
    parse_json_field,
    refuse_a_stale_selection,
    selectors_or_refusal,
)
from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.integrity import resolve_or_write
from src.core.database.models import (
    InventoryProfile,
    InventoryProfilePublisherSelection,
    Product,
    PropertyTag,
    Tenant,
)
from src.core.format_resolver import format_contract
from src.core.format_satisfaction import refuse_outside_common_intersection
from src.core.helpers.publisher_property_helpers import (
    RenderedSelection,
    SelectableProperty,
    id_selection,
    judge_selection_save,
    profile_form_prefill,
    selection_digest,
    selector_selection,
    tag_selection,
)
from src.core.operator_errors import FormIssue, InvalidFormInput, StateConflict
from src.core.publisher_format_catalog import options_by_key, resolve_declarations

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


def _parse_targeting_template(form_data: dict[str, str]) -> Any:
    """The form's optional targeting template, parsed; ``None`` when the field is blank."""
    return parse_json_field("targeting_template", form_data.get("targeting_template"))


def _inventory_config_from_form(form_data: dict[str, str]) -> dict[str, Any]:
    """The form's ad units and placements (JSON lists of ids) and whether descendants are included."""
    return {
        "ad_units": parse_json_field("targeted_ad_unit_ids", form_data.get("targeted_ad_unit_ids"), blank=[]),
        "placements": parse_json_field("targeted_placement_ids", form_data.get("targeted_placement_ids"), blank=[]),
        "include_descendants": form_data.get("include_descendants") == "on",
    }


def _selectors_from_form(
    authorized: Sequence[SelectableProperty], property_mode: str, stored: list | None = None
) -> list[dict]:
    """The form's property selection as selectors, one per publisher, in any of its modes.

    Each selector names the publisher whose *authorized* properties it matches, never the
    tenant's own host: a tenant is a seller representing many publishers (#1845), and AdCP
    3.2.7 ``core/publisher-property-selector.json`` makes property IDs publisher-scoped. A
    property no publisher authorizes this agent for is not offered, and is refused if
    submitted anyway, because a product would not sell it (``PublisherCatalog.refs``). The
    product forms validate through the same ``tag_selection`` / ``id_selection``. The JSON
    modes are held to the rule ``get_products`` sells stored selectors by
    (``selector_selection``), so every mode offers what it accepts; a selector the profile
    already *stored* is carried as it is.

    An unusable selection raises :class:`InvalidFormInput` naming the field it came from.
    """
    if property_mode == "tags":
        tags = [tag for tag in (t.strip().lower() for t in request.form.get("property_tags", "").split(",")) if tag]
        return selectors_or_refusal("property_tags", tag_selection(tags, authorized))
    if property_mode in _JSON_MODES:
        document = parse_json_field("publisher_properties", request.form.get("publisher_properties"), blank=[])
        return selectors_or_refusal("publisher_properties", selector_selection(document, authorized, stored or []))
    return selectors_or_refusal(
        "selected_property_ids", id_selection(request.form.getlist("selected_property_ids"), authorized)
    )


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
    """One label per format the profile declares, read off its format contract.

    A publisher-backed declaration is labelled as its publisher's current catalog entry.
    """
    options = options_by_key(profile.tenant.publishers) if profile.format_options else {}
    declarations = resolve_declarations(profile.format_options, options) if profile.format_options else None
    return _format_labels(format_contract(format_options=declarations, format_ids=profile.format_ids))


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


class InventoryProfileInUse(StateConflict):
    """A profile that products link cannot be deleted; the exception's text carries the count for the log."""

    MESSAGE = "Cannot delete an inventory profile that products use"


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
                    "property_summary": _get_property_summary(profile.stored_publisher_properties),
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
        # Parse form data
        form_data = request.form.to_dict()

        # Basic fields
        name = form_data.get("name", "").strip()
        if not name:
            raise InvalidFormInput(FormIssue("name", "Profile name is required"))

        profile_id = form_data.get("profile_id", "").strip() or _generate_profile_id(name)
        description = form_data.get("description", "").strip()

        # Parse inventory config
        inventory_config = _inventory_config_from_form(form_data)

        # Parse publisher properties based on property_mode
        # NOTE: New unified inventory page sends either:
        # - property_mode = "all" or "specific" with full publisher_properties JSON
        # - or legacy modes: "tags", "property_ids", "full"
        publisher_properties: list[dict] = []
        property_mode = form_data.get("property_mode", "tags")

        with get_db_session() as prop_session:
            if prop_session.get(Tenant, tenant_id) is None:
                abort(404)

            # The profile's canonical format declarations: what every linked product is
            # announced from. format_ids is not authored here (core/product.json: "Do not
            # author a new product from format_ids alone"); a legacy projection comes from
            # the declarations' v1_format_ref.
            catalog = _publisher_catalog(prop_session, tenant_id)
            format_options = _parse_authored_format_options(form_data.get("format_options"))
            if not format_options:
                raise format_options_refusal("At least one canonical format declaration (format_options) is required")

            if property_mode in {"tags", "property_ids"} | _JSON_MODES:
                publisher_properties = _selectors_from_form(catalog.refs, property_mode)
            _refuse_unresolved_format_options(format_options, publisher_properties, catalog)

        # Parse targeting template (optional)
        targeting_template = _parse_targeting_template(form_data)

        # Create inventory profile
        with get_db_session() as session:
            # Check for duplicate profile_id. uq_inventory_profile is the
            # authority, so this same answer serves the loser of a concurrent
            # create rather than a flash carrying the driver's message.
            existing_profile_stmt = select(InventoryProfile).where(
                InventoryProfile.tenant_id == tenant_id, InventoryProfile.profile_id == profile_id
            )

            profile = InventoryProfile(
                tenant_id=tenant_id,
                profile_id=profile_id,
                name=name,
                description=description if description else None,
                inventory_config=inventory_config,
                format_options=format_options,
                publisher_selections=InventoryProfilePublisherSelection.from_selectors(tenant_id, publisher_properties),
                targeting_template=targeting_template,
            )

            conflict = resolve_or_write(
                session,
                conflict=lambda: session.scalar(existing_profile_stmt),
                write=lambda: session.add(profile),
                constraint="uq_inventory_profile",
            )
            if conflict is not None:
                raise InvalidFormInput(
                    FormIssue("profile_id", f"Inventory profile with ID '{profile_id}' already exists")
                )
            session.commit()

            flash(f"Inventory profile '{name}' created successfully!", "success")
            return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))

    # GET: Show form
    with get_db_session() as session:
        tenant = session.get(Tenant, tenant_id)

        # The properties the publishers authorize now; nothing else is selectable.
        catalog = _publisher_catalog(session, tenant_id)
        authorized_properties = catalog.refs

        # Get property tags (as PropertyTag objects, not strings)
        property_tags_list: Sequence[PropertyTag] = session.scalars(
            select(PropertyTag).where(PropertyTag.tenant_id == tenant_id).order_by(PropertyTag.tag_id)
        ).all()

    return render_template(
        "add_inventory_profile.html",
        tenant_id=tenant_id,
        tenant=tenant,
        authorized_properties=authorized_properties,
        publisher_format_options=list(catalog.options.values()),
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
            abort(404)
        catalog = _publisher_catalog(session, tenant_id)

        if request.method == "POST":
            # The save is judged against the selection the page rendered, not one recomputed now.
            selection_save = judge_selection_save(
                request.form.get("rendered_selection"),
                request.form.getlist,
                selection_digest(profile.stored_publisher_properties),
            )
            refuse_a_stale_selection(selection_save, "profile")

            # Parse form data (same as add)
            form_data = request.form.to_dict()

            # Update fields
            name = form_data.get("name", "").strip()
            if name:
                profile.name = name

            description = form_data.get("description", "").strip()
            profile.description = description if description else None

            # Update inventory config
            inventory_config = _inventory_config_from_form(form_data)
            profile.inventory_config = inventory_config

            if session.get(Tenant, tenant_id) is None:
                abort(404)

            # The canonical arm. An empty editor leaves the stored arms alone, so a
            # legacy-only profile stays editable; authoring declarations retires its
            # stored format_ids, which its linked products no longer read once the
            # profile carries a canonical arm (``Product.format_contract``). A refusal is
            # answered by the admin error handler, and the session closes without a commit.
            authored_format_options = _parse_authored_format_options(form_data.get("format_options"))
            if authored_format_options:
                profile.format_options = authored_format_options
                profile.format_ids = None

            # Update publisher properties based on property_mode
            property_mode = form_data.get("property_mode", "tags")

            # A save posting what the page rendered leaves the stored selection as it is.
            if property_mode in {"tags", "property_ids"} | _JSON_MODES and selection_save == "changed":
                publisher_properties = _selectors_from_form(
                    catalog.refs, property_mode, profile.stored_publisher_properties
                )
                profile.publisher_selections = InventoryProfilePublisherSelection.from_selectors(
                    tenant_id, publisher_properties
                )

            # A publisher-backed format option must resolve for the profile as saved.
            _refuse_unresolved_format_options(profile.format_options, profile.stored_publisher_properties, catalog)

            profile.targeting_template = _parse_targeting_template(form_data)

            # The profile supplies its linked products' contract and publisher properties,
            # so each of them is held to the common-intersection rule as its own save is.
            for linked in profile.products:
                refuse_outside_common_intersection(linked, format_options_refusal)

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

        # GET: Show form with existing data
        tenant = session.get(Tenant, tenant_id)

        authorized_properties = list(catalog.refs)

        property_tags_list: Sequence[PropertyTag] = session.scalars(
            select(PropertyTag).where(PropertyTag.tenant_id == tenant_id).order_by(PropertyTag.tag_id)
        ).all()

        prefill = profile_form_prefill(profile.stored_publisher_properties, authorized_properties)
        rendered_selection = RenderedSelection(prefill.fields(), selection_digest(profile.stored_publisher_properties))

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
            publisher_format_options=list(catalog.options.values()),
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
            abort(404)

        # Check if any products use this profile
        product_count = (
            session.scalar(select(func.count()).select_from(Product).where(Product.inventory_profile_id == profile_id))
            or 0
        )

        if product_count > 0:
            raise InventoryProfileInUse(f"Inventory profile {profile_id} is used by {product_count} product(s)")

        profile_name = profile.name
        session.delete(profile)
        session.commit()

        if request.method == "DELETE":
            return jsonify({"success": True, "message": f"Inventory profile '{profile_name}' deleted successfully"})

        flash(f"Inventory profile '{profile_name}' deleted successfully", "success")
        return redirect(url_for("inventory_profiles.list_inventory_profiles", tenant_id=tenant_id))


@inventory_profiles_bp.route("/<int:profile_id>/api")
@require_tenant_access()
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
                "publisher_properties": profile.stored_publisher_properties,
                "property_mode": "all",  # Default to "all" mode for now (no DB column yet)
                "targeting_template": profile.targeting_template,
            }
        )


@inventory_profiles_bp.route("/<int:profile_id>/preview")
@require_tenant_access()
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
                "property_summary": _get_property_summary(profile.stored_publisher_properties),
            }
        )


@inventory_profiles_bp.route("/api/list")
@require_tenant_access()
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
                    "property_summary": _get_property_summary(profile.stored_publisher_properties),
                    "product_count": product_count,
                    "created_at": profile.created_at.isoformat() if profile.created_at else None,
                    "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
                }
            )

        return jsonify({"profiles": profiles_data, "total": len(profiles_data)})


def register_blueprint(app: Flask):
    """Register inventory profiles blueprint."""
    app.register_blueprint(inventory_profiles_bp, url_prefix="/admin/tenant/<tenant_id>/inventory-profiles")
